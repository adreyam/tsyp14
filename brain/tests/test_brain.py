"""
City Brain tests:  python -m pytest brain/tests -q   (from the triplet root)

They cover the three contracts, the live adapter against a TCP server that speaks the Twin protocol, the
orchestrator rules (trust gate, conflict veto, quarantine, cooldown), and the HTTP API (auth, validation).
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

BRAIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BRAIN, "src"), BRAIN]

from pydantic import ValidationError  # noqa: E402

from citybrain.brain import Brain  # noqa: E402
from citybrain.config import load_config  # noqa: E402
from citybrain.contracts import Action, ActionTarget, Event, Verdict, zone_of  # noqa: E402
from citybrain.sources import LiveTwinSource  # noqa: E402
from citybrain.twin import Normaliser, xz_from_twin_location  # noqa: E402


def cfg(tmp_path, **over):
    c = load_config(None, {"source": "mock", "logs": {"dir": str(tmp_path)}, **over})
    c.twin.poll_s, c.twin.incidents_poll_s = 0.01, 0.01
    return c


def reading(dev="FLOOD-02", fam="flood", x=-24, z=109, **metrics):
    return Event(source="mock", type="reading",
                 origin={"layer": "twin", "device_id": dev, "family": fam, "kind": "sensor"},
                 location={"zone": zone_of(x, z), "x": x, "z": z},
                 readings={m: {"value": v, "unit": ""} for m, v in metrics.items()})


# ---------------------------------------------------------------- contracts
def test_event_example_valid_and_round_trips():
    ev = reading(surface_water_depth=182.0, rise_rate=4.0)
    again = Event(**json.loads(json.dumps(ev.dump())))
    assert again.origin.device_id == "FLOOD-02" and again.location.zone == "J" and again.value("surface_water_depth") == 182.0


@pytest.mark.parametrize("bad, field", [
    ({"readings": {}}, "readings"),
    ({"origin": {"layer": "twin", "device_id": "flood 2", "family": "flood", "kind": "sensor"}}, "device_id"),
    ({"surprise": 1}, "surprise"),
    ({"location": {"zone": "Q", "x": 0, "z": 0}}, "zone"),
])
def test_event_refuses_bad_input_with_a_clear_message(bad, field):
    base = reading(surface_water_depth=1.0).dump()
    base.update(bad)
    with pytest.raises(ValidationError) as e:
        Event(**base)
    assert field in str(e.value)


def test_verdict_needs_reason_when_not_trusted():
    with pytest.raises(ValidationError):
        Verdict(source="mock", device_id="FLOOD-02", status="untrusted", confidence=0.9)
    assert Verdict(source="mock", device_id="FLOOD-02", status="trusted", confidence=0.9).status == "trusted"


def test_action_maps_to_the_exact_twin_request():
    a = Action(trace_id="tr-1", source="live", agent="emergency", action_type="dispatch_unit", severity="critical",
               priority=0, reason="fire", confidence=0.9,
               target=ActionTarget(actuator_id="INCIDENTS", command="dispatch", value={"id": "INC-1", "kind": "fire"}))
    assert a.twin_request() == {"id": a.action_id, "method": "command",
                                "params": {"id": "INCIDENTS", "command": "dispatch", "value": {"id": "INC-1", "kind": "fire"}}}


def test_zone_rule_matches_the_twin():
    assert zone_of(-380, -380) == "A" and zone_of(379, 379) == "P" and zone_of(6, 6) == "K" and zone_of(-95, -95) == "F"
    assert xz_from_twin_location({"junction": "Junction_r5_c5"}) == (0.0, 0.0)
    assert xz_from_twin_location({"edge": "E_r2c3_r2c4"}) == (-142.5, -285.0)


def test_normaliser_deadband_and_bad_rows():
    n = Normaliser("live", [], {"default": 0.5}, heartbeat_s=60)
    row = {"id": "AIR-01", "family": "air", "zone": "F", "location": {"x": -137, "z": -130},
           "readings": {"pm2_5": {"value": 8.0, "unit": "µg/m³"}}}
    assert len(n.rows({"devices": [row]})) == 1
    assert len(n.rows({"devices": [row]})) == 0          # unchanged: not re-sent
    row["readings"]["pm2_5"]["value"] = 9.0
    assert len(n.rows({"devices": [row, {"no": "id"}]})) == 1
    assert n.rejected


# ---------------------------------------------------------------- live adapter on the real wire format
@pytest.fixture
def mock_twin_server():
    from mocks.twin_standin import MockCity
    from citybrain.twin import TwinError
    city = MockCity()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        buf = b""
        while True:
            data = conn.recv(65536)
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                req = json.loads(line)
                try:
                    out = {"id": req["id"], "ok": True, "result": city.request(req["method"], req.get("params"))}
                except TwinError as e:
                    out = {"id": req["id"], "ok": False, "error": {"code": e.code, "message": e.message}}
                conn.sendall((json.dumps(out) + "\n").encode())

    threading.Thread(target=serve, daemon=True).start()
    yield port, city
    srv.close()


def test_live_source_reads_and_commands_over_tcp(tmp_path, mock_twin_server):
    port, city = mock_twin_server
    c = cfg(tmp_path)
    c.twin.port = port
    src = LiveTwinSource(c)
    events = src.poll()
    assert any(e.origin.device_id == "FLOOD-02" and e.source == "live" for e in events)
    a = Action(trace_id="tr-x", source="live", agent="pollution", action_type="parking_barrier", severity="alert",
               priority=2, reason="test", confidence=0.9, target=ActionTarget(actuator_id="LOT-06", command="closed"))
    fb = src.send(a)
    assert fb.state["ok"] and city.devices["LOT-06"].state["barrier_mode"] == "closed" and fb.trace_id == "tr-x"
    bad = a.model_copy(update={"target": ActionTarget(actuator_id="LOT-06", command="explode")})
    assert src.send(bad).state["error"]["code"] == "bad_command"


def test_live_source_reports_unreachable_twin(tmp_path):
    c = cfg(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        c.twin.port = s.getsockname()[1]
    src = LiveTwinSource(c)
    assert src.poll() == []
    st = src.status()
    assert not st["connected"] and "run_control_room.sh" in st["last_error"]


# ---------------------------------------------------------------- orchestrator rules
def test_untrusted_reading_is_ignored_with_reason(tmp_path):
    b = Brain(cfg(tmp_path))
    for _ in range(3):
        b.step()
    b.guardian.g.force("FLOOD-02", "untrusted", "test: spoofed")
    assert b.orch.ingest(reading(surface_water_depth=400.0, rise_rate=10.0)) == []
    assert any(r["kind"] == "ignored" and "spoofed" in r["summary"] for r in b.audit.since(0))


def test_trusted_flood_closes_the_zone_car_park(tmp_path):
    b = Brain(cfg(tmp_path, guardian={"mode": "off", "on_missing": "trust"}))
    for _ in range(3):
        b.step()
    sent = b.orch.ingest(reading(surface_water_depth=200.0, rise_rate=5.0))
    did = [(a.target.actuator_id, a.target.command) for a in sent]
    assert ("LOT-06", "closed") in did                                             # car park closed (A2A)
    assert sum(1 for a in sent if a.target.command == "send_unit" and a.target.value["kind"] == "police") == 2   # detour
    assert any(a.target.command == "send_unit" and a.target.value["kind"] == "ambulance" for a in sent)       # standby
    assert b.orch.ingest(reading(surface_water_depth=205.0, rise_rate=5.0)) == []    # not repeated


def test_quarantined_actuator_is_never_commanded(tmp_path):
    b = Brain(cfg(tmp_path))
    for _ in range(3):
        b.step()
    b.guardian.g.force("LOT-06", "quarantined", "test: tampered barrier")
    b.guardian.g.last.clear()                      # the water rose gradually (no jump)
    b.guardian.g.weather_wet = True                # and it is raining (neighbours check passes)
    sent = b.orch.ingest(reading(surface_water_depth=200.0, rise_rate=5.0))
    assert "LOT-06" not in [a.target.actuator_id for a in sent]
    assert any("quarantined LOT-06" in r["summary"] for r in b.audit.since(0))


def test_malformed_push_is_refused_and_brain_keeps_running(tmp_path):
    b = Brain(cfg(tmp_path))
    ok, why = b.orch.ingest_raw('{"type": ')
    assert not ok and "not JSON" in why
    ok, why = b.orch.ingest_raw({"type": "reading", "source": "mock"})
    assert not ok and "origin" in why
    assert b.step() >= 0


# ---------------------------------------------------------------- HTTP API
def test_http_api_auth_and_validation(tmp_path):
    from http.server import ThreadingHTTPServer
    from citybrain.server import make_handler
    b = Brain(cfg(tmp_path))
    handler, _ = make_handler(b)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/v1/health").read())["status"] == "ok"

        def post(path, body, token=b.cfg.brain.token()):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            try:
                r = urllib.request.urlopen(req)
                return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        assert post("/v1/events", reading(surface_water_depth=1.0).dump(), token="wrong")[0] == 401
        code, body = post("/v1/events", {"type": "reading"})
        assert code == 400 and "origin" in body["error"]["message"]
        assert post("/v1/events", reading(surface_water_depth=1.0).dump())[0] == 202
        top = json.loads(urllib.request.urlopen(base + "/v1/top").read())
        assert set(top["axes"]) == {"emergency", "transportation", "pollution", "energy"}
        assert "event" in json.loads(urllib.request.urlopen(base + "/v1/schemas").read())
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- emergency fleet (stand-in): no teleportation
def test_units_start_at_their_station_and_drive_on_the_roads():
    from mocks.fleet import BLOCK, EDGE, Fleet, LANE, RESPONSE_SPEED, STATIONS

    class City:                                   # just what the fleet reads from the stand-in
        def __init__(self):
            self.t, self.devices, self.incident_kinds = 0.0, {}, {"INC-1": "building_fire"}
            self.incidents = {"INC-1": {"location": {"x": 110.0, "z": -300.0}, "units": []}}

        def _signal(self, d, axis):
            return 2.0

    city = City()
    fleet = Fleet(city)
    u = fleet.dispatch("fire", "INC-1", 110.0, -300.0, 0.0)
    bays = {uid: (x, z) for uid, kind, x, z in STATIONS["FIRE-STATION"]}
    assert u.id in bays and (u.x, u.z) == bays[u.id] and u.status == "mobilizing"   # in its own bay at the fire station
    lines = [-EDGE + BLOCK * k for k in range(9)]
    prev, statuses, dt = (u.x, u.z), set(), 0.1
    for _ in range(600):
        city.t += dt
        fleet.step(dt, city.t)
        statuses.add(u.status)
        assert abs(u.x - prev[0]) + abs(u.z - prev[1]) <= RESPONSE_SPEED * dt + 0.01      # never a jump
        if u.status == "en_route":                # on the road network: on a road centre line
            assert min(abs(u.x - l) for l in lines) < 0.01 or min(abs(u.z - l) for l in lines) < 0.01
        prev = (u.x, u.z)
        if u.status == "on_scene":
            break
    assert {"mobilizing", "inserting", "en_route", "on_scene"} <= statuses
    assert abs(u.x - 95.0) + abs(u.z + 300.0) < 1.0 and LANE > 0


# ---------------------------------------------------------------- A2A and the green wave
def test_a2a_task_unknown_skill_is_rejected_with_the_skills_list(tmp_path):
    b = Brain(cfg(tmp_path))
    t = b.orch.a2a.send("operator", "energy", "fly", {}, "test")
    assert t.state == "rejected" and "isolate_feeder" in t.note
    assert any(r["kind"] == "a2a" and "rejected" in r["summary"] for r in b.audit.since(0))


def test_a2a_task_completes_from_the_twin_answer(tmp_path):
    b = Brain(cfg(tmp_path))
    b.step()
    t = b.orch.a2a.send("operator", "energy", "street_lights_on", {}, "test storm")
    b.step()
    assert t.state == "completed" and "LIGHTS on" in t.note
    assert b.source.city.lights == "on"


def test_fire_asks_energy_and_green_waves_follow_the_units(tmp_path):
    c = cfg(tmp_path, guardian={"mode": "off", "on_missing": "trust"})
    b = Brain(c)
    b.source.city.speed = 6.0
    b.step()
    b.source.city.new_incident("building_fire", 110.0, -300.0)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        b.step()
        time.sleep(0.05)
        done = [t for t in b.orch.a2a.tasks.values() if t.skill == "green_wave" and t.state == "completed"]
        if len(done) >= 2:
            break
    tasks = list(b.orch.a2a.tasks.values())
    assert any(t.skill == "isolate_feeder" and t.from_agent == "emergency" and t.to_agent == "energy" for t in tasks)
    waves = [t for t in tasks if t.skill == "green_wave"]
    assert {t.input["unit"] for t in waves} >= {"FIRE-1", "FIRE-2"}
    assert any(t.state == "completed" and "arrived" in t.note for t in waves)
    acts = [r for r in b.audit.since(0, 100000) if r["kind"] == "action" and r["payload"].get("action_type") == "green_wave"]
    greens = [a for a in acts if a["payload"]["target"]["command"] in ("ns_green", "ew_green")]
    resets = [a for a in acts if a["payload"]["target"]["command"] == "reset_cycle"]
    assert greens and resets                       # junctions turn green ahead of the units and go back behind them


def test_http_a2a_send_and_validation(tmp_path):
    from http.server import ThreadingHTTPServer
    from citybrain.server import make_handler
    b = Brain(cfg(tmp_path))
    tok = b.cfg.brain.token()
    handler, _ = make_handler(b)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body, token=tok):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert post("/v1/a2a/tasks", {"from_agent": "op", "to_agent": "pollution", "skill": "air_status", "input": {"zone": "J"},
                                      "reason": "test"}, token=None)[0] == 401
        code, body = post("/v1/a2a/tasks", {"from_agent": "emergency", "to_agent": "energy", "skill": "isolate_feeder", "reason": "spoof"})
        assert code == 400 and "axis agent" in body["error"]["message"]
        code, body = post("/v1/a2a/tasks", {"from_agent": "op", "to_agent": "water", "skill": "x1", "reason": "nope"})
        assert code == 400 and "to_agent" in body["error"]["message"]
        code, body = post("/v1/a2a/tasks", {"from_agent": "op", "to_agent": "pollution", "skill": "air_status", "input": {"zone": "J"},
                                            "reason": "how is the air?"})
        assert code == 202 and body["state"] == "submitted"
        for _ in range(3):
            b.step()
        with urllib.request.urlopen(f"{base}/v1/a2a/tasks/{body['task_id']}") as r:
            t = json.loads(r.read())
        assert t["state"] == "completed" and t["result"]["zone"] == "J"
        with urllib.request.urlopen(f"{base}/v1/a2a/agents") as r:
            cards = {a["axis"]: [s["id"] for s in a["skills"]] for a in json.loads(r.read())["agents"]}
        assert "green_wave" in cards["transportation"] and "isolate_feeder" in cards["energy"]
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- Test Book layout and the spotlight
def test_test_book_has_three_scenarios_per_axis_and_no_guardian_scenario():
    from citybrain.scenario import load_index, load_scenarios
    groups = {g["axis"]: g["scenarios"] for g in load_index()}
    for axis in ("emergency", "transportation", "pollution", "energy"):
        assert len(groups[axis]) == 3, axis
    sc = load_scenarios()
    assert not [s for s in sc.values() for st in s["steps"] if st["do"] in ("guardian", "guardian_down")]
    assert "jam" in json.dumps(sc[groups["transportation"][0]]).lower()


def test_spotlight_follows_the_units_and_drives_the_twin_view(tmp_path):
    from citybrain.scenario import ScenarioRunner, load_scenarios
    b = Brain(cfg(tmp_path))
    r = ScenarioRunner(b, load_scenarios()["TR02"], drive=True)
    th = threading.Thread(target=r.run, daemon=True)
    th.start()
    followed, deadline = set(), time.monotonic() + 30
    while time.monotonic() < deadline and len(followed) < 2:
        time.sleep(0.3)
        f = (r.view().get("focus") or {}).get("follow")
        if f and f.get("unit"):
            followed.add(f["unit"])
            assert b.source.city.view["following"] in followed          # the Twin's camera chases it too
            assert "J" in r.view()["focus"]["zones"] and "J" in b.source.city.view["zones"]
    r.stop()
    th.join(10)
    assert followed and r.result["verdict"] == "STOPPED"
    assert b.source.city.view["zones"] == [] and b.source.city.view["camera"] == "overview"   # put back

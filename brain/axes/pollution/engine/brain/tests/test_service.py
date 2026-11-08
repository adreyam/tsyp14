"""
The City Brain over the real network path: config file, HTTP clients, Bearer
tokens, and the service, against the mock Twin and mock Guardian served over
HTTP on free local ports. Cycles are driven by hand, so the tests are exact.
"""
import json
import urllib.error
import urllib.request

import pytest

from mocks import serve as mocks
from src.config import BrainSettings, ConfigError, load
from src.http_clients import HttpGuardianClient, PartnerError
from src.models import BinReading
from src.service import BrainService

BRAIN_TOKEN = "brain-token-0123456789"
TWIN_TOKEN = "twin-token-0123456789"


@pytest.fixture
def city(monkeypatch):
    monkeypatch.setenv("BRAIN_TOKEN", BRAIN_TOKEN)
    monkeypatch.setenv("TWIN_TOKEN", TWIN_TOKEN)
    c = mocks.MockCity("http://unset", BRAIN_TOKEN, TWIN_TOKEN, tick_real_s=1.0)
    twin_srv, guardian_srv = mocks.start(c, ("127.0.0.1", 0), ("127.0.0.1", 0),
                                         run_clock=False)
    settings = BrainSettings(
        listen={"host": "127.0.0.1", "port": 0},
        twin={"url": f"http://127.0.0.1:{twin_srv.server_address[1]}"},
        guardian={"url": f"http://127.0.0.1:{guardian_srv.server_address[1]}",
                  "retry_after_s": 0},
        city={"depot_node": "r0c0", "truck_capacity_kg": 200, "reading_interval_s": 300},
        cycle_s=1000)
    service = BrainService(settings)
    httpd = service.serve(block=False)
    port = httpd.server_address[1]
    c.brain_url = f"http://127.0.0.1:{port}/v1/readings"
    c.base = f"http://127.0.0.1:{port}"

    def cycle(n=1):
        for _ in range(n):
            c.step_once()
            service.run_cycle()

    c.cycle, c.service = cycle, service
    yield c
    service.stop()
    for s in (twin_srv, guardian_srv):
        s.shutdown()
        s.server_close()


def post(url, body, token=TWIN_TOKEN, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def events(c, name):
    return c.service.log.of(name)


# ---- end to end over HTTP ----------------------------------------------------------
def test_map_comes_from_the_twin(city):
    loaded = events(city, "map_loaded")
    assert loaded and loaded[0]["nodes"] == 25


def test_red_bin_reaches_the_twin_as_a_dispatch(city):
    city.twin.set_weight("b07", 46.0)                 # 92%: red on its first reading
    city.cycle()
    sent = [e for e in events(city, "dispatch") if e["stop"] == "b07"]
    assert sent, "the Brain should dispatch a truck to the red bin"
    truck = city.twin.trucks[sent[0]["truck_id"]]
    assert truck.target == ("bin", "b07")             # the order arrived over HTTP
    assert city.service.state["bins"]["b07"]["color"] == "red"


def test_spoofed_jump_is_blocked_and_no_truck_goes(city):
    city.cycle(2)
    city.control("spoof", "b06", 98)
    city.cycle(3)
    blocked = [e for e in events(city, "reading_blocked") if e["sensor_id"] == "b06"]
    assert blocked and "jumped" in blocked[0]["reason"]
    assert not [e for e in events(city, "dispatch") if e["stop"] == "b06"]
    assert "b06" in city.service.state["blocked"]


def test_real_fast_fill_is_trusted_and_collected(city):
    city.cycle(2)
    city.control("surge", "b09")
    city.cycle(10)
    assert not [e for e in events(city, "reading_blocked") if e["sensor_id"] == "b09"]
    assert [e for e in events(city, "dispatch") if e["stop"] == "b09"]


def test_guardian_outage_blocks_readings_then_recovers(city):
    city.cycle()
    city.control("guardian_down")
    city.cycle(2)
    assert len(events(city, "guardian_unavailable")) == 1      # logged once
    assert any(e["reason"] == "no valid verdict" for e in events(city, "reading_blocked"))
    city.control("guardian_up")
    city.cycle()
    assert len(events(city, "guardian_recovered")) == 1


def test_twin_refusing_dispatch_is_retried(city):
    city.twin.set_weight("b07", 46.0)
    city.control("twin_offline")
    city.cycle()
    assert events(city, "twin_unavailable")
    assert not events(city, "dispatch")
    city.control("twin_online")
    city.cycle()
    assert [e for e in events(city, "dispatch") if e["stop"] == "b07"]


# ---- inputs and authentication ----------------------------------------------------------
def test_wrong_token_is_refused_and_nothing_is_used(city):
    code, body = post(city.brain_url, [{"tool": "get_sensor_reading"}], token="x" * 20)
    assert code == 401
    assert events(city, "auth_refused")
    assert city.service.run_cycle() == 0


def test_body_that_is_not_json_is_refused(city):
    code, _ = post(city.brain_url, None, raw=b"{not json")
    assert code == 400


def test_malformed_message_is_rejected_with_fields_named(city):
    city.control("malformed", "b03")
    city.cycle()
    err = events(city, "rejected_message")[0]["error"]
    assert "weight_kg" in err and "capacity_kg" in err
    assert city.service.state["bins"]                # the rest kept working


def test_brain_token_is_required_by_the_mocks(city):
    code, _ = post(city.service.twin.dispatch_url, {"truck_id": "t1"}, token="y" * 20)
    assert code == 401


def test_timeline_and_events_are_served(city):
    city.cycle()
    with urllib.request.urlopen(city.base + "/timeline", timeout=3) as r:
        assert b"live timeline" in r.read()
    with urllib.request.urlopen(city.base + "/v1/events?after=0", timeout=3) as r:
        evts = json.loads(r.read())["events"]
    assert evts[0]["seq"] == 0 and all("wall" in e for e in evts)


# ---- clients and config ---------------------------------------------------------------
class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _reading():
    return BinReading(sensor_id="b01", weight_kg=1, capacity_kg=50,
                      location={"lat": 36.8, "lon": 10.18}, zone="z",
                      timestamp="2026-11-01T06:00:00Z")


def test_guardian_client_pauses_after_a_failure():
    clock = FakeClock()
    client = HttpGuardianClient("http://127.0.0.1:9", "t" * 20, timeout_s=0.5,
                                retry_after_s=5, clock=clock)
    with pytest.raises(PartnerError, match="unreachable"):
        client.get_trust_verdict(_reading())
    with pytest.raises(PartnerError, match="next try"):   # no network call while paused
        client.get_trust_verdict(_reading())
    clock.t += 6
    with pytest.raises(PartnerError, match="unreachable"):
        client.get_trust_verdict(_reading())


def test_missing_token_is_a_clear_config_error(monkeypatch):
    monkeypatch.delenv("BRAIN_TOKEN", raising=False)
    settings = BrainSettings(twin={"url": "http://a"}, guardian={"url": "http://b"})
    with pytest.raises(ConfigError, match="BRAIN_TOKEN"):
        settings.brain_token


def test_config_file_names_the_bad_field(tmp_path):
    p = tmp_path / "brain.json"
    p.write_text(json.dumps({"twin": {"url": "http://a"}, "guardian": {}}))
    with pytest.raises(ConfigError, match="guardian.url"):
        load(str(p))


def test_shipped_configs_load():
    for name in ("brain.json", "brain.partners.example.json"):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "config", name)
        s = load(path)
        assert s.twin.url.startswith("http") and s.guardian.url.startswith("http")


# ---- air-quality axis over HTTP ---------------------------------------------------
def test_air_release_closes_the_zone_car_park_over_http(city):
    city.cycle(2)
    city.control("air_release", "D", 60)
    city.cycle(2)
    assert city.service.state["air"]["D"]["level"] == "critical"
    assert city.air.barriers["LOT-04"] == "closed"          # set over HTTP by the Brain
    assert any(e["action"] == "close_parking" and e["target"] == "LOT-04"
               for e in events(city, "air_action"))


def test_air_spike_is_blocked_and_restricts_nothing(city):
    city.cycle(2)
    city.control("air_spike", "AIR-05", 180)
    city.cycle(3)
    assert any(e["sensor_id"] == "AIR-05" for e in events(city, "reading_blocked"))
    assert city.service.state["air"]["C"]["level"] == "normal"
    assert city.air.barriers["LOT-03"] == "auto"

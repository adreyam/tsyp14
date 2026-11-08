"""
The mock Twin and mock Guardian served over HTTP, so the City Brain service can
be run and demonstrated alone over the real network path (R1).

    python -m mocks.serve --config config/mocks.json

Mock Twin      GET  /v1/map        city map
               POST /v1/dispatch   dispatch_truck from the Brain
               every tick: POST <brain_url>/v1/readings with all readings
               GET  /control       demo control page (spoof, outage, bad message...)
Mock Guardian  POST /v1/trust-verdict

Both check the Brain's Bearer token. The mock Twin sends its own token to the Brain.
This file is test scaffolding: the real Twin and Guardian replace it entirely.
"""
import argparse
import hmac
import json
import os
import sys
import threading
import time
from types import SimpleNamespace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional

from demo.city import build_graph, build_twin, map_dict
from mocks.mock_air import MockAirCity
from mocks.mock_guardian import WatchingGuardian
from src.http_clients import PartnerError, request_json

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


def _token(env_name: str) -> str:
    value = os.environ.get(env_name, "")
    if len(value) < 16:
        raise SystemExit(f"[mocks] set {env_name} to a token of at least 16 characters")
    return value


def _authorized(handler, token: str) -> bool:
    header = handler.headers.get("Authorization") or ""
    return header.startswith("Bearer ") and hmac.compare_digest(
        header[7:].encode(), token.encode())


def _read_json(handler, limit: int = 1_000_000):
    length = int(handler.headers.get("Content-Length") or 0)
    if length > limit:
        raise ValueError("body too large")
    return json.loads(handler.rfile.read(length) or b"null")


def _send(handler, code: int, body, content_type: str = "application/json") -> None:
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


class MockCity:
    """Holds the simulated city and both mock partners, and runs the clock."""

    def __init__(self, brain_url: str, brain_token: str, twin_token: str,
                 tick_real_s: float = 1.0, trucks: int = 2, truck_capacity_kg: float = 200.0):
        self.twin = build_twin(build_graph(), trucks=trucks,
                               truck_capacity_kg=truck_capacity_kg)
        self.guardian = WatchingGuardian()
        self.air = MockAirCity(start=self.twin.now, tick_s=self.twin.tick_s)
        self.brain_url = brain_url.rstrip("/") + "/v1/readings"
        self.brain_token = brain_token          # what the Brain must send us
        self.twin_token = twin_token            # what we send the Brain
        self.tick_real_s = tick_real_s
        self.paused = False
        self.lock = threading.Lock()
        self.notes: List[str] = []              # what the control page did, newest last
        self._stop = threading.Event()
        self._brain_down = False
        self._surge: dict = {}                   # bin id -> normal fill rate, while surging

    # ---- simulation clock ------------------------------------------------
    def step_once(self) -> None:
        with self.lock:
            before = {sid: self.twin.bins[sid].weight_kg for sid in self._surge}
            bins, trucks, _ = self.twin.tick()
            for sid, rate in list(self._surge.items()):
                if self.twin.bins[sid].weight_kg < before[sid]:   # emptied by a truck
                    self.twin.bins[sid].rate_kg_per_h = rate
                    del self._surge[sid]
            air, _ = self.air.tick()
            for m in bins + air:
                self.guardian.observe(m)        # the Guardian watches the same stream
        try:
            request_json("POST", self.brain_url, self.twin_token, bins + trucks + air, 2.0)
            if self._brain_down:
                self._brain_down = False
                print("[mock twin] brain reachable again", file=sys.stderr)
        except PartnerError as e:
            if not self._brain_down:
                self._brain_down = True
                print(f"[mock twin] cannot reach the brain: {e}", file=sys.stderr)

    def loop(self) -> None:
        while not self._stop.is_set():
            if not self.paused:
                self.step_once()
            self._stop.wait(self.tick_real_s)

    # ---- demo controls ---------------------------------------------------
    def control(self, action: str, sensor_id: Optional[str] = None,
                value: Optional[float] = None) -> str:
        t, g = self.twin, self.guardian
        with self.lock:
            known = set(t.bins) | set(self.air.sensors) | {"A", "B", "C", "D"}
            if sensor_id is not None and sensor_id not in known:
                raise ValueError(f"unknown sensor or zone '{sensor_id}'")
            if action == "spoof":                   # attack: report a false weight
                pct = 98.0 if value is None else float(value)
                t.reported[sensor_id] = t.bins[sensor_id].capacity_kg * pct / 100.0
                note = f"{sensor_id} now REPORTS {pct:.0f}% (real level unchanged)"
            elif action == "unspoof":
                t.reported.pop(sensor_id, None)
                if hasattr(g, "forget"):
                    b = t.bins[sensor_id]
                    g.forget(sensor_id, baseline=b.weight_kg / b.capacity_kg * 100.0)
                note = f"{sensor_id} reports its real level again"
            elif action == "surge":                 # real incident: the bin fills fast
                # +12 points per tick: fast, but each step is physically plausible,
                # so a real incident is not mistaken for a spoofed jump.
                pct_per_tick = 12.0 if value is None else float(value)
                b = t.bins[sensor_id]
                self._surge.setdefault(sensor_id, b.rate_kg_per_h)
                b.rate_kg_per_h = b.capacity_kg * pct_per_tick / 100.0 * 3600.0 / t.tick_s
                note = f"{sensor_id} REALLY fills fast (+{pct_per_tick:.0f} points per reading)"
            elif action == "silence":
                t.silent.add(sensor_id)
                note = f"{sensor_id} stops sending"
            elif action == "unsilence":
                t.silent.discard(sensor_id)
                note = f"{sensor_id} sends again"
            elif action == "malformed":
                t.extra_bin_messages.append({"tool": "get_sensor_reading",
                                             "sensor_id": sensor_id or "b03",
                                             "weight_kg": -5})
                note = "a malformed reading is sent with the next tick"
            elif action == "twin_offline":
                t.offline = True
                note = "Twin refuses dispatches"
            elif action == "twin_online":
                t.offline = False
                note = "Twin accepts dispatches again"
            elif action == "guardian_down":
                g.down = True
                note = "Guardian is down"
            elif action == "guardian_up":
                g.down = False
                note = "Guardian is back"
            elif action == "air_spike":             # attack: impossible PM2.5 on one sensor
                sid = sensor_id or "AIR-05"
                self.air.reported[sid] = {"pm25": 180.0 if value is None else float(value)}
                note = f"{sid} now REPORTS PM2.5 {self.air.reported[sid]['pm25']:.0f} ug/m3 (fake)"
            elif action == "air_cap":               # cheating: a flat low value
                sid = sensor_id or "AIR-07"
                self.air.reported[sid] = {"pm25": 9.0, "o3": 9.0, "no2": 12.0}
                # scripted: the mock Guardian has no flat-line rule; the real Guardian should
                g.distrust(sid, "flat for 30 min while PM2.5 nearby varies by more than 15 %")
                note = f"{sid} now REPORTS a flat low value (Guardian told: scripted)"
            elif action == "air_stop":
                for sid in list(self.air.reported):
                    g.trust(sid)
                    if hasattr(g, "forget"):
                        g.forget(sid, baseline=self.air.true_air(self.air.sensors[sid], self.air.now)[0])
                self.air.reported.clear()
                note = "air sensors report the real air again"
            elif action == "air_release":           # real incident: industrial release
                zone = sensor_id or "D"
                self.air.event[zone] = 24.0 if value is None else float(value)
                note = f"REAL particle release in zone {zone}"
            elif action == "air_release_stop":
                self.air.event.clear()
                note = "release over"
            elif action == "pause":
                self.paused = True
                note = "simulation paused"
            elif action == "resume":
                self.paused = False
                note = "simulation resumed"
            else:
                raise ValueError(f"unknown action '{action}'")
            stamp = t.now.isoformat()[11:16]
            self.notes.append(f"{stamp}  {note}")
            self.notes = self.notes[-20:]
            return note

    def status(self) -> dict:
        with self.lock:
            t = self.twin
            return {
                "city_time": t.now.isoformat().replace("+00:00", "Z"),
                "paused": self.paused, "twin_offline": t.offline,
                "guardian_down": self.guardian.down,
                "bins": {b.id: {"real_pct": round(b.weight_kg / b.capacity_kg * 100, 1),
                                "reported_pct": round(t.reported[b.id] / b.capacity_kg * 100, 1)
                                if b.id in t.reported else None,
                                "silent": b.id in t.silent,
                                "overflow_kg": round(b.overflow_kg, 1)}
                         for b in t.bins.values()},
                "trucks": {k.id: {"node": k.node, "load_kg": round(k.load_kg, 1),
                                  "status": k.status} for k in t.trucks.values()},
                "air": {sid: {"zone": a.zone, "real_pm25": self.air.true_air(a, self.air.now)[0],
                              "reported_pm25": self.air.reported.get(sid, {}).get("pm25")}
                        for sid, a in self.air.sensors.items()},
                "barriers": dict(self.air.barriers),
                "notes": list(self.notes),
            }

    def stop(self) -> None:
        self._stop.set()


def twin_handler(city: MockCity):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/v1/map":
                if not _authorized(self, city.brain_token):
                    return _send(self, 401, {"error": "missing or wrong Bearer token"})
                return _send(self, 200, map_dict())
            if self.path == "/v1/parking-lots":
                if not _authorized(self, city.brain_token):
                    return _send(self, 401, {"error": "missing or wrong Bearer token"})
                with city.lock:
                    return _send(self, 200, {"lots": [{"id": l, "zone": z, "mode": city.air.barriers[l]}
                                                      for l, z in city.air.lots.items()]})
            if self.path == "/v1/health":
                return _send(self, 200, {"status": "ok", "layer": "mock_twin"})
            if self.path == "/v1/status":
                return _send(self, 200, city.status())
            if self.path in ("/", "/control"):
                with open(os.path.join(STATIC_DIR, "control.html"), "rb") as f:
                    return _send(self, 200, f.read(), "text/html; charset=utf-8")
            _send(self, 404, {"error": "unknown path"})

        def do_POST(self):
            if self.path == "/v1/dispatch":
                if not _authorized(self, city.brain_token):
                    return _send(self, 401, {"error": "missing or wrong Bearer token"})
                try:
                    msg = _read_json(self)
                    with city.lock:
                        if city.twin.offline:
                            return _send(self, 503, {"error": "twin offline (demo control)"})
                        if msg["truck_id"] not in city.twin.trucks:
                            return _send(self, 404, {"error": f"unknown truck {msg['truck_id']}"})
                        city.twin.send_dispatch(msg)
                except (ValueError, KeyError, TypeError) as e:
                    return _send(self, 400, {"error": f"bad dispatch: {e!r}"})
                return _send(self, 200, {"status": "accepted"})
            if self.path == "/v1/parking-barrier":
                if not _authorized(self, city.brain_token):
                    return _send(self, 401, {"error": "missing or wrong Bearer token"})
                try:
                    msg = _read_json(self)
                    with city.lock:
                        if city.twin.offline:
                            return _send(self, 503, {"error": "twin offline (demo control)"})
                        city.air.set_parking_barrier(msg["id"], msg["mode"])
                except (ValueError, KeyError, TypeError) as e:
                    return _send(self, 400, {"error": f"bad barrier command: {e!r}"})
                return _send(self, 200, {"status": "accepted"})
            if self.path == "/v1/control":
                try:
                    body = _read_json(self)
                    note = city.control(body.get("action"), body.get("sensor_id"),
                                        body.get("value"))
                except (ValueError, AttributeError, TypeError) as e:
                    return _send(self, 400, {"error": str(e)})
                return _send(self, 200, {"done": note})
            _send(self, 404, {"error": "unknown path"})

    return Handler


def guardian_handler(city: MockCity):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/v1/health":
                return _send(self, 200, {"status": "ok", "layer": "mock_guardian"})
            _send(self, 404, {"error": "unknown path"})

        def do_POST(self):
            if self.path != "/v1/trust-verdict":
                return _send(self, 404, {"error": "unknown path"})
            if city.guardian.down:
                return _send(self, 503, {"error": "guardian down (demo control)"})
            if not _authorized(self, city.brain_token):
                return _send(self, 401, {"error": "missing or wrong Bearer token"})
            try:
                body = _read_json(self)
                ts = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
                probe = SimpleNamespace(sensor_id=str(body["sensor_id"]), timestamp=ts)
                with city.lock:
                    verdict = city.guardian.get_trust_verdict(probe)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                return _send(self, 400, {"error": f"bad verdict request: {e!r}"})
            _send(self, 200, json.loads(verdict.model_dump_json()))

    return Handler


def start(city: MockCity, twin_addr, guardian_addr, run_clock: bool = True):
    servers = []
    for addr, handler in ((twin_addr, twin_handler(city)),
                          (guardian_addr, guardian_handler(city))):
        httpd = ThreadingHTTPServer(addr, handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, args=(0.1,), daemon=True).start()
        servers.append(httpd)
    if run_clock:
        threading.Thread(target=city.loop, daemon=True, name="mock-clock").start()
    return servers


def load_config(path: Optional[str]) -> dict:
    path = path or os.path.join(os.path.dirname(__file__), "..", "config", "mocks.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_from_config(cfg: dict) -> MockCity:
    auth = cfg.get("auth", {})
    tw = cfg["twin"]
    return MockCity(cfg["brain_url"], _token(auth.get("brain_token_env", "BRAIN_TOKEN")),
                    _token(auth.get("twin_token_env", "TWIN_TOKEN")),
                    tick_real_s=tw.get("tick_real_s", 1.0), trucks=tw.get("trucks", 2),
                    truck_capacity_kg=tw.get("truck_capacity_kg", 200.0))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the mock Twin and mock Guardian.")
    parser.add_argument("--config", help="default config/mocks.json")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    city = build_from_config(cfg)
    tw, gu = cfg["twin"], cfg["guardian"]
    start(city, (tw["host"], tw["port"]), (gu["host"], gu["port"]))
    print(f"[mocks] twin on http://{tw['host']}:{tw['port']}  "
          f"(controls: http://{tw['host']}:{tw['port']}/control)")
    print(f"[mocks] guardian on http://{gu['host']}:{gu['port']}")
    print(f"[mocks] pushing readings to {cfg['brain_url']}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        city.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

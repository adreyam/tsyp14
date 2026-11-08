"""
PLUM test bench server.

Exercises the single PLUM algorithm (plum_engine: full two-station network detection, Kilb et al.
2021) through the emergency agent, exactly as the City Brain uses it. Inputs are MOCK sensor
packets (mocks/mock_plum.py); every result on the page is computed here by the algorithm.

API
- GET  /               -> dashboard/plum_dashboard.html
- GET  /api/health     -> engine status
- GET  /api/stations   -> mock station network (ids, positions)
- POST /api/run        -> {"preset": "local"|"distant"|"spike"|"clipped", "magnitude": 6.5,
                           "distance_km": 0, "bearing_deg": 0, "stream": true, "order": "nearest"|"farthest"|"id"}
                          builds mock packets, feeds them to PLUM (one by one when stream=true, else as
                          one network packet) and returns the result after every packet
- POST /api/packets    -> {"events": [CityEvent, ...]} your own seismic_sensor events, streamed in order

Usage:
    python src/plum_dashboard.py      (http://localhost:8080)
"""
from __future__ import annotations

import http.server
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DASHBOARD_FILE = PROJECT_ROOT / "dashboard" / "plum_dashboard.html"
for _p in (PROJECT_ROOT, SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mocks.mock_city_scenarios import synth_station_network  # noqa: E402
from mocks.mock_plum import CENTER, DURATION_S, FS, PLUM_MOCK_STATIONS  # noqa: E402
from modules.emergency_agent import EmergencyAgent  # noqa: E402
from modules.plum_engine import PlumConfig, Station, StationData, station_intensity  # noqa: E402

PORT = 8080
CFG = PlumConfig()


def _offset(lat: float, lon: float, distance_km: float, bearing_deg: float) -> tuple[float, float]:
    b = math.radians(bearing_deg)
    return (lat + distance_km * math.cos(b) / 111.19,
            lon + distance_km * math.sin(b) / (111.19 * math.cos(math.radians(lat))))


def build_packets(preset: str, magnitude: float, distance_km: float, bearing_deg: float, order: str) -> tuple[list, dict]:
    """Mock per-sensor packets for a preset (the 'true' quake parameters stay here, PLUM never sees them)."""
    start = time.time() - DURATION_S
    epi = _offset(*CENTER, distance_km, bearing_deg)
    spike = PLUM_MOCK_STATIONS[0]["sensor_id"] if preset in {"spike", "clipped"} else None
    net = synth_station_network(PLUM_MOCK_STATIONS, epi[0], epi[1], magnitude=magnitude, fs=FS,
                                duration_s=DURATION_S, spike_station=spike)
    stations = net["stations"]
    if order == "nearest":
        stations = sorted(stations, key=lambda s: (s["lat"] - epi[0]) ** 2 + (s["lon"] - epi[1]) ** 2)
    elif order == "farthest":
        stations = sorted(stations, key=lambda s: -((s["lat"] - epi[0]) ** 2 + (s["lon"] - epi[1]) ** 2))
    if spike:   # the faulty sensor reports first
        stations = sorted(stations, key=lambda s: s["sensor_id"] != spike)
    events = [{
        "source": "mock_seismic_sensor", "kind": "seismic_sensor", "lat": s["lat"], "lon": s["lon"], "ts": start + DURATION_S,
        "data": {"mock": True, "rawWaveformTelemetry": {
            "sensorId": s["sensor_id"], "lat": s["lat"], "lon": s["lon"], "startTime": start, "samplingRateHz": FS,
            "components": s["accel"], "units": "m/s^2",
            "clippingDetected": preset == "clipped" and s["sensor_id"] == spike, "noiseFloorExceeded": False}},
    } for s in stations]
    truth = {"preset": preset, "magnitude": magnitude, "epicenter": epi, "distance_km": distance_km,
             "spike_station": spike, "note": "simulation parameters, not seen by PLUM"}
    return events, truth


def _summary(resp: dict) -> dict:
    return {k: resp.get(k) for k in ("risk_level", "risk_score", "peakMMI", "plumAlertTriggered", "plumDetections",
                                     "plumPending", "plumSuppressed", "stationPeakMMI")}


def run_events(events: list, stream: bool) -> dict:
    agent = EmergencyAgent()            # fresh state for every test
    steps = []
    if stream:
        for ev in events:
            resp = agent.process_event(ev)
            steps.append({"added": ev["data"]["rawWaveformTelemetry"].get("sensorId"), **_summary(resp)})
    else:                               # same packets, one gateway message
        tel = [e["data"]["rawWaveformTelemetry"] for e in events]
        net = {"samplingRateHz": tel[0]["samplingRateHz"], "startTime": tel[0]["startTime"],
               "stations": [{"sensor_id": t["sensorId"], "lat": t["lat"], "lon": t["lon"], "accel": t["components"]} for t in tel]}
        resp = agent.process_event({"source": "mock_seismic_network", "kind": "seismic_sensor", "lat": CENTER[0],
                                    "lon": CENTER[1], "ts": events[0]["ts"], "data": {"mock": True, "stationNetwork": net}})
        steps.append({"added": "whole network", **_summary(resp)})
    # per-second MMI series per station, for the chart (same function PLUM uses)
    series = {}
    for ev in events:
        t = ev["data"]["rawWaveformTelemetry"]
        sd = StationData(Station(t["sensorId"], t["lat"], t["lon"]), float(t["samplingRateHz"]),
                         accel={k: np.asarray(v, dtype=float) for k, v in t["components"].items()})
        series[t["sensorId"]] = [round(float(x), 2) for x in station_intensity(sd, CFG)]
    return {"steps": steps, "final": resp, "mmi_series": series,
            "stations": [{"sensor_id": e["data"]["rawWaveformTelemetry"]["sensorId"], "lat": e["lat"], "lon": e["lon"]} for e in events],
            "thresholds": {"primary_mmi": CFG.primary_mmi, "secondary_mmi": CFG.secondary_mmi,
                           "confirm_window_s": CFG.confirm_window_s, "max_neighbor_km": CFG.max_neighbor_km}}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/plum_dashboard.html"):
            body = DASHBOARD_FILE.read_bytes() if DASHBOARD_FILE.exists() else b"<h1>Dashboard file not found</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/api/health":
            self._json({"status": "ok", "engine": "PLUM network algorithm (PlumStream)", "data": "mock"})
        elif path == "/api/stations":
            self._json({"stations": PLUM_MOCK_STATIONS, "center": CENTER})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/run":
                preset = body.get("preset", "local")
                if preset not in {"local", "distant", "spike", "clipped"}:
                    raise ValueError(f"unknown preset '{preset}'")
                events, truth = build_packets(preset, float(body.get("magnitude", 6.5)), float(body.get("distance_km", 0)),
                                              float(body.get("bearing_deg", 0)), body.get("order", "nearest"))
                return self._json({**run_events(events, bool(body.get("stream", True))), "simulation": truth})
            if self.path == "/api/packets":
                events = body.get("events") or []
                if not events or not all("rawWaveformTelemetry" in (e.get("data") or {}) for e in events):
                    raise ValueError("send {'events': [...]} with rawWaveformTelemetry packets")
                return self._json(run_events(events, True))
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)


def start_server() -> None:
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("", PORT), Handler) as httpd:
        print("=" * 60)
        print("PLUM TEST BENCH  (one algorithm: full two-station network PLUM)")
        print(f"Open: http://localhost:{PORT}   ·   inputs are mock sensor packets")
        print("=" * 60)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


if __name__ == "__main__":
    start_server()

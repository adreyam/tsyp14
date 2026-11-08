"""
A stand-in for the Twin's Godot device API (scripts/sim_api.gd): line-delimited
JSON over one local TCP socket, {"id", "method", "params"} -> {"id", "ok", "result"}.

It lets the Twin's REAL MCP server (mocks/vendor/city_mcp_server.py) run without
Godot, so the Brain's MCP client is tested against the Twin's own code.
Only the waste-bin part of the city is simulated. The answer shapes follow what
city_mcp_server.py reads; fields it does not read (zone, position) are our best
guess and are listed as TO CONFIRM in INTERFACE.md.

    python -m mocks.fake_city_api --port 8820
"""
import argparse
import json
import socket
import socketserver
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

RESIDUE_KG = 0.3          # what a liner weighs after emptying


@dataclass
class FakeBin:
    id: str
    name: str
    capacity_kg: float
    x: float
    z: float
    zone: str
    weight_kg: float = 0.0
    rate_kg_per_s: float = 0.0
    deposits: int = 0

    @property
    def fill(self) -> float:
        return round(self.weight_kg / self.capacity_kg * 100.0, 1)

    def device(self) -> dict:
        return {"id": self.id, "name": self.name, "family": "waste", "kind": "sensor",
                "zone": self.zone, "position": {"x": self.x, "y": 0.17, "z": self.z},
                "readings": {"bin_weight": {"value": round(self.weight_kg, 2), "unit": "kg"},
                             "fill_level": {"value": self.fill, "unit": "%"},
                             "deposits": {"value": self.deposits, "unit": "count"}}}


def default_bins() -> List[FakeBin]:
    zones = "ABCDEFGHIJKLMNOP"
    bins = []
    for i in range(12):
        kind_can = i < 8
        bins.append(FakeBin(
            id=f"WGT-T{i + 1:03d}" if kind_can else f"WGT-C{i - 7:03d}",
            name="Street bin" if kind_can else "Refuse container",
            capacity_kg=25.0 if kind_can else 120.0,
            x=-300.0 + 55.0 * i, z=-200.0 + 37.0 * (i % 5), zone=zones[i % 16],
            weight_kg=(2.0 + 1.7 * i) * (1 if kind_can else 4),
            rate_kg_per_s=(0.02 + 0.006 * i) * (1 if kind_can else 4)))
    return bins


@dataclass
class FakeAir:
    """An air monitor. pm25 in ug/m3, o3 in ppb, no2 in ug/m3 (to exercise the conversion)."""
    id: str
    zone: str
    x: float
    z: float
    pm25: float
    o3: float
    no2_ugm3: float

    def device(self) -> dict:
        return {"id": self.id, "name": "Air quality monitor", "family": "air", "kind": "sensor",
                "zone": self.zone, "position": {"x": self.x, "y": 0.17, "z": self.z},
                "readings": {"pm2_5": {"value": self.pm25, "unit": "ug/m3"},
                             "o3": {"value": self.o3, "unit": "ppb"},
                             "no2": {"value": self.no2_ugm3, "unit": "ug/m3"}}}


def default_air() -> List[FakeAir]:
    return [FakeAir("AIR-01", "A", -200, -150, 9.0, 14.0, 20.0),
            FakeAir("AIR-02", "F", 50, 40, 12.0, 12.0, 30.0),
            FakeAir("AIR-03", "K", 180, 160, 8.0, 15.0, 18.0)]


class FakeCityApi:
    def __init__(self, bins: Optional[List[FakeBin]] = None, speed: float = 1.0,
                 air: Optional[List[FakeAir]] = None):
        self.bins: Dict[str, FakeBin] = {b.id: b for b in (bins if bins is not None else default_bins())}
        self.air: Dict[str, FakeAir] = {a.id: a for a in (air if air is not None else default_air())}
        self.lots: Dict[str, dict] = {"LOT-01": {"id": "LOT-01", "zone": "A", "barrier": "auto"},
                                      "LOT-02": {"id": "LOT-02", "zone": "F", "barrier": "auto"}}
        self.speed = speed                       # 0 = frozen (tests)
        self.lock = threading.Lock()
        self.commands: List[dict] = []           # every command received, for tests
        self._last = time.monotonic()
        self.server: Optional[socketserver.ThreadingTCPServer] = None

    def _advance(self) -> None:
        now = time.monotonic()
        dt, self._last = (now - self._last) * self.speed, now
        for b in self.bins.values():
            b.weight_kg = min(b.capacity_kg * 1.05, b.weight_kg + b.rate_kg_per_s * dt)

    def handle(self, method: str, params: dict):
        with self.lock:
            self._advance()
            if method == "info":
                return {"clock": datetime.now().strftime("%H:%M"), "weather": "clear",
                        "devices": {"waste": len(self.bins)}}
            if method == "list_parking_lots":
                return {"lots": [dict(l) for l in self.lots.values()
                                 if params.get("zone") in (None, l["zone"])]}
            if method == "list_devices":
                devs = [b for b in self.bins.values()
                        if params.get("family") in (None, "", "waste")]
                limit = int(params.get("limit", 50))
                return {"total": len(devs),
                        "devices": [{"id": b.id, "name": b.name, "family": "waste",
                                     "zone": b.zone} for b in devs[:limit]]}
            if method == "read_many":
                return {"devices": [self.bins[i].device() for i in params["ids"] if i in self.bins]}
            if method == "read":
                b = self.bins.get(params.get("id"))
                if b is None:
                    raise KeyError(f"unknown device {params.get('id')}")
                return {**b.device(), "sampled_at": {"clock": "12:00", "frame": 1}}
            if method == "read_all":
                if params.get("family") == "air":
                    return {"devices": [a.device() for a in self.air.values()]}
                return {"devices": [b.device() for b in self.bins.values()]}
            if method == "command":
                self.commands.append(dict(params))
                lot = self.lots.get(params.get("id"))
                if lot is not None:
                    if params.get("command") not in ("auto", "open", "closed"):
                        raise KeyError(f"unsupported barrier mode {params.get('command')}")
                    before = lot["barrier"]
                    lot["barrier"] = params["command"]
                    return {"id": lot["id"], "before": before, "after": lot["barrier"]}
                b = self.bins.get(params.get("id"))
                if b is None:
                    raise KeyError(f"unknown device {params.get('id')}")
                if params.get("command") != "empty":
                    raise KeyError(f"unsupported command {params.get('command')}")
                before = b.device()["readings"]
                b.weight_kg = RESIDUE_KG
                return {"id": b.id, "command": "empty", "before": before,
                        "after": b.device()["readings"]}
            raise KeyError(f"unknown method {method}")

    # ---- control for tests and demos --------------------------------------
    def set_weight(self, bin_id: str, kg: float) -> None:
        with self.lock:
            self.bins[bin_id].weight_kg = kg

    def serve(self, port: int = 0) -> int:
        api = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    if not line.strip():
                        continue
                    req = json.loads(line)
                    try:
                        out = {"id": req.get("id"), "ok": True,
                               "result": api.handle(req.get("method"), req.get("params") or {})}
                    except KeyError as e:
                        out = {"id": req.get("id"), "ok": False,
                               "error": {"code": "not_found", "message": str(e).strip("'\"")}}
                    self.wfile.write((json.dumps(out) + "\n").encode())

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.1,), daemon=True).start()
        return self.server.server_address[1]

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fake Godot device API (waste bins only).")
    p.add_argument("--port", type=int, default=8820)
    p.add_argument("--speed", type=float, default=20.0, help="fill speed multiplier")
    a = p.parse_args(argv)
    api = FakeCityApi(speed=a.speed)
    port = api.serve(a.port)
    print(f"[fake city] device API on 127.0.0.1:{port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        api.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

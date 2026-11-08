"""Mock City Twin (energy part) -- a stand-in so the Brain runs on its own (R1).

It simulates zone loads, tie/feeder switches and a scenario control API used by
the Test Book trigger scripts. The `expose` block of a city file controls how
much the Twin *advertises* about itself, which lets us test the Brain at each
knowledge level (L0 blind / L1 bounded / L2 mapped) with the same code.

Run:  python -m mocks.twin --city mocks/cities/city_a.yaml --port 8101
"""
from __future__ import annotations

import argparse
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ZoneSim:
    zone_id: str
    base_kw: float
    amplitude_kw: float = 0.0
    period_s: float = 120.0
    noise_kw: float = 0.0
    capacity_kw: Optional[float] = None
    tags: list[str] = field(default_factory=list)
    customers: int = 0          # for reliability metrics (customer-minutes interrupted); not exposed


@dataclass
class SwitchSim:
    switch_id: str
    kind: str
    from_zone: str
    to_zone: Optional[str]
    transfer_fraction: float
    state: str
    stuck: bool = False
    phantom: bool = False            # reports the commanded state but does not really move (spoofed status)
    reported: Optional[str] = None


@dataclass
class Ramp:
    zone_id: str
    add_kw: float
    start: float
    rise_s: float
    hold_s: float

    def extra(self, t: float) -> float:
        dt = t - self.start
        if dt < 0 or dt > self.rise_s + self.hold_s:
            return 0.0
        return self.add_kw * min(1.0, dt / self.rise_s) if self.rise_s > 0 else self.add_kw


@dataclass
class Spoof:
    value_kw: float
    until: float


@dataclass
class Drift:
    """Stealthy manipulation: an offset that grows slowly, so no single sample looks like a jump."""
    rate_kw_s: float
    start: float
    until: float

    def offset(self, t: float) -> float:
        return self.rate_kw_s * (min(t, self.until) - self.start) if t >= self.start else 0.0


class CitySim:
    """Pure simulation state. Thread-safe; time is injectable for tests."""

    def __init__(self, spec: dict, clock=time.monotonic, seed: int = 7):
        self.city_id = spec.get("city_id", "mock-city")
        self.expose = {"capacity": True, "switch_effects": True, "tags": True, **spec.get("expose", {})}
        self.zones = {z["zone_id"]: ZoneSim(**{k: v for k, v in z.items()}) for z in spec["zones"]}
        self.switches: dict[str, SwitchSim] = {}
        for s in spec.get("switches", []):
            kind = s["kind"]
            self.switches[s["switch_id"]] = SwitchSim(
                switch_id=s["switch_id"], kind=kind, from_zone=s["from_zone"], to_zone=s.get("to_zone"),
                transfer_fraction=float(s.get("transfer_fraction", 1.0)),
                state=s.get("initial", "open" if kind == "tie" else "closed"),
            )
        self.clock = clock
        self.t0 = clock()
        self.rng = random.Random(seed)
        self.ramps: list[Ramp] = []
        self.spoofs: dict[str, Spoof] = {}
        self.drifts: dict[str, Drift] = {}
        self.meters = spec.get("aggregate_meters", [])           # substation / feeder-head meters (optional)
        self.meter_rng = random.Random(seed + 77)                 # own noise stream: zone noise unchanged
        self.seen_commands: dict[str, dict] = {}
        self.seq = 0
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- physics
    def t(self) -> float:
        return self.clock() - self.t0

    def demand(self, zone_id: str, t: float) -> float:
        z = self.zones[zone_id]
        d = z.base_kw + z.amplitude_kw * math.sin(2 * math.pi * t / z.period_s)
        d += sum(r.extra(t) for r in self.ramps if r.zone_id == zone_id)
        if z.noise_kw:
            d += self.rng.gauss(0, z.noise_kw)
        return max(0.0, d)

    def true_loads(self, t: Optional[float] = None) -> dict[str, float]:
        t = self.t() if t is None else t
        demand = {zid: self.demand(zid, t) for zid in self.zones}
        load = dict(demand)
        for s in self.switches.values():
            if s.kind == "tie" and s.state == "closed" and s.to_zone:
                moved = s.transfer_fraction * demand[s.from_zone]
                load[s.from_zone] -= moved
                load[s.to_zone] += moved
        for s in self.switches.values():
            if s.kind == "feeder" and s.state == "open":
                load[s.from_zone] = 0.0
        return {k: round(max(0.0, v), 2) for k, v in load.items()}

    # ---------------------------------------------------------------- API views
    def descriptor(self) -> dict:
        zones = []
        for z in self.zones.values():
            d = {"zone_id": z.zone_id}
            if self.expose["capacity"] and z.capacity_kw is not None:
                d["capacity_kw"] = z.capacity_kw
            if self.expose["tags"]:
                d["tags"] = z.tags
            zones.append(d)
        switches = []
        for s in self.switches.values():
            d = {"switch_id": s.switch_id}
            if self.expose["switch_effects"]:
                d.update(kind=s.kind, from_zone=s.from_zone, transfer_fraction=s.transfer_fraction)
                if s.to_zone:
                    d["to_zone"] = s.to_zone
            switches.append(d)
        d = {"city_id": self.city_id, "zones": zones, "switches": switches}
        if self.meters and self.expose.get("aggregate_meters", True):
            d["aggregate_meters"] = [{"sensor_id": m["sensor_id"], "zones": m["zones"]} for m in self.meters]
        return d

    def readings(self) -> list[dict]:
        with self.lock:
            now_t = self.t()
            loads = self.true_loads(now_t)
            out = []
            for zid, kw in loads.items():
                sid = f"PL-{zid}"
                sp = self.spoofs.get(sid)
                if sp and now_t <= sp.until:
                    kw = sp.value_kw
                dr = self.drifts.get(sid)
                if dr:
                    kw = max(0.0, kw + dr.offset(now_t))
                self.seq += 1
                out.append({"type": "power_reading", "sensor_id": sid, "zone_id": zid, "ts": _now().isoformat(),
                            "value": kw, "unit": "kW", "seq": self.seq})
            for m in self.meters:   # measures the real power, whatever the zone sensors claim
                self.seq += 1
                kw = sum(loads[z] for z in m["zones"]) + self.meter_rng.gauss(0, m.get("noise_kw", 1.0))
                out.append({"type": "power_reading", "sensor_id": m["sensor_id"], "zone_id": m["sensor_id"],
                            "ts": _now().isoformat(), "value": round(max(0.0, kw), 2), "unit": "kW", "seq": self.seq})
            return out

    def switch_status(self) -> list[dict]:
        with self.lock:
            return [{"type": "switch_status", "switch_id": s.switch_id, "state": s.reported or s.state, "available": True,
                     "ts": _now().isoformat()} for s in self.switches.values()]

    def command(self, cmd: dict) -> dict:
        with self.lock:
            cid = cmd["command_id"]
            if cid in self.seen_commands:
                return {"type": "command_ack", "command_id": cid, "status": "duplicate", "ts": _now().isoformat(),
                        "detail": "command_id already processed; not re-executed"}
            sw = self.switches.get(cmd["switch_id"])
            if sw is None:
                ack = {"status": "rejected", "detail": f"unknown switch_id '{cmd['switch_id']}'"}
            else:
                ack = {"status": "accepted", "detail": None}
                target = "closed" if cmd["operation"] == "close" else "open"
                if sw.phantom:      # status spoofed: says it moved, the breaker did not
                    sw.reported = target
                elif not sw.stuck:  # a stuck switch accepts the command but never moves (actuator failure)
                    sw.state = target
            ack = {"type": "command_ack", "command_id": cid, "ts": _now().isoformat(), **ack}
            self.seen_commands[cid] = ack
            return ack

    # ---------------------------------------------------------------- scenario control
    def add_ramp(self, zone_id: str, add_kw: float, rise_s: float, hold_s: float) -> None:
        with self.lock:
            self.ramps.append(Ramp(zone_id, add_kw, self.t(), rise_s, hold_s))

    def spoof(self, sensor_id: str, value_kw: float, duration_s: float) -> None:
        with self.lock:
            self.spoofs[sensor_id] = Spoof(value_kw, self.t() + duration_s)

    def drift(self, sensor_id: str, rate_kw_s: float, duration_s: float) -> None:
        with self.lock:
            self.drifts[sensor_id] = Drift(rate_kw_s, self.t(), self.t() + duration_s)

    def set_stuck(self, switch_id: str, stuck: bool, phantom: bool = False) -> None:
        with self.lock:
            self.switches[switch_id].stuck = stuck
            self.switches[switch_id].phantom = phantom

    def set_switch(self, switch_id: str, state: str) -> None:
        """Operator/scenario switching (not a Brain command). The Brain can learn from these."""
        with self.lock:
            self.switches[switch_id].state = state


# ======================================================================== HTTP app

class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandIn(_In):
    type: str = "switch_command"
    command_id: str = Field(min_length=1)
    switch_id: str = Field(min_length=1)
    operation: str = Field(pattern="^(open|close)$")
    issued_at: str
    decision_id: str
    reason: str
    verdict_refs: list[str] = []
    degraded: bool = False


class RampIn(_In):
    zone_id: str
    add_kw: float
    rise_s: float = 30.0
    hold_s: float = 120.0


class SpoofIn(_In):
    sensor_id: str
    value_kw: float
    duration_s: float = 30.0


class DriftIn(_In):
    sensor_id: str
    rate_kw_s: float
    duration_s: float = 120.0


class StuckIn(_In):
    switch_id: str
    phantom: bool = False
    stuck: bool = True


class SetSwitchIn(_In):
    switch_id: str
    state: str = Field(pattern="^(open|closed)$")


def create_app(spec: dict, api_key: Optional[str] = None, clock=time.monotonic, seed: int = 7,
               signing_secret: Optional[str] = None) -> FastAPI:
    from ._signing import add_signing
    sim = CitySim(spec, clock=clock, seed=seed)
    app = FastAPI(title="Mock Twin (energy)", version="1.0")
    app.state.sim = sim
    add_signing(app, signing_secret)

    def auth(x_api_key: Optional[str] = Header(default=None)):
        if api_key and x_api_key != api_key:
            raise HTTPException(401, "missing or invalid X-API-Key")

    def need_zone(zid: str):
        if zid not in sim.zones:
            raise HTTPException(404, f"unknown zone_id '{zid}'")

    def need_switch(sid: str):
        if sid not in sim.switches:
            raise HTTPException(404, f"unknown switch_id '{sid}'")

    @app.get("/health")
    def health():
        return {"status": "ok", "layer": "twin-mock", "city_id": sim.city_id}

    @app.get("/energy/city", dependencies=[Depends(auth)])
    def city():
        return sim.descriptor()

    @app.get("/energy/readings", dependencies=[Depends(auth)])
    def readings():
        return sim.readings()

    @app.get("/energy/switches", dependencies=[Depends(auth)])
    def switches():
        return sim.switch_status()

    @app.post("/energy/switches/{switch_id}/command", dependencies=[Depends(auth)])
    def command(switch_id: str, cmd: CommandIn):
        if cmd.switch_id != switch_id:
            raise HTTPException(422, f"path switch '{switch_id}' does not match body switch_id '{cmd.switch_id}'")
        return sim.command(cmd.model_dump())

    # scenario control (used by scenario trigger scripts, never by the Brain)
    @app.post("/_scenario/ramp", dependencies=[Depends(auth)])
    def ramp(r: RampIn):
        need_zone(r.zone_id)
        sim.add_ramp(r.zone_id, r.add_kw, r.rise_s, r.hold_s)
        return {"ok": True}

    @app.post("/_scenario/spoof", dependencies=[Depends(auth)])
    def spoof(s: SpoofIn):
        need_zone(s.sensor_id.removeprefix("PL-"))
        sim.spoof(s.sensor_id, s.value_kw, s.duration_s)
        return {"ok": True}

    @app.post("/_scenario/drift", dependencies=[Depends(auth)])
    def drift(d: DriftIn):
        need_zone(d.sensor_id.removeprefix("PL-"))
        sim.drift(d.sensor_id, d.rate_kw_s, d.duration_s)
        return {"ok": True}

    @app.post("/_scenario/stuck", dependencies=[Depends(auth)])
    def stuck(s: StuckIn):
        need_switch(s.switch_id)
        sim.set_stuck(s.switch_id, s.stuck, s.phantom)
        return {"ok": True}

    @app.post("/_scenario/switch", dependencies=[Depends(auth)])
    def set_switch(s: SetSwitchIn):
        need_switch(s.switch_id)
        sim.set_switch(s.switch_id, s.state)
        return {"ok": True}

    @app.get("/_scenario/truth", dependencies=[Depends(auth)])
    def truth():
        return {"t": round(sim.t(), 2), "loads_kw": sim.true_loads(),
                "switches": {s.switch_id: {"state": s.state, "stuck": s.stuck} for s in sim.switches.values()}}

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default=str(Path(__file__).parent / "cities" / "city_a.yaml"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8101)
    args = ap.parse_args()
    import uvicorn
    spec = yaml.safe_load(Path(args.city).read_text())
    uvicorn.run(create_app(spec, api_key=os.environ.get("TWIN_API_KEY", "dev-twin-key"),
                           signing_secret=os.environ.get("TWIN_SIGNING_SECRET") or None),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

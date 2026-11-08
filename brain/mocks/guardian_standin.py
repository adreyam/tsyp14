"""
Guardian stand-in. It watches the same events as the Brain and gives a trust verdict for each one,
with the checks a real Guardian would make, so the Brain can be shown alone (spec R1):

    1. range       a value outside what the sensor can physically measure            -> untrusted
    2. jump        a change faster than the physics allows (e.g. +200 mm of water in 2 s) -> untrusted
    3. neighbours  one sensor far from every other sensor of its family, no weather
                   or event to explain it (fake flood, fake pollution spike)         -> untrusted
    4. stuck       a sensor repeating the exact same value while its family varies  -> suspect
    5. flooding    a device sending far more messages than its sampling rate        -> quarantined
    6. twin check  a pushed reading that disagrees with what the Twin itself reports -> untrusted

Scenarios can also force a verdict (the Guardian quarantines an actuator) or take the Guardian down.
The verdicts use the same Verdict model a real Guardian must send (brain/INTERFACE.md).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Dict, Optional

from citybrain.contracts import Event, Verdict

RANGES = {
    "surface_water_depth": (0, 1500), "rise_rate": (-900, 900), "pm2_5": (0, 600), "pm10": (0, 900),
    "nitrogen_dioxide": (0, 1000), "vehicle_smoke": (0, 100), "occupancy": (0, 100), "speed": (0, 160),
    "vehicle_count": (0, 200), "shaking_intensity": (1, 12), "voltage": (0, 260), "fill_level": (0, 100),
    "energy_level": (0, 100), "rainfall": (0, 300), "wind_speed": (0, 80), "db_a": (20, 140),
    "occupancy_percent": (0, 100),
}
MAX_STEP_PER_S = {"surface_water_depth": 25.0, "pm2_5": 20.0, "pm10": 30.0, "nitrogen_dioxide": 25.0,
                  "voltage": 60.0, "fill_level": 15.0, "energy_level": 5.0}
SPREAD = {"surface_water_depth": 120.0, "pm2_5": 35.0, "pm10": 50.0, "nitrogen_dioxide": 45.0, "shaking_intensity": 2.5,
          "energy_level": 15.0}
MAX_MSGS_PER_10S = 40
# Air peaks can be local (a fire, a factory): only the jump and Twin checks apply to them.
# Earthquakes are confirmed by the Brain itself (two stations, as PLUM).
NEIGHBOUR_FAMILIES = {"flood"}


class MockGuardian:
    def __init__(self, source: str = "mock"):
        self.source = source
        self.down = False
        self.forced: Dict[str, Verdict] = {}
        self.last: Dict[str, tuple] = {}                    # device -> (readings, monotonic)
        self.family_latest: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)
        self.repeats: Dict[str, int] = defaultdict(int)
        self.rate: Dict[str, deque] = defaultdict(lambda: deque(maxlen=400))
        self.quarantined: Dict[str, str] = {}
        self.twin_view: Dict[str, Dict[str, float]] = {}    # what the Twin itself reports (pulled events)
        self.weather_wet = False                            # manual override (tests): it is raining
        self.wet = False
        self.speed = 1.0                                    # mock city speed: rates are judged in city seconds
        self.wet_at = -1e9

    def force(self, device: str, status: str, reason: str, containment: Optional[str] = None) -> None:
        if status in ("released", "trusted"):
            self.forced.pop(device, None)
            self.quarantined.pop(device, None)
            return
        self.forced[device] = Verdict(source=self.source, device_id=device, status=status, confidence=0.97,
                                      reason=reason, containment=containment or ("quarantine" if status == "quarantined" else "isolate"))
        if status == "quarantined":
            self.quarantined[device] = reason

    def _v(self, ev: Event, status: str, conf: float, reason: Optional[str], containment: Optional[str] = None) -> Verdict:
        return Verdict(source=ev.source, device_id=ev.origin.device_id, event_id=ev.event_id, trace_id=ev.trace_id,
                       status=status, confidence=conf, reason=reason, containment=containment)

    def judge(self, ev: Event, pushed: bool = False) -> Optional[Verdict]:
        if self.down:
            return None
        dev = ev.origin.device_id
        now = time.monotonic()
        if dev in self.forced:
            f = self.forced[dev]
            return self._v(ev, f.status, f.confidence, f.reason, f.containment)
        if ev.type != "reading":
            return self._v(ev, "trusted", 0.95, None)
        q = self.rate[dev]
        if pushed:                                          # flooding is network traffic: pushed messages
            q.append(now)
        while q and now - q[0] > 10:
            q.popleft()
        if len(q) > MAX_MSGS_PER_10S:
            self.quarantined[dev] = f"{len(q)} messages in 10 s from {dev}: network flooding"
            self.forced[dev] = Verdict(source=ev.source, device_id=dev, status="quarantined", confidence=0.99,
                                       reason=self.quarantined[dev], containment="quarantine")
            return self._v(ev, "quarantined", 0.99, self.quarantined[dev], "quarantine")
        vals = {m: r.value for m, r in ev.readings.items()}
        fam = ev.origin.family
        if fam == "weather" and vals.get("rainfall", 0.0) >= 2.0:
            self.wet_at = now                               # standing water stays for a while after the rain stops
        self.wet = self.weather_wet or (now - self.wet_at) * self.speed < 1800
        for m, v in vals.items():
            lo, hi = RANGES.get(m, (float("-inf"), float("inf")))
            if not lo <= v <= hi:
                return self._v(ev, "untrusted", 0.98, f"{m}={v:g} is outside the physical range {lo:g}..{hi:g} of the sensor", "isolate")
        if pushed and dev in self.twin_view:
            for m, v in vals.items():
                tv = self.twin_view[dev].get(m)
                if tv is not None and m in SPREAD and abs(v - tv) > SPREAD[m]:
                    return self._v(ev, "untrusted", 0.96,
                                   f"pushed {m}={v:g} but the Twin itself reads {tv:g} for {dev}: injected data", "isolate")
        prev = self.last.get(dev)
        if prev is not None:
            dt = max(0.2, now - prev[1]) * self.speed
            for m, v in vals.items():
                if m in MAX_STEP_PER_S and m in prev[0] and abs(v - prev[0][m]) / dt > MAX_STEP_PER_S[m] * 3:
                    self.last[dev] = (vals, now)
                    return self._v(ev, "untrusted", 0.93,
                                   f"{m} jumped {prev[0][m]:g} -> {v:g} in {dt:.1f} s, faster than the physics allows", "isolate")
            if vals == prev[0] and any(vals.values()) and fam in SPREAD:
                self.repeats[dev] += 1
            else:
                self.repeats[dev] = 0
        self.last[dev] = (vals, now)
        if not pushed:
            self.twin_view[dev] = vals
        verdict = self._neighbours(ev, vals)
        self.family_latest[fam][dev] = vals
        if verdict:
            return verdict
        if self.repeats[dev] >= 8:
            return self._v(ev, "suspect", 0.7, f"{dev} repeated the same value {self.repeats[dev]} times while its family varies: stuck sensor")
        return self._v(ev, "trusted", 0.95, None)

    def _neighbours(self, ev: Event, vals: Dict[str, float]) -> Optional[Verdict]:
        fam, dev = ev.origin.family, ev.origin.device_id
        if fam not in NEIGHBOUR_FAMILIES:
            return None
        others = {d: v for d, v in self.family_latest.get(fam, {}).items() if d != dev}
        if len(others) < 2:
            return None
        for m, v in vals.items():
            if m not in SPREAD:
                continue
            peers = sorted(o[m] for o in others.values() if m in o)
            if len(peers) < 2:
                continue
            median = peers[len(peers) // 2]
            high_peers = sum(1 for p in peers if abs(p - v) <= SPREAD[m])
            explained = (fam == "flood" and self.wet) or high_peers >= 1
            if abs(v - median) > SPREAD[m] and not explained:
                return self._v(ev, "untrusted", 0.94,
                               f"{dev} reads {m}={v:g} while every other {fam} sensor reads about {median:g}"
                               f"{' and no rain is falling' if fam == 'flood' else ''}: inconsistent with its neighbours", "isolate")
        return None

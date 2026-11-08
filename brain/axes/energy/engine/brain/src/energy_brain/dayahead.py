"""J-1 (day-ahead) forecast: the load each zone is expected to have at each time
of day, prepared the day before from yesterday's measurements.

Method: smoothed persistence ("today will look like yesterday"), the standard
day-ahead baseline in load forecasting. It knows nothing about today's
incidents; that is its purpose. The Brain uses it to
  - show the plan against reality and report large deviations (plan_deviation)
  - judge capacity-blind (L0) zones against "normal for this time" instead of a mean
  - estimate how much load comes back when it re-energizes a shed zone

File format (JSON):
  {"period_s": 600, "step_s": 1, "made_at": "...", "method": "...",
   "zones": {"Z1": [kW at t=0, kW at t=step, ...], ...}}
Time of day wraps with period_s.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


@dataclass
class DayAhead:
    period_s: float
    step_s: float
    zones: dict[str, list[float]]
    method: str = "smoothed persistence of J-1 measurements"
    made_at: Optional[str] = None

    def expected(self, zone_id: str, tod: float) -> Optional[float]:
        prof = self.zones.get(zone_id)
        if not prof:
            return None
        x = (tod % self.period_s) / self.step_s
        i = int(x)
        a, b = prof[i % len(prof)], prof[(i + 1) % len(prof)]
        return a + (b - a) * (x - i)

    def series(self, zone_id: str, t0: float, t1: float, step: float) -> list[tuple[float, float]]:
        out, t = [], t0
        while t <= t1 + 1e-9:
            v = self.expected(zone_id, t)
            if v is not None:
                out.append((round(t, 2), round(v, 1)))
            t += step
        return out

    # ------------------------------------------------------------- build / io
    @classmethod
    def from_measurements(cls, samples: dict[str, list[float]], step_s: float = 1.0, smooth: int = 15,
                          made_at: Optional[str] = None) -> "DayAhead":
        """samples: zone -> yesterday's readings at step_s spacing, covering one period."""
        zones = {}
        for z, xs in samples.items():
            n, h = len(xs), smooth // 2
            zones[z] = [round(sum(xs[(i + k) % n] for k in range(-h, h + 1)) / (2 * h + 1), 1) for i in range(n)]
        n = len(next(iter(samples.values())))
        return cls(period_s=n * step_s, step_s=step_s, zones=zones, made_at=made_at)

    @classmethod
    def load(cls, path: str | Path) -> "DayAhead":
        d = json.loads(Path(path).read_text())
        return cls(period_s=float(d["period_s"]), step_s=float(d.get("step_s", 1)),
                   zones={k: [float(x) for x in v] for k, v in d["zones"].items()},
                   method=d.get("method", cls.method), made_at=d.get("made_at"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"period_s": self.period_s, "step_s": self.step_s, "made_at": self.made_at,
                                          "method": self.method, "zones": self.zones}))


def record_yesterday(read_loads: Callable[[float], dict[str, float]], period_s: int,
                     step_s: float = 1.0) -> dict[str, list[float]]:
    """Sample a load source once per step over one period (used by the simulator to make 'J-1')."""
    samples: dict[str, list[float]] = {}
    t = 0.0
    while t < period_s:
        for z, kw in read_loads(t).items():
            samples.setdefault(z, []).append(kw)
        t += step_s
    return samples

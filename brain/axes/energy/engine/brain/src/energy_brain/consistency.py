"""Physics-based consistency: power balance between aggregate meters and zone sensors.

If the Twin exposes a meter that measures the sum of several zones (a substation or
feeder-head meter), then at every sample

    meter  ~=  sum of the zone sensors it covers

because power is conserved (losses inside a zone are small and inside the tolerance).
A persistent residual means at least one of those sensors (or the meter) does not
tell the truth: a fault, a calibration drift, or manipulation crafted to look
plausible to every per-sensor check, such as a slow drift that never jumps.

This is evidence the Guardian does not have on its own: it comes from the grid's
physics, not from each sensor's behaviour. The Brain uses it in three ways:
  1. sends it to the Guardian as an event (the Guardian keeps the authority on trust)
  2. treats the sensors involved as *inconsistent*: the gate blocks ordinary
     autonomous action that depends on them (a trusted verdict does not make
     contradictory evidence usable), and learning ignores them
  3. tells the operator what each zone would really carry if its own sensor is the
     wrong one (meter minus the others), and raises a possible HIDDEN overload when
     that estimate is above capacity: a masked surge becomes visible.

Detection, two complementary rules (flag if either fires):
  - level rule: residual smoothed (EMA) above max(abs_tol_kw, rel_tol x meter) for
    `persistence` samples. Catches large mismatches.
  - CUSUM (Page's cumulative-sum test): the residual's noise sigma is learned online
    while the balance is healthy (floored at abs_tol_kw / 3); S+ = max(0, S+ + r - k),
    S- = max(0, S- - r - k) with k = cusum_k x sigma; flag when either exceeds
    h = cusum_h x sigma after growing for 3 consecutive samples (one glitch never flags). The textbook detector for small *persistent* shifts: a slow
    drift is caught within seconds, long before it crosses the level tolerance.
Cleared after `clear_samples` with the smoothed residual below half the tolerance.
Only cycles in which the meter and every covered zone reported fresh, accepted
values are checked (no false alarm from a missing or rejected reading).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schemas import AggregateMeter


@dataclass
class _MeterState:
    meter: AggregateMeter
    kw: Optional[float] = None
    t: Optional[float] = None
    ema: Optional[float] = None
    bad: int = 0
    good: int = 0
    flagged: bool = False
    last_residual: float = 0.0
    var: Optional[float] = None       # learned residual noise variance (healthy periods only)
    s_hi: float = 0.0
    s_lo: float = 0.0
    run: int = 0                      # consecutive samples with the CUSUM statistic growing in one direction
    rule: str = ""


@dataclass
class ConsistencyMonitor:
    meters: dict[str, _MeterState]
    cfg: object                       # ConsistencyConfig
    checks: int = 0
    events: list = field(default_factory=list)

    @classmethod
    def build(cls, meters: list[AggregateMeter], cfg) -> Optional["ConsistencyMonitor"]:
        if not meters or not cfg.enabled:
            return None
        return cls({m.sensor_id: _MeterState(m) for m in meters}, cfg)

    def is_meter(self, sensor_id: str) -> bool:
        return sensor_id in self.meters

    def observe(self, sensor_id: str, kw: float, t: float) -> None:
        m = self.meters[sensor_id]
        m.kw, m.t = kw, t

    def corroborates(self, state, zone_id: str, x: float, raw: dict[str, float], prev: dict[str, Optional[float]],
                     t: float) -> bool:
        """Does an aggregate meter confirm that zone_id really jumped to x kW in this cycle?

        Yes if, for a healthy meter covering the zone with a fresh reading: (1) the balance closes with the new value
        (meter ~= x + the other zones' readings of this cycle) and (2) the meter moved like the sum of its zones'
        readings, in the zone's direction and by at least half the zone's jump. A spoofed jump fails (1); two
        coordinated spoofs that keep the sum constant fail (2)."""
        last = state.zones[zone_id].last_kw
        if last is None:
            return False
        for m in self.meters.values():
            if zone_id not in m.meter.zones or m.flagged or m.t != t or prev.get(m.meter.sensor_id) is None:
                continue
            if any(z not in raw for z in m.meter.zones):
                continue
            tol = max(self.cfg.abs_tol_kw, self.cfg.rel_tol * m.kw)
            others = sum(raw[z] for z in m.meter.zones if z != zone_id)
            balance = abs(m.kw - (others + x)) <= tol
            jump_z = x - last
            jump_m = m.kw - prev[m.meter.sensor_id]
            # every zone under the meter may move in the same cycle (a tie shares a surge between two zones), so the
            # meter must move like the SUM of their readings, in the zone's direction and at least half its size
            last_all = [state.zones[z].last_kw for z in m.meter.zones]
            if any(v is None for v in last_all):
                continue
            jump_sum = sum(raw[z] for z in m.meter.zones) - sum(last_all)
            same_move = (abs(jump_m - jump_sum) <= tol + 0.25 * abs(jump_sum)
                         and jump_m * jump_z > 0 and abs(jump_m) >= 0.5 * abs(jump_z))
            if balance and same_move:
                return True
        return False

    def contradicts(self, state, zone_id: str, x: float, raw: dict[str, float], t: float) -> bool:
        """Does a fresh, healthy aggregate meter say this zone CANNOT be at x kW (balance off by more than the
        tolerance with x, while it closes with the zone's last accepted value)? Then persistence alone must not
        make the reading believable: a spoof the Guardian has not flagged yet stays out."""
        last = state.zones[zone_id].last_kw
        for m in self.meters.values():
            if zone_id not in m.meter.zones or m.flagged or m.t != t or any(z not in raw for z in m.meter.zones):
                continue
            tol = max(self.cfg.abs_tol_kw, self.cfg.rel_tol * m.kw)
            others = sum(raw[z] for z in m.meter.zones if z != zone_id)
            if abs(m.kw - (others + x)) > tol and (last is None or abs(m.kw - (others + last)) <= tol + abs(x - last) / 2):
                return True
        return False

    def flagged_sensors(self, state) -> set[str]:
        out = set()
        for m in self.meters.values():
            if m.flagged:
                out.add(m.meter.sensor_id)
                out |= {state.sensor_of(z) for z in m.meter.zones if state.sensor_of(z)}
        return out

    def check(self, state, t: float) -> list[dict]:
        """One cycle. Returns transitions: {'meter', 'flagged': bool, 'residual', 'total', 'meter_kw', 'estimates'}."""
        c = self.cfg
        out = []
        for m in self.meters.values():
            zs = [state.zones.get(z) for z in m.meter.zones]
            if m.t != t or any(z is None or z.last_t != t or z.last_kw is None for z in zs):
                continue                       # not a complete, fresh snapshot: no verdict this cycle
            total = sum(z.last_kw for z in zs)
            r = m.kw - total
            m.ema = r if m.ema is None else c.smoothing * r + (1 - c.smoothing) * m.ema
            m.last_residual = m.ema
            self.checks += 1
            tol = max(c.abs_tol_kw, c.rel_tol * max(m.kw, total))
            m.bad = m.bad + 1 if abs(r) > tol and abs(m.ema) > tol else 0     # raw AND smoothed: a glitch never counts twice
            m.good = m.good + 1 if abs(m.ema) < tol / 2 else 0
            sigma = max((m.var or 0.0) ** 0.5, c.abs_tol_kw / 3)
            k, h = c.cusum_k * sigma, c.cusum_h * sigma
            hi, lo = max(0.0, m.s_hi + r - k), max(0.0, m.s_lo - r - k)
            m.run = m.run + 1 if (hi > m.s_hi or lo > m.s_lo) else 0
            m.s_hi, m.s_lo = hi, lo
            if not m.flagged and abs(r) < tol / 2:            # learn the healthy noise level
                m.var = r * r if m.var is None else 0.98 * m.var + 0.02 * r * r
            transition = True
            cusum = c.cusum and max(m.s_hi, m.s_lo) > h and m.run >= 3     # never on a single glitch
            if not m.flagged and (m.bad >= c.persistence or cusum):
                m.flagged = True
                m.rule = "level" if m.bad >= c.persistence else "CUSUM"
            elif m.flagged and m.good >= c.clear_samples:
                m.flagged = False
                m.s_hi = m.s_lo = 0.0
            elif m.flagged:
                transition = False             # still flagged: report estimates every cycle (hidden-overload watch)
            else:
                continue
            est = {z.zone_id: round(m.kw - (total - z.last_kw), 1) for z in zs}
            out.append({"meter": m.meter.sensor_id, "flagged": m.flagged, "transition": transition, "rule": m.rule,
                        "residual": round(m.ema, 1),
                        "tolerance": round(tol, 1), "meter_kw": round(m.kw, 1), "total": round(total, 1),
                        "zones": list(m.meter.zones), "estimates": est,
                        "readings": {z.zone_id: round(z.last_kw, 1) for z in zs}})
        return out

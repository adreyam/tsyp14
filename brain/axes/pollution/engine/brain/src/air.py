"""
City Brain, air-quality axis: from PM2.5, O3 and NO2 readings to a risk level per
zone, and proportionate actions.

The risk score (the team's method)
  Ox      = (2.075 * O3 + 1.07 * NO2) / 3.145                 oxidant, ppb
  Z_pm(t) = 0.9 * PM(t) + 0.1 * PM(t-1)                       smoothed with the previous reading
  Z_ox(t) = 0.5 * Ox(t) + 0.5 * Ox(t-1)
  R_pm    = Z_pm / T_pm * 100,   R_ox = Z_ox / T_ox * 100     normalised by season
            warm season: T_pm = 31 ug/m3, T_ox = 43 ppb
            cold season: T_pm = 25 ug/m3, T_ox = 26 ppb
  Risk    = max(R_pm, R_ox) + 0.10 * min(R_pm, R_ox)
  Level   = normal < 70 <= warning < 100 <= high <= 130 < critical

What the Brain adds around it
- Zero trust: a reading counts only if the Guardian says "trusted".
- A zone's risk is the highest risk among its trusted, recent sensors.
- Hysteresis: a level goes UP at once, but goes DOWN only when the risk stays below
  the level's lower bound minus a margin for several readings, after the level
  has held at least an hour, and one level at a time. A value that oscillates
  around a threshold does not switch alerts on and off, an action that works
  (fewer cars) is not lifted at once, and a sudden drop cannot lift everything.
- Unknown: a zone with no trusted recent sensor is "unknown". The Brain never
  announces clean air on a blocked or silent sensor: active alerts and
  restrictions stay in place until trusted data comes back.
- Every decision carries a readable reason (the numbers of the formula).
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field, ValidationError

from .audit_log import AuditLog
from .models import Location, TrustStatus

LEVELS = ["normal", "warning", "high", "critical"]
RANK = {name: i for i, name in enumerate(LEVELS)}


# ---- input message ---------------------------------------------------------------
class AirReading(BaseModel):
    """get_sensor_reading, type=air_quality (Twin -> City Brain)."""
    tool: str = "get_sensor_reading"
    sensor_id: str
    type: str = "air_quality"
    pm25: float = Field(ge=0, le=2000)        # ug/m3
    o3: float = Field(ge=0, le=2000)          # ppb
    no2: float = Field(ge=0, le=2000)         # ppb
    zone: str
    location: Optional[Location] = None
    timestamp: datetime


# ---- configuration -------------------------------------------------------------------
@dataclass(frozen=True)
class AirConfig:
    o3_weight: float = 2.075
    no2_weight: float = 1.07
    pm_weights: Tuple[float, float] = (0.9, 0.1)        # (t, t-1)
    ox_weights: Tuple[float, float] = (0.5, 0.5)
    lag_s: float = 3600.0                  # t-1 = the reading at least this much older
    thresholds: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "warm": {"pm": 31.0, "ox": 43.0}, "cold": {"pm": 25.0, "ox": 26.0}})
    warm_months: Tuple[int, ...] = (4, 5, 6, 7, 8, 9)
    c: float = 0.10
    warning_at: float = 70.0
    high_at: float = 100.0
    critical_above: float = 130.0
    exit_margin: float = 5.0               # hysteresis: go down only below bound - margin
    exit_readings: int = 2                 # ... for this many consecutive cycles
    min_level_s: float = 3600.0            # and not before the level has held this long:
                                           # an action that works (fewer cars) must not be
                                           # lifted at once, or pollution comes straight back
    silence_s: float = 900.0               # a sensor silent longer than this is not used
    # Actions by level (cumulative): what the Brain asks for in a zone.
    sensitive_alert_from: str = "warning"  # schools, hospitals, care homes
    public_alert_from: str = "high"
    close_parking_from: str = "high"       # fewer cars driving into the zone


# ---- the formula -------------------------------------------------------------------------
def oxidant(o3: float, no2: float, cfg: AirConfig = AirConfig()) -> float:
    return (cfg.o3_weight * o3 + cfg.no2_weight * no2) / (cfg.o3_weight + cfg.no2_weight)


def season_of(ts: datetime, cfg: AirConfig = AirConfig()) -> str:
    return "warm" if ts.month in cfg.warm_months else "cold"


def risk_score(r_pm: float, r_ox: float, c: float = 0.10) -> float:
    return max(r_pm, r_ox) + c * min(r_pm, r_ox)


def level_of(risk: float, cfg: AirConfig = AirConfig()) -> str:
    if risk > cfg.critical_above:
        return "critical"
    if risk >= cfg.high_at:
        return "high"
    if risk >= cfg.warning_at:
        return "warning"
    return "normal"


def lower_bound(level: str, cfg: AirConfig = AirConfig()) -> float:
    return {"normal": 0.0, "warning": cfg.warning_at, "high": cfg.high_at,
            "critical": cfg.critical_above}[level]


@dataclass
class SensorRisk:
    sensor_id: str
    zone: str
    timestamp: datetime
    pm25: float
    ox: float
    z_pm: float
    z_ox: float
    r_pm: float
    r_ox: float
    risk: float
    season: str
    lag: str                       # "t-1" or why it was not available

    @property
    def driver(self) -> str:
        return "PM2.5" if self.r_pm >= self.r_ox else "Ox"

    def explain(self, cfg: AirConfig) -> str:
        t = cfg.thresholds[self.season]
        lo = min(self.r_pm, self.r_ox)
        return (f"risk {self.risk:.0f} = max(R_pm {self.r_pm:.0f}, R_ox {self.r_ox:.0f}) "
                f"+ {cfg.c:.2f} x {lo:.0f}; {self.driver} driven "
                f"(Z_pm {self.z_pm:.1f}/{t['pm']:.0f} ug/m3, Z_ox {self.z_ox:.1f}/{t['ox']:.0f} ppb, "
                f"{self.season} season{'' if self.lag == 't-1' else ', ' + self.lag})")


class SensorHistory:
    """Past readings of one sensor, to find t-1 (and t-2, kept for the log)."""

    def __init__(self) -> None:
        self.samples: Deque[Tuple[datetime, float, float]] = deque(maxlen=200)

    def previous(self, ts: datetime, lag_s: float) -> Tuple[Optional[Tuple[float, float]], str]:
        target = ts - timedelta(seconds=lag_s)
        older = [s for s in self.samples if s[0] <= target]
        if older:
            return (older[-1][1], older[-1][2]), "t-1"
        earlier = [s for s in self.samples if s[0] < ts]
        if earlier:            # warming up: the oldest reading we have
            return (earlier[0][1], earlier[0][2]), "t-1 not yet available, oldest reading used"
        return None, "first reading, t-1 = t"

    def add(self, ts: datetime, pm: float, ox: float) -> None:
        self.samples.append((ts, pm, ox))


def compute(reading: AirReading, history: SensorHistory, cfg: AirConfig) -> SensorRisk:
    ox = oxidant(reading.o3, reading.no2, cfg)
    prev, lag = history.previous(reading.timestamp, cfg.lag_s)
    pm_prev, ox_prev = prev if prev else (reading.pm25, ox)
    z_pm = cfg.pm_weights[0] * reading.pm25 + cfg.pm_weights[1] * pm_prev
    z_ox = cfg.ox_weights[0] * ox + cfg.ox_weights[1] * ox_prev
    season = season_of(reading.timestamp, cfg)
    t = cfg.thresholds[season]
    r_pm, r_ox = z_pm / t["pm"] * 100.0, z_ox / t["ox"] * 100.0
    history.add(reading.timestamp, reading.pm25, ox)
    return SensorRisk(reading.sensor_id, reading.zone, reading.timestamp, reading.pm25, ox,
                      z_pm, z_ox, r_pm, r_ox, risk_score(r_pm, r_ox, cfg.c), season, lag)


# ---- zones -------------------------------------------------------------------------------
@dataclass
class ZoneState:
    zone: str
    level: str = "normal"
    known: bool = False                     # False until a first trusted reading
    unknown_logged: bool = False
    risk: Optional[float] = None
    reason: str = "no trusted reading yet"
    below_count: int = 0
    since: Optional[datetime] = None        # when the current level started
    sensors: Dict[str, SensorRisk] = field(default_factory=dict)   # last trusted, per sensor
    applied: Set[str] = field(default_factory=set)                 # actions in force

    @property
    def shown_level(self) -> str:
        return self.level if self.known else "unknown"


class AirBrain:
    """One step() per cycle: readings -> Guardian -> risk -> zone level -> actions."""

    def __init__(self, guardian, twin, log: AuditLog, config: AirConfig = AirConfig(),
                 outage=None):
        """outage: an object whose `_guardian_down` flag is shared with the other axis,
        so one Guardian outage is logged once for the whole Brain."""
        self.cfg = config
        self._outage = outage if outage is not None else self
        self.guardian = guardian
        self.twin = twin
        self.log = log
        self.history: Dict[str, SensorHistory] = {}
        self.zones: Dict[str, ZoneState] = {}
        self.untrusted: Dict[str, str] = {}
        self.parking: Optional[Dict[str, str]] = None    # lot id -> zone
        self.closed_lots: Set[str] = set()
        self._guardian_down = False

    # ---- cycle -----------------------------------------------------------------
    def step(self, messages: List, now: datetime) -> None:
        for raw in messages:
            self._ingest(raw, now)
        for zone in sorted(self.zones):
            self._update_zone(self.zones[zone], now)

    def _ingest(self, raw, now: datetime) -> Optional[str]:
        if not isinstance(raw, dict):
            self._reject(now, raw, "message is not a JSON object")
            return None
        try:
            reading = AirReading(**raw)
        except ValidationError as e:
            self._reject(now, raw, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}"
                                            for x in e.errors()[:4]))
            return None
        sid = reading.sensor_id
        zone = self.zones.setdefault(reading.zone, ZoneState(reading.zone))
        self.log.log(now, "twin", "air_reading", sensor_id=sid, zone=reading.zone,
                     pm25=reading.pm25, o3=reading.o3, no2=reading.no2)
        verdict = self._ask_guardian(reading, now)
        if verdict is None or verdict.status != TrustStatus.TRUSTED:
            why = "no valid verdict" if verdict is None else (verdict.reason or verdict.status.value)
            self.untrusted[sid] = why
            zone.sensors.pop(sid, None)              # its old value must not keep counting
            self.log.log(now, "city_brain", "reading_blocked", sensor_id=sid, reason=why,
                         axis="air")
            return reading.zone
        self.untrusted.pop(sid, None)
        sr = compute(reading, self.history.setdefault(sid, SensorHistory()), self.cfg)
        zone.sensors[sid] = sr
        return reading.zone

    def _ask_guardian(self, reading: AirReading, now: datetime):
        try:
            verdict = self.guardian.get_trust_verdict(reading)
        except Exception as e:
            if not self._outage._guardian_down:
                self._outage._guardian_down = True
                self.log.log(now, "city_brain", "guardian_unavailable", error=str(e))
            return None
        if self._outage._guardian_down:
            self._outage._guardian_down = False
            self.log.log(now, "city_brain", "guardian_recovered")
        if verdict.sensor_id != reading.sensor_id:
            self.log.log(now, "city_brain", "invalid_verdict", sensor_id=reading.sensor_id,
                         got=verdict.sensor_id)
            return None
        self.log.log(now, "guardian", "trust_verdict", sensor_id=reading.sensor_id,
                     status=verdict.status, confidence=verdict.confidence, reason=verdict.reason)
        return verdict

    def _reject(self, now: datetime, raw, error: str) -> None:
        sid = raw.get("sensor_id") if isinstance(raw, dict) else None
        self.log.log(now, "city_brain", "rejected_message", tool="get_sensor_reading",
                     id=sid, error=error)

    # ---- zone level ----------------------------------------------------------------
    def _update_zone(self, z: ZoneState, now: datetime) -> None:
        fresh = {sid: s for sid, s in z.sensors.items()
                 if (now - s.timestamp).total_seconds() <= self.cfg.silence_s
                 and sid not in self.untrusted}
        if not fresh:
            if not z.unknown_logged:
                z.unknown_logged = True
                why = ("no trusted recent sensor in the zone"
                       + (f"; level {z.level} and its actions kept, clean air is never assumed"
                          if z.known else ""))
                self.log.log(now, "city_brain", "air_zone_unknown", zone=z.zone,
                             kept_level=z.level if z.known else None, reason=why)
            return
        if z.unknown_logged:
            z.unknown_logged = False
            self.log.log(now, "city_brain", "air_zone_known", zone=z.zone)
        worst = max(fresh.values(), key=lambda s: s.risk)
        z.risk = worst.risk
        target = level_of(worst.risk, self.cfg)
        before = z.level if z.known else None
        z.known = True
        reason = f"{worst.sensor_id}: {worst.explain(self.cfg)}"

        if before is None:
            z.level, z.since = target, now
        elif RANK[target] > RANK[z.level]:
            z.level, z.below_count, z.since = target, 0, now       # up: at once
        elif RANK[target] < RANK[z.level]:
            bound = lower_bound(z.level, self.cfg) - self.cfg.exit_margin
            held = (now - z.since).total_seconds() if z.since else float("inf")
            if worst.risk < bound:
                z.below_count += 1
                if z.below_count >= self.cfg.exit_readings and held >= self.cfg.min_level_s:
                    # Down ONE level at a time: a sudden drop (a sensor capped by a cheater
                    # before the Guardian notices) cannot lift every measure at once.
                    z.level, z.below_count, z.since = LEVELS[RANK[z.level] - 1], 0, now
                elif z.below_count >= self.cfg.exit_readings:
                    reason += (f"; holding {z.level}: in force for {held / 60:.0f} min, "
                               f"minimum {self.cfg.min_level_s / 60:.0f} min")
                else:
                    reason += (f"; holding {z.level}: below {bound:.0f} "
                               f"{z.below_count}/{self.cfg.exit_readings}")
            else:
                z.below_count = 0
                reason += f"; holding {z.level} until risk < {bound:.0f}"
        else:
            z.below_count = 0
        z.reason = reason
        self.log.log(now, "city_brain", "air_zone_risk", zone=z.zone, risk=round(z.risk, 1),
                     level=z.level, sensor_id=worst.sensor_id, r_pm=round(worst.r_pm, 1),
                     r_ox=round(worst.r_ox, 1), season=worst.season, reason=reason)
        if before != z.level:
            self.log.log(now, "city_brain", "air_level", zone=z.zone, level=z.level,
                         previous=before, risk=round(z.risk, 1), reason=reason)
        self._apply_actions(z, now)

    # ---- actions ------------------------------------------------------------------------
    def wanted(self, level: str) -> Set[str]:
        out = set()
        r = RANK[level]
        if r >= RANK[self.cfg.sensitive_alert_from]:
            out.add("sensitive_alert")
        if r >= RANK[self.cfg.public_alert_from]:
            out.add("public_alert")
        if r >= RANK[self.cfg.close_parking_from]:
            out.add("close_parking")
        return out

    def _lots_in(self, zone: str, now: datetime) -> List[str]:
        if self.parking is None:
            try:
                self.parking = dict(self.twin.list_parking_lots())
            except Exception as e:
                self.log.log(now, "city_brain", "twin_unavailable", truck_id=None,
                             error=f"parking lots: {e}")
                return []
        return sorted(l for l, z in self.parking.items() if z == zone)

    def _apply_actions(self, z: ZoneState, now: datetime) -> None:
        want = self.wanted(z.level)
        severity = z.level
        for action in sorted(want - z.applied):
            if action == "close_parking":
                if self._set_lots(z, "closed", now):
                    z.applied.add(action)
                continue
            audience = "sensitive sites" if action == "sensitive_alert" else "public"
            self.log.log(now, "city_brain", "air_action", zone=z.zone, action=action,
                         severity=severity, audience=audience,
                         message=f"Air quality {severity} in zone {z.zone}: "
                                 + ("limit outdoor activity" if action == "public_alert"
                                    else "keep vulnerable people indoors"),
                         reason=z.reason)
            z.applied.add(action)
        for action in sorted(z.applied - want):
            if action == "close_parking":
                if self._set_lots(z, "auto", now):
                    z.applied.discard(action)
                continue
            self.log.log(now, "city_brain", "air_action", zone=z.zone, action=f"lift_{action}",
                         severity=severity, reason=z.reason)
            z.applied.discard(action)

    def _set_lots(self, z: ZoneState, mode: str, now: datetime) -> bool:
        lots = self._lots_in(z.zone, now)
        ok = True
        for lot in lots:
            if (mode == "closed") == (lot in self.closed_lots):
                continue
            try:
                self.twin.set_parking_barrier(lot, mode)
            except Exception as e:
                self.log.log(now, "city_brain", "twin_unavailable", truck_id=None,
                             error=f"set_parking_barrier {lot} {mode}: {e}")
                ok = False
                continue
            (self.closed_lots.add if mode == "closed" else self.closed_lots.discard)(lot)
            self.log.log(now, "city_brain", "air_action", zone=z.zone,
                         action="close_parking" if mode == "closed" else "reopen_parking",
                         target=lot, severity=z.level,
                         message={"tool": "set_parking_barrier", "arguments": {"id": lot, "mode": mode}},
                         reason=z.reason)
        if not lots and mode == "closed":
            self.log.log(now, "city_brain", "air_action", zone=z.zone, action="close_parking",
                         target=None, severity=z.level, reason="no car park in this zone")
        return ok

    # ---- for the timeline -----------------------------------------------------------
    def snapshot(self) -> dict:
        return {zone: {"level": z.shown_level, "kept_level": z.level if z.known else None,
                       "risk": None if z.risk is None else round(z.risk, 1),
                       "unknown": z.unknown_logged or not z.known,
                       "actions": sorted(z.applied), "reason": z.reason}
                for zone, z in sorted(self.zones.items())}

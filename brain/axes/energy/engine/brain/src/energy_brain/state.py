"""Energy Digital State: the Brain's validated view of the grid.

Readings pass four checks before they can influence a decision:
  1. known zone, fresh timestamp, not a duplicate / replay (seq)
  2. not from a sensor the Guardian currently marks untrusted
  3. physically plausible: a zone's aggregate load does not jump by more than
     max_step_frac of capacity in one sample unless a switch just operated.
     A jump is held as *suspect* until it persists for N samples AND the
     Guardian still trusts the sensor -- then the forecaster is rebased.
  4. accepted readings feed the zone's trend forecaster
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from datetime import datetime, timezone
from typing import Optional

from .config import BrainConfig
from .forecast import AdaptiveForecaster, TrendForecaster
from .schemas import CityDescriptor, PowerReading, SwitchState, SwitchStatus, TrustVerdict, Verdict


def make_forecaster(f) -> TrendForecaster:
    if f.model == "trend":
        return TrendForecaster(f.window_samples, f.warmup_samples)
    w = list(f.adaptive_windows)
    w[1] = f.window_samples
    return AdaptiveForecaster(max(w), f.warmup_samples, f.horizon_s, windows=tuple(w), half_life=f.error_half_life,
                              calibration_samples=f.calibration_samples)


@dataclass
class ZoneState:
    zone_id: str
    sensor_id: Optional[str] = None
    model: TrendForecaster = None
    last_kw: Optional[float] = None
    last_t: Optional[float] = None
    last_seq: Optional[int] = None
    last_ts: Optional[datetime] = None
    deviation_count: int = 0
    deviating: bool = False
    suspect_kw: Optional[float] = None
    suspect_count: int = 0
    risk_count: int = 0
    clear_count: int = 0
    at_risk: bool = False
    alerted: bool = False


@dataclass
class VerdictEntry:
    verdict: TrustVerdict
    received_t: float
    age_at_receipt: float = 0.0


@dataclass
class EnergyState:
    cfg: BrainConfig
    city: CityDescriptor
    zones: dict[str, ZoneState] = field(default_factory=dict)
    switches: dict[str, SwitchStatus] = field(default_factory=dict)
    verdicts: dict[str, VerdictEntry] = field(default_factory=dict)
    priority_hints: dict[str, tuple[str, datetime]] = field(default_factory=dict)  # zone -> (reason, expires)
    t: float = 0.0   # Brain clock at the current cycle
    flips: dict[str, list[float]] = field(default_factory=dict)   # subject -> times its verdict changed
    flapping: set[str] = field(default_factory=set)
    inconsistent: set[str] = field(default_factory=set)   # sensors contradicted by an aggregate meter (consistency.py)
    corroborate: Optional[Callable[[str, float], bool]] = None   # meter confirms a jump (set each cycle by the Brain)
    contradict: Optional[Callable[[str, float], bool]] = None     # meter says the reading cannot be right

    def __post_init__(self):
        f = self.cfg.forecast
        for z in self.city.zones:
            self.zones[z.zone_id] = ZoneState(z.zone_id, model=make_forecaster(f))

    # ------------------------------------------------------------------ lookups
    def zone(self, zone_id: str):
        return next(z for z in self.city.zones if z.zone_id == zone_id)

    def capacity(self, zone_id: str) -> Optional[float]:
        return self.zone(zone_id).capacity_kw

    def is_priority(self, zone_id: str, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        hint = self.priority_hints.get(zone_id)
        return self.zone(zone_id).is_priority or bool(hint and hint[1] > now)

    def sensor_of(self, zone_id: str) -> Optional[str]:
        return self.zones[zone_id].sensor_id

    def verdict_status(self, subject_id: str, now: Optional[datetime] = None) -> str:
        """'trusted' | 'untrusted' | 'missing' (never received or older than the TTL)."""
        e = self.verdicts.get(subject_id)
        if e is None:
            return "missing"
        if subject_id in self.flapping:
            return "untrusted"           # a verdict that keeps flipping cannot be relied on either way
        # age = how old the verdict already was when received + how long we have held it (Brain clock)
        age = e.age_at_receipt + max(self.t - e.received_t, 0.0)
        if age > self.cfg.trust.verdict_ttl_s:
            return "missing"
        return e.verdict.verdict.value

    def is_flapping(self, subject_id: str) -> bool:
        return subject_id in self.flapping

    def quarantined(self, subject_id: str) -> bool:
        e = self.verdicts.get(subject_id)
        return bool(e and e.verdict.quarantined)

    # ------------------------------------------------------------------ updates
    def update_verdicts(self, verdicts: list[TrustVerdict], t: float,
                        now: Optional[datetime] = None) -> tuple[list[tuple[str, str, str]], list[tuple[str, bool]]]:
        """Returns (changes, flapping_changes): (subject, old, new) per changed verdict, and
        (subject, now_flapping) whenever a subject starts or stops flapping."""
        now = now or datetime.now(timezone.utc)
        tp = self.cfg.trust
        changes, flap = [], []
        for v in verdicts:
            old = self.verdicts.get(v.subject_id)
            old_val = old.verdict.verdict.value if old else "none"
            self.verdicts[v.subject_id] = VerdictEntry(v, t, max((now - v.ts).total_seconds(), 0.0))
            if old_val != v.verdict.value or (old and old.verdict.quarantined != v.quarantined):
                changes.append((v.subject_id, old_val, v.verdict.value + (" (quarantined)" if v.quarantined else "")))
                if old is not None:
                    self.flips.setdefault(v.subject_id, []).append(t)
        for subj in list(self.flips) + list(self.flapping):
            recent = [x for x in self.flips.get(subj, []) if t - x <= tp.flap_window_s]
            self.flips[subj] = recent
            if len(recent) >= tp.flap_changes and subj not in self.flapping:
                self.flapping.add(subj)
                flap.append((subj, True))
            elif subj in self.flapping and not recent:
                self.flapping.discard(subj)
                flap.append((subj, False))
        return changes, flap

    def update_switches(self, statuses: list[SwitchStatus]) -> list[tuple[str, Optional[SwitchState], SwitchState]]:
        changes = []
        for s in statuses:
            old = self.switches.get(s.switch_id)
            if old is not None and old.state != s.state:
                changes.append((s.switch_id, old.state, s.state))
            self.switches[s.switch_id] = s
        return changes

    def ingest(self, r: PowerReading, t: float, exempt: bool, now: Optional[datetime] = None) -> tuple[str, str]:
        """Returns (outcome, detail). outcome in accepted|rejected|duplicate|untrusted|suspect|confirmed."""
        now = now or datetime.now(timezone.utc)
        z = self.zones.get(r.zone_id)
        if z is None:
            return "rejected", f"unknown zone '{r.zone_id}'"
        if z.sensor_id is None:
            z.sensor_id = r.sensor_id
        age = (now - r.ts).total_seconds()
        if age > self.cfg.ingest.stale_s:
            return "rejected", f"stale reading ({age:.1f} s old)"
        if -age > self.cfg.ingest.max_future_s:
            return "rejected", f"timestamp {-age:.1f} s in the future (clock skew or forgery)"
        reset = ""
        if r.seq is not None and z.last_seq is not None and r.seq <= z.last_seq:
            if z.last_ts is not None and r.ts > z.last_ts:
                reset = f"sequence restarted ({z.last_seq} -> {r.seq}) with a newer timestamp: Twin restart assumed"
            else:
                return "duplicate", f"seq {r.seq} <= {z.last_seq}"
        elif r.seq is None and z.last_ts is not None and r.ts <= z.last_ts:
            return "duplicate", "timestamp not newer than the previous reading"
        if r.seq is not None:
            z.last_seq = r.seq
        z.last_ts = r.ts
        if reset:
            out = self._ingest_value(z, r, t, exempt, now)
            return ("reset", reset) if out[0] == "accepted" else out
        return self._ingest_value(z, r, t, exempt, now)

    def _ingest_value(self, z: ZoneState, r: PowerReading, t: float, exempt: bool, now: datetime) -> tuple[str, str]:
        if self.verdict_status(r.sensor_id, now) == "untrusted":
            return "untrusted", "sensor marked untrusted by Guardian"

        x = r.kw
        if z.last_kw is None or exempt:
            detail = "first sample" if z.last_kw is None else "rebased after switching"
            if z.last_kw is not None:
                z.model.rebase(x)
            z.suspect_kw, z.suspect_count = None, 0
            return self._accept(z, x, t), detail

        ref = self.capacity(r.zone_id) or max(z.last_kw, 1.0)
        limit = self.cfg.ingest.max_step_frac * ref
        if abs(x - z.last_kw) > limit:
            if (self.corroborate and self.verdict_status(r.sensor_id, now) == "trusted"
                    and r.sensor_id not in self.inconsistent and self.corroborate(r.zone_id, x)):
                z.model.restart(x)
                z.suspect_kw, z.suspect_count = None, 0
                self._accept(z, x, t)
                return "confirmed", (f"jump to {x:.0f} kW confirmed at once by an aggregate "
                                     f"meter (power balance closes, meter moved alike); forecaster restarted at the new level")
            if z.suspect_kw is not None and abs(x - z.suspect_kw) <= limit:
                z.suspect_count += 1
            else:
                z.suspect_kw, z.suspect_count = x, 1
            if self.contradict and self.contradict(r.zone_id, x):
                # persistence never overrides physics: the meter says this jump did not happen
                return "suspect", (f"jump {z.last_kw:.0f} -> {x:.0f} kW contradicted by an aggregate meter "
                                   f"(power balance would not close); not accepted however long it lasts")
            if (z.suspect_count >= self.cfg.ingest.suspect_confirm_samples
                    and self.verdict_status(r.sensor_id, now) == "trusted"):
                z.model.rebase(x)
                z.suspect_kw, z.suspect_count = None, 0
                self._accept(z, x, t)
                return "confirmed", f"jump to {x:.0f} kW persisted and sensor trusted; forecaster rebased"
            return "suspect", f"jump {z.last_kw:.0f} -> {x:.0f} kW exceeds {limit:.0f} kW/sample"
        z.suspect_kw, z.suspect_count = None, 0
        return self._accept(z, x, t), ""

    def _accept(self, z: ZoneState, x: float, t: float) -> str:
        z.model.update(t, x)
        z.last_kw, z.last_t = x, t
        return "accepted"

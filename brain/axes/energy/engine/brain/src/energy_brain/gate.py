"""Safety & Trust Gate: the mandatory boundary between deciding and acting.

Safety is always checked. A trusted verdict never skips safety.
Trust policy for a switch action, over every subject it depends on
(load sensors of the zones it touches + the switch's own status sensor):
  - any 'untrusted'                -> BLOCK (never overridden, not even in degraded mode)
  - all 'trusted' and fresh        -> PASS
  - some 'missing' (Guardian down,
    or verdict older than TTL)     -> DEGRADED MODE: pass only if the action is
                                      reversible, relieves a PRIORITY zone, and the
                                      risk has persisted >= N consistent samples.
                                      Never restoration or load shedding. Otherwise BLOCK.
  - a verdict that keeps flipping  -> counts as 'untrusted'.
Physics (v0.7): a sensor contradicted by an aggregate meter (power balance does not
close, consistency.py) blocks ordinary action even if the Guardian still trusts it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .decision import Candidate
from .schemas import SwitchKind, SwitchOperation, SwitchState, Switch
from .state import EnergyState


@dataclass
class GateResult:
    passed: bool
    rule: str                       # which rule decided
    detail: str
    degraded: bool = False
    verdict_refs: list[str] = field(default_factory=list)
    trust: dict[str, str] = field(default_factory=dict)


def subjects_for(state: EnergyState, sw: Switch) -> list[str]:
    zones = [z for z in (sw.from_zone, sw.to_zone) if z]
    return [state.sensor_of(z) or f"<no sensor seen for {z}>" for z in zones] + [sw.switch_id]


def check(state: EnergyState, c: Candidate, sw: Switch, *, t: float, cooldown_until: dict[str, float],
          busy: set[str], failed: set[str], now: Optional[datetime] = None,
          busy_zones: frozenset[str] = frozenset()) -> GateResult:
    st = state.switches.get(c.switch_id)
    # ------------------------------------------------------------- safety
    if st is None:
        return GateResult(False, "safety", f"no status known for {c.switch_id}")
    if not st.available:
        return GateResult(False, "safety", f"{c.switch_id} reported unavailable by the Twin")
    if c.switch_id in failed:
        return GateResult(False, "safety", f"{c.switch_id} failed to operate earlier; excluded until operator review")
    if c.switch_id in busy:
        return GateResult(False, "safety", f"{c.switch_id} has a command awaiting verification")
    overlap = sorted(set(c.shift) & busy_zones)
    if overlap:
        return GateResult(False, "safety", f"{', '.join(overlap)} already affected by an action awaiting "
                          f"verification; one change at a time per zone")
    if t < cooldown_until.get(c.switch_id, float("-inf")):
        return GateResult(False, "safety", f"{c.switch_id} in cooldown ({cooldown_until[c.switch_id] - t:.0f} s left)")
    expected_from = SwitchState.open if c.operation == SwitchOperation.close else SwitchState.closed
    if st.state != expected_from:
        return GateResult(False, "safety", f"cannot {c.operation.value} {c.switch_id}: it is already {st.state.value}")
    if sw.kind == SwitchKind.feeder and c.operation == SwitchOperation.open and state.is_priority(sw.from_zone, now):
        return GateResult(False, "safety", f"opening {c.switch_id} would de-energize priority zone {sw.from_zone}")
    subjects = subjects_for(state, sw)
    for s in subjects:
        if state.quarantined(s):
            return GateResult(False, "safety", f"{s} is quarantined by the Guardian")
    contradicted = [s for s in subjects if s in state.inconsistent]
    if contradicted:
        return GateResult(False, "consistency", f"{', '.join(contradicted)} contradicted by an aggregate meter "
                          f"(power balance does not close); evidence not usable until resolved")

    # ------------------------------------------------------------- trust
    trust = {s: state.verdict_status(s, now) for s in subjects}
    refs = [state.verdicts[s].verdict.verdict_id for s in subjects if trust[s] != "missing"]
    untrusted = [s for s, v in trust.items() if v == "untrusted"]
    if untrusted:
        return GateResult(False, "trust", f"untrusted: {', '.join(untrusted)}", trust=trust)
    missing = [s for s, v in trust.items() if v == "missing"]
    if not missing:
        return GateResult(True, "trust", "all evidence trusted and fresh", verdict_refs=refs, trust=trust)

    # ------------------------------------------------------------- degraded mode
    if c.kind in ("restore", "shed"):
        what = "restoration" if c.kind == "restore" else "load shedding"
        return GateResult(False, "trust_unavailable", f"verdicts missing for {', '.join(missing)}; {what} "
                          f"waits for trusted evidence", trust=trust)
    need = state.cfg.trust.degraded_min_consistent_samples
    prio = [z for z in c.relieves if state.is_priority(z, now) and state.zones[z].risk_count >= need]
    if c.reversible and prio:
        return GateResult(True, "degraded", f"verdicts missing for {', '.join(missing)}; reversible action relieving "
                          f"priority zone {', '.join(prio)} after >= {need} consistent samples",
                          degraded=True, verdict_refs=refs, trust=trust)
    why = "not reversible" if not c.reversible else "does not relieve a persistent priority-zone overload"
    return GateResult(False, "trust_unavailable", f"verdicts missing for {', '.join(missing)} and action {why}",
                      trust=trust)

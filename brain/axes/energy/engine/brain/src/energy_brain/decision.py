"""Future Generator + What-If evaluation + Decision ranking.

Candidates ("futures"): do nothing, plus one toggle of every switch whose effect
is known. Each is evaluated with a load-transfer model at the forecast horizon.
Deliberately not a power-flow model: it needs only zone loads, capacities and
switch effects, which is what a Twin with load + switch-status sensors supports.

Uncertainty-aware scoring. Each zone's peak load over the horizon is modelled as
L ~ Normal(mu, s) from the trend forecast and its prediction interval, so

    P(overload)        = Phi((mu - cap) / s)
    expected exposure  = E[max(0, L - cap)] / cap = ((mu-cap) Phi(z) + s phi(z)) / cap,  z = (mu-cap)/s

Score (lower is better) = sum_z w_z * expected exposure_z + switching_cost (+ shedding penalty)
with w_z = priority_weight for hospital / flood pump / traffic signals / hinted zones.
An action is chosen only if it beats doing nothing by more than its cost.

Hard constraints (candidate infeasible), checked on the conservative upper band:
  - de-energizing a priority zone
  - pushing a zone that was within capacity over capacity
  - loading a priority zone above `priority_headroom` x capacity
  - load shedding (opening a feeder) unless it is the LAST RESORT: no tie option
    helps (on the forecast, nor on the loads as measured now), the zone is measured
    over capacity now (never on a forecast alone), and its expected peak is
    >= shed_above x capacity

Restoration: a switch the Brain changed is put back when the forecast shows every
affected zone below restore_margin x capacity for restore_persistence samples.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .schemas import Switch, SwitchKind, SwitchOperation, SwitchState
from .state import EnergyState


def _phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


@dataclass
class ZoneOutlook:
    mu: float          # expected peak load over the horizon, kW
    s: float           # its standard deviation, kW
    upper: float       # conservative upper band, kW
    now: float         # last accepted reading, kW


@dataclass
class Candidate:
    switch_id: Optional[str]                  # None = do nothing
    operation: Optional[SwitchOperation]
    loads: dict[str, float]                   # predicted kW at horizon (upper band) -- for constraints
    central: dict[str, float] = field(default_factory=dict)   # expected peak kW per zone -- for display
    p_over: dict[str, float] = field(default_factory=dict)    # P(overload) per zone
    score: float = 0.0
    feasible: bool = True
    why_not: Optional[str] = None
    shift: dict[str, float] = field(default_factory=dict)      # change per zone at the horizon (kW)
    shift_now: dict[str, float] = field(default_factory=dict)  # immediate change per zone (for verification)
    reversible: bool = True
    relieves: list[str] = field(default_factory=list)
    kind: str = "relief"                      # relief | shed | restore
    plan: list[str] = field(default_factory=list)   # multi-step plan this action starts (planner.py)

    @property
    def label(self) -> str:
        return "no-action" if self.switch_id is None else f"{self.operation.value} {self.switch_id}"

    def brief(self, state: EnergyState) -> dict:
        pct = {z: round(100 * v / c) for z, v in self.loads.items() if (c := state.capacity(z))}
        return {"action": self.label, "kind": self.kind, "score": round(self.score, 4), "feasible": self.feasible,
                "why_not": self.why_not, "load_pct_at_horizon": pct,
                "central_kw": {z: round(v, 1) for z, v in self.central.items()},
                "p_overload": {z: round(p, 3) for z, p in self.p_over.items() if p >= 0.005},
                "shift_kw": {z: round(v, 1) for z, v in self.shift.items()},
                **({"plan": self.plan} if self.plan else {})}


@dataclass
class DecisionResult:
    no_action: Candidate
    ranked: list[Candidate]          # feasible actions that beat no-action, best first
    rejected: list[Candidate]        # infeasible or not better than doing nothing

    def summary(self, state: EnergyState) -> dict:
        return {"no_action": self.no_action.brief(state),
                "ranked": [c.brief(state) for c in self.ranked],
                "rejected": [c.brief(state) for c in self.rejected]}


# ---------------------------------------------------------------- outlook

def outlook(state: EnergyState) -> dict[str, ZoneOutlook]:
    f = state.cfg.forecast
    out = {}
    for zid, z in state.zones.items():
        if z.last_kw is None:
            continue
        fc = z.model.snapshot()
        cap = state.capacity(zid) or max(z.last_kw, 1.0)
        floor_s = 0.01 * cap   # never pretend to be certain
        if f.mode == "reactive" or not fc.ready:
            out[zid] = ZoneOutlook(z.last_kw, floor_s, z.last_kw, z.last_kw)
        else:
            mu, s = fc.peak(f.horizon_s)
            out[zid] = ZoneOutlook(max(mu, z.last_kw), max(s, floor_s),
                                   max(z.last_kw, fc.peak_upper(f.horizon_s, f.band_sigma)), z.last_kw)
    return out


def predicted_loads(state: EnergyState, central: bool = False) -> dict[str, float]:
    """Load each zone may reach within the horizon: upper band (conservative) or expected peak."""
    return {z: (o.mu if central else o.upper) for z, o in outlook(state).items()}


def p_overload(mu: float, s: float, cap: float) -> float:
    return _Phi((mu - cap) / s) if s > 0 else float(mu > cap)


def expected_exposure(mu: float, s: float, cap: float) -> float:
    if s <= 0:
        return max(0.0, mu - cap) / cap
    z = (mu - cap) / s
    return ((mu - cap) * _Phi(z) + s * _phi(z)) / cap


def _weight(state: EnergyState, zid: str) -> float:
    return state.cfg.action.priority_weight if state.is_priority(zid) else 1.0


def score_loads(state: EnergyState, mus: dict[str, float], ss: dict[str, float]) -> tuple[float, dict[str, float]]:
    total, probs = 0.0, {}
    for zid, mu in mus.items():
        cap = state.capacity(zid)
        if not cap:
            continue
        if mu <= 0:
            probs[zid] = 0.0
            continue
        total += _weight(state, zid) * expected_exposure(mu, ss[zid], cap)
        probs[zid] = p_overload(mu, ss[zid], cap)
    return total, probs


def _demand_estimate(state: EnergyState, effects: dict[str, Switch], zone_id: str, served: float) -> float:
    """Served load of a zone understates its demand if some of it is already transferred away."""
    moved_away = sum(s.transfer_fraction for sid, s in effects.items()
                     if s.kind == SwitchKind.tie and s.from_zone == zone_id
                     and state.switches.get(sid) and state.switches[sid].state == SwitchState.closed)
    return served / max(1e-6, 1.0 - min(moved_away, 0.95))


def _apply(state, effects, sw: Switch, st_state: SwitchState, ol: dict[str, ZoneOutlook], restore_kw=None):
    """Shift (horizon) and shift_now (immediate) of toggling `sw`; None if not applicable."""
    if sw.kind == SwitchKind.tie:
        closing = st_state == SwitchState.open
        demand = _demand_estimate(state, effects, sw.from_zone, ol[sw.from_zone].mu)
        demand_now = _demand_estimate(state, effects, sw.from_zone, ol[sw.from_zone].now)
        sign = 1 if closing else -1
        m, mn = sw.transfer_fraction * demand, sw.transfer_fraction * demand_now
        return ((SwitchOperation.close if closing else SwitchOperation.open),
                {sw.from_zone: -sign * m, sw.to_zone: sign * m},
                {sw.from_zone: -sign * mn, sw.to_zone: sign * mn}, True)
    if st_state == SwitchState.closed:            # feeder open = shed the zone
        return (SwitchOperation.open, {sw.from_zone: -ol[sw.from_zone].mu},
                {sw.from_zone: -ol[sw.from_zone].now}, False)
    if restore_kw is not None:                    # feeder close = re-energize (restoration only)
        return (SwitchOperation.close, {sw.from_zone: restore_kw}, {sw.from_zone: restore_kw}, True)
    return None


def _build(state, sw, op, shift, shift_now, reversible, ol, kind) -> Candidate:
    mus = {z: o.mu for z, o in ol.items()}
    ss = {z: o.s for z, o in ol.items()}
    loads = {z: o.upper for z, o in ol.items()}
    for z, d in shift.items():
        mus[z] = max(0.0, mus[z] + d)
        loads[z] = max(0.0, loads[z] + d)
        if mus[z] == 0.0:
            ss[z] = 0.0
    sc, probs = score_loads(state, mus, ss)
    c = Candidate(sw.switch_id, op, loads, central=mus, p_over=probs, shift=shift, shift_now=shift_now,
                  reversible=reversible, kind=kind)
    c.score = sc + state.cfg.action.switching_cost
    c.relieves = [z for z, d in shift.items()
                  if d < 0 and state.capacity(z) and ol[z].upper > state.capacity(z)]
    return c


def _constraints(state: EnergyState, c: Candidate, ol: dict[str, ZoneOutlook]) -> None:
    headroom = state.cfg.action.priority_headroom
    for z, d in c.shift.items():
        cap = state.capacity(z)
        if not c.feasible:
            return
        if cap and ol[z].upper <= cap < c.loads[z]:
            tag = "priority zone" if state.is_priority(z) else "zone"
            c.feasible, c.why_not = False, f"would push {tag} {z} over capacity ({100 * c.loads[z] / cap:.0f}%)"
        elif cap and d > 0 and state.is_priority(z) and c.loads[z] > headroom * cap:
            c.feasible, c.why_not = False, (f"would load priority zone {z} to {100 * c.loads[z] / cap:.0f}% "
                                            f"(priority zones keep {100 * (1 - headroom):.0f}% headroom)")
        elif cap is None and d > 0:
            c.feasible, c.why_not = False, f"capacity of receiving zone {z} unknown"


def measured_outlook(state: EnergyState) -> dict[str, ZoneOutlook]:
    """The present only: last accepted readings, minimal uncertainty (used to double-check before shedding)."""
    out = {}
    for zid, z in state.zones.items():
        if z.last_kw is None:
            continue
        cap = state.capacity(zid) or max(z.last_kw, 1.0)
        out[zid] = ZoneOutlook(z.last_kw, 0.01 * cap, z.last_kw, z.last_kw)
    return out


def evaluate(state: EnergyState, effects: dict[str, Switch], usable: set[str],
             ol: Optional[dict[str, ZoneOutlook]] = None, _check_now: bool = True) -> DecisionResult:
    ol = ol if ol is not None else outlook(state)
    a = state.cfg.action
    mus = {z: o.mu for z, o in ol.items()}
    ss = {z: o.s for z, o in ol.items()}
    sc, probs = score_loads(state, mus, ss)
    no_action = Candidate(None, None, {z: o.upper for z, o in ol.items()}, central=mus, p_over=probs, score=sc)
    ties: list[Candidate] = []
    sheds: list[Candidate] = []

    for sid in sorted(usable):
        sw, st = effects.get(sid), state.switches.get(sid)
        if sw is None or st is None:
            continue
        missing = [z for z in (sw.from_zone, sw.to_zone) if z and z not in ol]
        if missing:
            c = Candidate(sid, SwitchOperation.close if st.state == SwitchState.open else SwitchOperation.open,
                          {z: o.upper for z, o in ol.items()}, feasible=False,
                          why_not=f"no trusted load data for {', '.join(missing)}")
            ties.append(c)
            continue
        applied = _apply(state, effects, sw, st.state, ol)
        if applied is None:
            continue
        op, shift, shift_now, reversible = applied
        is_shed = sw.kind == SwitchKind.feeder
        c = _build(state, sw, op, shift, shift_now, reversible, ol, "shed" if is_shed else "relief")
        if is_shed:
            z = sw.from_zone
            cap = state.capacity(z)
            c.score += a.shed_penalty * _weight(state, z)
            if state.is_priority(z):
                c.feasible, c.why_not = False, f"would de-energize priority zone {z}"
            elif not cap or ol[z].now < cap or ol[z].mu < a.shed_above * cap:
                c.feasible, c.why_not = False, (f"shedding {z} is a last resort: only when it is MEASURED over "
                                                f"capacity and its expected peak reaches {100 * a.shed_above:.0f}%")
            sheds.append(c)
        else:
            _constraints(state, c, ol)
            ties.append(c)

    better = lambda c: c.feasible and c.score < no_action.score  # noqa: E731
    ranked = sorted([c for c in ties if better(c)], key=lambda c: c.score)
    for c in sheds:
        if c.feasible:
            _constraints(state, c, ol)
    if not ranked and any(c.feasible for c in sheds) and _check_now:
        # Before cutting customers: does a tie relieve the overload as MEASURED now? The forecast may over-extrapolate
        # a ramp and make every tie look infeasible; a reversible tie that works on the present is always preferred.
        now = evaluate(state, effects, {sid for sid in usable if effects.get(sid) and effects[sid].kind == SwitchKind.tie},
                       ol=measured_outlook(state), _check_now=False)
        alt = [c for c in now.ranked if c.kind == "relief"]
        if alt:
            for c in alt:
                c.why_not = None
                c.relieves = c.relieves or [z for z, d in c.shift.items() if d < 0]
            for c in sheds:
                if c.feasible:
                    c.why_not = (f"not needed: {alt[0].label} relieves the measured overload now "
                                 f"(shedding is a last resort)")
            ranked = alt
    if not ranked:   # last resort
        ranked = sorted([c for c in sheds if c.feasible], key=lambda c: c.score)
    elif any(c.feasible for c in sheds):
        for c in sheds:
            if c.feasible:
                c.why_not = "not needed: a switching option exists (shedding is a last resort)"
    rejected = [c for c in ties + sheds if c not in ranked]
    for c in rejected:
        if c.feasible and not c.why_not:
            c.why_not = "does not improve on doing nothing"
    return DecisionResult(no_action, ranked, rejected)


def evaluate_restore(state: EnergyState, effects: dict[str, Switch], sid: str,
                     restore_kw: Optional[float] = None) -> tuple[Optional[Candidate], str]:
    """Candidate that puts `sid` back, and whether it is safe now (reason text)."""
    ol = outlook(state)
    sw, st = effects.get(sid), state.switches.get(sid)
    if sw is None or st is None:
        return None, "unknown switch"
    zones = [z for z in (sw.from_zone, sw.to_zone) if z]
    if sw.kind == SwitchKind.feeder and sw.from_zone not in ol:
        ol[sw.from_zone] = ZoneOutlook(0.0, 0.0, 0.0, 0.0)
    if any(z not in ol for z in zones):
        return None, "no trusted data"
    applied = _apply(state, effects, sw, st.state, ol, restore_kw=restore_kw)
    if applied is None:
        return None, "nothing to restore"
    op, shift, shift_now, reversible = applied
    c = _build(state, sw, op, shift, shift_now, reversible, ol, "restore")
    m = state.cfg.action.restore_margin
    for z in zones:
        cap = state.capacity(z)
        if not cap or c.loads[z] > m * cap:
            pct = f"{100 * c.loads[z] / cap:.0f}%" if cap else "unknown capacity"
            return c, f"not yet: {z} would be at {pct} (needs <= {100 * m:.0f}%)"
    return c, "safe"

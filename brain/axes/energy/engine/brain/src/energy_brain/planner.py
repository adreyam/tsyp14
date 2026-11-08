"""Multi-zone coordination: short switching plans instead of single toggles.

Why. The single-toggle decision (decision.py) is myopic. Two situations break it:
  - Shared spare capacity. Zone D was relieved into B earlier; now A surges and
    its only tie also goes to B, which no longer has room. Every single toggle
    is infeasible, so v0.6 alerts or, as a last resort, sheds A.
  - Re-dispatch. The fix exists but needs several moves: relieve D into C
    instead (close T-DC, then open T-DB: *make before break*), then close T-AB.

What. A bounded search over sequences of up to `max_plan_steps` tie toggles:
  1. Each zone's *demand* is reconstructed from its served load by undoing the
     transfers of the ties that are currently closed (the same load-transfer
     model the decision engine uses, solved as a small fixed point).
  2. For any tie configuration, a zone's load is a linear combination of zone
     demands, so its mean AND its standard deviation follow directly
     (independent zone forecasts): load_z = sum_j a_zj d_j, var = sum_j a_zj^2 sd_j^2.
  3. A sequence is feasible only if EVERY intermediate configuration respects
     the same hard constraints as a single action (no zone pushed over
     capacity, priority zones keep their headroom, no load into zones of unknown
     capacity, no switch toggled twice). Break-before-make orders that would
     overload a zone between two steps are therefore rejected by construction.
  4. Score = expected overload of the final configuration (priority-weighted,
     as in decision.py) + switching cost x number of steps.

How it acts. Receding horizon: only the FIRST step is executed (through the
same Safety & Trust Gate, verified like any action). The next cycle re-plans
from what was actually observed, so a plan never runs blind on an unverified
step. Feeders (shedding) are never part of a plan: shedding stays the explicit
last resort of decision.py.

Size: with n usable ties, at most n + n(n-1) + n(n-1)(n-2) sequences (n=6 -> 156),
each evaluated in microseconds.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

from .decision import Candidate, ZoneOutlook, expected_exposure, p_overload
from .schemas import Switch, SwitchKind, SwitchOperation, SwitchState
from .state import EnergyState


@dataclass
class Plan:
    steps: list[tuple[str, SwitchOperation]]
    score: float
    mu: dict[str, float]                       # expected peak per zone after the whole plan
    p_over: dict[str, float]
    intermediate: list[dict[str, float]] = field(default_factory=list)   # upper band per zone after each step

    @property
    def labels(self) -> list[str]:
        return [f"{op.value} {sid}" for sid, op in self.steps]


def _ties(state: EnergyState, effects: dict[str, Switch], usable: set[str], ol: dict) -> dict[str, Switch]:
    out = {}
    for sid in sorted(usable):
        sw, st = effects.get(sid), state.switches.get(sid)
        if sw is None or st is None or sw.kind != SwitchKind.tie:
            continue
        if sw.from_zone not in ol or sw.to_zone not in ol:
            continue
        out[sid] = sw
    return out


def _dark(state: EnergyState, effects: dict[str, Switch]) -> set[str]:
    return {s.from_zone for sid, s in effects.items() if s.kind == SwitchKind.feeder and state.switches.get(sid)
            and state.switches[sid].state == SwitchState.open}


def _coeffs(zones: list[str], ties: dict[str, Switch], closed: set[str]) -> dict[str, dict[str, float]]:
    """a[z][j]: share of zone j's demand served by zone z under a tie configuration."""
    a = {z: {j: float(z == j) for j in zones} for z in zones}
    for sid in closed:
        s = ties[sid]
        a[s.from_zone][s.from_zone] -= s.transfer_fraction
        a[s.to_zone][s.from_zone] += s.transfer_fraction
    return a


def reconstruct_demand(served: dict[str, float], ties: dict[str, Switch], closed: set[str]) -> dict[str, float]:
    """Invert served = A(closed) . demand. Out-shares only depend on the zone itself, in-shares on others:
    fixed-point iteration converges in a few rounds for the small tie fractions grids use."""
    out_frac = {z: 0.0 for z in served}
    for sid in closed:
        out_frac[ties[sid].from_zone] += ties[sid].transfer_fraction
    d = dict(served)
    for _ in range(20):
        nxt = {}
        for z in served:
            incoming = sum(ties[s].transfer_fraction * d[ties[s].from_zone] for s in closed if ties[s].to_zone == z)
            nxt[z] = max(0.0, (served[z] - incoming) / max(0.05, 1.0 - min(out_frac[z], 0.95)))
        if max(abs(nxt[z] - d[z]) for z in d) < 1e-6:
            d = nxt
            break
        d = nxt
    return d


@dataclass
class _Model:
    zones: list[str]
    ties: dict[str, Switch]
    d_mu: dict[str, float]      # expected peak demand per zone (horizon)
    d_sd: dict[str, float]
    d_now: dict[str, float]
    k: float

    def loads(self, closed: set[str]) -> tuple[dict, dict, dict, dict]:
        a = _coeffs(self.zones, self.ties, closed)
        mu = {z: sum(a[z][j] * self.d_mu[j] for j in self.zones) for z in self.zones}
        sd = {z: sum((a[z][j] * self.d_sd[j]) ** 2 for j in self.zones) ** 0.5 for z in self.zones}
        now = {z: sum(a[z][j] * self.d_now[j] for j in self.zones) for z in self.zones}
        up = {z: mu[z] + self.k * sd[z] for z in self.zones}
        return mu, sd, up, now


def build_model(state: EnergyState, effects: dict[str, Switch], usable: set[str],
                ol: dict[str, ZoneOutlook]) -> Optional[_Model]:
    dark = _dark(state, effects)
    ties = {sid: s for sid, s in _ties(state, effects, usable | _closed_ties(state, effects), ol).items()
            if s.from_zone not in dark and s.to_zone not in dark}
    if not ties:
        return None
    zones = sorted(z for z in ol if z not in dark)
    closed = {sid for sid in ties if state.switches[sid].state == SwitchState.closed}
    mu = reconstruct_demand({z: ol[z].mu for z in zones}, ties, closed)
    now = reconstruct_demand({z: ol[z].now for z in zones}, ties, closed)
    out_frac = {z: sum(ties[s].transfer_fraction for s in closed if ties[s].from_zone == z) for z in zones}
    sd = {z: ol[z].s / max(0.05, 1.0 - min(out_frac[z], 0.95)) for z in zones}
    return _Model(zones, ties, mu, sd, now, state.cfg.forecast.band_sigma)


def _closed_ties(state: EnergyState, effects: dict[str, Switch]) -> set[str]:
    """Closed ties stay in the model even when not actionable (their transfers are real)."""
    return {sid for sid, s in effects.items() if s.kind == SwitchKind.tie and state.switches.get(sid)
            and state.switches[sid].state == SwitchState.closed}


def _score(state: EnergyState, mu: dict, sd: dict) -> tuple[float, dict]:
    a = state.cfg.action
    tot, probs = 0.0, {}
    for z, m in mu.items():
        cap = state.capacity(z)
        if not cap or m <= 0:
            continue
        w = a.priority_weight if state.is_priority(z) else 1.0
        tot += w * expected_exposure(m, max(sd[z], 0.01 * cap), cap)
        probs[z] = p_overload(m, max(sd[z], 0.01 * cap), cap)
    return tot, probs


def _feasible(state: EnergyState, base_up: dict, base_mu: dict, up: dict, mu: dict) -> Optional[str]:
    head = state.cfg.action.priority_headroom
    for z in up:
        cap = state.capacity(z)
        gain = mu[z] - base_mu[z]
        if gain <= 1e-6:
            continue
        if cap is None:
            return f"capacity of receiving zone {z} unknown"
        if base_up[z] <= cap < up[z]:
            return f"would push {z} over capacity ({100 * up[z] / cap:.0f}%)"
        if state.is_priority(z) and up[z] > head * cap:
            return f"would load priority zone {z} to {100 * up[z] / cap:.0f}%"
    return None


def search(state: EnergyState, effects: dict[str, Switch], usable: set[str], ol: dict[str, ZoneOutlook],
           max_steps: int = 3, not_now: frozenset[str] = frozenset()) -> tuple[Optional[Plan], float]:
    """Best feasible plan of 1..max_steps tie toggles, and the do-nothing score (same model)."""
    m = build_model(state, effects, usable, ol)
    if m is None:
        return None, 0.0
    closed0 = {sid for sid in m.ties if state.switches[sid].state == SwitchState.closed}
    mu0, sd0, up0, _ = m.loads(closed0)
    base_score, _ = _score(state, mu0, sd0)
    movable = [sid for sid in m.ties if sid in usable]
    cost = state.cfg.action.switching_cost
    best: Optional[Plan] = None
    for n in range(1, max_steps + 1):
        for seq in itertools.permutations(movable, n):
            if seq[0] in not_now:       # first step must be executable now (cooldown, pending, veto)
                continue
            closed, ok, inter = set(closed0), True, []
            for sid in seq:
                closed ^= {sid}
                mu, sd, up, _ = m.loads(closed)
                if _feasible(state, up0, mu0, up, mu):
                    ok = False
                    break
                inter.append({z: round(v, 1) for z, v in up.items()})
            if not ok:
                continue
            sc, probs = _score(state, mu, sd)
            sc += cost * n
            if best is None or sc < best.score - 1e-9:
                steps = [(sid, SwitchOperation.open if sid in closed0 else SwitchOperation.close) for sid in seq]
                best = Plan(steps, sc, {z: round(v, 1) for z, v in mu.items()}, probs, inter)
    return best, base_score


def first_step_candidate(state: EnergyState, effects: dict[str, Switch], usable: set[str],
                         ol: dict[str, ZoneOutlook], plan: Plan) -> Candidate:
    """The plan's first move as an ordinary Candidate (gate, execution and verification unchanged)."""
    m = build_model(state, effects, usable, ol)
    closed0 = {sid for sid in m.ties if state.switches[sid].state == SwitchState.closed}
    mu0, _, up0, now0 = m.loads(closed0)
    sid, op = plan.steps[0]
    mu1, sd1, up1, now1 = m.loads(closed0 ^ {sid})
    sw = m.ties[sid]
    zones = [sw.from_zone, sw.to_zone]
    shift = {z: mu1[z] - mu0[z] for z in zones}
    shift_now = {z: now1[z] - now0[z] for z in zones}
    loads = {z: o.upper for z, o in ol.items()}
    central = {z: o.mu for z, o in ol.items()}
    for z in zones:
        loads[z] = max(0.0, ol[z].upper + (up1[z] - up0[z]))
        central[z] = max(0.0, ol[z].mu + shift[z])
    _, probs = _score(state, {z: mu1[z] for z in zones}, {z: sd1[z] for z in zones})
    c = Candidate(sid, op, loads, central=central, p_over=probs, shift=shift, shift_now=shift_now,
                  reversible=True, kind="relief", score=plan.score)
    c.relieves = [z for z in zones if shift[z] < 0 and state.capacity(z) and ol[z].upper > state.capacity(z)]
    c.plan = plan.labels
    return c

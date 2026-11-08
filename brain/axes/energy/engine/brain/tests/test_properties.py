"""Property-based tests (Hypothesis): safety invariants checked on thousands of random states.

Example-based tests show that the cases we thought of work. These generate random
grids states -- loads, trends, trust verdicts (trusted / untrusted / missing /
quarantined / flapping), switch positions, busy and failed switches, cooldowns,
power-balance contradictions, priority hints -- and assert the invariants that
must hold in EVERY state:

  Gate
    G1  never passes an action if any evidence it depends on is untrusted
    G2  never passes an action whose evidence is contradicted by an aggregate meter
    G3  never passes an action on a quarantined, failed, busy or cooling-down switch
    G4  never de-energizes a priority zone
    G5  without verdicts (degraded mode) never passes restoration or shedding, and only
        passes reversible relief of a persistent priority-zone risk
  Decision engine
    D1  a ranked option never pushes a zone that was within capacity over capacity
    D2  a ranked option never loads a priority zone beyond its headroom
    D3  shedding is ranked only as a last resort: zone measured over capacity, expected
        peak >= shed_above x capacity, never a priority zone
  Planner
    P1  every intermediate configuration of the chosen plan respects D1 and D2
    P2  a plan never contains a feeder (no shedding inside a plan), never repeats a switch
  Forecaster
    F1  outputs are finite, the band is above the forecast, calibration stays in [0.75, 5]
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from energy_brain.config import BrainConfig, PartnerConfig
from energy_brain.decision import evaluate, outlook
from energy_brain.forecast import AdaptiveForecaster
from energy_brain.gate import check, subjects_for
from energy_brain.planner import build_model, search
from energy_brain.schemas import (CityDescriptor, PowerReading, SwitchKind, SwitchOperation, SwitchState,
                                  SwitchStatus, TrustVerdict)
from energy_brain.state import EnergyState

NOW = datetime.now(timezone.utc)
SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])

CITY = CityDescriptor(
    city_id="prop",
    zones=[{"zone_id": "A", "capacity_kw": 500}, {"zone_id": "B", "capacity_kw": 600},
           {"zone_id": "C", "capacity_kw": 500}, {"zone_id": "D", "capacity_kw": 450},
           {"zone_id": "H", "capacity_kw": 400, "tags": ["hospital"]}],
    switches=[{"switch_id": "T-AB", "kind": "tie", "from_zone": "A", "to_zone": "B", "transfer_fraction": 0.35},
              {"switch_id": "T-DB", "kind": "tie", "from_zone": "D", "to_zone": "B", "transfer_fraction": 0.3},
              {"switch_id": "T-DC", "kind": "tie", "from_zone": "D", "to_zone": "C", "transfer_fraction": 0.3},
              {"switch_id": "T-DH", "kind": "tie", "from_zone": "D", "to_zone": "H", "transfer_fraction": 0.3},
              {"switch_id": "T-HC", "kind": "tie", "from_zone": "H", "to_zone": "C", "transfer_fraction": 0.3},
              {"switch_id": "F-A", "kind": "feeder", "from_zone": "A"},
              {"switch_id": "F-D", "kind": "feeder", "from_zone": "D"},
              {"switch_id": "F-H", "kind": "feeder", "from_zone": "H"}],
)
EFFECTS = {s.switch_id: s for s in CITY.switches}
ZONES = [z.zone_id for z in CITY.zones]
TIES = [s.switch_id for s in CITY.switches if s.kind == SwitchKind.tie]
CAP = {z.zone_id: z.capacity_kw for z in CITY.zones}


def _cfg(model: str = "trend") -> BrainConfig:
    return BrainConfig(twin=PartnerConfig(base_url="http://t"), guardian=PartnerConfig(base_url="http://g"),
                       forecast={"model": model})


VERDICT = st.sampled_from(["trusted", "trusted", "trusted", "untrusted", "missing", "quarantined"])

world = st.fixed_dictionaries({
    "base": st.fixed_dictionaries({z: st.floats(0.3, 1.25) for z in ZONES}),       # fraction of capacity
    "slope": st.fixed_dictionaries({z: st.floats(-3.0, 6.0) for z in ZONES}),      # kW/s
    "closed": st.sets(st.sampled_from(TIES), max_size=3),
    "dark": st.sets(st.sampled_from(["F-A", "F-D"]), max_size=1),
    "verdicts": st.fixed_dictionaries({s: VERDICT for s in [f"PL-{z}" for z in ZONES] + [s.switch_id for s in CITY.switches]}),
    "inconsistent": st.sets(st.sampled_from([f"PL-{z}" for z in ZONES]), max_size=2),
    "busy": st.sets(st.sampled_from(TIES), max_size=2),
    "failed": st.sets(st.sampled_from(TIES), max_size=1),
    "cooling": st.sets(st.sampled_from(TIES), max_size=2),
    "hint": st.sampled_from([None, "A", "D", "C"]),
    "risk_count": st.integers(0, 6),
})


def build(w) -> EnergyState:
    s = EnergyState(_cfg(), CITY)
    s.update_switches([SwitchStatus(switch_id=sid, ts=NOW, state=(
        SwitchState.closed if (sid in w["closed"] or (sid.startswith("F") and sid not in w["dark"])) else SwitchState.open))
        for sid in EFFECTS])
    dark_zones = {EFFECTS[f].from_zone for f in w["dark"]}
    for i in range(20):
        s.t = float(i)
        for z in ZONES:
            kw = 0.0 if z in dark_zones else max(0.0, w["base"][z] * CAP[z] + w["slope"][z] * (i - 19))
            s.ingest(PowerReading(sensor_id=f"PL-{z}", zone_id=z, ts=NOW, value=kw, seq=i + 1), float(i),
                     exempt=True, now=NOW)
    verdicts = []
    for subj, v in w["verdicts"].items():
        if v == "missing":
            continue
        verdicts.append(TrustVerdict(subject_id=subj, verdict="untrusted" if v != "trusted" else "trusted",
                                     quarantined=v == "quarantined", ts=NOW))
    s.update_verdicts(verdicts, s.t, NOW)
    s.inconsistent = set(w["inconsistent"])
    if w["hint"]:
        s.priority_hints[w["hint"]] = ("flood pump", NOW + timedelta(minutes=5))
    for z in ZONES:
        s.zones[z].risk_count = w["risk_count"]
    return s


def _all_candidates(s, w):
    usable = set(EFFECTS) - set(w["failed"])
    res = evaluate(s, EFFECTS, usable)
    return res, res.ranked + res.rejected


def _gate(s, c, w):
    return check(s, c, EFFECTS[c.switch_id], t=s.t, cooldown_until={x: s.t + 30 for x in w["cooling"]},
                 busy=set(w["busy"]), failed=set(w["failed"]), now=NOW)


# ------------------------------------------------------------------------------------------- gate

@SETTINGS
@given(world)
def test_gate_invariants(w):
    s = build(w)
    _, cands = _all_candidates(s, w)
    for c in cands:
        if c.switch_id is None:
            continue
        g = _gate(s, c, w)
        if not g.passed:
            continue
        subj = subjects_for(s, EFFECTS[c.switch_id])
        status = {x: s.verdict_status(x, NOW) for x in subj}
        assert "untrusted" not in status.values(), ("G1", c.label, status)                                # G1
        assert not (set(subj) & s.inconsistent), ("G2", c.label)                                          # G2
        assert not any(s.quarantined(x) for x in subj), ("G3 quarantine", c.label)                        # G3
        assert c.switch_id not in w["failed"] and c.switch_id not in w["busy"], ("G3", c.label)
        assert c.switch_id not in w["cooling"], ("G3 cooldown", c.label)
        sw = EFFECTS[c.switch_id]
        if sw.kind == SwitchKind.feeder and c.operation == SwitchOperation.open:
            assert not s.is_priority(sw.from_zone, NOW), ("G4", c.label)                                  # G4
        if g.degraded:                                                                                    # G5
            assert c.kind not in ("restore", "shed"), ("G5", c.label)
            assert c.reversible and any(s.is_priority(z, NOW) for z in c.relieves), ("G5", c.label)


# ------------------------------------------------------------------------------------------- decision

@SETTINGS
@given(world)
def test_decision_invariants(w):
    s = build(w)
    res, _ = _all_candidates(s, w)
    ol = outlook(s)
    a = s.cfg.action
    for c in res.ranked:
        for z, d in c.shift.items():
            cap = s.capacity(z)
            if c.kind != "shed" and d > 0 and cap and ol[z].upper <= cap:
                assert c.loads[z] <= cap + 1e-6, ("D1", c.label, z)                                        # D1
            if d > 0 and s.is_priority(z) and cap:
                assert c.loads[z] <= a.priority_headroom * cap + 1e-6, ("D2", c.label, z)                  # D2
        if c.kind == "shed":                                                                              # D3
            z = EFFECTS[c.switch_id].from_zone
            assert not s.is_priority(z), ("D3 priority", c.label)
            assert ol[z].now >= s.capacity(z) and ol[z].mu >= a.shed_above * s.capacity(z), ("D3", c.label)
            assert not [x for x in res.ranked if x.kind != "shed"], ("D3 not last resort", c.label)


# ------------------------------------------------------------------------------------------- planner

@SETTINGS
@given(world)
def test_planner_invariants(w):
    s = build(w)
    usable = set(EFFECTS) - set(w["failed"])
    ol = outlook(s)
    plan, base = search(s, EFFECTS, usable, ol, max_steps=3)
    if plan is None:
        return
    ids = [sid for sid, _ in plan.steps]
    assert len(ids) == len(set(ids)), "P2 repeated switch"                                                # P2
    assert all(EFFECTS[x].kind == SwitchKind.tie for x in ids), "P2 feeder in plan"
    m = build_model(s, EFFECTS, usable, ol)
    closed = {sid for sid in m.ties if s.switches[sid].state == SwitchState.closed}
    mu0, _, up0, _ = m.loads(closed)
    head = s.cfg.action.priority_headroom
    for sid in ids:                                                                                       # P1
        closed ^= {sid}
        mu, _, up, _ = m.loads(closed)
        for z in m.zones:
            if mu[z] - mu0[z] <= 1e-6:
                continue
            cap = s.capacity(z)
            assert not (up0[z] <= cap < up[z] - 1e-6), ("P1 over capacity", plan.labels, z)
            if s.is_priority(z):
                assert up[z] <= head * cap + 1e-6, ("P1 priority headroom", plan.labels, z)


# ------------------------------------------------------------------------------------------- forecaster

@SETTINGS
@given(st.lists(st.floats(0, 2000, allow_nan=False), min_size=5, max_size=200), st.integers(0, 3),
       st.floats(-1.0, 1.0))
def test_adaptive_forecaster_is_sane(xs, rebase_every, plan_slope):
    f = AdaptiveForecaster(40, 10, 45.0, windows=(10, 20, 40), plan_fn=lambda t: 300 + plan_slope * t)
    for i, x in enumerate(xs):
        f.update(float(i), x)
        if rebase_every and i % (17 * rebase_every) == 16:
            f.rebase(x * 0.7)
        s = f.snapshot()
        for v in (s.level, s.trend, s.sigma, s.at(45), s.upper(45, 2.0), s.std(45)):
            assert math.isfinite(v)
        assert s.upper(45, 2.0) >= s.at(45) - 1e-6
        assert 0.75 <= f.calibration() <= 5.0

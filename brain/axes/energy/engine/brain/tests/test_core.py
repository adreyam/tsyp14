"""Unit tests for forecasting, ingestion, decision, gate and learning."""
from datetime import datetime, timedelta, timezone

import pytest

from energy_brain.config import BrainConfig, PartnerConfig
from energy_brain.decision import evaluate
from energy_brain.forecast import TrendForecaster
from energy_brain.gate import check
from energy_brain.learning import EffectLearner
from energy_brain.config import LearningConfig
from energy_brain.schemas import (CityDescriptor, PowerReading, SwitchKind, SwitchState, SwitchStatus,
                                  TrustVerdict)
from energy_brain.state import EnergyState

NOW = datetime.now(timezone.utc)


def cfg(**kw):
    return BrainConfig(twin=PartnerConfig(base_url="http://t"), guardian=PartnerConfig(base_url="http://g"), **kw)


CITY = CityDescriptor(
    city_id="unit",
    zones=[{"zone_id": "A", "capacity_kw": 500}, {"zone_id": "B", "capacity_kw": 600},
           {"zone_id": "H", "capacity_kw": 400, "tags": ["hospital"]}],
    switches=[{"switch_id": "SAB", "kind": "tie", "from_zone": "A", "to_zone": "B", "transfer_fraction": 0.3},
              {"switch_id": "SAH", "kind": "tie", "from_zone": "A", "to_zone": "H", "transfer_fraction": 0.3},
              {"switch_id": "FA", "kind": "feeder", "from_zone": "A"},
              {"switch_id": "FH", "kind": "feeder", "from_zone": "H"}],
)


def reading(zone, kw, seq):
    return PowerReading(sensor_id=f"PL-{zone}", zone_id=zone, ts=NOW, value=kw, seq=seq)


def make_state(loads, slope_a=0.0, trusted=True):
    st = EnergyState(cfg(), CITY)
    st.update_switches([SwitchStatus(switch_id=s, state=SwitchState.open if s.startswith("S") else SwitchState.closed,
                                     ts=NOW) for s in ("SAB", "SAH", "FA", "FH")])
    subjects = [f"PL-{z}" for z in loads] + ["SAB", "SAH", "FA", "FH"]
    for i in range(20):
        st.t = float(i)
        st.update_verdicts([TrustVerdict(subject_id=s, verdict="trusted" if trusted else "untrusted", ts=NOW)
                            for s in subjects], float(i), NOW)
        for z, kw in loads.items():
            st.ingest(reading(z, kw + (slope_a * i if z == "A" else 0), i + 1), float(i), exempt=False, now=NOW)
    for z in loads:
        st.zones[z].at_risk = True
        st.zones[z].risk_count = 5
    return st


def effects():
    return {s.switch_id: s for s in CITY.switches}


# ------------------------------------------------------------------ forecast

def test_trend_forecaster_recovers_slope_and_bands():
    f = TrendForecaster(window=20, warmup=10)
    for t in range(20):
        f.update(float(t), 100 + 4.0 * t)
    s = f.snapshot()
    assert s.ready and s.trend == pytest.approx(4.0) and s.at(10) == pytest.approx(176 + 40)
    assert s.upper(45, 2) >= s.at(45)


def test_rebase_scales_slope_with_the_load_that_remains():
    """v0.7: a tie moves a fraction of demand, so the zone keeps the same fraction of its trend."""
    f = TrendForecaster(window=20, warmup=10)
    for t in range(20):
        f.update(float(t), 300 + 2.0 * t)
    level = f.snapshot().level
    f.rebase(200)
    s = f.snapshot()
    assert s.level == pytest.approx(200) and s.trend == pytest.approx(2.0 * 200 / level)


# ------------------------------------------------------------------ ingestion

def test_implausible_jump_held_then_confirmed_only_if_trusted():
    st = make_state({"A": 300, "B": 300, "H": 200})
    outcomes = [st.ingest(reading("A", 450, 100 + i), 20.0 + i, exempt=False, now=NOW)[0] for i in range(5)]
    assert outcomes[:4] == ["suspect"] * 4 and outcomes[4] == "confirmed"
    st2 = make_state({"A": 300, "B": 300, "H": 200})
    st2.update_verdicts([TrustVerdict(subject_id="PL-A", verdict="untrusted", ts=NOW)], 20.0, NOW)
    assert st2.ingest(reading("A", 450, 200), 21.0, exempt=False, now=NOW)[0] == "untrusted"


def test_stale_and_replayed_readings_rejected():
    st = make_state({"A": 300, "B": 300, "H": 200})
    old = PowerReading(sensor_id="PL-A", zone_id="A", ts=NOW - timedelta(seconds=60), value=300, seq=999)
    assert st.ingest(old, 30.0, exempt=False, now=NOW)[0] == "rejected"
    assert st.ingest(reading("A", 300, 3), 31.0, exempt=False, now=NOW)[0] == "duplicate"


def test_verdict_expires_on_brain_clock():
    st = make_state({"A": 300, "B": 300, "H": 200})
    assert st.verdict_status("PL-A", NOW) == "trusted"
    st.t = 100.0
    assert st.verdict_status("PL-A", NOW) == "missing"


# ------------------------------------------------------------------ decision

def test_prefers_non_priority_receiver_and_never_sheds_hospital():
    st = make_state({"A": 480, "B": 300, "H": 300}, slope_a=3.0)   # A heading well over 500
    r = evaluate(st, effects(), {"SAB", "SAH", "FA", "FH"})
    assert r.ranked[0].label == "close SAB"
    why = {c.label: c.why_not for c in r.rejected}
    assert "priority zone H" in why["close SAH"]
    assert all(c.label != "open FH" for c in r.ranked)


def test_no_action_when_nothing_is_at_risk():
    st = make_state({"A": 200, "B": 200, "H": 100})
    r = evaluate(st, effects(), {"SAB", "SAH", "FA"})
    assert r.ranked == []


# ------------------------------------------------------------------ gate

def _best(st):
    r = evaluate(st, effects(), {"SAB"})
    return r.ranked[0]


def test_gate_passes_trusted_and_blocks_untrusted():
    st = make_state({"A": 480, "B": 300, "H": 300}, slope_a=3.0)
    c = _best(st)
    g = check(st, c, effects()["SAB"], t=19.0, cooldown_until={}, busy=set(), failed=set(), now=NOW)
    assert g.passed and not g.degraded and len(g.verdict_refs) == 3
    st.update_verdicts([TrustVerdict(subject_id="PL-B", verdict="untrusted", ts=NOW)], 19.0, NOW)
    g = check(st, c, effects()["SAB"], t=19.0, cooldown_until={}, busy=set(), failed=set(), now=NOW)
    assert not g.passed and g.rule == "trust" and "PL-B" in g.detail


def test_gate_safety_rules():
    st = make_state({"A": 480, "B": 300, "H": 300}, slope_a=3.0)
    c, sw = _best(st), effects()["SAB"]
    kw = dict(t=19.0, busy=set(), failed=set(), now=NOW)
    assert check(st, c, sw, cooldown_until={"SAB": 50.0}, **kw).rule == "safety"
    assert "failed" in check(st, c, sw, cooldown_until={}, **{**kw, "failed": {"SAB"}}).detail
    st.update_verdicts([TrustVerdict(subject_id="SAB", verdict="untrusted", quarantined=True, ts=NOW)], 19.0, NOW)
    assert "quarantined" in check(st, c, sw, cooldown_until={}, **kw).detail


def test_degraded_mode_only_for_reversible_priority_relief():
    # Guardian silent: all verdicts expired
    st = make_state({"A": 480, "B": 300, "H": 380}, slope_a=3.0)
    st.t = 100.0
    c = _best(st)   # relieves A (not priority) -> must be blocked
    g = check(st, c, effects()["SAB"], t=100.0, cooldown_until={}, busy=set(), failed=set(), now=NOW)
    assert not g.passed and g.rule == "trust_unavailable"
    st.zones["A"].risk_count = 5
    st.city = CityDescriptor(city_id="u", zones=[{"zone_id": "A", "capacity_kw": 500, "tags": ["flood_pump"]},
                                                 *CITY.zones[1:]], switches=CITY.switches)
    c = _best(st)
    g = check(st, c, effects()["SAB"], t=100.0, cooldown_until={}, busy=set(), failed=set(), now=NOW)
    assert g.passed and g.degraded


# ------------------------------------------------------------------ learning

def test_learns_tie_after_two_consistent_observations():
    L = EffectLearner(LearningConfig())
    L.observe("X", SwitchState.closed, {"A": 500, "B": 300}, {"A": 350, "B": 452})
    assert L.learned("X") is None
    L.observe("X", SwitchState.open, {"A": 352, "B": 448}, {"A": 505, "B": 297})
    sw = L.learned("X")
    assert sw.kind == SwitchKind.tie and (sw.from_zone, sw.to_zone) == ("A", "B")
    assert sw.transfer_fraction == pytest.approx(0.30, abs=0.02)


def test_inconsistent_observations_are_not_adopted():
    L = EffectLearner(LearningConfig())
    L.observe("X", SwitchState.closed, {"A": 500, "B": 300}, {"A": 350, "B": 452})
    L.observe("X", SwitchState.closed, {"A": 500, "B": 300, "C": 200}, {"A": 500, "B": 150, "C": 352})
    assert L.learned("X") is None

"""Unit tests for the v0.7 additions: adaptive forecaster, planner model, power-balance monitor."""
import math
import random

import pytest

from energy_brain.config import ConsistencyConfig
from energy_brain.consistency import ConsistencyMonitor
from energy_brain.forecast import AdaptiveForecaster, TrendForecaster
from energy_brain.planner import reconstruct_demand
from energy_brain.schemas import AggregateMeter, Switch, SwitchKind


def _coverage(model, h=45, n=900, seed=3):
    """One-sided coverage of the 2-sigma upper band on a noisy daily-shape signal."""
    rng = random.Random(seed)
    sig = [320 + 30 * math.sin(2 * math.pi * t / 180) + rng.gauss(0, 4) for t in range(n + h)]
    hit = tot = 0
    for t in range(n):
        model.update(float(t), sig[t])
        s = model.snapshot()
        if t > 120 and s.ready:
            tot += 1
            hit += sig[t + h] <= s.upper(h, 2.0)
    return hit / tot


def test_adaptive_band_is_calibrated_where_fixed_trend_is_not():
    plan = lambda t: 320 + 30 * math.sin(2 * math.pi * t / 180)  # noqa: E731  (J-1 knows the daily shape)
    fixed = _coverage(TrendForecaster(20, 10))
    adaptive = _coverage(AdaptiveForecaster(40, 10, 45.0, plan_fn=plan))
    assert fixed < 0.9                      # v0.6: curvature makes the band far too narrow
    assert adaptive > 0.95                  # target is 97.7 %


def test_adaptive_weights_favour_the_expert_that_is_right():
    plan = lambda t: 320 + 30 * math.sin(2 * math.pi * t / 180)  # noqa: E731
    f = AdaptiveForecaster(40, 10, 45.0, plan_fn=plan)
    for t in range(600):
        f.update(float(t), plan(t))
    w = f.snapshot().info["weights"]
    assert w["plan"] == max(w.values()) and w["plan"] > 0.9


def test_adaptive_rebase_drops_predictions_that_straddle_a_switch():
    f = AdaptiveForecaster(40, 10, 45.0)
    for t in range(60):
        f.update(float(t), 300.0)
    assert f.pending
    f.rebase(200.0)
    assert not f.pending and f.snapshot().level == pytest.approx(200.0)


def test_reconstruct_demand_inverts_transfers():
    ties = {"T1": Switch(switch_id="T1", kind=SwitchKind.tie, from_zone="A", to_zone="B", transfer_fraction=0.3),
            "T2": Switch(switch_id="T2", kind=SwitchKind.tie, from_zone="B", to_zone="C", transfer_fraction=0.2)}
    demand = {"A": 400.0, "B": 300.0, "C": 100.0}
    served = {"A": 400 * 0.7, "B": 300 * 0.8 + 120, "C": 100 + 0.2 * 300}
    got = reconstruct_demand(served, ties, {"T1", "T2"})
    assert got == pytest.approx(demand, abs=0.01)


class _Z:
    def __init__(self, zid, kw, t):
        self.zone_id, self.last_kw, self.last_t, self.sensor_id = zid, kw, t, f"PL-{zid}"


class _St:
    def __init__(self):
        self.zones = {}

    def sensor_of(self, z):
        return self.zones[z].sensor_id


def test_power_balance_flags_persistent_residual_and_clears():
    mon = ConsistencyMonitor.build([AggregateMeter(sensor_id="SUB", zones=["A", "B"])], ConsistencyConfig())
    st = _St()
    out = []
    for t in range(40):
        drift = 4.0 * t if 5 <= t < 20 else 0.0           # A under-reads more and more, then is fixed
        st.zones = {"A": _Z("A", 300 - drift, t), "B": _Z("B", 200, t)}
        mon.observe("SUB", 500.0, t)
        out += [(t, e["flagged"]) for e in mon.check(st, t) if e["transition"]]
    flags = [t for t, f in out if f]
    clears = [t for t, f in out if not f]
    assert flags and 6 <= flags[0] <= 10                # CUSUM: within a few samples of the drift start (t=5)
    assert clears and clears[0] > 20
    assert mon.flagged_sensors(st) == set()


def test_power_balance_skips_incomplete_snapshots():
    mon = ConsistencyMonitor.build([AggregateMeter(sensor_id="SUB", zones=["A", "B"])], ConsistencyConfig())
    st = _St()
    for t in range(30):                                  # B's reading is stale (rejected): never judged
        st.zones = {"A": _Z("A", 100, t), "B": _Z("B", 200, -1)}
        mon.observe("SUB", 900.0, t)
        assert not mon.check(st, t)


def test_power_balance_ignores_a_single_glitch():
    mon = ConsistencyMonitor.build([AggregateMeter(sensor_id="SUB", zones=["A", "B"])], ConsistencyConfig())
    st = _St()
    for t in range(40):
        st.zones = {"A": _Z("A", 300 + (400 if t == 20 else 0), t), "B": _Z("B", 200, t)}
        mon.observe("SUB", 500.0, t)
        assert not mon.check(st, t), t

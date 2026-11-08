"""Air-quality axis: the team's risk formula, levels, hysteresis, unknown state, actions."""
from datetime import datetime, timedelta, timezone

import pytest

from mocks.mock_guardian import ScriptedGuardian
from src.air import (AirBrain, AirConfig, AirReading, SensorHistory, compute, level_of,
                     oxidant, risk_score, season_of)
from src.audit_log import AuditLog
from src.models import TrustStatus

COLD = datetime(2026, 1, 10, 8, tzinfo=timezone.utc)
WARM = datetime(2026, 7, 10, 8, tzinfo=timezone.utc)
CFG = AirConfig()


def reading(sid, pm, o3, no2, ts, zone="A"):
    return {"sensor_id": sid, "type": "air_quality", "pm25": pm, "o3": o3, "no2": no2,
            "zone": zone, "timestamp": ts.isoformat()}


# ---- the formula ---------------------------------------------------------------
def test_oxidant_is_the_weighted_mean():
    assert oxidant(20, 30) == pytest.approx((2.075 * 20 + 1.07 * 30) / 3.145)
    assert oxidant(40, 40) == pytest.approx(40)


def test_worked_example_cold_season():
    r = compute(AirReading(**reading("s", 30, 20, 30, COLD)), SensorHistory(), CFG)
    assert r.r_pm == pytest.approx(120.0)
    assert r.r_ox == pytest.approx(oxidant(20, 30) / 26 * 100)
    assert r.risk == pytest.approx(120 + 0.10 * r.r_ox)
    assert r.driver == "PM2.5" and level_of(r.risk) == "high"


def test_same_values_are_less_risky_in_warm_season():
    cold = compute(AirReading(**reading("s", 30, 20, 30, COLD)), SensorHistory(), CFG)
    warm = compute(AirReading(**reading("s", 30, 20, 30, WARM)), SensorHistory(), CFG)
    assert season_of(WARM) == "warm" and season_of(COLD) == "cold"
    assert warm.r_pm == pytest.approx(30 / 31 * 100) and warm.risk < cold.risk


def test_lag_weights_use_the_previous_hour():
    h = SensorHistory()
    compute(AirReading(**reading("s", 10, 10, 10, COLD)), h, CFG)
    r = compute(AirReading(**reading("s", 50, 30, 30, COLD + timedelta(hours=1))), h, CFG)
    assert r.z_pm == pytest.approx(0.9 * 50 + 0.1 * 10)
    assert r.z_ox == pytest.approx(0.5 * oxidant(30, 30) + 0.5 * oxidant(10, 10))
    assert r.lag == "t-1"


def test_level_bounds():
    assert [level_of(x) for x in (69.9, 70, 99.9, 100, 130, 130.1)] == \
        ["normal", "warning", "warning", "high", "high", "critical"]
    assert risk_score(100, 50) == pytest.approx(105)


# ---- the brain --------------------------------------------------------------------
class Twin:
    def __init__(self):
        self.calls, self.fail = [], False

    def list_parking_lots(self):
        return {"LOT-01": "A", "LOT-02": "B"}

    def set_parking_barrier(self, lot, mode):
        if self.fail:
            raise ConnectionError("down")
        self.calls.append((lot, mode))


def run(brain, values, start=COLD, step_min=10, sid="s1", zone="A"):
    """values: list of (pm, o3, no2); one cycle per value."""
    t = start
    for pm, o3, no2 in values:
        brain.step([reading(sid, pm, o3, no2, t, zone)], t)
        t += timedelta(minutes=step_min)
    return t


def make(cfg=CFG):
    log, twin, g = AuditLog(), Twin(), ScriptedGuardian()
    return AirBrain(g, twin, log, cfg), log, twin, g


def test_high_level_alerts_and_closes_parking_then_reopens():
    brain, log, twin, _ = make(AirConfig(lag_s=0))
    run(brain, [(10, 10, 10), (30, 20, 30), (30, 20, 30)] + [(5, 5, 5)] * 7)   # 1 h minimum
    levels = [e["level"] for e in log.of("air_level")]
    assert levels == ["normal", "high", "warning"]          # down one level at a time
    assert twin.calls == [("LOT-01", "closed"), ("LOT-01", "auto")]     # zone A only
    actions = [e["action"] for e in log.of("air_action")]
    assert "public_alert" in actions and "lift_public_alert" in actions


def test_hysteresis_no_flapping_around_a_threshold():
    """Risk oscillates 101 / 98 / 102 / 97: enters high once, never leaves while near 100."""
    brain, log, twin, _ = make(AirConfig(lag_s=0, c=0.0))
    pm_for = lambda risk: risk / 100 * 25                     # cold season, O3 = NO2 = 0
    run(brain, [(pm_for(r), 0, 0) for r in (101, 98, 102, 97, 101, 98)])
    assert [e["level"] for e in log.of("air_level")] == ["high"]
    assert twin.calls == [("LOT-01", "closed")]
    # clearly below 95 for 2 readings: then it goes down
    run(brain, [(pm_for(80), 0, 0)] * 2, start=COLD + timedelta(hours=2))
    assert [e["level"] for e in log.of("air_level")] == ["high", "warning"]


def test_blocked_sensor_never_gives_all_clear():
    brain, log, twin, g = make(AirConfig(lag_s=0))
    run(brain, [(30, 20, 30)])                                    # high
    g.distrust("s1", "flat for 3 h while neighbours vary 20 %")
    run(brain, [(2, 1, 1)] * 4, start=COLD + timedelta(hours=1))  # "clean" but untrusted
    unknown = log.of("air_zone_unknown")
    assert unknown and unknown[0]["kept_level"] == "high"
    assert [e["level"] for e in log.of("air_level")] == ["high"]  # never normal
    assert twin.calls == [("LOT-01", "closed")]                    # parking stays closed
    assert brain.snapshot()["A"]["level"] == "high" and brain.snapshot()["A"]["unknown"]


def test_zone_takes_the_worst_trusted_sensor():
    brain, log, _, g = make(AirConfig(lag_s=0))
    t = COLD
    brain.step([reading("s1", 5, 5, 5, t), reading("s2", 30, 20, 30, t)], t)
    assert brain.zones["A"].level == "high"
    g.distrust("s2", "spike with no neighbour change")
    t2 = t + timedelta(minutes=10)
    brain.step([reading("s1", 5, 5, 5, t2), reading("s2", 300, 20, 30, t2)], t2)
    risk = log.of("air_zone_risk")[-1]
    assert risk["sensor_id"] == "s1"                              # the spike is not used


def test_guardian_down_means_unknown_not_normal():
    brain, log, _, g = make(AirConfig(lag_s=0))
    run(brain, [(30, 20, 30)])
    g.down = True
    run(brain, [(1, 1, 1)] * 3, start=COLD + timedelta(hours=1))
    assert len(log.of("guardian_unavailable")) == 1
    assert log.of("air_zone_unknown") and brain.zones["A"].level == "high"


def test_silent_sensor_makes_the_zone_unknown():
    brain, log, _, _ = make(AirConfig(lag_s=0, silence_s=600))
    run(brain, [(5, 5, 5)])
    brain.step([], COLD + timedelta(minutes=30))
    assert log.of("air_zone_unknown")


def test_invalid_reading_is_rejected_with_fields_named():
    brain, log, _, _ = make()
    brain.step([{"sensor_id": "s1", "pm25": -3, "zone": "A", "timestamp": COLD.isoformat()}], COLD)
    err = log.of("rejected_message")[0]["error"]
    assert "pm25" in err and "o3" in err


def test_parking_failure_is_retried():
    brain, log, twin, _ = make(AirConfig(lag_s=0))
    twin.fail = True
    run(brain, [(30, 20, 30)])
    assert log.of("twin_unavailable") and not twin.calls
    twin.fail = False
    run(brain, [(30, 20, 30)], start=COLD + timedelta(minutes=10))
    assert twin.calls == [("LOT-01", "closed")]


def test_sudden_drop_steps_down_one_level_at_a_time():
    """Critical, then a sensor suddenly reads clean: one step per hour, not all at once."""
    brain, log, twin, _ = make(AirConfig(lag_s=0))
    run(brain, [(45, 20, 30)] + [(2, 1, 1)] * 20)            # 10-min readings over 3.5 h
    assert [e["level"] for e in log.of("air_level")] == ["critical", "high", "warning", "normal"]
    steps = [e["ts"][11:16] for e in log.of("air_level")]
    assert steps == ["08:00", "09:00", "10:00", "11:00"]


def test_scenarios_a1_to_a5_pass():
    from demo.air import SCENARIOS
    for sid, fn in SCENARIOS.items():
        r = fn(write_log=False)
        assert r.passed, (sid, [t for t, ok in r.checks if not ok])

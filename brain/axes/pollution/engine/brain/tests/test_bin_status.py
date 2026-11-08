from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.bin_status import BinColor, BinConfig, BinTracker
from src.models import BinReading

T0 = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
CAP = 50.0


def cfg(horizon_minutes):
    """Config with one fixed horizon, to test the formula in isolation."""
    return BinConfig(pass_interval_minutes={},
                     fallback_pass_interval_minutes=horizon_minutes)


def reading(pct, minutes, sensor_id="bin-001", cap=CAP, zone_type=None):
    """A reading at `pct` % full, `minutes` after T0."""
    return BinReading(
        sensor_id=sensor_id,
        weight_kg=cap * pct / 100.0,
        capacity_kg=cap,
        location={"lat": 36.8065, "lon": 10.1815},
        zone="zoneC",
        zone_type=zone_type,
        timestamp=T0 + timedelta(minutes=minutes),
    )


def feed(tracker, points, sensor_id="bin-001"):
    """Feed (pct, minutes) pairs; return the status of the last one."""
    status = None
    for pct, minutes in points:
        status = tracker.update(reading(pct, minutes, sensor_id))
    return status


def test_kg_to_percent():
    assert reading(28.4, 0).fill_pct == pytest.approx(28.4)


def test_green_when_almost_empty():
    s = feed(BinTracker(), [(4, 0), (5, 60)])
    assert s.color == BinColor.GREEN


def test_red_at_or_above_90():
    s = feed(BinTracker(), [(85, 0), (90, 60)])
    assert s.color == BinColor.RED


def test_yellow_example_30_percent_plus_10_per_hour():
    # Next pass in 1 hour: 30% + 10%/h -> 40%, no need to collect.
    tracker = BinTracker(cfg(60))
    s = feed(tracker, [(20, 0), (30, 60)])
    assert s.rate_pct_per_h == pytest.approx(10.0)
    assert s.projected_pct == pytest.approx(40.0)
    assert s.color == BinColor.YELLOW


def test_orange_example_60_percent_plus_15_per_hour():
    # Next pass in 2 hours: 60% + 15%/h*2 = 90% -> must be taken now.
    tracker = BinTracker(cfg(120))
    s = feed(tracker, [(45, 0), (60, 60)])
    assert s.rate_pct_per_h == pytest.approx(15.0)
    assert s.projected_pct == pytest.approx(90.0)
    assert s.color == BinColor.ORANGE


def test_same_bin_is_yellow_if_next_pass_is_sooner():
    # Same bin as above, but a truck passes again in 1 hour: 75%, can wait.
    tracker = BinTracker(cfg(60))
    s = feed(tracker, [(45, 0), (60, 60)])
    assert s.color == BinColor.YELLOW


def test_single_reading_is_yellow_with_unknown_rate():
    s = BinTracker().update(reading(50, 0))
    assert s.rate_pct_per_h is None and s.projected_pct is None
    assert s.color == BinColor.YELLOW


def test_emptied_bin_resets_history():
    tracker = BinTracker()
    feed(tracker, [(80, 0), (85, 30)])
    s = tracker.update(reading(3, 60))          # a truck emptied it
    assert s.just_emptied is True
    assert s.rate_pct_per_h is None             # old history discarded
    assert s.color == BinColor.GREEN


def test_small_dip_is_noise_not_an_empty():
    tracker = BinTracker()
    s = feed(tracker, [(40, 0), (39, 30)])
    assert s.just_emptied is False
    assert s.rate_pct_per_h == 0.0              # never negative


def test_stale_reading_is_ignored():
    tracker = BinTracker()
    good = feed(tracker, [(20, 0), (30, 60)])
    stale = tracker.update(reading(99, 30))     # older timestamp
    assert stale == good


def test_bins_are_tracked_independently():
    tracker = BinTracker()
    feed(tracker, [(20, 0), (30, 60)], "bin-A")
    s = feed(tracker, [(70, 0), (95, 60)], "bin-B")
    assert s.color == BinColor.RED


def test_overfull_bin_is_clamped_to_100():
    r = BinReading(sensor_id="b", weight_kg=60, capacity_kg=50,
                   location={"lat": 0, "lon": 0}, zone="z", timestamp=T0)
    assert r.fill_pct == 100.0


def test_malformed_messages_are_rejected():
    base = dict(sensor_id="b", location={"lat": 0, "lon": 0}, zone="z",
                timestamp=T0)
    with pytest.raises(ValidationError):
        BinReading(weight_kg=-1, capacity_kg=50, **base)
    with pytest.raises(ValidationError):
        BinReading(weight_kg=10, capacity_kg=0, **base)
    with pytest.raises(ValidationError):
        BinReading(weight_kg=10, capacity_kg=50,
                   **{**base, "location": {"lat": 200, "lon": 0}})


# --- pass interval by zone type: crowded every 4 h, open every 8 h -----------

def two_readings(zone_type, start, end):
    """Two readings 1 h apart -> a rate of (end - start) %/h."""
    tracker = BinTracker()
    tracker.update(reading(start, 0, zone_type=zone_type))
    return tracker.update(reading(end, 60, zone_type=zone_type))


def test_same_bin_crowded_can_wait_but_open_cannot():
    # 30% at +10%/h. Crowded: next pass in 4 h -> 70%, can wait.
    # Open: next pass only in 8 h -> would hit 100%, so collect now.
    assert two_readings("crowded", 20, 30).color == BinColor.YELLOW
    assert two_readings("crowded", 20, 30).projected_pct == pytest.approx(70.0)
    assert two_readings("open", 20, 30).color == BinColor.ORANGE


def test_unknown_zone_type_uses_the_shorter_interval():
    s = two_readings(None, 20, 30)
    assert s.projected_pct == pytest.approx(70.0)      # 4 h, not 8 h
    assert two_readings("industrial", 20, 30).projected_pct == pytest.approx(70.0)


def test_red_does_not_depend_on_zone_type():
    assert two_readings("open", 85, 92).color == BinColor.RED
    assert two_readings("crowded", 85, 92).color == BinColor.RED

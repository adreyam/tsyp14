from datetime import datetime, timezone

from src.bin_status import BinColor, BinStatus
from src.graph import Graph
from src.models import Location, TruckState, TruckStatus
from src.planner import Planner, PlannerConfig

NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
RED, ORANGE, YELLOW, GREEN = (BinColor.RED, BinColor.ORANGE,
                              BinColor.YELLOW, BinColor.GREEN)


def line_city(n=9):
    """N0 - N1 - ... - N(n-1) along one street, 40 km/h."""
    g = Graph()
    for i in range(n):
        g.add_node(f"N{i}", 36.800, 10.180 + 0.001 * i)
    for i in range(n - 1):
        g.add_road(f"N{i}", f"N{i + 1}", 40)
    return g


def loc(i):
    return Location(lat=36.800, lon=10.180 + 0.001 * i)


def make_bin(bin_id, at, color, weight=10.0, just_emptied=False):
    return BinStatus(sensor_id=bin_id, location=loc(at), timestamp=NOW,
                     fill_pct=50.0, weight_kg=weight, rate_pct_per_h=None,
                     projected_pct=None, color=color, reason="test",
                     just_emptied=just_emptied)


def make_truck(truck_id, at, status=TruckStatus.IDLE, load=None):
    return TruckState(truck_id=truck_id, location=loc(at), status=status,
                      current_load_kg=load, timestamp=NOW)


def bins_of(*items):
    return {b.sensor_id: b for b in items}


def planner(capacity=1000.0, horizon=3, depot=None):
    return Planner(line_city(), PlannerConfig(truck_capacity_kg=capacity,
                                              horizon_stops=horizon,
                                              depot_node=depot))


def test_red_is_taken_before_a_closer_orange():
    p = planner()
    bins = bins_of(make_bin("orange", 1, ORANGE), make_bin("red", 5, RED))
    d = p.replan(bins, {"t1": make_truck("t1", 0)}, NOW)
    assert [x.stop.bin_id for x in d] == ["red"]
    assert p.plans["t1"].provisional[0].bin_id == "orange"   # next, if capacity allows


def test_yellow_and_green_bins_are_ignored():
    p = planner()
    bins = bins_of(make_bin("y", 1, YELLOW), make_bin("g", 2, GREEN))
    assert p.replan(bins, {"t1": make_truck("t1", 0)}, NOW) == []


def test_truck_never_exceeds_its_capacity():
    p = planner(capacity=100.0)
    bins = bins_of(make_bin("a", 1, RED, 60), make_bin("b", 2, RED, 60),
                   make_bin("c", 3, RED, 60))
    p.replan(bins, {"t1": make_truck("t1", 0)}, NOW)
    plan = p.plans["t1"]
    assert plan.committed.bin_id == "a"
    assert plan.provisional == []          # 60 + 60 > 100


def test_two_trucks_never_take_the_same_bin():
    p = planner(capacity=100.0)
    bins = bins_of(make_bin("b1", 1, RED, 60), make_bin("b2", 2, RED, 60),
                   make_bin("b3", 3, RED, 60))
    trucks = {"t1": make_truck("t1", 0), "t2": make_truck("t2", 6)}
    p.replan(bins, trucks, NOW)
    assigned = [s.bin_id for plan in p.plans.values()
                for s in [plan.committed, *plan.provisional] if s]
    assert sorted(assigned) == sorted(set(assigned))          # no duplicates
    assert p.plans["t1"].committed.bin_id == "b1"
    assert p.plans["t2"].committed.bin_id == "b3"             # b2 does not fit anywhere


def test_horizon_limits_how_far_ahead_a_truck_plans():
    p = planner(horizon=2)
    bins = bins_of(*[make_bin(f"b{i}", i, RED) for i in range(1, 6)])
    p.replan(bins, {"t1": make_truck("t1", 0)}, NOW)
    assert len(p.plans["t1"].provisional) == 1               # committed + 1


def test_committed_stop_is_frozen_when_a_closer_red_bin_appears():
    p = planner()
    truck = {"t1": make_truck("t1", 0)}
    first = p.replan(bins_of(make_bin("far", 5, RED)), truck, NOW)
    assert first[0].stop.bin_id == "far"

    again = p.replan(bins_of(make_bin("far", 5, RED), make_bin("near", 1, RED)),
                     truck, NOW)
    assert again == []                                        # no new dispatch
    assert p.plans["t1"].committed.bin_id == "far"
    assert p.plans["t1"].provisional[0].bin_id == "near"      # planned after it


def test_served_bin_credits_the_load_and_the_truck_moves_on():
    p = planner()
    p.replan(bins_of(make_bin("far", 3, RED, 40)), {"t1": make_truck("t1", 0)}, NOW)

    bins = bins_of(make_bin("far", 3, GREEN, 2, just_emptied=True),
                   make_bin("next", 5, RED, 30))
    d = p.replan(bins, {"t1": make_truck("t1", 3)}, NOW)
    assert p.load("t1") == 40.0
    assert [x.stop.bin_id for x in d] == ["next"]


def test_committed_bin_that_turns_yellow_is_released_without_credit():
    p = planner()
    p.replan(bins_of(make_bin("a", 3, RED, 40)), {"t1": make_truck("t1", 0)}, NOW)
    d = p.replan(bins_of(make_bin("a", 3, YELLOW, 40)),
                 {"t1": make_truck("t1", 0)}, NOW)
    assert d == [] and p.plans["t1"].committed is None
    assert p.load("t1") == 0.0


def test_bin_the_guardian_does_not_trust_is_never_planned():
    p = planner()
    bins = bins_of(make_bin("a", 3, RED))
    d = p.replan(bins, {"t1": make_truck("t1", 0)}, NOW, untrusted=frozenset({"a"}))
    assert d == []


def test_committed_bin_that_becomes_untrusted_is_dropped():
    p = planner()
    truck = {"t1": make_truck("t1", 0)}
    bins = bins_of(make_bin("a", 3, RED), make_bin("b", 6, RED))
    assert p.replan(bins, truck, NOW)[0].stop.bin_id == "a"
    d = p.replan(bins, truck, NOW, untrusted=frozenset({"a"}))
    assert [x.stop.bin_id for x in d] == ["b"]


def test_unreachable_bin_is_skipped_without_crashing():
    g = line_city()
    g.add_node("ISLAND", 36.900, 10.300)                      # no road to it
    p = Planner(g, PlannerConfig(truck_capacity_kg=1000.0))
    island_bin = BinStatus(
        sensor_id="lost", location=Location(lat=36.900, lon=10.300), timestamp=NOW,
        fill_pct=95, weight_kg=10, rate_pct_per_h=None, projected_pct=None,
        color=RED, reason="test")
    bins = {"lost": island_bin, "ok": make_bin("ok", 2, RED)}
    d = p.replan(bins, {"t1": make_truck("t1", 0)}, NOW)
    assert [x.stop.bin_id for x in d] == ["ok"]


def test_dispatch_message_matches_the_interface_card():
    p = planner()
    d = p.replan(bins_of(make_bin("bin-9", 3, RED)), {"t1": make_truck("t1", 0)}, NOW)
    m = d[0].to_message()
    assert m["tool"] == "dispatch_truck" and m["truck_id"] == "t1"
    assert m["next_stop"] == {"kind": "bin", "sensor_id": "bin-9",
                              "lat": 36.800, "lon": 10.183}
    assert m["route"][0] == {"lat": 36.800, "lon": 10.180}   # starts at the truck
    assert m["route"][-1] == {"lat": 36.800, "lon": 10.183}  # ends at the bin
    assert m["timestamp"] == "2026-10-17T08:00:00Z"


# --- a red bin replaces a committed orange stop ------------------------------

def test_red_replaces_a_committed_orange_stop():
    p = planner()
    p.replan(bins_of(make_bin("o", 5, ORANGE)), {"t1": make_truck("t1", 0)}, NOW)
    assert p.plans["t1"].committed.bin_id == "o"

    bins = bins_of(make_bin("o", 5, ORANGE), make_bin("r", 2, RED))
    d = p.replan(bins, {"t1": make_truck("t1", 1, TruckStatus.EN_ROUTE)}, NOW)
    assert [x.stop.bin_id for x in d] == ["r"]
    assert p.plans["t1"].committed.bin_id == "r"
    assert p.plans["t1"].provisional[0].bin_id == "o"       # orange is not lost


def test_red_does_not_replace_orange_if_it_cannot_fit_in_the_truck():
    p = planner(capacity=100.0)
    p.replan(bins_of(make_bin("o", 5, ORANGE, 10)), {"t1": make_truck("t1", 0)}, NOW)
    bins = bins_of(make_bin("o", 5, ORANGE, 10), make_bin("big", 2, RED, 200))
    d = p.replan(bins, {"t1": make_truck("t1", 1, TruckStatus.EN_ROUTE)}, NOW)
    assert d == [] and p.plans["t1"].committed.bin_id == "o"


def test_orange_never_replaces_a_committed_orange():
    p = planner()
    truck = {"t1": make_truck("t1", 0)}
    p.replan(bins_of(make_bin("far", 5, ORANGE)), truck, NOW)
    d = p.replan(bins_of(make_bin("far", 5, ORANGE), make_bin("near", 1, ORANGE)),
                 truck, NOW)
    assert d == [] and p.plans["t1"].committed.bin_id == "far"


def test_committed_red_is_never_replaced():
    p = planner()
    truck = {"t1": make_truck("t1", 0)}
    p.replan(bins_of(make_bin("far", 5, RED)), truck, NOW)
    d = p.replan(bins_of(make_bin("far", 5, RED), make_bin("near", 1, RED)), truck, NOW)
    assert d == [] and p.plans["t1"].committed.bin_id == "far"


def test_orange_taken_over_by_a_red_goes_to_an_idle_truck():
    p = planner()
    p.replan(bins_of(make_bin("o", 5, ORANGE)), {"t1": make_truck("t1", 0)}, NOW)
    bins = bins_of(make_bin("o", 5, ORANGE), make_bin("r", 3, RED))
    trucks = {"t1": make_truck("t1", 4, TruckStatus.EN_ROUTE),
              "t2": make_truck("t2", 8)}
    d = p.replan(bins, trucks, NOW)
    assert {x.truck_id: x.stop.bin_id for x in d} == {"t1": "r", "t2": "o"}


def test_idle_truck_is_not_left_idle_while_a_busy_one_queues_stops():
    p = planner()
    p.replan(bins_of(make_bin("a", 5, RED)), {"t1": make_truck("t1", 0)}, NOW)
    bins = bins_of(make_bin("a", 5, RED), make_bin("b", 6, ORANGE))
    trucks = {"t1": make_truck("t1", 4, TruckStatus.EN_ROUTE),
              "t2": make_truck("t2", 8)}
    p.replan(bins, trucks, NOW)
    assert p.plans["t2"].committed.bin_id == "b"            # t2 works, not just t1


# --- depot -------------------------------------------------------------------

def depot_scenario():
    """Capacity 100. t1 serves a 70 kg bin, then a 60 kg bin is waiting."""
    p = planner(capacity=100.0, depot="N0")
    p.replan(bins_of(make_bin("a", 5, RED, 70)), {"t1": make_truck("t1", 4)}, NOW)
    bins = bins_of(make_bin("a", 5, GREEN, 2, just_emptied=True),
                   make_bin("b", 7, RED, 60))
    d = p.replan(bins, {"t1": make_truck("t1", 5)}, NOW)
    return p, bins, d


def test_truck_too_full_for_a_waiting_bin_goes_to_the_depot():
    p, _, d = depot_scenario()
    assert p.load("t1") == 70.0
    assert [(x.truck_id, x.stop.kind) for x in d] == [("t1", "depot")]
    m = d[0].to_message()
    assert m["next_stop"]["kind"] == "depot"
    assert m["route"][-1] == {"lat": 36.800, "lon": 10.180}   # ends at N0


def test_truck_unloads_at_the_depot_then_takes_the_waiting_bin():
    p, bins, _ = depot_scenario()
    d = p.replan(bins, {"t1": make_truck("t1", 0, TruckStatus.EN_ROUTE)}, NOW)
    assert p.load("t1") == 0.0
    assert [(x.truck_id, x.stop.bin_id) for x in d] == [("t1", "b")]


def test_truck_with_enough_room_does_not_go_to_the_depot():
    p = planner(capacity=100.0, depot="N0")
    p.replan(bins_of(make_bin("a", 5, RED, 20)), {"t1": make_truck("t1", 4)}, NOW)
    bins = bins_of(make_bin("a", 5, GREEN, 2, just_emptied=True),
                   make_bin("b", 7, RED, 30))
    d = p.replan(bins, {"t1": make_truck("t1", 5)}, NOW)
    assert [x.stop.kind for x in d] == ["bin"]


def test_truck_passing_through_the_depot_node_is_not_unloaded():
    p, bins, _ = depot_scenario()
    p.plans["t1"].committed = None                            # simulate another route
    bins2 = bins_of(make_bin("c", 7, RED, 10))
    p.replan(bins2, {"t1": make_truck("t1", 0, TruckStatus.EN_ROUTE)}, NOW)
    assert p.load("t1") == 70.0                               # not idle, not heading to depot


def test_depot_must_exist_in_the_map():
    import pytest
    with pytest.raises(ValueError, match="depot"):
        Planner(line_city(), PlannerConfig(depot_node="NOPE"))


# --- real truck load from the Twin ------------------------------------------

def test_load_reported_by_the_twin_replaces_the_estimate():
    p = planner(capacity=100.0)
    p.replan(bins_of(make_bin("a", 5, RED, 40)), {"t1": make_truck("t1", 4)}, NOW)
    bins = bins_of(make_bin("a", 5, GREEN, 2, just_emptied=True))
    p.replan(bins, {"t1": make_truck("t1", 5, load=43.0)}, NOW)
    assert p.load("t1") == 43.0                               # not the estimated 40

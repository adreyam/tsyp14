import random

import pytest

from src.graph import Graph, haversine_m
from src.pathfinding import astar, route_points


def small_city():
    """
    B --- C        A->B->C is 200 m at 30 km/h.
    |  /  |        A->D->C is 200 m but A-D is 60 km/h: the fastest.
    A --- D        A-C is a short diagonal but a slow 10 km/h street.
    """
    g = Graph()
    g.add_node("A", 36.800, 10.180)
    g.add_node("B", 36.801, 10.180)
    g.add_node("C", 36.801, 10.181)
    g.add_node("D", 36.800, 10.181)
    g.add_road("A", "B", 30)
    g.add_road("B", "C", 30)
    g.add_road("A", "D", 60)
    g.add_road("D", "C", 30)
    g.add_road("A", "C", 10)
    return g


def test_picks_the_fastest_path_not_the_shortest_distance():
    r = astar(small_city(), "A", "C")
    assert r.nodes == ["A", "D", "C"]


def test_cost_is_travel_time_in_seconds():
    g = small_city()
    r = astar(g, "A", "D")
    d = haversine_m(36.800, 10.180, 36.800, 10.181)
    assert r.cost_s == pytest.approx(d / (60 / 3.6))


def test_same_start_and_goal():
    r = astar(small_city(), "B", "B")
    assert r.nodes == ["B"] and r.cost_s == 0.0


def test_unreachable_goal_returns_none():
    g = small_city()
    g.add_node("Z", 36.9, 10.3)            # no road to it
    assert astar(g, "A", "Z") is None


def test_one_way_road_is_respected():
    g = small_city()
    g.add_node("E", 36.799, 10.180)
    g.add_road("A", "E", 30, one_way=True)  # A -> E only
    assert astar(g, "A", "E") is not None
    assert astar(g, "E", "A") is None


def test_unknown_node_gives_a_clear_error():
    with pytest.raises(ValueError, match="unknown node 'X'"):
        astar(small_city(), "A", "X")


def test_astar_is_optimal_on_random_cities():
    """A* must match Dijkstra (no heuristic) and explore fewer nodes."""
    rng = random.Random(7)
    g = Graph()
    n = 9
    for i in range(n):
        for j in range(n):
            g.add_node(f"{i}-{j}", 36.80 + i * 0.001 + rng.uniform(0, 0.0003),
                       10.18 + j * 0.001 + rng.uniform(0, 0.0003))
    for i in range(n):
        for j in range(n):
            for di, dj in ((1, 0), (0, 1), (1, 1)):
                if i + di < n and j + dj < n and rng.random() < 0.85:
                    a, b = f"{i}-{j}", f"{i + di}-{j + dj}"
                    na, nb = g.nodes[a], g.nodes[b]
                    straight = haversine_m(na.lat, na.lon, nb.lat, nb.lon)
                    g.add_road(a, b, rng.uniform(20, 60),
                               length_m=straight * rng.uniform(1.0, 1.6))
    ids = list(g.nodes)
    expanded_astar = expanded_dijkstra = 0
    for _ in range(40):
        s, t = rng.choice(ids), rng.choice(ids)
        a = astar(g, s, t)
        d = astar(g, s, t, use_heuristic=False)
        assert (a is None) == (d is None)
        if a is not None:
            assert a.cost_s == pytest.approx(d.cost_s)
            expanded_astar += a.expanded
            expanded_dijkstra += d.expanded
    assert expanded_astar < expanded_dijkstra


def test_too_short_road_is_raised_to_the_straight_line_with_a_warning():
    g = Graph()
    g.add_node("A", 36.800, 10.180)
    g.add_node("B", 36.801, 10.180)
    g.add_road("A", "B", 30, length_m=5.0)   # impossible: ~111 m apart
    assert len(g.warnings) == 1
    r = astar(g, "A", "B")
    assert r.cost_s == pytest.approx(
        haversine_m(36.800, 10.180, 36.801, 10.180) / (30 / 3.6))


def test_bad_map_data_is_rejected_clearly():
    g = Graph()
    g.add_node("A", 0, 0)
    with pytest.raises(ValueError, match="duplicate"):
        g.add_node("A", 1, 1)
    with pytest.raises(ValueError, match="unknown node"):
        g.add_road("A", "B", 30)
    g.add_node("B", 0, 0.001)
    with pytest.raises(ValueError, match="speed"):
        g.add_road("A", "B", 0)


def test_nearest_node_attaches_a_bin_to_the_graph():
    g = small_city()
    assert g.nearest_node(36.8008, 10.1801).id == "B"
    assert g.nearest_node(36.7999, 10.1809).id == "D"


def test_load_map_and_build_dispatch_route():
    g = Graph.from_dict({
        "nodes": [{"id": "N1", "lat": 36.800, "lon": 10.180},
                  {"id": "N2", "lat": 36.800, "lon": 10.181},
                  {"id": "N3", "lat": 36.801, "lon": 10.181}],
        "roads": [{"from": "N1", "to": "N2", "speed_kmh": 40},
                  {"from": "N2", "to": "N3", "speed_kmh": 40, "length_m": 150}],
    })
    r = astar(g, "N1", "N3")
    assert route_points(g, r) == [
        {"lat": 36.800, "lon": 10.180},
        {"lat": 36.800, "lon": 10.181},
        {"lat": 36.801, "lon": 10.181},
    ]

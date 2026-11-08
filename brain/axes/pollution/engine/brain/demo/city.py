"""The simulated city used by the scenarios: a 5x5 street grid with a depot."""
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from mocks.mock_twin import MockTwin, SimBin, SimTruck
from src.graph import Graph

START = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
DEPOT = "r0c0"
CAPACITY_BIN_KG = 50.0
SPACING_DEG = 0.01            # about 1.1 km between intersections


def map_dict() -> dict:
    """The same city as a map message (INTERFACE.md section 0), as the Twin sends it."""
    nodes = [{"id": f"r{i}c{j}", "lat": round(36.80 + SPACING_DEG * i, 6),
              "lon": round(10.18 + SPACING_DEG * j, 6)}
             for i in range(5) for j in range(5)]
    roads = []
    for i in range(5):
        for j in range(5):
            if i < 4:
                roads.append({"from": f"r{i}c{j}", "to": f"r{i + 1}c{j}", "speed_kmh": 30})
            if j < 4:
                roads.append({"from": f"r{i}c{j}", "to": f"r{i}c{j + 1}", "speed_kmh": 30})
    return {"nodes": nodes, "roads": roads}


def build_graph() -> Graph:
    return Graph.from_dict(map_dict())


# id, node, rate kg/h, zone, zone type, initial kg
DEFAULT_BINS: List[Tuple[str, str, float, str, str, float]] = [
    ("b01", "r1c1", 6.0, "zoneA", "crowded", 15.0),
    ("b02", "r1c3", 5.0, "zoneA", "crowded", 10.0),
    ("b03", "r2c2", 4.5, "zoneB", "crowded", 20.0),
    ("b04", "r3c1", 5.5, "zoneB", "crowded", 5.0),
    ("b05", "r3c4", 4.0, "zoneC", "crowded", 12.0),
    ("b06", "r4c0", 1.5, "zoneD", "open", 8.0),
    ("b07", "r4c2", 1.0, "zoneD", "open", 15.0),
    ("b08", "r2c4", 2.0, "zoneC", "open", 10.0),
    ("b09", "r0c3", 1.2, "zoneE", "open", 5.0),
    ("b10", "r4c4", 1.8, "zoneE", "open", 20.0),
]


def build_twin(graph: Graph, bins=None, trucks: int = 2,
               truck_capacity_kg: float = 200.0) -> MockTwin:
    sim_bins = [SimBin(id=i, node=n, capacity_kg=CAPACITY_BIN_KG, rate_kg_per_h=r,
                       zone=z, zone_type=zt, weight_kg=w)
                for i, n, r, z, zt, w in (bins or DEFAULT_BINS)]
    sim_trucks = [SimTruck(id=f"t{k + 1}", node=DEPOT, capacity_kg=truck_capacity_kg)
                  for k in range(trucks)]
    return MockTwin(graph, sim_bins, sim_trucks, DEPOT, START)

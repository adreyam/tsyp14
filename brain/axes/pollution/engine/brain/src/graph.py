"""
City graph: intersections (nodes) joined by roads (edges).

The edge cost is TRAVEL TIME in seconds (length / speed), not distance, so
that traffic can later change a road's cost without touching the algorithm.

The A* heuristic is straight-line distance / fastest speed on the map. It
never overestimates the real travel time, so A* returns the optimal path.
That only holds if a road is never shorter than the straight line between
its ends, so add_road() raises a too-short length to the straight-line
distance and records a warning (bad partner data must not crash the layer).
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line (great-circle) distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Node:
    id: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Edge:
    to: str
    length_m: float
    time_s: float


class Graph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}
        self._adj: Dict[str, List[Edge]] = {}
        self.warnings: List[str] = []
        self._max_speed_mps = 0.0

    # ---- building ----------------------------------------------------
    def add_node(self, node_id: str, lat: float, lon: float) -> None:
        if node_id in self.nodes:
            raise ValueError(f"duplicate node id '{node_id}'")
        self.nodes[node_id] = Node(node_id, lat, lon)
        self._adj[node_id] = []

    def add_road(self, a: str, b: str, speed_kmh: float,
                 length_m: Optional[float] = None, one_way: bool = False) -> None:
        for n in (a, b):
            if n not in self.nodes:
                raise ValueError(f"road {a}->{b}: unknown node '{n}'")
        if speed_kmh <= 0:
            raise ValueError(f"road {a}->{b}: speed must be > 0, got {speed_kmh}")

        na, nb = self.nodes[a], self.nodes[b]
        straight = haversine_m(na.lat, na.lon, nb.lat, nb.lon)
        if length_m is None:
            length_m = straight
        elif length_m < straight:
            self.warnings.append(
                f"road {a}->{b}: length {length_m:.0f} m is shorter than the "
                f"straight line ({straight:.0f} m); using {straight:.0f} m")
            length_m = straight

        speed_mps = speed_kmh / 3.6
        self._max_speed_mps = max(self._max_speed_mps, speed_mps)
        time_s = length_m / speed_mps
        self._adj[a].append(Edge(b, length_m, time_s))
        if not one_way:
            self._adj[b].append(Edge(a, length_m, time_s))

    @classmethod
    def from_dict(cls, data: dict) -> "Graph":
        """Load the map message: {"nodes": [...], "roads": [...]}.
        Only this method knows the map format, so a format change stays here."""
        g = cls()
        for n in data["nodes"]:
            g.add_node(n["id"], n["lat"], n["lon"])
        for r in data["roads"]:
            g.add_road(r["from"], r["to"], r["speed_kmh"],
                       length_m=r.get("length_m"),
                       one_way=r.get("one_way", False))
        return g

    # ---- queries -----------------------------------------------------
    def neighbors(self, node_id: str) -> List[Edge]:
        return self._adj[node_id]

    def nearest_node(self, lat: float, lon: float) -> Node:
        """Closest intersection to a point (used to attach a bin to the graph).
        Linear scan: fine for a city-sized simulation."""
        if not self.nodes:
            raise ValueError("graph has no nodes")
        return min(self.nodes.values(),
                   key=lambda n: haversine_m(lat, lon, n.lat, n.lon))

    def heuristic(self, a: str, b: str) -> float:
        """Optimistic travel time (s) from a to b."""
        if self._max_speed_mps == 0:
            return 0.0
        na, nb = self.nodes[a], self.nodes[b]
        return haversine_m(na.lat, na.lon, nb.lat, nb.lon) / self._max_speed_mps

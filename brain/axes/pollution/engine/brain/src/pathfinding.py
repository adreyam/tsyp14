"""
Shortest path between two nodes by travel time.

astar() is the single entry point the planner calls. A bidirectional version
can later be added with the same signature and result type, so the two can be
swapped and benchmarked (compare `expanded`, the number of nodes explored).
With use_heuristic=False it behaves as Dijkstra, which the tests use to
check that A* really returns the optimal path.
"""
import heapq
import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional

from .graph import Graph


@dataclass(frozen=True)
class PathResult:
    nodes: List[str]      # node ids from start to goal
    cost_s: float         # total travel time in seconds
    expanded: int         # nodes explored (lower = faster search)


def astar(graph: Graph, start: str, goal: str,
          use_heuristic: bool = True) -> Optional[PathResult]:
    """Return the fastest path, or None if the goal is unreachable."""
    for n in (start, goal):
        if n not in graph.nodes:
            raise ValueError(f"unknown node '{n}'")

    h = graph.heuristic if use_heuristic else (lambda a, b: 0.0)
    tie = itertools.count()                    # breaks heap ties deterministically
    best_g: Dict[str, float] = {start: 0.0}
    parent: Dict[str, Optional[str]] = {start: None}
    heap = [(h(start, goal), next(tie), 0.0, start)]
    done = set()
    expanded = 0

    while heap:
        _, _, g, node = heapq.heappop(heap)
        if node in done:
            continue                           # stale entry for a better path
        done.add(node)
        expanded += 1

        if node == goal:
            path = []
            cur: Optional[str] = goal
            while cur is not None:
                path.append(cur)
                cur = parent[cur]
            return PathResult(path[::-1], g, expanded)

        for edge in graph.neighbors(node):
            new_g = g + edge.time_s
            if new_g < best_g.get(edge.to, float("inf")):
                best_g[edge.to] = new_g
                parent[edge.to] = node
                heapq.heappush(
                    heap, (new_g + h(edge.to, goal), next(tie), new_g, edge.to))
    return None


def route_points(graph: Graph, result: PathResult) -> List[dict]:
    """Path as lat/lon points: the `route` field of the dispatch message."""
    return [{"lat": graph.nodes[n].lat, "lon": graph.nodes[n].lon}
            for n in result.nodes]

"""A* routes for the garbage truck on the city's SUMO road network.

The round used to be driven with SUMO's own router (findRoute / changeTarget): fastest by travel time, but on a
regular grid many routes take exactly the same time, and the one SUMO picks often zigzags block by block (turn
left, turn right, turn left...). This router is the same A* as the pollution axis' planner
(brain/axes/pollution/engine/brain/src/pathfinding.py), run on the live road graph:

* a state is a street (a SUMO edge, one direction); its successors are the streets its lanes connect to;
* the cost of a move is the travel time of the next street (free-flow time plus half its current delay, capped, so
  a jam counts without making the truck zigzag round every slow block; a closed street costs 10 minutes) plus a
  turn cost: straight on is free, a right turn costs TURN_RIGHT_S, a left turn TURN_LEFT_S (slowing down, waiting
  for oncoming traffic and pedestrians), a U-turn U_TURN_S;
* the heuristic is the straight-line distance to the target street divided by the fastest speed on the map: it never
  overestimates, so the route returned is the cheapest one (fastest, and with the fewest turns among equally fast).

The result carries `expanded` (streets explored) as the planner's PathResult does, so the search can be compared
with plain Dijkstra (use_heuristic=False).
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass

TURN_RIGHT_S = 10.0
TURN_LEFT_S = 16.0
U_TURN_S = 45.0


@dataclass(frozen=True)
class Route:
    edges: list[str]
    cost_s: float
    turns: int
    expanded: int


class AStarRouter:
    def __init__(self, traci_module) -> None:
        self.traci = traci_module
        self._built = False
        self.successors: dict[str, list[str]] = {}
        self.start: dict[str, tuple[float, float]] = {}
        self.end: dict[str, tuple[float, float]] = {}
        self.length: dict[str, float] = {}
        self.speed: dict[str, float] = {}
        self.max_speed = 13.89

    def _build(self) -> None:
        if self._built:
            return
        for edge in self.traci.edge.getIDList():
            if edge.startswith(":"):
                continue
            lanes = self.traci.edge.getLaneNumber(edge)
            shape = self.traci.lane.getShape(f"{edge}_0")
            self.start[edge], self.end[edge] = shape[0], shape[-1]
            self.length[edge] = self.traci.lane.getLength(f"{edge}_0")
            self.speed[edge] = max(1.0, self.traci.lane.getMaxSpeed(f"{edge}_0"))
            following: set[str] = set()
            for index in range(lanes):
                for link in self.traci.lane.getLinks(f"{edge}_{index}"):
                    target = link[0].rsplit("_", 1)[0]
                    if not target.startswith(":"):
                        following.add(target)
            self.successors[edge] = sorted(following)
        self.max_speed = max(self.speed.values(), default=13.89)
        self._built = True

    def _direction(self, edge: str) -> tuple[float, float]:
        (x1, y1), (x2, y2) = self.start[edge], self.end[edge]
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        return (x2 - x1) / length, (y2 - y1) / length

    def _turn(self, a: str, b: str) -> tuple[float, int]:
        ax, ay = self._direction(a)
        bx, by = self._direction(b)
        dot, cross = ax * bx + ay * by, ax * by - ay * bx
        if dot > 0.7:
            return 0.0, 0                              # straight on
        if dot < -0.7:
            return U_TURN_S, 1                         # turning back
        return (TURN_LEFT_S, 1) if cross > 0 else (TURN_RIGHT_S, 1)   # SUMO y points north: cross > 0 is a left turn

    def _travel(self, edge: str) -> float:
        try:
            measured = float(self.traci.edge.getTraveltime(edge))
        except self.traci.exceptions.TraCIException:
            measured = 0.0
        free = self.length[edge] / self.speed[edge]
        if measured >= 600.0:
            return free + 600.0                        # a closed street: only if there is no other way
        # Congestion counts, but damped and capped: one slow block must not send the truck zigzagging round it.
        return free + min(max(0.0, measured - free), 40.0) * 0.5

    def route(self, origin: str, target: str, use_heuristic: bool = True) -> Route | None:
        """The cheapest street sequence from origin (included) to target (included), or None."""
        self._build()
        if origin not in self.successors or target not in self.successors:
            return None
        if origin == target:
            return Route([origin], 0.0, 0, 1)
        goal = self.start[target]

        def h(edge: str) -> float:
            if not use_heuristic:
                return 0.0
            x, y = self.end[edge]
            return math.hypot(goal[0] - x, goal[1] - y) / self.max_speed

        tie = itertools.count()
        best = {origin: 0.0}
        parent: dict[str, str | None] = {origin: None}
        turns = {origin: 0}
        heap = [(h(origin), next(tie), 0.0, origin)]
        done: set[str] = set()
        expanded = 0
        while heap:
            _, _, cost, edge = heapq.heappop(heap)
            if edge in done:
                continue
            done.add(edge)
            expanded += 1
            if edge == target:
                path = []
                current: str | None = edge
                while current is not None:
                    path.append(current)
                    current = parent[current]
                return Route(path[::-1], round(cost, 1), turns[edge], expanded)
            for following in self.successors.get(edge, ()):
                penalty, turned = self._turn(edge, following)
                new_cost = cost + self._travel(following) + penalty
                if new_cost < best.get(following, math.inf):
                    best[following] = new_cost
                    parent[following] = edge
                    turns[following] = turns[edge] + turned
                    heapq.heappush(heap, (new_cost + h(following), next(tie), new_cost, following))
        return None

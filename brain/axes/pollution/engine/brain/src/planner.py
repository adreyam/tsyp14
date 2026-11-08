"""
Truck planner with horizon replanning.

Every cycle, replan() looks at the current bin colors and truck positions and
plans up to `horizon_stops` stops ahead for each truck. Only the FIRST stop of
a truck is committed (sent to the Twin as a dispatch). The rest is provisional
and is recomputed on every call, so a bin that fills faster than predicted, a
bin the Guardian stopped trusting, or a truck that finished early are all
picked up on the next cycle.

Rules:
  * only RED and ORANGE bins are collected; all reds come before any orange
  * a RED bin can take over a truck that is committed to an ORANGE bin (the
    orange bin goes back to the pool); a committed RED stop is never changed
  * a truck never takes more than its capacity
  * a truck too full for a waiting bin goes to the depot, unloads, and starts
    again empty
  * two trucks never take the same bin
  * a bin the Guardian does not trust is never planned

Assignment is greedy: repeatedly pick the (truck, bin) pair with the best
(color priority, travel time from the truck's last planned position).
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, FrozenSet, List, Optional, Tuple

from .bin_status import BinColor, BinStatus
from .graph import Graph
from .models import TruckState, TruckStatus
from .pathfinding import PathResult, astar, route_points

PRIORITY = {BinColor.RED: 0, BinColor.ORANGE: 1}
DEPOT_ID = "depot"


@dataclass(frozen=True)
class PlannerConfig:
    truck_capacity_kg: float = 2000.0
    horizon_stops: int = 3          # stops planned ahead per truck, committed one included
    depot_node: Optional[str] = None  # node where trucks unload (None = no depot)


@dataclass(frozen=True)
class Stop:
    bin_id: str                     # the bin's sensor id, or "depot"
    node: str
    lat: float
    lon: float
    color: Optional[BinColor]       # None for the depot
    weight_kg: float                # expected load added by this stop
    kind: str = "bin"               # "bin" | "depot"


@dataclass
class TruckPlan:
    committed: Optional[Stop] = None
    provisional: List[Stop] = field(default_factory=list)


@dataclass(frozen=True)
class Dispatch:
    truck_id: str
    stop: Stop
    route: List[dict]
    timestamp: datetime

    def to_message(self) -> dict:
        """The dispatch_truck message of the Interface Card."""
        return {
            "tool": "dispatch_truck",
            "truck_id": self.truck_id,
            "next_stop": {"kind": self.stop.kind, "sensor_id": self.stop.bin_id,
                          "lat": self.stop.lat, "lon": self.stop.lon},
            "route": self.route,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
        }


class Planner:
    def __init__(self, graph: Graph, config: PlannerConfig = PlannerConfig()):
        if config.depot_node is not None and config.depot_node not in graph.nodes:
            raise ValueError(f"depot node '{config.depot_node}' is not in the map")
        self.graph = graph
        self.config = config
        self.plans: Dict[str, TruckPlan] = {}
        self._load: Dict[str, float] = {}
        self._bin_nodes: Dict[str, str] = {}
        self._paths: Dict[Tuple[str, str], Optional[PathResult]] = {}

    # ---- fleet load ----------------------------------------------------
    def load(self, truck_id: str) -> float:
        return self._load.get(truck_id, 0.0)

    def clear_path_cache(self) -> None:
        """Call when road costs change (for example with traffic)."""
        self._paths.clear()

    # ---- main entry point ----------------------------------------------
    def replan(self, bins: Dict[str, BinStatus], trucks: Dict[str, TruckState],
               now: datetime, untrusted: FrozenSet[str] = frozenset()
               ) -> List[Dispatch]:
        for tid, truck in trucks.items():
            self.plans.setdefault(tid, TruckPlan())
            if truck.current_load_kg is not None:        # the Twin's value wins
                self._load[tid] = truck.current_load_kg
        here = {tid: self.graph.nearest_node(t.location.lat, t.location.lon).id
                for tid, t in trucks.items()}

        self._release_finished(bins, trucks, here, untrusted)

        taken = {p.committed.bin_id for p in self.plans.values()
                 if p.committed and p.committed.kind == "bin"}
        candidates = {bid: st for bid, st in bins.items()
                      if st.color in PRIORITY and bid not in untrusted
                      and bid not in taken}

        dispatches: List[Dispatch] = []
        self._assign_reds_first(bins, trucks, here, candidates, dispatches, now)
        self._fill_plans(trucks, here, candidates, dispatches, now)
        self._send_full_trucks_to_depot(trucks, here, candidates, dispatches, now)
        return dispatches

    # ---- phase 0: finished or invalid commitments -----------------------
    def _release_finished(self, bins, trucks, here, untrusted) -> None:
        depot = self.config.depot_node
        for tid, plan in self.plans.items():
            truck = trucks.get(tid)
            stop = plan.committed
            twin_knows_load = truck is not None and truck.current_load_kg is not None

            # unloading: at the depot, heading there or idle
            if depot is not None and truck is not None and here.get(tid) == depot:
                heading_to_depot = stop is not None and stop.kind == "depot"
                if heading_to_depot or truck.status == TruckStatus.IDLE:
                    if not twin_knows_load:
                        self._load[tid] = 0.0
                    if heading_to_depot:
                        plan.committed = None
                        stop = None

            if stop is None or stop.kind == "depot":
                continue                                  # depot stop stays frozen

            st = bins.get(stop.bin_id)
            if st is None or stop.bin_id in untrusted:
                plan.committed = None                     # lost or untrusted
            elif st.just_emptied or st.color == BinColor.GREEN:
                if not twin_knows_load:                   # served: count its weight
                    self._load[tid] = self.load(tid) + stop.weight_kg
                plan.committed = None
            elif st.color not in PRIORITY:
                plan.committed = None                     # YELLOW: can wait now

    # ---- phase 1: reds first, even over a committed orange --------------
    def _assign_reds_first(self, bins, trucks, here, candidates, dispatches, now):
        reds = {b: s for b, s in candidates.items() if s.color == BinColor.RED}
        eligible = [tid for tid in sorted(trucks)
                    if self.plans[tid].committed is None
                    or (self.plans[tid].committed.kind == "bin"
                        and self.plans[tid].committed.color == BinColor.ORANGE)]
        done = set()
        while reds:
            best = None
            for tid in eligible:
                if tid in done:
                    continue
                for bid, st in reds.items():
                    if self.load(tid) + st.weight_kg > self.config.truck_capacity_kg:
                        continue
                    path = self._path(here[tid], self._node_of_bin(st))
                    if path is None:
                        continue
                    key = (path.cost_s, tid, bid)
                    if best is None or key < best[0]:
                        best = (key, tid, bid, st, path)
            if best is None:
                break
            _, tid, bid, st, path = best
            plan = self.plans[tid]
            if plan.committed is not None:                # orange goes back to the pool
                candidates[plan.committed.bin_id] = bins[plan.committed.bin_id]
            plan.committed = self._stop_of(st)
            dispatches.append(Dispatch(tid, plan.committed,
                                       route_points(self.graph, path), now))
            del reds[bid]
            del candidates[bid]
            done.add(tid)

    # ---- phase 2: first stop for free trucks, then provisional stops -----
    def _fill_plans(self, trucks, here, candidates, dispatches, now):
        pos: Dict[str, str] = {}
        planned_load: Dict[str, float] = {}
        count: Dict[str, int] = {}
        for tid in trucks:
            plan = self.plans[tid]
            plan.provisional = []
            c = plan.committed
            if c is None:
                pos[tid], planned_load[tid], count[tid] = here[tid], self.load(tid), 0
            elif c.kind == "depot":
                pos[tid], planned_load[tid], count[tid] = c.node, 0.0, 1   # empty after
            else:
                pos[tid] = c.node
                planned_load[tid] = self.load(tid) + c.weight_kg
                count[tid] = 1

        # Pass 1: only trucks with nothing committed compete, so an idle truck
        # is never left idle while a busy truck queues more stops.
        # Pass 2: every truck may add provisional stops up to the horizon.
        for only_free in (True, False):
            while True:
                best = None
                for tid in sorted(trucks):
                    if count[tid] >= self.config.horizon_stops:
                        continue
                    if only_free and self.plans[tid].committed is not None:
                        continue
                    for bid, st in candidates.items():
                        if planned_load[tid] + st.weight_kg > self.config.truck_capacity_kg:
                            continue
                        path = self._path(pos[tid], self._node_of_bin(st))
                        if path is None:
                            continue
                        key = (PRIORITY[st.color], path.cost_s, tid, bid)
                        if best is None or key < best[0]:
                            best = (key, tid, bid, st, path)
                if best is None:
                    break
                _, tid, bid, st, path = best
                stop = self._stop_of(st)
                plan = self.plans[tid]
                if plan.committed is None:
                    plan.committed = stop
                    dispatches.append(
                        Dispatch(tid, stop, route_points(self.graph, path), now))
                else:
                    plan.provisional.append(stop)
                pos[tid] = stop.node
                planned_load[tid] += st.weight_kg
                count[tid] += 1
                del candidates[bid]

    # ---- phase 3: a truck too full for a waiting bin goes to unload ------
    def _send_full_trucks_to_depot(self, trucks, here, candidates, dispatches, now):
        depot = self.config.depot_node
        if depot is None:
            return
        cap = self.config.truck_capacity_kg
        for tid in sorted(trucks):
            plan = self.plans[tid]
            if plan.committed is not None or self.load(tid) <= 0 or here[tid] == depot:
                continue
            blocked = any(self.load(tid) + st.weight_kg > cap and st.weight_kg <= cap
                          for st in candidates.values())
            path = self._path(here[tid], depot) if blocked else None
            if path is None:
                continue
            node = self.graph.nodes[depot]
            plan.committed = Stop(DEPOT_ID, depot, node.lat, node.lon, None, 0.0, "depot")
            dispatches.append(Dispatch(tid, plan.committed,
                                       route_points(self.graph, path), now))

    # ---- helpers --------------------------------------------------------
    def _stop_of(self, st: BinStatus) -> Stop:
        return Stop(st.sensor_id, self._node_of_bin(st), st.location.lat,
                    st.location.lon, st.color, st.weight_kg)

    def _node_of_bin(self, st: BinStatus) -> str:
        if st.sensor_id not in self._bin_nodes:
            self._bin_nodes[st.sensor_id] = self.graph.nearest_node(
                st.location.lat, st.location.lon).id
        return self._bin_nodes[st.sensor_id]

    def _path(self, a: str, b: str) -> Optional[PathResult]:
        if (a, b) not in self._paths:
            self._paths[(a, b)] = astar(self.graph, a, b)
        return self._paths[(a, b)]

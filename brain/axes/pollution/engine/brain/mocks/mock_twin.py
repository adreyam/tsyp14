"""
Stand-in for the Twin layer (R1): a small deterministic city simulation.

Bins fill at a constant rate, trucks follow the route they are dispatched on,
empty the bin when they arrive, and unload at the depot. Every tick it
produces the same messages the real Twin is expected to send, and scenarios can
tamper with them (spoofed weight, silent sensor, malformed message).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from src.graph import Graph


@dataclass
class SimBin:
    id: str
    node: str
    capacity_kg: float
    rate_kg_per_h: float
    zone: str
    zone_type: str
    weight_kg: float = 0.0
    overflow_kg: float = 0.0


@dataclass
class SimTruck:
    id: str
    node: str
    capacity_kg: float
    load_kg: float = 0.0
    status: str = "idle"
    route: List[str] = field(default_factory=list)
    target: Optional[Tuple[str, str]] = None      # (kind, sensor_id)
    progress_s: float = 0.0
    max_load_kg: float = 0.0


class MockTwin:
    def __init__(self, graph: Graph, bins: List[SimBin], trucks: List[SimTruck],
                 depot_node: str, start: datetime, tick_s: float = 300.0):
        self.graph = graph
        self.bins: Dict[str, SimBin] = {b.id: b for b in bins}
        self.trucks: Dict[str, SimTruck] = {t.id: t for t in trucks}
        self.depot_node = depot_node
        self.now = start
        self.tick_s = tick_s
        self.reported: Dict[str, float] = {}      # sensor id -> fake weight to report
        self.silent: Set[str] = set()             # sensors that stop sending
        self.extra_bin_messages: List[dict] = []  # raw messages injected next tick
        self.offline = False                      # refuse dispatches
        self.served: List[Tuple[datetime, str, str]] = []   # (time, truck, bin)
        self.unloads = 0

    # ---- what City Brain calls ------------------------------------------------
    def send_dispatch(self, message: dict) -> None:
        if self.offline:
            raise ConnectionError("twin unreachable")
        truck = self.trucks[message["truck_id"]]
        nodes = [self.graph.nearest_node(p["lat"], p["lon"]).id
                 for p in message["route"]]
        if nodes and nodes[0] == truck.node:
            nodes = nodes[1:]
        truck.route = nodes
        truck.progress_s = 0.0
        stop = message["next_stop"]
        truck.target = (stop["kind"], stop["sensor_id"])
        truck.status = "en_route"
        if not truck.route:
            self._arrive(truck)

    # ---- scenario controls ----------------------------------------------------
    def set_weight(self, bin_id: str, kg: float) -> None:
        self.bins[bin_id].weight_kg = min(kg, self.bins[bin_id].capacity_kg)

    # ---- simulation ---------------------------------------------------------------
    def tick(self) -> Tuple[List[dict], List[dict], datetime]:
        self.now += timedelta(seconds=self.tick_s)
        for truck in self.trucks.values():
            self._move(truck)
        hours = self.tick_s / 3600.0
        for b in self.bins.values():
            b.weight_kg += b.rate_kg_per_h * hours
            if b.weight_kg > b.capacity_kg:
                b.overflow_kg += b.weight_kg - b.capacity_kg
                b.weight_kg = b.capacity_kg
        return self._bin_messages(), self._truck_messages(), self.now

    def _move(self, truck: SimTruck) -> None:
        budget = self.tick_s
        while truck.route and budget > 0:
            nxt = truck.route[0]
            edge = next((e for e in self.graph.neighbors(truck.node) if e.to == nxt), None)
            if edge is None:
                raise ValueError(f"route is not connected: {truck.node} -> {nxt}")
            remaining = edge.time_s - truck.progress_s
            if budget >= remaining:
                budget -= remaining
                truck.node = nxt
                truck.route.pop(0)
                truck.progress_s = 0.0
            else:
                truck.progress_s += budget
                budget = 0
        if truck.target and not truck.route:
            self._arrive(truck)

    def _arrive(self, truck: SimTruck) -> None:
        kind, sensor_id = truck.target
        if kind == "bin":
            b = self.bins[sensor_id]
            truck.load_kg += b.weight_kg
            truck.max_load_kg = max(truck.max_load_kg, truck.load_kg)
            b.weight_kg = 0.0
            self.served.append((self.now, truck.id, sensor_id))
        else:
            truck.load_kg = 0.0
            self.unloads += 1
        truck.target = None
        truck.status = "idle"

    def _bin_messages(self) -> List[dict]:
        out = []
        for b in self.bins.values():
            if b.id in self.silent:
                continue
            node = self.graph.nodes[b.node]
            out.append({
                "tool": "get_sensor_reading", "sensor_id": b.id, "type": "bin_weight",
                "weight_kg": self.reported.get(b.id, b.weight_kg),
                "capacity_kg": b.capacity_kg,
                "location": {"lat": node.lat, "lon": node.lon},
                "zone": b.zone, "zone_type": b.zone_type,
                "timestamp": self.now.isoformat().replace("+00:00", "Z"),
            })
        out.extend(self.extra_bin_messages)
        self.extra_bin_messages = []
        return out

    def _truck_messages(self) -> List[dict]:
        out = []
        for t in self.trucks.values():
            node = self.graph.nodes[t.node]
            out.append({
                "tool": "get_truck_state", "truck_id": t.id,
                "location": {"lat": node.lat, "lon": node.lon},
                "status": t.status, "current_load_kg": t.load_kg,
                "timestamp": self.now.isoformat().replace("+00:00", "Z"),
            })
        return out

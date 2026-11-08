#!/usr/bin/env python3
"""Run bundled SUMO and publish live traffic state to Godot over localhost UDP.

Contract (see docs/INCIDENT_SYSTEM_ARCHITECTURE.md):
* every simulated vehicle is published and rendered — the civilian fleet is a
  closed population kept inside the city by legal route continuations;
* published ``x``/``z`` are the vehicle *body centre* and ``len``/``wid``/``cls``
  describe the exact geometry Godot renders;
* the incident engine (when present) owns emergency units and incident
  choreography; this module only transports its state.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sys
import time
import xml.etree.ElementTree as ET
import zlib


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "runtime" / "sumo"
SUMO_BINARY = RUNTIME / "usr" / "bin" / "sumo"
SUMO_HOME = RUNTIME / "usr" / "share" / "sumo"
CONFIG = PROJECT / "sumo" / "city.sumocfg"
ROUTES = PROJECT / "sumo" / "city.rou.xml"
VEHICLE_CLASSES_FILE = PROJECT / "sumo" / "vehicle_classes.json"
WORLD_HALF_EXTENT = 380.0
GRID_SIZE = 9
STOP_REQUESTED = False
SIMULATION_SPEED = 1
WEATHER_NAME = "clear"
WEATHER_TRAFFIC_FACTOR = 1.0
UDP_LIMIT = 65507
CIVILIAN_TYPES = ("car", "taxi", "bus")
# Stop reasons need extra TraCI queries; refreshing them at ~3 Hz keeps the
# 15 Hz stream cheap with a fully rendered fleet while staying explainable.
STOP_REASON_REFRESH_STEPS = 5
CONTINUATION_CHECK_STEPS = 15
GRIDLOCK_REROUTE_SECONDS = 150.0

_vehicle_class_data = json.loads(VEHICLE_CLASSES_FILE.read_text(encoding="utf-8"))
SUMO_TYPE_CLASS: dict[str, str] = dict(_vehicle_class_data["sumo_type_class"])
VEHICLE_CLASSES: dict[str, dict] = dict(_vehicle_class_data["classes"])


def outbound_gate_edges() -> list[tuple[str, float, float]]:
    """Return destination edges that leave through a non-corner city gate."""
    gates: list[tuple[str, float, float]] = []
    for coordinate in range(2, 9):
        axis = float((coordinate - 1) * 95)
        gates.extend(
            [
                (f"E_r2c{coordinate}_r1c{coordinate}", axis, 0.0),
                (f"E_r8c{coordinate}_r9c{coordinate}", axis, 760.0),
                (f"E_r{coordinate}c2_r{coordinate}c1", 0.0, axis),
                (f"E_r{coordinate}c8_r{coordinate}c9", 760.0, axis),
            ]
        )
    return gates


def inbound_gate_edges() -> list[str]:
    """Perimeter-to-interior edges: the only place a new civilian may appear."""
    gates: list[str] = []
    for coordinate in range(2, 9):
        gates.extend(
            [
                f"E_r1c{coordinate}_r2c{coordinate}",
                f"E_r9c{coordinate}_r8c{coordinate}",
                f"E_r{coordinate}c1_r{coordinate}c2",
                f"E_r{coordinate}c9_r{coordinate}c8",
            ]
        )
    return gates


OUTBOUND_GATES = outbound_gate_edges()
INBOUND_GATES = inbound_gate_edges()


def request_stop(_signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def prepare_environment() -> None:
    library_root = RUNTIME / "usr" / "lib" / "x86_64-linux-gnu"
    bundled_paths = [
        str(library_root),
        str(library_root / "blas"),
        str(library_root / "lapack"),
        str(RUNTIME / "usr" / "lib"),
    ]
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(bundled_paths + ([existing] if existing else []))
    os.environ["SUMO_HOME"] = str(SUMO_HOME)
    sys.path.insert(0, str(SUMO_HOME / "tools"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8814)
    parser.add_argument("--control-port", type=int, default=8815, help="localhost operator-control UDP port")
    parser.add_argument("--monitor-port", type=int, default=0, help="optional localhost UDP mirror for the separate control room")
    parser.add_argument("--rate", type=float, default=20.0, help="SUMO and broadcast updates per second")
    parser.add_argument("--no-parking", action="store_true", help="run without the smart car parks")
    parser.add_argument("--warmup", type=float, default=90.0, help="simulation seconds before broadcasting")
    parser.add_argument("--test", action="store_true", help="validate SUMO/TraCI and exit without UDP")
    parser.add_argument("--test-steps", type=int, default=400)
    parser.add_argument("--ready-file", type=Path, help="write this file after SUMO warm-up is complete")
    parser.add_argument("--no-incidents", action="store_true", help="run without the incident/emergency engine")
    return parser.parse_args()


def civilian_fleet_target() -> int:
    """The closed civilian population is exactly the trips in the route file."""
    return len(ET.parse(ROUTES).getroot().findall("trip"))


def sumo_command(step_length: float) -> list[str]:
    return [
        str(SUMO_BINARY),
        "-c", str(CONFIG),
        "--step-length", str(step_length),
        "--no-step-log", "true",
        "--duration-log.disable", "true",
        "--no-warnings", "true",
        "--device.rerouting.probability", "0.12",
        "--device.rerouting.period", "45",
        "--seed", "90210",
    ]


def is_perimeter_point(x: float, y: float, margin: float = 90.0) -> bool:
    return min(x, y, WORLD_HALF_EXTENT * 2.0 - x, WORLD_HALF_EXTENT * 2.0 - y) <= margin


def is_perimeter_entry(traci_module, vehicle_id: str, margin: float = 90.0) -> bool:
    """Return true only while a vehicle is in the city's perimeter band."""
    x, y = traci_module.vehicle.getPosition(vehicle_id)
    return is_perimeter_point(x, y, margin)


def visual_class_for_type(type_id: str) -> str:
    return SUMO_TYPE_CLASS.get(type_id, "car")


def body_centre(front_x: float, front_y: float, angle_degrees: float, length: float) -> tuple[float, float]:
    """SUMO reports the front bumper; renderers and contact tests use the centre."""
    heading = math.radians(angle_degrees)
    return front_x - math.sin(heading) * length * 0.5, front_y - math.cos(heading) * length * 0.5


def maintain_visible_fleet_routes(traci_module, tracked, continuation_counts: dict[str, int], excluded: set[str] | None = None) -> int:
    """Give displayed cars another cross-city destination before their exit edge.

    SUMO remains authoritative: ``changeTarget`` performs a legal network
    reroute. Triggering three edges before arrival makes the decision at an
    interior junction, so a car neither vanishes at the map border nor
    performs an artificial visual U-turn.
    """
    active = set(traci_module.vehicle.getIDList())
    continued_this_step = 0
    for vehicle_id in tracked:
        if vehicle_id not in active or (excluded is not None and vehicle_id in excluded):
            continue
        try:
            route = list(traci_module.vehicle.getRoute(vehicle_id))
            route_index = int(traci_module.vehicle.getRouteIndex(vehicle_id))
            remaining = len(route) - route_index - 1
            if remaining > 3 or not route:
                continue
            x, y = traci_module.vehicle.getPosition(vehicle_id)
            far_gates = [gate for gate in OUTBOUND_GATES if gate[0] != route[-1] and (gate[1] - x) ** 2 + (gate[2] - y) ** 2 >= 430.0 ** 2]
            if not far_gates:
                far_gates = [gate for gate in OUTBOUND_GATES if gate[0] != route[-1]]
            cycle = int(continuation_counts.get(vehicle_id, 0))
            seed = zlib.crc32(vehicle_id.encode("utf-8")) + cycle * 17
            target_edge = far_gates[seed % len(far_gates)][0]
            traci_module.vehicle.changeTarget(vehicle_id, target_edge)
            continuation_counts[vehicle_id] = cycle + 1
            continued_this_step += 1
        except traci_module.exceptions.TraCIException:
            # A vehicle can finish between list collection and the command.
            continue
    for vehicle_id in list(continuation_counts):
        if vehicle_id not in active:
            continuation_counts.pop(vehicle_id, None)
    return continued_this_step


EDGE_PATTERN = re.compile(r"E_r(\d+)c(\d+)_r(\d+)c(\d+)_")


def approach_for_lane(lane_id: str) -> str | None:
    match = EDGE_PATTERN.search(lane_id)
    if match is None:
        return None
    from_row, from_column, to_row, to_column = map(int, match.groups())
    if from_row < to_row:
        return "south"
    if from_row > to_row:
        return "north"
    if from_column < to_column:
        return "west"
    if from_column > to_column:
        return "east"
    return None


def strongest_signal(current: str, candidate: str) -> str:
    strength = {"r": 0, "u": 0, "o": 0, "O": 0, "y": 1, "Y": 1, "g": 2, "G": 3}
    return candidate if strength.get(candidate, 0) > strength.get(current, 0) else current


def classify_stop(traci_module, vehicle_id: str, speed: float, road_id: str, position: tuple[float, float] | None = None) -> str:
    """Return an explainable SUMO stop reason instead of a bare stopped flag."""
    if speed >= 0.15:
        return "moving"
    try:
        x, y = position if position is not None else traci_module.vehicle.getPosition(vehicle_id)
        # Entry-metering backpressure at the perimeter is normal, not random.
        if is_perimeter_point(x, y, margin=150.0):
            return "entry_gate"
        for tls_data in traci_module.vehicle.getNextTLS(vehicle_id):
            _tls_id, _link_index, distance, state = tls_data
            if distance <= 55.0 and str(state).lower() in {"r", "y"}:
                return "traffic_light"
            if str(state).lower() in {"r", "y"}:
                return "downstream_signal"
        leader = traci_module.vehicle.getLeader(vehicle_id, 80.0)
        if leader is not None:
            return "queue"
    except traci_module.exceptions.TraCIException:
        pass
    if road_id.startswith(":"):
        return "yielding_turn"
    return "unexplained"


class TrafficState:
    """Owns the per-step snapshot of the whole (fully rendered) SUMO fleet."""

    def __init__(self, traci_module) -> None:
        self.traci = traci_module
        self.tc = traci_module.constants
        self.subscribed: set[str] = set()
        self.continuation_counts: dict[str, int] = {}
        self.controlled_links_cache: dict = {}
        self.stop_reasons: dict[str, str] = {}
        self.step_index = 0
        self.civilian_target = civilian_fleet_target()
        self.replacement_index = 0
        self.closed_edges: set[str] = set()   # streets closed to traffic (road_blocks.py): no new car enters through them
        self.gridlock_reroutes = 0
        self.last_snapshot: dict[str, dict] = {}
        # Replacement only starts after the scheduled fleet has fully entered;
        # before that a "missing" vehicle is simply one whose depart time is
        # still in the future.
        self.departed_civilians: set[str] = set()

    def _variables(self) -> list[int]:
        # VAR_SIGNALS carries brake light, blinkers and reversing for the
        # vehicles' integrated lamps.
        return [
            self.tc.VAR_POSITION, self.tc.VAR_SPEED, self.tc.VAR_ROAD_ID, self.tc.VAR_ANGLE,
            self.tc.VAR_ACCELERATION, self.tc.VAR_TYPE, self.tc.VAR_LENGTH, self.tc.VAR_WIDTH,
            self.tc.VAR_LANE_ID, self.tc.VAR_LANEPOSITION, self.tc.VAR_WAITING_TIME,
            self.tc.VAR_SIGNALS,
        ]

    def _subscribe_new(self) -> None:
        for vehicle_id in self.traci.simulation.getDepartedIDList():
            if vehicle_id.startswith("civ_"):
                self.departed_civilians.add(vehicle_id)
            if vehicle_id not in self.subscribed:
                try:
                    self.traci.vehicle.subscribe(vehicle_id, self._variables())
                    self.traci.vehicle.setSpeedFactor(vehicle_id, WEATHER_TRAFFIC_FACTOR)
                    self.subscribed.add(vehicle_id)
                except self.traci.exceptions.TraCIException:
                    continue
        for vehicle_id in self.traci.simulation.getArrivedIDList():
            self.subscribed.discard(vehicle_id)
            self.stop_reasons.pop(vehicle_id, None)

    def bootstrap(self) -> None:
        """Subscribe vehicles that departed before the bridge started stepping."""
        for vehicle_id in self.traci.vehicle.getIDList():
            if vehicle_id.startswith("civ_"):
                self.departed_civilians.add(vehicle_id)
            if vehicle_id not in self.subscribed:
                self.traci.vehicle.subscribe(vehicle_id, self._variables())
                self.subscribed.add(vehicle_id)

    def collect(self) -> dict[str, dict]:
        """Read every subscribed vehicle once per step into plain dictionaries."""
        self._subscribe_new()
        results = self.traci.vehicle.getAllSubscriptionResults()
        # Subscription results can outlive a vehicle removed through TraCI by one
        # step; only report vehicles that exist now.
        active = set(self.traci.vehicle.getIDList())
        tc = self.tc
        snapshot: dict[str, dict] = {}
        for vehicle_id, values in results.items():
            if tc.VAR_POSITION not in values or vehicle_id not in active:
                continue
            x, y = values[tc.VAR_POSITION]
            snapshot[vehicle_id] = {
                "front": (float(x), float(y)),
                "speed": float(values[tc.VAR_SPEED]),
                "road": str(values[tc.VAR_ROAD_ID]),
                "angle": float(values[tc.VAR_ANGLE]),
                "acceleration": float(values[tc.VAR_ACCELERATION]),
                "type": str(values[tc.VAR_TYPE]),
                "length": float(values[tc.VAR_LENGTH]),
                "width": float(values[tc.VAR_WIDTH]),
                "lane": str(values[tc.VAR_LANE_ID]),
                "lane_position": float(values[tc.VAR_LANEPOSITION]),
                "waiting": float(values[tc.VAR_WAITING_TIME]),
                # Brake light, blinkers and reversing (TraCI signal bits).
                "signals": int(values.get(tc.VAR_SIGNALS, 0)),
            }
        self.last_snapshot = snapshot
        return snapshot

    def civilian_ids(self, snapshot: dict[str, dict]) -> list[str]:
        return [vehicle_id for vehicle_id, data in snapshot.items() if data["type"] in CIVILIAN_TYPES]

    def maintain_fleet(self, snapshot: dict[str, dict], excluded: set[str]) -> int:
        """Keep civilians inside the city and the population at its target."""
        # Each vehicle is still checked once every CONTINUATION_CHECK_STEPS
        # steps, but as a rotating slice per step: checking the whole fleet in
        # one step cost ~11 ms and blew the time budget of accelerated ticks.
        civilians = self.civilian_ids(snapshot)
        due = [vehicle_id for index, vehicle_id in enumerate(civilians) if (index + self.step_index) % CONTINUATION_CHECK_STEPS == 0]
        continued = maintain_visible_fleet_routes(self.traci, due, self.continuation_counts, excluded) if due else 0
        if self.step_index % CONTINUATION_CHECK_STEPS == 0:
            self._replenish(len(civilians))
            self._resolve_gridlock(snapshot, excluded)
        return continued

    def _replenish(self, civilian_count: int) -> None:
        # Vehicles removed by an incident (a towed wreck) are replaced by a new
        # car entering through a perimeter gate, never in the middle of a street.
        if len(self.departed_civilians) < self.civilian_target:
            return
        pending = [vehicle_id for vehicle_id in self.traci.simulation.getPendingVehicles() if vehicle_id.startswith("civ")]
        missing = self.civilian_target - civilian_count - len(pending)
        gates = [gate for gate in INBOUND_GATES if gate not in self.closed_edges] or list(INBOUND_GATES)
        for _ in range(max(0, min(missing, 2))):
            gate = gates[(self.replacement_index * 11) % len(gates)]
            vehicle_id = f"civ_r{self.replacement_index:04d}"
            self.replacement_index += 1
            route_id = f"route_{vehicle_id}"
            try:
                self.traci.route.add(route_id, [gate])
                self.traci.vehicle.add(vehicle_id, route_id, typeID="car", depart="now", departLane="best", departSpeed="max")
                self.continuation_counts[vehicle_id] = 0
            except self.traci.exceptions.TraCIException:
                continue

    def _resolve_gridlock(self, snapshot: dict[str, dict], excluded: set[str]) -> None:
        # Teleporting is disabled because every vehicle is visible. A civilian
        # that has waited abnormally long gets a legal travel-time reroute.
        for vehicle_id, data in snapshot.items():
            if vehicle_id in excluded or data["type"] not in CIVILIAN_TYPES:
                continue
            if data["waiting"] >= GRIDLOCK_REROUTE_SECONDS and not data["road"].startswith(":"):
                try:
                    self.traci.vehicle.rerouteTraveltime(vehicle_id)
                    self.gridlock_reroutes += 1
                except self.traci.exceptions.TraCIException:
                    continue

    def stop_reason(self, vehicle_id: str, data: dict) -> str:
        if data["speed"] >= 0.15:
            self.stop_reasons.pop(vehicle_id, None)
            return "moving"
        cached = self.stop_reasons.get(vehicle_id)
        if cached is None or self.step_index % STOP_REASON_REFRESH_STEPS == 0:
            cached = classify_stop(self.traci, vehicle_id, data["speed"], data["road"], data["front"])
            self.stop_reasons[vehicle_id] = cached
        return cached

    def signals(self) -> list[dict]:
        traci_module = self.traci
        output = []
        now = traci_module.simulation.getTime()
        for signal_id in traci_module.trafficlight.getIDList():
            signal_state = traci_module.trafficlight.getRedYellowGreenState(signal_id)
            if signal_id not in self.controlled_links_cache:
                self.controlled_links_cache[signal_id] = traci_module.trafficlight.getControlledLinks(signal_id)
            approaches = {"north": "r", "south": "r", "east": "r", "west": "r"}
            for link_index, link_group in enumerate(self.controlled_links_cache[signal_id]):
                if link_index >= len(signal_state):
                    continue
                for link in link_group:
                    approach = approach_for_lane(link[0])
                    if approach is not None:
                        approaches[approach] = strongest_signal(approaches[approach], signal_state[link_index])
            output.append(
                {
                    "id": signal_id,
                    "phase": traci_module.trafficlight.getPhase(signal_id),
                    "state": signal_state,
                    "approaches": approaches,
                    "seconds_to_switch": round(max(0.0, traci_module.trafficlight.getNextSwitch(signal_id) - now), 1),
                }
            )
        return output


def vehicle_entry(vehicle_id: str, data: dict, stop_reason: str, continuation_count: int) -> dict:
    front_x, front_y = data["front"]
    centre_x, centre_y = body_centre(front_x, front_y, data["angle"], data["length"])
    road_id = data["road"]
    angle = data["angle"]
    nearest_cardinal = round(angle / 90.0) * 90.0
    return {
        "id": vehicle_id,
        # Body centre in Godot space. SUMO row 0 is the northern edge, matching
        # Godot's negative Z, so the axes map directly after re-centring.
        "x": round(centre_x - WORLD_HALF_EXTENT, 3),
        "z": round(centre_y - WORLD_HALF_EXTENT, 3),
        "angle": round(angle, 2),
        "speed": round(data["speed"], 2),
        "acceleration": round(data["acceleration"], 2),
        "road": road_id,
        "entry_boundary": is_perimeter_point(front_x, front_y),
        "turning": road_id.startswith(":") and abs(angle - nearest_cardinal) > 4.0,
        "stopped": data["speed"] < 0.15,
        "stop_reason": stop_reason,
        "type": data["type"],
        "cls": visual_class_for_type(data["type"]),
        "len": round(data["length"], 2),
        "wid": round(data["width"], 2),
        "sig": int(data.get("signals", 0)),
        "continuation_count": int(continuation_count),
    }


PARKING = None


def make_state(traffic: TrafficState, engine=None, build_packet: bool = True) -> dict:
    """Advance bookkeeping for one simulation step and build the packet.

    ``build_packet=False`` runs only the fleet/incident bookkeeping (used for
    the warm-up, where nothing is published)."""
    traci_module = traffic.traci
    snapshot = traffic.collect()
    controlled = engine.controlled_vehicle_ids() if engine is not None else set()
    if PARKING is not None:
        # Cars on their way to a car park (or being put back on the road) are not given new cross-city destinations.
        controlled = controlled | PARKING.intent_ids()
    traffic.closed_edges = engine.road_blocks.blocked_edges() if engine is not None else set()
    continued = traffic.maintain_fleet(snapshot, controlled | (engine.jam_vehicle_ids() if engine is not None else set()))
    if engine is not None:
        engine.step(snapshot)
        for removed_id in engine.consume_removed_ids():
            snapshot.pop(removed_id, None)
            traffic.subscribed.discard(removed_id)
            traffic.stop_reasons.pop(removed_id, None)
    if PARKING is not None:
        PARKING.evacuate_zones(set(engine.road_blocks.lockdowns) if engine is not None else set())
        PARKING.step(snapshot, engine.controlled_vehicle_ids() if engine is not None else set())
        for removed_id in PARKING.consume_removed_ids():
            snapshot.pop(removed_id, None)
            traffic.subscribed.discard(removed_id)
            traffic.stop_reasons.pop(removed_id, None)
    if not build_packet:
        traffic.step_index += 1
        return {}
    vehicles = []
    stopped_count = 0
    turning_count = 0
    for vehicle_id, data in snapshot.items():
        entry = vehicle_entry(vehicle_id, data, traffic.stop_reason(vehicle_id, data), traffic.continuation_counts.get(vehicle_id, 0))
        if engine is not None:
            engine.annotate_vehicle(vehicle_id, entry)
        stopped_count += int(entry["stopped"])
        turning_count += int(entry["turning"])
        vehicles.append(entry)
    state = {
        "source": "SUMO 1.25.0 TraCI",
        "time": round(traci_module.simulation.getTime(), 4),
        "simulated_vehicle_count": len(snapshot),
        "displayed_vehicle_count": len(vehicles),
        "civilian_fleet_target": traffic.civilian_target,
        "stopped_vehicle_count": stopped_count,
        "turning_vehicle_count": turning_count,
        "arrived_last_step": traci_module.simulation.getArrivedNumber(),
        "continued_visible_trips": continued,
        "gridlock_reroutes": traffic.gridlock_reroutes,
        "vehicles": vehicles,
        "signals": traffic.signals(),
    }
    if engine is not None:
        state.update(engine.public_state())
    if PARKING is not None:
        state["parking"] = PARKING.public_state()
        state["handoffs"] = list(state.get("handoffs", [])) + PARKING.handoffs()
    traffic.step_index += 1
    return state


RESOLVED_KEYS = ("id", "kind", "title", "category", "phase", "severity", "created", "resolved_at", "location")
_OVERSIZE_WARNED = False


def _dumps(state: dict) -> bytes:
    return json.dumps(state, separators=(",", ":")).encode("utf-8")


def encode_state(state: dict) -> bytes | None:
    """The packet for Godot, always under the UDP limit.

    A busy session (several open incidents, extra units, the resolved list, the
    car parks) used to push the packet past 64 kB; the bridge then raised and
    died, freezing every incident, unit and garbage round in Godot ("WAITING FOR
    SUMO BRIDGE"). Now only redundant detail is shed, step by step, and a packet
    that still cannot fit is skipped (the next one is sent) instead of killing
    the bridge."""
    global _OVERSIZE_WARNED
    # Resolved incidents only need their identity (the Brain and the night
    # garbage schedule read id, kind and place): never their full scene.
    state["resolved_incidents"] = [{key: record[key] for key in RESOLVED_KEYS if key in record}
                                   for record in state.get("resolved_incidents", []) if isinstance(record, dict)]
    payload = _dumps(state)
    if len(payload) <= UDP_LIMIT:
        return payload
    # Never silently drop a vehicle: shed only redundant diagnostics.
    for vehicle in state.get("vehicles", []):
        vehicle.pop("acceleration", None)
    for signal_data in state.get("signals", []):
        signal_data.pop("state", None)
    payload = _dumps(state)
    if len(payload) <= UDP_LIMIT:
        return payload
    for record in state.get("incidents", []):
        if isinstance(record, dict) and isinstance(record.get("timeline"), list):
            record["timeline"] = record["timeline"][-4:]
    state["events"] = list(state.get("events", []))[-8:]
    payload = _dumps(state)
    if len(payload) <= UDP_LIMIT:
        return payload
    state["resolved_incidents"] = []
    for record in state.get("incidents", []):
        if isinstance(record, dict):
            record["timeline"] = record.get("timeline", [])[-1:]
    payload = _dumps(state)
    if len(payload) <= UDP_LIMIT:
        return payload
    if not _OVERSIZE_WARNED:
        _OVERSIZE_WARNED = True
        print(f"WARNING: SUMO state still {len(payload)} bytes after trimming; packet skipped", file=sys.stderr, flush=True)
    return None


PHASE_COMMANDS = {
    "ns_green": 0,
    "ns_yellow": 1,
    "all_red": 2,
    "ew_green": 6,
    "ew_yellow": 7,
    "reset_cycle": 0,
}


def set_signal_phase(traci_module, signal_id: str, phase: int) -> bool:
    """Put a junction on its base program at a phase. A junction left on an override program (one phase) is
    returned to its base program first; any refusal is reported, never raised."""
    try:
        if traci_module.trafficlight.getProgram(signal_id) != "0":
            traci_module.trafficlight.setProgram(signal_id, "0")
        traci_module.trafficlight.setPhase(signal_id, phase)
        return True
    except traci_module.exceptions.TraCIException:
        return False


def apply_operator_command(command: dict, traci_module, paused: bool, engine=None) -> tuple[bool, str]:
    global SIMULATION_SPEED, WEATHER_NAME, WEATHER_TRAFFIC_FACTOR
    name = str(command.get("command", ""))
    if name == "pause":
        return True, "simulation paused"
    if name == "resume":
        return False, "simulation resumed"
    if name == "set_speed":
        SIMULATION_SPEED = max(1, min(8, int(round(float(command.get("multiplier", 1.0))))))
        return paused, f"simulation speed {SIMULATION_SPEED}x"
    if name == "set_weather":
        WEATHER_NAME = str(command.get("weather", "clear"))
        WEATHER_TRAFFIC_FACTOR = max(0.35, min(1.0, float(command.get("traffic_factor", 1.0))))
        for vehicle_id in traci_module.vehicle.getIDList():
            try:
                traci_module.vehicle.setSpeedFactor(vehicle_id, WEATHER_TRAFFIC_FACTOR)
            except traci_module.exceptions.TraCIException:
                continue
        return paused, f"weather {WEATHER_NAME}: traffic factor {WEATHER_TRAFFIC_FACTOR:.2f}"
    if name in PHASE_COMMANDS:
        # Apply a single phase to every controller together. This preserves
        # the perpendicular red/green relationship at every crossing. Junctions
        # under incident police control keep their safe program.
        # Junctions an emergency unit is crossing (emergency_priority.py holds them on a one-phase "online"
        # program) keep their priority too: a phase index sent to them used to raise a TraCIException that
        # killed the whole bridge (a fire plus the Brain's green waves froze the city).
        protected = engine.protected_signal_ids() if engine is not None else set()
        if engine is not None and getattr(engine, "priority", None) is not None:
            protected = protected | set(getattr(engine.priority, "preempted", {}) or {})
        only = str(command.get("signal", ""))
        smart = getattr(engine, "smart_signals", None) if engine is not None else None
        if smart is not None:
            # SMART-SIG: ns_green / ew_green / all_red are held (not a 6-second phase jump); reset_cycle hands the
            # junction back to the queue-adaptive control.
            now = float(getattr(engine, "now", 0.0))
            targets = [only] if only else list(traci_module.trafficlight.getIDList())
            if only and (only not in traci_module.trafficlight.getIDList() or only in protected):
                return paused, "signal %s is unknown or under incident / emergency-priority control" % only
            for signal_id in targets:
                if signal_id in protected:
                    continue
                try:
                    if traci_module.trafficlight.getProgram(signal_id) not in ("0", "closure"):
                        traci_module.trafficlight.setProgram(signal_id, "0")
                    smart.command(signal_id, name, now)
                except traci_module.exceptions.TraCIException:
                    if only:
                        return paused, "signal %s refused %s" % (only, name.replace("_", " "))
            held = {"ns_green": " (held up to 45 s)", "ew_green": " (held up to 45 s)", "all_red": " (held)"}.get(name, "")
            return paused, ("signal %s: %s%s" % (only, name.replace("_", " "), held)) if only else "all 49 signals: %s" % name.replace("_", " ")
        if only:
            if only in traci_module.trafficlight.getIDList() and only not in protected:
                if set_signal_phase(traci_module, only, PHASE_COMMANDS[name]):
                    return paused, "signal %s: %s" % (only, name.replace("_", " "))
                return paused, "signal %s refused %s" % (only, name.replace("_", " "))
            return paused, "signal %s is unknown or under incident / emergency-priority control" % only
        for signal_id in traci_module.trafficlight.getIDList():
            if signal_id not in protected:
                set_signal_phase(traci_module, signal_id, PHASE_COMMANDS[name])
        return paused, "all 49 signals: %s" % name.replace("_", " ")
    if name == "incident" and engine is not None:
        return paused, engine.handle_command(command)
    if name == "parking" and PARKING is not None:
        return paused, PARKING.set_mode(str(command.get("lot", "")), str(command.get("mode", "")))
    if name == "parking_driver" and PARKING is not None:
        return paused, PARKING.driver_event(str(command.get("vehicle", "")), str(command.get("event", "")))
    return paused, ""


def poll_operator_commands(receiver: socket.socket, traci_module, paused: bool, engine=None) -> tuple[bool, str]:
    """Apply only safe, coordinated operator controls from the Godot control room."""
    latest_action = ""
    while True:
        try:
            packet, address = receiver.recvfrom(8192)
        except BlockingIOError:
            break
        except OSError:
            break
        if address[0] != "127.0.0.1":
            continue
        try:
            command = json.loads(packet.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(command, dict):
            continue
        try:
            paused, action = apply_operator_command(command, traci_module, paused, engine)
        except Exception as error:  # noqa: BLE001 - a bad operator command must never stop the bridge
            import traceback
            traceback.print_exc()
            action = f"command {command.get('command', '?')} failed: {type(error).__name__}: {error}"[:200]
        if action:
            latest_action = action
    return paused, latest_action


def create_engine(traci_module, enabled: bool, step_length: float = 1.0 / 15.0):
    if not enabled:
        return None
    import incident_engine

    return incident_engine.IncidentEngine(traci_module, WORLD_HALF_EXTENT, step_length)


def main() -> int:
    args = parse_args()
    prepare_environment()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if not SUMO_BINARY.is_file() or not CONFIG.is_file():
        print("Bundled SUMO runtime or city configuration is missing.", file=sys.stderr)
        return 2

    import traci  # type: ignore  # Loaded from the bundled SUMO tools directory.

    step_length = 1.0 / max(args.rate, 1.0)
    traci.start(sumo_command(step_length), verbose=False)
    traffic = TrafficState(traci)
    engine = create_engine(traci, not args.no_incidents, step_length)
    global PARKING
    if not args.no_parking:
        import parking_lots

        PARKING = parking_lots.ParkingLots(traci, OUTBOUND_GATES, visual_class_for_type)
        if not PARKING.lots:
            PARKING = None
    sender = None if args.test else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    monitor_sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if not args.test and args.monitor_port > 0 else None
    control_receiver = None
    if not args.test:
        control_receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        control_receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        control_receiver.bind((args.host, args.control_port))
        control_receiver.setblocking(False)
    steps_sent = 0
    observed_stopped = 0
    observed_turning = 0
    observed_signal_states: set[str] = set()
    paused = False
    last_operator_action = ""
    try:
        warmup_steps = int(max(args.warmup, 0.0) / step_length)
        for _ in range(warmup_steps):
            if STOP_REQUESTED:
                return 0
            traci.simulationStep()
            # Bookkeeping runs during warm-up too, so the opening fleet is
            # already inside the city and every emergency unit is stationed.
            make_state(traffic, engine, build_packet=False)

        if args.ready_file is not None:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            args.ready_file.write_text("ready\n", encoding="utf-8")

        next_tick = time.monotonic()
        while not STOP_REQUESTED:
            if control_receiver is not None:
                paused, operator_action = poll_operator_commands(control_receiver, traci, paused, engine)
                if operator_action:
                    last_operator_action = operator_action
            if not paused:
                state = {}
                # Each accelerated wall-clock tick executes every intervening
                # SUMO/incident step. Logic is never skipped; only publishing
                # is coalesced to the normal smooth 15 Hz visual stream.
                steps_this_tick = SIMULATION_SPEED
                for step_in_tick in range(steps_this_tick):
                    traci.simulationStep()
                    # Only the last step of a tick is published, so only that
                    # one pays for building the 40 kB packet (the others run
                    # the full bookkeeping and incident logic, never skipped).
                    state = make_state(traffic, engine, build_packet=step_in_tick == steps_this_tick - 1)
            else:
                state = make_state(traffic, engine)
            state["operator"] = {
                "paused": paused,
                "last_action": last_operator_action,
                "speed": SIMULATION_SPEED,
                "weather": WEATHER_NAME,
                "weather_traffic_factor": WEATHER_TRAFFIC_FACTOR,
            }
            payload = encode_state(state)

            if payload is None:
                next_tick = time.monotonic() + step_length
                time.sleep(step_length)
            elif args.test:
                steps_sent += 1
                observed_stopped = max(observed_stopped, state["stopped_vehicle_count"])
                observed_turning = max(observed_turning, state["turning_vehicle_count"])
                for signal_data in state["signals"]:
                    observed_signal_states.update(signal_data["approaches"].values())
                if steps_sent >= args.test_steps:
                    if observed_stopped == 0 or observed_turning == 0 or "G" not in observed_signal_states or "r" not in observed_signal_states:
                        raise RuntimeError(
                            "Traffic behavior validation failed: "
                            f"stopped={observed_stopped}, turning={observed_turning}, signals={observed_signal_states}"
                        )
                    if state["simulated_vehicle_count"] != state["displayed_vehicle_count"]:
                        raise RuntimeError("an invisible vehicle exists in the simulation")
                    print(
                        "SUMO_BRIDGE_TEST_OK "
                        f"time={state['time']:.1f} simulated={state['simulated_vehicle_count']} "
                        f"visible={state['displayed_vehicle_count']} signals={len(state['signals'])} "
                        f"turning={observed_turning} stopped={observed_stopped} packet_bytes={len(payload)}"
                    )
                    return 0
            else:
                assert sender is not None
                sender.sendto(payload, (args.host, args.port))
                if monitor_sender is not None:
                    # The desktop control room observes the exact JSON packet
                    # that drives CityLife; it never manufactures telemetry.
                    monitor_sender.sendto(payload, (args.host, args.monitor_port))
                next_tick += step_length
                now = time.monotonic()
                if next_tick < now - 0.25:
                    # A tick that overran badly: carry on from now rather than
                    # sprinting to repay the debt, which showed as jerky bursts.
                    next_tick = now
                time.sleep(max(0.0, next_tick - now))
    finally:
        if sender is not None:
            sender.close()
        if monitor_sender is not None:
            monitor_sender.close()
        if control_receiver is not None:
            control_receiver.close()
        traci.close(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

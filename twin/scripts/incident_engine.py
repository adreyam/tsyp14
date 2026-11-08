"""Emergency fleet and incident engine running inside the SUMO bridge.

SUMO stays authoritative for every road vehicle; this module only issues legal
TraCI commands (insert, route, stop, type swap, speed/lane-change modes) and
publishes the resulting state. See docs/INCIDENT_SYSTEM_ARCHITECTURE.md.

Robustness rules applied throughout:
* every TraCI call that targets a vehicle tolerates that vehicle vanishing;
* every waiting state has a timeout with a defined legal fallback;
* one-shot facts (events, hand-offs, acknowledgements) are republished for a
  few seconds with sequence numbers, because Godot decodes only the newest
  datagram and may skip one.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import random

# A steer-back request must expire within about a step: a longer one
# stays active after the vehicle is home and SUMO, which counts the
# oncoming lane as its neighbour, flips it back out every other step.
RETURN_REQUEST_SECONDS = 0.04

PROJECT = Path(__file__).resolve().parents[1]
DEPOTS_FILE = PROJECT / "sumo" / "emergency_depots.json"
CLASSES_FILE = PROJECT / "sumo" / "vehicle_classes.json"
WORLD_HALF_EXTENT = 380.0
BLOCK = 95.0
JUNCTION_HALF = 8.5
LANE_LENGTH = 78.0
ONE_SHOT_SECONDS = 4.0

# Light bits shared with Godot's VehicleLightRig.
LIGHT_BAR = 1
LIGHT_HAZARD = 2
LIGHT_SIREN = 4
LIGHT_AMBER = 8
LIGHT_WORK = 16

RESPONSE_TYPES = {"police": "police_response", "ambulance": "ambulance_response", "fire": "fire_response", "tow": "tow_response"}
BASE_TYPES = {"police": "police", "ambulance": "ambulance", "fire": "fire", "tow": "tow", "utility": "utility", "garbage": "garbage"}

# Fallback timeouts for Godot-owned phases apply only while Godot is silent
# (headless runs, a closed window); a live presenter always reports progress.
MOBILIZE_TIMEOUT = 35.0
# With Godot running, its acknowledgements normally move a unit on; these
# caps make sure a lost or impossible one (a crew member who cannot reach
# the door) can delay a unit but never freeze it and everything around it.
MOBILIZE_LIVE_LIMIT = 60.0
# Incident safety valve (see IncidentEngine._supervise_incidents).
SCENE_LIMIT_SECONDS = 360.0
SERVICE_ROUND_LIMIT_SECONDS = 900.0
ARMING_LIMIT_SECONDS = 180.0
STOP_PARKING = 1
# A unit parked off the lane is drawn pulled over onto the kerb by this much,
# easing over at this lateral speed.
KERB_PULL_OVER_METRES = 2.3
KERB_PULL_OVER_SPEED = 1.4
CREW_BOARD_LIVE_LIMIT = 90.0
PARK_LIVE_LIMIT = 60.0
PARK_TIMEOUT = 30.0
GODOT_SILENCE_SECONDS = 8.0
CREW_BOARD_HEADLESS_SECONDS = 4.0
# A garaged unit whose bay is this close to a scene on the station's own street
# works it from the bay: the crew goes on foot (no pull-out into the block).
STAGE_FROM_BAY_METRES = 45.0
STUCK_REROUTE_SECONDS = 45.0
# A unit crossing on red needs the junction free of green-light arrivals for
# its whole crossing (about 4 s at 5.5 m/s) plus a margin.
RED_CROSSING_HORIZON_SECONDS = 8.0
OPPOSITE_STUCK_SECONDS = 6.0
TOW_LENGTH = 7.4
# Beyond the flagger's return margin (3 m past the scene end, which is the
# wreck's front + 0.5 m), so a tow can always be waved into it and return.
TOW_SLOT_CLEARANCE = 4.0
MUTUAL_AID_DEPOT = {"id": "MUTUAL-AID", "label": "Mutual aid", "edge": ""}
MUTUAL_AID_MAX_ACTIVE = 4


def edge_endpoints(edge_id: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    try:
        head, tail = edge_id[2:].split("_")
        from_row, from_column = head[1:].split("c")
        to_row, to_column = tail[1:].split("c")
        return (int(from_row), int(from_column)), (int(to_row), int(to_column))
    except (ValueError, IndexError):
        return None


def junction_sumo_xy(row: int, column: int) -> tuple[float, float]:
    return (column - 1) * BLOCK, (row - 1) * BLOCK


def reverse_edge_id(edge_id: str) -> str:
    try:
        head, tail = edge_id[2:].split("_")
    except ValueError:
        return ""
    return f"E_{tail}_{head}"


def gate_xy(edge_id: str, end: bool) -> tuple[float, float]:
    endpoints = edge_endpoints(edge_id)
    if endpoints is None:
        return (math.inf, math.inf)
    return junction_sumo_xy(*endpoints[1 if end else 0])


def edge_point(edge_id: str, lane_position: float, lateral: float = 2.25) -> tuple[float, float] | None:
    """SUMO coordinates of a lane position (lateral to the right of travel)."""
    ends = edge_endpoints(edge_id)
    if ends is None:
        return None
    (fx, fy), (tx, ty) = junction_sumo_xy(*ends[0]), junction_sumo_xy(*ends[1])
    length = math.hypot(tx - fx, ty - fy)
    dx, dy = (tx - fx) / length, (ty - fy) / length
    # Right of travel is (dy, -dx) in this grid (verified against lane shapes).
    distance = JUNCTION_HALF + lane_position
    return fx + dx * distance + dy * lateral, fy + dy * distance - dx * lateral


class OneShotLog:
    """Facts that must survive Godot skipping a datagram."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.sequence = 0

    def push(self, now: float, payload: dict) -> dict:
        self.sequence += 1
        entry = dict(payload)
        entry["seq"] = self.sequence
        entry["t"] = round(now, 2)
        self.entries.append(entry)
        return entry

    def recent(self, now: float) -> list[dict]:
        self.entries = [entry for entry in self.entries if now - float(entry["t"]) <= ONE_SHOT_SECONDS]
        return list(self.entries)


class Unit:
    def __init__(self, unit_id: str, kind: str, depot: dict, bay: dict | None, start: str) -> None:
        self.id = unit_id
        self.kind = kind
        self.depot = depot
        self.bay = bay
        self.start = start
        self.status = "garaged" if start == "garaged" else "patrol"
        self.vehicle_id = ""
        self.sortie = 0
        self.incident_id = ""
        self.destination: dict | None = None
        self.status_since = 0.0
        self.at_curb_reported = False
        self.parked_reported = False
        # Released from a scene while its crew is still out: the vehicle waits
        # (stopped) until Godot reports everyone aboard.
        self.crew_aboard_reported = False
        self.depart_response = False
        # Next stop of a multi-stop round (garbage collection): taken once the
        # crew is back aboard instead of returning to base.
        self.next_destination: dict | None = None
        # Lane-change mode to restore after a commanded return from the
        # oncoming lane (-1: none pending).
        self.saved_lane_change_mode = -1
        self.response = False
        self.on_scene_lights = LIGHT_BAR
        self.patrol_cycle = 0
        # A tow truck's loaded wreck (Godot keeps the model on the flatbed).
        self.cargo = ""
        self.guard_hold = False
        self.guard_junction = ""

    @property
    def is_available(self) -> bool:
        return self.status in ("garaged", "patrol") and not self.incident_id and self.start != "mutual_aid"

    def public(self) -> dict:
        return {
            "unit": self.id, "kind": self.kind, "depot": self.depot["id"], "status": self.status,
            "vehicle": self.vehicle_id, "incident": self.incident_id,
            "bay": self.bay["unit"] if self.bay else "",
            "response": self.response,
            "cargo": self.cargo,
        }


class EmergencyFleet:
    """Owns every emergency unit; exactly one status per unit at all times.

    garaged → mobilizing → (Godot drives bay→curb, acks) → inserting → en_route
    → on_scene → returning → entering_depot → (Godot parks, acks) → garaged.
    Patrol units: patrol → en_route → on_scene → returning(patrol) → patrol.
    """

    def __init__(self, traci_module, events: OneShotLog, handoffs: OneShotLog) -> None:
        self.traci = traci_module
        self.events = events
        self.handoffs = handoffs
        self.units: dict[str, Unit] = {}
        self.vehicle_to_unit: dict[str, str] = {}
        self.rng = random.Random(4411)
        self.depots: list[dict] = []
        if DEPOTS_FILE.is_file():
            self.depots = json.loads(DEPOTS_FILE.read_text(encoding="utf-8")).get("depots", [])
        for depot in self.depots:
            for bay in depot.get("bays", []):
                self.units[bay["unit"]] = Unit(bay["unit"], bay["class"], depot, bay, "garaged")
            for patrol in depot.get("patrol_units", []):
                self.units[patrol["unit"]] = Unit(patrol["unit"], patrol["class"], depot, None, "patrol")
        self.mutual_aid_calls = 0
        # Set by the engine: whether a vehicle is driving in an oncoming lane
        # (route commands must never touch it).
        self.in_opposite_lane = lambda vehicle_id: False
        self.started = False
        # Vehicles removed from SUMO this step (hand-offs); the bridge drops them
        # from the snapshot before building the packet.
        self.removed_vehicle_ids: set[str] = set()
        self.last_godot_contact = -1000.0

    def godot_silent(self, now: float) -> bool:
        return now - self.last_godot_contact > GODOT_SILENCE_SECONDS

    # -- lifecycle helpers --------------------------------------------------

    def _set_status(self, unit: Unit, status: str, now: float) -> None:
        if unit.status != status and unit.guard_junction and status not in ("en_route", "returning"):
            # A red-light crossing never carries over into another state.
            try:
                self.traci.vehicle.setSpeedMode(unit.vehicle_id, 31)
            except self.traci.exceptions.TraCIException:
                pass
            unit.guard_junction = ""
            unit.guard_hold = False
        if unit.status != status:
            unit.status = status
            unit.status_since = now
            self.events.push(now, {"kind": "unit_status", "unit": unit.id, "status": status, "incident": unit.incident_id})

    def _new_vehicle_id(self, unit: Unit) -> str:
        unit.sortie += 1
        return f"{unit.id}#{unit.sortie}"

    def _insert(self, unit: Unit, edge: str, position: float, type_id: str) -> bool:
        vehicle_id = self._new_vehicle_id(unit)
        route_id = f"route_{vehicle_id}"
        try:
            self.traci.route.add(route_id, [edge])
            self.traci.vehicle.add(
                vehicle_id, route_id, typeID=type_id, depart="now", departLane="0",
                departPos=f"{position:.2f}", departSpeed="0",
            )
        except self.traci.exceptions.TraCIException:
            return False
        unit.vehicle_id = vehicle_id
        self.vehicle_to_unit[vehicle_id] = unit.id
        return True

    def start(self, now: float) -> None:
        """Put patrol units on the road at the start of the shift (warm-up)."""
        if self.started:
            return
        self.started = True
        for unit in self.units.values():
            if unit.status == "patrol":
                depot = unit.depot
                position = 20.0 + 25.0 * (int(unit.id.split("-")[-1]) % 2)
                if self._insert(unit, depot["edge"], position, BASE_TYPES[unit.kind]):
                    unit.status_since = now

    def exists(self, unit: Unit, snapshot: dict) -> bool:
        return bool(unit.vehicle_id) and unit.vehicle_id in snapshot

    def remove_vehicle(self, vehicle_id: str) -> None:
        """Remove a vehicle from SUMO cleanly (unsubscribe first: a removed
        vehicle's live subscription makes SUMO report errors every step)."""
        try:
            self.traci.vehicle.unsubscribe(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass
        try:
            self.traci.vehicle.remove(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass
        self.removed_vehicle_ids.add(vehicle_id)

    # -- public API used by incidents and operator commands -----------------

    def select_unit(self, kind: str, target_xy: tuple[float, float], exclude: set[str] | None = None) -> Unit | None:
        """Closest available unit of a kind (patrols by position, garaged by depot)."""
        best: Unit | None = None
        best_distance = math.inf
        for unit in self.units.values():
            if unit.kind != kind or not unit.is_available or (exclude and unit.id in exclude):
                continue
            if unit.status == "patrol" and unit.vehicle_id:
                try:
                    x, y = self.traci.vehicle.getPosition(unit.vehicle_id)
                except self.traci.exceptions.TraCIException:
                    continue
                # A patrol car is already rolling: prefer it over a cold start.
                distance = math.hypot(x - target_xy[0], y - target_xy[1]) * 0.8
            else:
                bay = unit.bay or {}
                distance = math.hypot(float(bay.get("x", 0.0)) + WORLD_HALF_EXTENT - target_xy[0], float(bay.get("z", 0.0)) + WORLD_HALF_EXTENT - target_xy[1]) + 60.0
            if distance < best_distance:
                best_distance = distance
                best = unit
        return best

    def call_mutual_aid(self, kind: str, target_xy: tuple[float, float], now: float) -> Unit | None:
        """A contracted unit from outside the city: it enters through the
        inbound perimeter gate nearest the scene (where every vehicle from
        outside enters) and leaves through an outbound gate when released."""
        if kind not in BASE_TYPES:
            return None
        active = sum(1 for unit in self.units.values() if unit.start == "mutual_aid" and unit.status != "stood_down")
        if active >= MUTUAL_AID_MAX_ACTIVE:
            return None
        from sumo_bridge import INBOUND_GATES

        gates = sorted(INBOUND_GATES, key=lambda edge: math.dist(gate_xy(edge, end=True), target_xy))
        unit = Unit(f"AID-{kind.upper()}-{self.mutual_aid_calls + 1}", kind, MUTUAL_AID_DEPOT, None, "mutual_aid")
        unit.status = "mobilizing"
        unit.status_since = now
        for gate in gates[:6]:
            # Enter only through a gate whose first stretch is clear: two
            # units inserted back to back would meet at the gate junction.
            try:
                occupied = any(self.traci.vehicle.getLanePosition(other) < 20.0 for other in self.traci.lane.getLastStepVehicleIDs(f"{gate}_0"))
            except self.traci.exceptions.TraCIException:
                occupied = True
            if occupied or any(other.vehicle_id and other.status == "inserting" for other in self.units.values() if other.start == "mutual_aid"):
                continue
            if self._insert(unit, gate, 1.0, BASE_TYPES[kind]):
                self.mutual_aid_calls += 1
                self.units[unit.id] = unit
                self._set_status(unit, "inserting", now)
                self.events.push(now, {"kind": "mutual_aid", "unit": unit.id, "gate": gate})
                return unit
        return None

    def can_stage_from_bay(self, unit: Unit, destination: dict, target_xy: tuple[float, float]) -> bool:
        if unit.status != "garaged" or not unit.bay or destination.get("edge") != unit.depot.get("edge"):
            return False
        bay_xy = (float(unit.bay["x"]) + WORLD_HALF_EXTENT, float(unit.bay["z"]) + WORLD_HALF_EXTENT)
        return math.dist(bay_xy, target_xy) <= STAGE_FROM_BAY_METRES

    def stage_from_bay(self, unit: Unit, destination: dict, incident_id: str, now: float, on_scene_lights: int = LIGHT_BAR) -> None:
        unit.incident_id = incident_id
        unit.destination = dict(destination)
        unit.response = False
        unit.on_scene_lights = on_scene_lights
        self._set_status(unit, "on_scene", now)

    def dispatch(self, unit: Unit, destination: dict, incident_id: str, now: float, response: bool = True, on_scene_lights: int = LIGHT_BAR) -> None:
        """destination: {"edge", "pos"} — a stop in the lane at lane position pos."""
        unit.incident_id = incident_id
        unit.destination = dict(destination)
        unit.response = response and unit.kind in RESPONSE_TYPES
        unit.on_scene_lights = on_scene_lights
        unit.at_curb_reported = False
        if unit.status == "patrol" and unit.vehicle_id:
            self._route_rolling_unit(unit, now)
        elif unit.status != "inserting":
            # (A mutual-aid unit is already entering at its gate.)
            self._set_status(unit, "mobilizing", now)

    def release(self, unit: Unit, now: float, keep_response: bool = False) -> None:
        """Incident finished with this unit: patrol resumes, others return to base.

        keep_response: an ambulance transporting a patient drives back to its
        hospital under lights and sirens."""
        unit.incident_id = ""
        unit.destination = None
        unit.next_destination = None
        if unit.status == "on_scene":
            # Never drive away from a crew: hold until they are aboard (a unit
            # staged in its bay is garaged once they are back).
            unit.crew_aboard_reported = False
            unit.depart_response = keep_response
            self._set_status(unit, "departing", now)
            return
        self._depart(unit, now, keep_response)

    def move_on(self, unit: Unit, destination: dict, now: float) -> None:
        """Send an on-scene unit to its next stop once its crew is aboard (a
        collection round): the same boarding handshake as a release."""
        unit.next_destination = dict(destination)
        if unit.status == "on_scene":
            unit.crew_aboard_reported = False
            unit.depart_response = False
            self._set_status(unit, "departing", now)
        else:
            unit.destination = unit.next_destination
            unit.next_destination = None
            self._route_rolling_unit(unit, now)

    def _depart(self, unit: Unit, now: float, keep_response: bool) -> None:
        if not unit.vehicle_id:
            # Never left the station (cancelled during mobilisation).
            self._set_status(unit, "garaged" if unit.start == "garaged" else "patrol", now)
            return
        try:
            self._clear_stops(unit.vehicle_id)
            self.traci.vehicle.setSpeed(unit.vehicle_id, -1)
            self.traci.vehicle.setType(unit.vehicle_id, RESPONSE_TYPES[unit.kind] if keep_response and unit.kind in RESPONSE_TYPES else BASE_TYPES[unit.kind])
        except self.traci.exceptions.TraCIException:
            pass
        unit.response = keep_response and unit.kind in RESPONSE_TYPES
        if unit.start == "patrol":
            self._set_status(unit, "patrol", now)
            self._continue_patrol(unit, force=True)
            return
        self._set_status(unit, "returning", now)
        if unit.start == "mutual_aid":
            from sumo_bridge import OUTBOUND_GATES

            try:
                here = self.traci.vehicle.getPosition(unit.vehicle_id)
                route = self.traci.vehicle.getRoute(unit.vehicle_id)
            except self.traci.exceptions.TraCIException:
                return
            gates = [str(gate[0]) for gate in OUTBOUND_GATES]  # (edge, x, y)
            if self.in_opposite_lane(unit.vehicle_id):
                return
            for gate in sorted(gates, key=lambda edge: math.dist(gate_xy(edge, end=False), here)):
                if gate in route[:1]:
                    continue
                try:
                    self.traci.vehicle.changeTarget(unit.vehicle_id, gate)
                    return
                except self.traci.exceptions.TraCIException:
                    continue
            return
        try:
            self._route_to(unit.vehicle_id, unit.depot["edge"], float(unit.bay["arrive_pos"]))
            self.traci.vehicle.setStop(unit.vehicle_id, unit.depot["edge"], pos=float(unit.bay["arrive_pos"]), laneIndex=0, duration=2.0)
        except self.traci.exceptions.TraCIException:
            pass

    def arrive_in_place(self, unit: Unit, now: float) -> None:
        """Declare a unit on scene where it stands (its planned stop is blocked
        by queued traffic). It is held with a stop so traffic can pass it.
        Never in an oncoming lane: there it keeps driving to its own lane."""
        try:
            road = self.traci.vehicle.getRoadID(unit.vehicle_id)
            if not road.startswith(":") and road not in self.traci.vehicle.getRoute(unit.vehicle_id):
                return
        except self.traci.exceptions.TraCIException:
            return
        try:
            self._clear_stops(unit.vehicle_id)
            road = self.traci.vehicle.getRoadID(unit.vehicle_id)
            if not road.startswith(":"):
                position = self.traci.vehicle.getLanePosition(unit.vehicle_id)
                self.traci.vehicle.setStop(unit.vehicle_id, road, pos=position, laneIndex=self.traci.vehicle.getLaneIndex(unit.vehicle_id), duration=100000.0,
                                           flags=STOP_PARKING if (unit.destination or {}).get("park") else 0)
            else:
                self.traci.vehicle.setSpeed(unit.vehicle_id, 0.0)
        except self.traci.exceptions.TraCIException:
            pass
        self._set_status(unit, "on_scene", now)

    def report_at_curb(self, unit_id: str) -> None:
        unit = self.units.get(unit_id)
        if unit is not None:
            unit.at_curb_reported = True

    def report_crew_aboard(self, unit_id: str) -> None:
        unit = self.units.get(unit_id)
        if unit is not None:
            unit.crew_aboard_reported = True

    def report_parked(self, unit_id: str) -> None:
        unit = self.units.get(unit_id)
        if unit is not None:
            unit.parked_reported = True

    # -- routing ---------------------------------------------------------------

    def _clear_stops(self, vehicle_id: str) -> None:
        """Remove every pending stop. A stop the vehicle is halted at is left
        with resume(); SUMO keeps listing it at index 0 until the next step, so
        only the stops after it are replaced."""
        try:
            halted = self.traci.vehicle.isStopped(vehicle_id)
            if halted:
                self.traci.vehicle.resume(vehicle_id)
            first_removable = 1 if halted else 0
            for _attempt in range(6):
                if len(self.traci.vehicle.getStops(vehicle_id)) <= first_removable:
                    return
                self.traci.vehicle.replaceStop(vehicle_id, first_removable, "")
        except self.traci.exceptions.TraCIException:
            return

    def _route_to(self, vehicle_id: str, edge: str, position: float) -> None:
        """Legal route to (edge, position), looping the block when the target is behind."""
        if self.in_opposite_lane(vehicle_id):
            # Re-routed by the stuck watchdog once it is back in its lane.
            raise self.traci.exceptions.TraCIException("vehicle is driving in an oncoming lane")
        road = self.traci.vehicle.getRoadID(vehicle_id)
        unit = self.units.get(self.vehicle_to_unit.get(vehicle_id, ""))
        router = getattr(self, "astar", None)
        if unit is not None and unit.kind == "garbage" and router is not None and not road.startswith(":"):
            # The garbage truck drives A* routes (astar_router.py): fastest, and with the fewest turns among the
            # equally fast ones, instead of SUMO's block-by-block zigzag on the grid.
            behind = road == edge and self.traci.vehicle.getLanePosition(vehicle_id) + 12.0 > position
            options = []
            if behind:
                lane = self.traci.vehicle.getLaneID(vehicle_id)
                for link in self.traci.lane.getLinks(lane):
                    successor = link[0].rsplit("_", 1)[0]
                    found = router.route(successor, edge)
                    if found is not None:
                        options.append((found.cost_s, [road] + found.edges, found))
            else:
                found = router.route(road, edge)
                if found is not None:
                    options.append((found.cost_s, found.edges, found))
            if options:
                _cost, edges, found = min(options, key=lambda option: option[0])
                self.traci.vehicle.setRoute(vehicle_id, edges)
                unit.astar_route = {"edges": len(edges), "turns": found.turns, "cost_s": found.cost_s, "expanded": found.expanded}
                return
        if road == edge and self.traci.vehicle.getLanePosition(vehicle_id) + 12.0 > position:
            best_route: list[str] | None = None
            lane = self.traci.vehicle.getLaneID(vehicle_id)
            for link in self.traci.lane.getLinks(lane):
                successor = link[0].rsplit("_", 1)[0]
                route = list(self.traci.simulation.findRoute(successor, edge).edges)
                if route and (best_route is None or len(route) < len(best_route)):
                    best_route = route
            if best_route:
                self.traci.vehicle.setRoute(vehicle_id, [road] + best_route)
                return
        self.traci.vehicle.changeTarget(vehicle_id, edge)

    def _route_rolling_unit(self, unit: Unit, now: float) -> None:
        destination = unit.destination or {}
        try:
            if unit.response:
                self.traci.vehicle.setType(unit.vehicle_id, RESPONSE_TYPES[unit.kind])
            self._clear_stops(unit.vehicle_id)
            self._route_to(unit.vehicle_id, destination["edge"], float(destination["pos"]))
            self.traci.vehicle.setStop(unit.vehicle_id, destination["edge"], pos=float(destination["pos"]), laneIndex=0, duration=100000.0, flags=STOP_PARKING if destination.get("park") else 0)
            self._set_status(unit, "en_route", now)
        except self.traci.exceptions.TraCIException as error:
            # A stop that can no longer be reached legally (e.g. already passed in
            # a junction) is retried next step through the stuck watchdog.
            self.events.push(now, {"kind": "unit_route_retry", "unit": unit.id, "reason": str(error)[:120]})
            self._set_status(unit, "en_route", now)

    def _continue_patrol(self, unit: Unit, force: bool = False) -> None:
        if self.in_opposite_lane(unit.vehicle_id):
            return
        try:
            route = self.traci.vehicle.getRoute(unit.vehicle_id)
            index = self.traci.vehicle.getRouteIndex(unit.vehicle_id)
        except self.traci.exceptions.TraCIException:
            return
        if not force and len(route) - index - 1 > 2:
            return
        unit.patrol_cycle += 1
        for _attempt in range(6):
            row = self.rng.randint(2, 8)
            column = self.rng.randint(2, 7)
            target = f"E_r{row}c{column}_r{row}c{column + 1}" if self.rng.random() < 0.5 else f"E_r{column}c{row}_r{column + 1}c{row}"
            if target in route[index:]:
                continue
            try:
                self.traci.vehicle.changeTarget(unit.vehicle_id, target)
                return
            except self.traci.exceptions.TraCIException:
                continue

    # -- per-step update -------------------------------------------------------

    def step(self, now: float, snapshot: dict) -> None:
        for unit in self.units.values():
            if unit.status == "mobilizing":
                # Godot reports when the crew has boarded and the unit waits at
                # the curb; the timeout keeps headless runs and lost acks moving.
                waited = now - unit.status_since
                if unit.at_curb_reported or (self.godot_silent(now) and waited >= MOBILIZE_TIMEOUT) or waited >= MOBILIZE_LIVE_LIMIT:
                    type_id = RESPONSE_TYPES[unit.kind] if unit.response else BASE_TYPES[unit.kind]
                    if self._insert(unit, unit.depot["edge"], float(unit.bay["exit_pos"]), type_id):
                        self._set_status(unit, "inserting", now)
            elif unit.status == "inserting":
                if unit.vehicle_id in snapshot:
                    self._route_rolling_unit(unit, now)
            elif unit.status == "en_route":
                self._step_en_route(unit, now, snapshot)
            elif unit.status == "departing":
                waited = now - unit.status_since
                if unit.crew_aboard_reported or (self.godot_silent(now) and waited >= CREW_BOARD_HEADLESS_SECONDS) or waited >= CREW_BOARD_LIVE_LIMIT:
                    unit.crew_aboard_reported = False
                    if unit.next_destination is not None and unit.incident_id:
                        unit.destination = unit.next_destination
                        unit.next_destination = None
                        self._route_rolling_unit(unit, now)
                    else:
                        unit.next_destination = None
                        self._depart(unit, now, unit.depart_response)
            elif unit.status == "returning":
                self._step_returning(unit, now, snapshot)
            elif unit.status == "entering_depot":
                waited = now - unit.status_since
                if unit.parked_reported or (self.godot_silent(now) and waited >= PARK_TIMEOUT) or waited >= PARK_LIVE_LIMIT:
                    unit.parked_reported = False
                    unit.cargo = ""
                    self._set_status(unit, "garaged", now)
            elif unit.status == "patrol" and unit.vehicle_id in snapshot:
                self._continue_patrol(unit)

    def _red_light_guard(self, unit: Unit, data: dict, snapshot: dict) -> None:
        """A unit clearing a red light approaches slowly and only enters once
        the junction is empty and no green-light traffic is about to cross —
        green-light drivers in SUMO do not yield to it on their own."""
        try:
            next_signals = self.traci.vehicle.getNextTLS(unit.vehicle_id)
        except self.traci.exceptions.TraCIException:
            return
        if not next_signals:
            unit.guard_hold = False
            return
        junction, _index, distance, state = next_signals[0]
        if unit.guard_junction and junction != unit.guard_junction and not data["road"].startswith(f":{unit.guard_junction}_"):
            # Fully out of the junction it crossed on red: SUMO drives again.
            self.traci.vehicle.setSpeed(unit.vehicle_id, -1)
            self.traci.vehicle.setSpeedMode(unit.vehicle_id, 31)
            unit.guard_junction = ""
        if str(state).lower() not in ("r", "y") or distance > 30.0:
            if unit.guard_hold:
                self.traci.vehicle.setSpeed(unit.vehicle_id, -1)
                unit.guard_hold = False
            return
        prefix = f":{junction}_"
        busy = False
        for other_id, other in snapshot.items():
            if other_id == unit.vehicle_id:
                continue
            if other["road"].startswith(prefix) and other["speed"] > 0.3:
                busy = True
                break
            # Green-light traffic that can reach the junction while the unit
            # crosses it (time-based, so fast cars count from further out and
            # a car standing at its stop line on green counts at once).
            if other["road"].startswith("E_") and other["road"] != data["road"]:
                try:
                    signals = self.traci.vehicle.getNextTLS(other_id)
                except self.traci.exceptions.TraCIException:
                    continue
                if signals and signals[0][0] == junction and str(signals[0][3]) in ("G", "g"):
                    arrival = signals[0][2] / max(other["speed"], 2.0)
                    if arrival < RED_CROSSING_HORIZON_SECONDS:
                        busy = True
                        break
        if busy:
            # Stop before the line (comfortable deceleration) and wait.
            target = 0.0 if distance < 8.0 else 3.0
            self.traci.vehicle.slowDown(unit.vehicle_id, target, max(0.5, (data["speed"] - target) / 4.0))
            unit.guard_hold = True
        elif unit.guard_hold or (distance < 12.0 and not unit.guard_junction):
            # Clear: enter at the bounded red-light speed and cross without
            # stopping inside the box (no yielding at a left-turn waiting
            # point in the path of green traffic; safe speed to the leader
            # stays on). Control returns to SUMO once the signal is behind.
            self.traci.vehicle.setSpeed(unit.vehicle_id, 5.5)
            self.traci.vehicle.setSpeedMode(unit.vehicle_id, 23)
            unit.guard_hold = False
            unit.guard_junction = junction

    def _step_en_route(self, unit: Unit, now: float, snapshot: dict) -> None:
        data = snapshot.get(unit.vehicle_id)
        if data is None:
            return
        if unit.response:
            self._red_light_guard(unit, data, snapshot)
        try:
            stopped_at_target = self.traci.vehicle.isStopped(unit.vehicle_id)
        except self.traci.exceptions.TraCIException:
            return
        if unit.saved_lane_change_mode >= 0 and not data["road"].startswith(":"):
            try:
                if data["road"] in self.traci.vehicle.getRoute(unit.vehicle_id):
                    self.traci.vehicle.setLaneChangeMode(unit.vehicle_id, unit.saved_lane_change_mode)
                    unit.saved_lane_change_mode = -1
            except self.traci.exceptions.TraCIException:
                unit.saved_lane_change_mode = -1
        if stopped_at_target:
            self._set_status(unit, "on_scene", now)
            return
        if data["waiting"] >= OPPOSITE_STUCK_SECONDS and self._return_from_opposite(unit, data, snapshot):
            return
        if data["waiting"] >= STUCK_REROUTE_SECONDS and int(now) % 10 == 0 and not self.in_opposite_lane(unit.vehicle_id):
            try:
                self.traci.vehicle.rerouteTraveltime(unit.vehicle_id)
                destination = unit.destination or {}
                self.traci.vehicle.setStop(unit.vehicle_id, destination["edge"], pos=float(destination["pos"]), laneIndex=0, duration=100000.0, flags=STOP_PARKING if destination.get("park") else 0)
            except (self.traci.exceptions.TraCIException, KeyError):
                self._route_rolling_unit(unit, now)

    def _return_from_opposite(self, unit: Unit, data: dict, snapshot: dict) -> bool:
        """A unit halted in the oncoming lane (it passed a queue and its own
        lane refuses the merge on minimum gap alone) changes back to the same
        spot of its own lane when that spot is clear beyond SUMO's minimum gap,
        so nothing is left parked head-on in live traffic. It is a SUMO lane
        change, never a teleport (moveTo of an opposite-driving vehicle
        corrupts SUMO's lane occupancy)."""
        try:
            route = self.traci.vehicle.getRoute(unit.vehicle_id)
        except self.traci.exceptions.TraCIException:
            return False
        road = data["road"]
        if road.startswith(":") or road in route:
            return False
        own = next((edge for edge in route if reverse_edge_id(edge) == road), "")
        if not own:
            return False
        front = ScenePlanner.lane_position_of(own, data["front"][0], data["front"][1])
        back = front - data["length"]
        if back < 0.5 or front > LANE_LENGTH - 0.5:
            return False
        for other_id, other in snapshot.items():
            if other_id == unit.vehicle_id or other["road"] != own:
                continue
            if other["lane_position"] > back - 3.0 and other["lane_position"] - other["length"] < front + 3.0:
                return False
        try:
            if unit.saved_lane_change_mode < 0:
                unit.saved_lane_change_mode = self.traci.vehicle.getLaneChangeMode(unit.vehicle_id)
            self.traci.vehicle.setLaneChangeMode(unit.vehicle_id, 0)
            self.traci.vehicle.changeLaneRelative(unit.vehicle_id, -1, RETURN_REQUEST_SECONDS)
        except self.traci.exceptions.TraCIException:
            return False
        self.events.push(self.traci.simulation.getTime(), {"kind": "unit_rejoined_lane", "unit": unit.id, "edge": own})
        return True

    def _step_returning(self, unit: Unit, now: float, snapshot: dict) -> None:
        if unit.response and unit.vehicle_id in snapshot:
            # An ambulance transporting a patient crosses reds the same way.
            self._red_light_guard(unit, snapshot[unit.vehicle_id], snapshot)
        if unit.start == "mutual_aid":
            self._step_leaving_city(unit, now, snapshot)
            return
        if unit.vehicle_id not in snapshot:
            return
        try:
            road = self.traci.vehicle.getRoadID(unit.vehicle_id)
            stopped = self.traci.vehicle.isStopped(unit.vehicle_id)
            position = self.traci.vehicle.getLanePosition(unit.vehicle_id)
        except self.traci.exceptions.TraCIException:
            return
        arrive_pos = float(unit.bay["arrive_pos"])
        if road == unit.depot["edge"] and stopped and abs(position - arrive_pos) < 3.0:
            # Hand the vehicle to Godot at the turn-in point; it drives the
            # validated curve into its bay. The SUMO body leaves the lane.
            vehicle_id = unit.vehicle_id
            self.handoffs.push(now, {"id": vehicle_id, "unit": unit.id, "reason": "depot_entry", "depot": unit.depot["id"], "cargo": unit.cargo})
            self.remove_vehicle(vehicle_id)
            self.vehicle_to_unit.pop(vehicle_id, None)
            unit.vehicle_id = ""
            unit.parked_reported = False
            self._set_status(unit, "entering_depot", now)
        elif not stopped and snapshot[unit.vehicle_id]["waiting"] >= STUCK_REROUTE_SECONDS and int(now) % 10 == 0 and not self.in_opposite_lane(unit.vehicle_id):
            try:
                self._route_to(unit.vehicle_id, unit.depot["edge"], arrive_pos)
                self.traci.vehicle.setStop(unit.vehicle_id, unit.depot["edge"], pos=arrive_pos, laneIndex=0, duration=2.0)
            except self.traci.exceptions.TraCIException:
                pass

    def _step_leaving_city(self, unit: Unit, now: float, snapshot: dict) -> None:
        """A mutual-aid unit stands down at the end of its outbound gate, the
        city's edge, exactly where civilian traffic leaves."""
        data = snapshot.get(unit.vehicle_id)
        if data is not None:
            try:
                road = self.traci.vehicle.getRoadID(unit.vehicle_id)
                at_exit = road in self.traci.vehicle.getRoute(unit.vehicle_id)[-1:] and data["lane_position"] >= self.traci.lane.getLength(f"{road}_0") - 8.0
            except self.traci.exceptions.TraCIException:
                at_exit = False
            if not at_exit:
                if data["waiting"] >= STUCK_REROUTE_SECONDS and int(now) % 10 == 0 and not self.in_opposite_lane(unit.vehicle_id):
                    try:
                        self.traci.vehicle.rerouteTraveltime(unit.vehicle_id)
                    except self.traci.exceptions.TraCIException:
                        pass
                return
        vehicle_id = unit.vehicle_id
        self.handoffs.push(now, {"id": vehicle_id, "unit": unit.id, "reason": "left_city", "cargo": unit.cargo})
        self.remove_vehicle(vehicle_id)
        self.vehicle_to_unit.pop(vehicle_id, None)
        unit.vehicle_id = ""
        unit.cargo = ""
        self._set_status(unit, "stood_down", now)

    # -- publication -------------------------------------------------------------

    def lights_for(self, unit: Unit) -> int:
        if unit.status in ("en_route", "inserting", "returning") and unit.response:
            # A responding tow keeps its amber beacons; only the emergency
            # services show the light bar and sound the siren.
            return (LIGHT_AMBER | LIGHT_WORK) if unit.kind == "tow" else (LIGHT_BAR | LIGHT_SIREN)
        if unit.status in ("on_scene", "departing"):
            return unit.on_scene_lights
        if unit.kind in ("tow", "utility", "garbage") and unit.status in ("en_route", "inserting"):
            return LIGHT_AMBER
        return 0

    def annotate(self, vehicle_id: str, entry: dict) -> None:
        unit_id = self.vehicle_to_unit.get(vehicle_id)
        if unit_id is None:
            return
        unit = self.units[unit_id]
        entry["unit"] = unit.id
        entry["lights"] = self.lights_for(unit)
        entry["role"] = unit.status
        if unit.cargo:
            entry["cargo"] = unit.cargo

    def public(self) -> list[dict]:
        output = []
        for unit in self.units.values():
            if unit.status == "stood_down":
                continue
            record = unit.public()
            record["lights"] = self.lights_for(unit)
            output.append(record)
        return output


class ScenePlanner:
    """Where each responding unit stops: in-lane positions that keep the
    wreck reachable, leave the oncoming lane open and stay inside the edge."""

    STOP_SPACING = 10.0

    def __init__(self, traci_module) -> None:
        self.traci = traci_module

    @staticmethod
    def lane_position_of(edge_id: str, x: float, y: float) -> float:
        ends = edge_endpoints(edge_id)
        if ends is None:
            return 0.0
        (fx, fy), (tx, ty) = junction_sumo_xy(*ends[0]), junction_sumo_xy(*ends[1])
        length = math.hypot(tx - fx, ty - fy)
        return ((x - fx) * (tx - fx) + (y - fy) * (ty - fy)) / length - JUNCTION_HALF

    @staticmethod
    def stop(edge_id: str, position: float) -> dict:
        position = max(6.0, min(LANE_LENGTH - 3.0, position))
        point = edge_point(edge_id, position) or (WORLD_HALF_EXTENT, WORLD_HALF_EXTENT)
        return {"edge": edge_id, "pos": round(position, 1), "xy": [round(point[0], 2), round(point[1], 2)]}

    def stops_for(self, incident, boxes: list) -> list[dict]:
        """Midblock scene: index 0 is the anchor directly behind the obstacle
        (every unit except the tow targets it, so the block forms in arrival
        order and nobody overtakes the scene); later entries are downstream
        recovery slots ahead of the frontmost wreck."""
        if getattr(incident, "junction", ""):
            return self.junction_stops(incident, incident.junction, boxes)
        edge_id = incident.location.get("edge", "")
        if not edge_id:
            return []
        positions = []
        for box in boxes:
            for corner in box.corners():
                positions.append(self.lane_position_of(edge_id, corner[0], corner[1]))
        front = max(positions) if positions else 40.0
        back = min(positions) if positions else 30.0
        stops = [self.stop(edge_id, back - 2.2)]
        stops[0]["anchor"] = True
        # Tow slots: rear of the flatbed beyond SUMO's minimum gap (2.5 m) of
        # the wreck's front, so the tow can always merge into it.
        # One slot: it must lie inside the flagger's zone (a second slot would
        # reach past the line where oncoming traffic is held). Tows use it in
        # turn; the next is waved through once the first has left.
        cursor = front + TOW_SLOT_CLEARANCE + TOW_LENGTH
        if cursor <= LANE_LENGTH - 3.0:
            stops.append(self.stop(edge_id, cursor))
        return stops

    def junction_stops(self, incident, junction: str, boxes: list) -> list[dict]:
        try:
            row, column = (int(value) for value in junction.split("_r")[1].split("_c"))
        except (ValueError, IndexError):
            return []
        stops = []
        # Units wait on the junction's approaches just short of the stop line:
        # always reachable (the queue there moves with the incident signal
        # control), never through the wreck.
        for step_row, step_column in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            from_row, from_column = row + step_row, column + step_column
            if not (1 <= from_row <= 9 and 1 <= from_column <= 9):
                continue
            edge_id = f"E_r{from_row}c{from_column}_r{row}c{column}"
            point = edge_point(edge_id, 64.0)
            if point is None or any(box.contains(point, 3.0) for box in boxes):
                continue
            # Units wait pulled over at the kerb (a parking stop: traffic
            # keeps flowing past them into the controlled junction).
            stop = self.stop(edge_id, 64.0)
            stop["park"] = True
            stops.append(stop)
        return stops

    def building_stops(self, incident) -> list[dict]:
        # All apparatus target the anchor abreast of the building; the block
        # forms behind it in arrival order.
        anchor = self.stop(incident.edge, incident.pos + 6.0)
        anchor["anchor"] = True
        return [anchor]

    def point_stops(self, incident, edge_id: str, position: float) -> list[dict]:
        anchor = self.stop(edge_id, position - 6.0)
        anchor["anchor"] = True
        return [anchor]


class IncidentEngine:
    """Bridge-facing facade: fleet + incidents + road control + commands."""

    def __init__(self, traci_module, world_half_extent: float = WORLD_HALF_EXTENT, step_length: float = 1.0 / 15.0) -> None:
        import incident_types
        from road_control import RoadControl

        self.traci = traci_module
        self.world_half_extent = world_half_extent
        self.step_length = step_length
        self.events = OneShotLog()
        self.handoffs = OneShotLog()
        self.acks = OneShotLog()
        self.fleet = EmergencyFleet(traci_module, self.events, self.handoffs)
        self.road = RoadControl(traci_module)
        from emergency_priority import EmergencyPriority
        from smart_signals import SmartSignals
        self.priority = EmergencyPriority(traci_module, self.fleet, self.road)
        # SMART-SIG: queue-adaptive green on every junction, and real holds for the Brain's signal commands.
        self.smart_signals = SmartSignals(traci_module)
        from road_blocks import RoadBlocks
        self.road_blocks = RoadBlocks(self)
        self.priority.closed_links = self.smart_signals._closed_links
        from astar_router import AStarRouter
        self.astar = AStarRouter(traci_module)
        self.fleet.astar = self.astar
        self.road.on_block = self.road_blocks.block
        self.road.on_unblock = self.road_blocks.unblock
        self.fleet.in_opposite_lane = lambda vehicle_id: vehicle_id in self.road.opposite_lane_vehicles() or vehicle_id in self.priority.opposite_vehicles()
        self.scene_planner = ScenePlanner(traci_module)
        self.types = incident_types.INCIDENT_TYPES
        self.incidents: dict = {}
        self.resolved: list[dict] = []
        self.pose_offsets: dict[str, list[float]] = {}
        # Offsets that belong to a moveTo issued this step (published next step).
        self.pending_pose_offsets: dict[str, list[float]] = {}
        # Unit vehicle -> how far it is drawn pulled over to the kerb.
        self.kerb_offsets: dict[str, float] = {}
        self.last_snapshot: dict = {}
        self.now = 0.0
        self.counter = 0
        self.handled_requests: dict[str, str] = {}
        # Vehicles held in an oncoming lane until they can steer back safely.
        self.rejoining: set[str] = set()
        self.released_watch: dict[str, float] = {}
        # When each wreck / repaired car was handed back: the two cars of a cleared crash still touch as they part.
        self.released_at: dict[str, float] = {}
        self.secondary_collisions = 0
        self.recent_pairs: dict[tuple, float] = {}

    def _unblock_responders(self, snapshot: dict) -> None:
        """A responding unit stuck behind another unit already parked at a scene (an ambulance stopped at the stop line
        on the approach, say) can neither pass it (no room before the junction) nor wait it out: it used to sit there
        until the incident ended. Close to its own scene it now parks where it stands and its crew goes on foot."""
        blocked = getattr(self, "_blocked_since", {})
        self._blocked_since = blocked
        protected = self.protected_signal_ids()
        self._keep_out_of_scene_junctions(snapshot, protected)
        for unit in self.fleet.units.values():
            data = snapshot.get(unit.vehicle_id) if unit.vehicle_id else None
            if unit.status == "en_route" and unit.incident_id and data is not None and protected and not data["road"].startswith(":"):
                # About to enter its own scene's junction (under incident control) while a car is stranded in the box
                # (its way out blocked by the wreck): it stops at the junction and the crew walks in, instead of
                # driving into the box (responders cross red lights) and hitting the stranded car.
                incident = self.incidents.get(unit.incident_id)
                junction = incident.location.get("junction") if incident is not None else None
                if junction in protected:
                    try:
                        upcoming = self.traci.vehicle.getNextTLS(unit.vehicle_id)
                    except self.traci.exceptions.TraCIException:
                        upcoming = ()
                    if upcoming and upcoming[0][0] == junction and upcoming[0][2] < 25.0:
                        prefix = f":{junction}_"
                        stranded = any(other_id not in incident.participants and other_id not in self.fleet.vehicle_to_unit
                                       and other["road"].startswith(prefix) and other["speed"] < 0.1
                                       for other_id, other in snapshot.items())
                        if stranded:
                            self.fleet.arrive_in_place(unit, self.now)
                            incident.note(f"{unit.id} stops at the junction (a car is stranded in it); crew continues on foot")
                            continue
            if unit.status != "en_route" or not unit.incident_id or data is None or data["speed"] > 0.1:
                blocked.pop(unit.id, None)
                continue
            try:
                leader = self.traci.vehicle.getLeader(unit.vehicle_id, 30.0)
            except self.traci.exceptions.TraCIException:
                continue
            ahead_unit = self.fleet.units.get(self.fleet.vehicle_to_unit.get(leader[0], "")) if leader else None
            if ahead_unit is None or ahead_unit.status != "on_scene":
                blocked.pop(unit.id, None)
                continue
            incident = self.incidents.get(unit.incident_id)
            location = incident.location if incident is not None else {}
            near = ahead_unit.incident_id == unit.incident_id or (
                "sx" in location and math.hypot(data["front"][0] - location["sx"], data["front"][1] - location["sy"]) <= 160.0)
            if not near:
                continue
            since = blocked.setdefault(unit.id, self.now)
            if self.now - since >= 6.0:
                blocked.pop(unit.id, None)
                self.fleet.arrive_in_place(unit, self.now)
                if incident is not None:
                    incident.note(f"{unit.id} blocked behind {ahead_unit.id} on the approach; parked there, crew continues on foot")

    def _keep_out_of_scene_junctions(self, snapshot: dict, protected: set[str]) -> None:
        """A unit driving past another incident's junction (an ambulance taking a patient to hospital) is routed
        round it; if it still has to cross it, it waits at the stop line while a car is stranded in the box
        (responders may cross red lights, but not through a car)."""
        rerouted = getattr(self, "_scene_rerouted", set())
        self._scene_rerouted = rerouted
        holding = getattr(self, "_scene_holding", {})
        self._scene_holding = holding
        for vehicle_id, junction in list(holding.items()):
            prefix = f":{junction}_"
            still = junction in protected and vehicle_id in snapshot and any(
                other["road"].startswith(prefix) and other["speed"] < 0.1 for other_id, other in snapshot.items() if other_id != vehicle_id)
            if not still:
                holding.pop(vehicle_id, None)
                try:
                    self.traci.vehicle.setSpeed(vehicle_id, -1)
                except self.traci.exceptions.TraCIException:
                    pass
        if not protected:
            rerouted.clear()
            return
        for unit in self.fleet.units.values():
            data = snapshot.get(unit.vehicle_id) if unit.vehicle_id else None
            if data is None or unit.status not in ("en_route", "returning") or data["road"].startswith(":") or unit.vehicle_id in holding:
                continue
            incident = self.incidents.get(unit.incident_id) if unit.incident_id else None
            own = incident.location.get("junction") if incident is not None else None
            try:
                upcoming = self.traci.vehicle.getNextTLS(unit.vehicle_id)
            except self.traci.exceptions.TraCIException:
                continue
            if not upcoming or upcoming[0][0] not in protected or upcoming[0][0] == own:
                continue
            junction, distance = upcoming[0][0], upcoming[0][2]
            if (unit.id, junction) not in rerouted and distance > 25.0:
                rerouted.add((unit.id, junction))
                try:
                    self.traci.vehicle.rerouteTraveltime(unit.vehicle_id)
                except self.traci.exceptions.TraCIException:
                    pass
                continue
            prefix = f":{junction}_"
            if distance < 20.0 and any(other["road"].startswith(prefix) and other["speed"] < 0.1 for other in snapshot.values()):
                try:
                    self.traci.vehicle.slowDown(unit.vehicle_id, 0.0, max(0.5, data["speed"] / 4.0))
                    self.traci.vehicle.setSpeed(unit.vehicle_id, 0.0)
                    holding[unit.vehicle_id] = junction
                except self.traci.exceptions.TraCIException:
                    pass

    def watch_released(self, vehicle_id: str, seconds: float = 120.0) -> None:
        """A car handed back to normal driving (a cleared wreck, a repaired breakdown) is watched for a while: if an
        incident hold stop survived the release (SUMO had not yet reached it, so it could not be removed), the car
        would park on it for 100000 s and stay in the lane for good, looking like a wreck nobody towed."""
        self.released_watch[vehicle_id] = self.now + seconds
        self.released_at.setdefault(vehicle_id, self.now)

    def _free_released(self) -> None:
        if not self.released_watch:
            return
        known = set(self.traci.vehicle.getIDList())
        held_by_scene = {vehicle_id for incident in self.incidents.values()
                         for vehicle_id, record in incident.participants.items() if record.get("status") not in ("released", "towed")}
        for vehicle_id, until in list(self.released_watch.items()):
            if self.now > until or vehicle_id not in known:
                self.released_watch.pop(vehicle_id, None)
                self.released_at.pop(vehicle_id, None)
                continue
            if vehicle_id in self.fleet.vehicle_to_unit or vehicle_id in held_by_scene:
                continue
            try:
                stops = self.traci.vehicle.getStops(vehicle_id)
                if not any(stop.duration > 1000.0 for stop in stops):
                    continue
                if self.traci.vehicle.isStopped(vehicle_id):
                    self.traci.vehicle.resume(vehicle_id)
                for index, stop in enumerate(stops):
                    if stop.duration > 1000.0:
                        try:
                            self.traci.vehicle.replaceStop(vehicle_id, index, "")
                        except self.traci.exceptions.TraCIException:
                            # same place, no wait: SUMO updates the stop in place
                            self.traci.vehicle.setStop(vehicle_id, stop.lane.rsplit("_", 1)[0], pos=stop.endPos,
                                                       laneIndex=int(stop.lane.rsplit("_", 1)[1]), duration=0.0)
                        break
                self.traci.vehicle.setSpeed(vehicle_id, -1)
                self.traci.vehicle.setSpeedMode(vehicle_id, 31)
            except (self.traci.exceptions.TraCIException, ValueError, IndexError):
                continue

    def rejoin_own_lane(self, vehicle_id: str) -> None:
        self.rejoining.add(vehicle_id)

    def _step_rejoining(self, snapshot: dict) -> None:
        for vehicle_id in list(self.rejoining):
            data = snapshot.get(vehicle_id)
            if data is None:
                self.rejoining.discard(vehicle_id)
                continue
            try:
                route = self.traci.vehicle.getRoute(vehicle_id)
                if data["road"].startswith(":") or data["road"] in route:
                    self.traci.vehicle.setSpeed(vehicle_id, -1)
                    self.traci.vehicle.setLaneChangeMode(vehicle_id, 1621)
                    self.rejoining.discard(vehicle_id)
                    continue
                own = reverse_edge_id(data["road"])
                front = ScenePlanner.lane_position_of(own, data["front"][0], data["front"][1])
                rear = front - data["length"]
                alongside = [other for other_id, other in snapshot.items() if other_id != vehicle_id and other["road"] == own
                             and other["lane_position"] > rear - 3.0 and other["lane_position"] - other["length"] < front + 3.0]
                if alongside:
                    # Like a driver aborting an overtake: fall back behind the
                    # car beside it, never stop dead in the oncoming lane.
                    self.traci.vehicle.setSpeed(vehicle_id, max(0.0, min(other["speed"] for other in alongside) - 2.0))
                else:
                    self.traci.vehicle.setLaneChangeMode(vehicle_id, 0)
                    self.traci.vehicle.changeLaneRelative(vehicle_id, -1, RETURN_REQUEST_SECONDS)
                    self.traci.vehicle.setSpeed(vehicle_id, -1)
            except self.traci.exceptions.TraCIException:
                self.rejoining.discard(vehicle_id)

    def _convert_unscripted_collisions(self) -> None:
        """Any contact that no incident choreographed becomes a secondary
        collision incident instead of two models overlapping and driving on."""
        involved = self.busy_vehicles() | set(self.fleet.vehicle_to_unit)
        for collision in self.traci.simulation.getCollisions():
            pair = (collision.collider, collision.victim)
            if any(vehicle_id in involved for vehicle_id in pair):
                continue
            if any(self.now - self.released_at.get(vehicle_id, -1000.0) < 30.0 for vehicle_id in pair):
                # The cars of a crash that was just cleared still touch while they drive apart: that contact is the
                # same crash ending, not a new one. Turning it into a "secondary collision" froze them as wrecks
                # again, over and over, so they stayed in the lane and never got towed.
                continue
            key = tuple(sorted(pair))
            if self.now - self.recent_pairs.get(key, -1000.0) < 20.0:
                continue
            self.recent_pairs[key] = self.now
            self.counter += 1
            incident_id = f"INC-{self.counter:04d}"
            self.incidents[incident_id] = self.types["secondary_collision"](self, incident_id, {"collider": pair[0], "victim": pair[1]})
            involved |= set(pair)
            self.secondary_collisions += 1

    def seed_for(self, incident_id: str) -> int:
        return sum(ord(character) * (index + 1) for index, character in enumerate(incident_id)) + 9091

    # -- bridge hooks -------------------------------------------------------------

    def busy_vehicles(self) -> set[str]:
        busy: set[str] = set()
        for incident in self.incidents.values():
            busy |= set(incident.participants)
        return busy

    def controlled_vehicle_ids(self) -> set[str]:
        controlled = set(self.fleet.vehicle_to_unit) | self.rejoining | self.road.opposite_lane_vehicles()
        for incident in self.incidents.values():
            controlled |= incident.controlled_ids()
        return controlled

    def jam_vehicle_ids(self) -> set[str]:
        """Cars queued on purpose behind a rush-hour breakdown: not rerouted out of the queue while it lasts."""
        jam: set[str] = set()
        for incident in self.incidents.values():
            if hasattr(incident, "jam_vehicle_ids"):
                jam |= incident.jam_vehicle_ids()
        return jam

    def protected_signal_ids(self) -> set[str]:
        return self.road.protected_signals()

    def step(self, snapshot: dict) -> None:
        self.now = float(self.traci.simulation.getTime())
        self.last_snapshot = snapshot
        if self.pending_pose_offsets:
            self.pose_offsets.update(self.pending_pose_offsets)
            self.pending_pose_offsets = {}
        self.fleet.start(self.now)
        self.fleet.step(self.now, snapshot)
        # Units on their way to a scene: signal priority and safe overtakes.
        self.priority.step(self.now, snapshot, self.protected_signal_ids())
        self.smart_signals.step(self.now, self.protected_signal_ids() | set(self.priority.preempted))
        self.road_blocks.step(snapshot)
        self._unblock_responders(snapshot)
        self._convert_unscripted_collisions()
        self._step_rejoining(snapshot)
        for incident_id in list(self.incidents):
            incident = self.incidents[incident_id]
            try:
                incident.step(snapshot)
            except self.traci.exceptions.TraCIException as error:
                # A participant vanished mid-command: the incident's own
                # watchdogs re-select or fall back on the next step.
                self.events.push(self.now, {"kind": "incident_warning", "incident": incident_id, "text": str(error)[:160]})
            except Exception as error:  # noqa: BLE001 - one broken incident must never stop the whole bridge
                import traceback
                traceback.print_exc()
                self.events.push(self.now, {"kind": "incident_warning", "incident": incident_id, "text": f"{type(error).__name__}: {error}"[:160]})
                incident.request_resolve()
                failures = getattr(incident, "_step_failures", 0) + 1
                incident._step_failures = failures
                if failures >= 3:
                    try:
                        incident.release_units()
                        self.road.release(incident.id)
                    except Exception:  # noqa: BLE001
                        pass
                    incident.phase = "resolved"
            if incident.finished():
                for vehicle_id, record in list(incident.participants.items()):
                    self.pose_offsets.pop(vehicle_id, None)
                    if record.get("status") != "towed":
                        self.watch_released(vehicle_id)
                summary = incident.public()
                summary["resolved_at"] = round(self.now, 1)
                self.resolved.append(summary)
                self.resolved = self.resolved[-12:]
                del self.incidents[incident_id]
        self._supervise_incidents()
        self._free_released()
        self._pull_over_parked_units(snapshot)
        # Scripted drivers and wrecks are never "steered back" by the guard: a
        # head-on wreck stays where it came to rest until it is towed.
        self.road.guard_exempt = {vehicle_id for incident in self.incidents.values() if incident.phase != "arming"
                                  for vehicle_id, record in incident.participants.items() if record.get("status") not in ("released", "towed")}
        self.road.step_closures(snapshot, self.controlled_vehicle_ids(), self.now)
        # Wrecks stay put; every other vehicle (units included) obeys the officer.
        scene_vehicles: set[str] = set()
        for incident in self.incidents.values():
            scene_vehicles |= set(incident.participants)
        self.road.step_junctions(self.now, scene_vehicles)

    def _pull_over_parked_units(self, snapshot: dict) -> None:
        """Units on a parking stop are off the lane in SUMO; in view they ease
        over to the kerb (and back out when they leave) instead of standing
        in the lane traffic is driving through."""
        for unit in self.fleet.units.values():
            vehicle_id = unit.vehicle_id
            data = snapshot.get(vehicle_id)
            if data is None:
                continue
            parked = unit.status in ("on_scene", "departing") and bool((unit.destination or {}).get("park")) and unit.status_since < self.now
            current = self.kerb_offsets.get(vehicle_id, 0.0)
            target = KERB_PULL_OVER_METRES if parked else 0.0
            if current == target == 0.0:
                continue
            step = KERB_PULL_OVER_SPEED * self.step_length
            current = min(target, current + step) if target > current else max(target, current - step)
            heading = math.radians(data["angle"])
            right = (math.cos(heading), -math.sin(heading))
            if current <= 0.0:
                self.kerb_offsets.pop(vehicle_id, None)
                self.pose_offsets.pop(vehicle_id, None)
            else:
                self.kerb_offsets[vehicle_id] = current
                self.pose_offsets[vehicle_id] = [round(right[0] * current, 3), round(right[1] * current, 3), 0.0]

    def _supervise_incidents(self) -> None:
        """Safety valve: no incident may stay open indefinitely. A scene far
        past its normal duration (its units cut off by a jam elsewhere, say)
        is cleared through the same orderly path an operator's Resolve takes,
        and an incident that never finds a place to happen is cancelled."""
        for incident in self.incidents.values():
            if incident.resolve_requested or incident.finished():
                continue
            age = self.now - incident.created
            limit = SERVICE_ROUND_LIMIT_SECONDS if incident.category == "service" else SCENE_LIMIT_SECONDS
            if incident.phase == "arming" and age >= ARMING_LIMIT_SECONDS:
                incident.note("No suitable place for this incident was found; cancelled")
                incident.request_resolve()
            elif age >= limit:
                incident.note("Scene open far longer than normal: supervisor orders it cleared")
                incident.request_resolve()

    def consume_removed_ids(self) -> set[str]:
        removed = self.fleet.removed_vehicle_ids
        self.fleet.removed_vehicle_ids = set()
        return removed

    def annotate_vehicle(self, vehicle_id: str, entry: dict) -> None:
        self.fleet.annotate(vehicle_id, entry)
        pose = self.pose_offsets.get(vehicle_id)
        if pose is not None:
            # Godot space: SUMO x → x, SUMO y → z; yaw delta in radians.
            entry["pose"] = pose
        for incident in self.incidents.values():
            record = incident.participants.get(vehicle_id)
            if record is None:
                continue
            entry["incident"] = incident.id
            entry["role"] = record.get("role", "")
            status = record.get("status", "")
            if status in ("wreck", "loading", "driver_returning", "coasting"):
                entry["lights"] = int(entry.get("lights", 0)) | LIGHT_HAZARD
            if record.get("level", 0.0) > 0.0 and "zone" in record:
                entry["dmg"] = {"zone": record["zone"], "level": record["level"]}
            break

    def public_state(self) -> dict:
        return {
            "fleet": self.fleet.public(),
            "incidents": [incident.public() for incident in self.incidents.values()],
            "resolved_incidents": self.resolved[-6:],
            "closures": self.road.closures_public(),
            "lockdowns": self.road_blocks.public(),
            "events": self.events.recent(self.now),
            "handoffs": self.handoffs.recent(self.now),
            "acks": self.acks.recent(self.now),
        }

    # -- operator commands ----------------------------------------------------------

    def handle_command(self, command: dict) -> str:
        action = str(command.get("action", ""))
        self.fleet.last_godot_contact = self.now
        if action == "heartbeat":
            return ""
        request = str(command.get("request", ""))
        if request and request in self.handled_requests:
            # Idempotent: Godot repeats a request until it sees the ack.
            self.acks.push(self.now, {"request": request, "result": self.handled_requests[request]})
            return ""
        result = self._apply(action, command)
        if request:
            self.handled_requests[request] = result
            self.acks.push(self.now, {"request": request, "result": result})
        return result if action in ("trigger", "resolve", "resolve_all", "send_unit", "recall_unit", "dispatch_extra", "lockdown", "reopen") else ""

    def _apply(self, action: str, command: dict) -> str:
        if action == "unit_at_curb":
            self.fleet.report_at_curb(str(command.get("unit", "")))
            return "ok"
        if action == "crew_aboard":
            self.fleet.report_crew_aboard(str(command.get("unit", "")))
            return "ok"
        if action == "unit_parked":
            self.fleet.report_parked(str(command.get("unit", "")))
            return "ok"
        if action == "trigger":
            kind = str(command.get("type", ""))
            incident_type = self.types.get(kind)
            if incident_type is None:
                return f"unknown incident type {kind}"
            if kind == "garbage_collection":
                # One truck round at a time: a second request (the Brain and a scenario, or the night schedule)
                # joins the round already under way instead of queueing a truck that never comes.
                running = next((incident for incident in self.incidents.values()
                                if incident.kind == "garbage_collection" and not incident.finished() and not incident.resolve_requested), None)
                if running is not None:
                    return running.id
            self.counter += 1
            incident_id = f"INC-{self.counter:04d}"
            self.incidents[incident_id] = incident_type(self, incident_id, dict(command.get("params", {})))
            return incident_id
        if action == "hold_junction":
            incident = self.incidents.get(str(command.get("incident", "")))
            junction = getattr(incident, "junction", "") if incident is not None else ""
            if not junction:
                return "no junction"
            self.road.hold_junction(junction, self.now + min(25.0, float(command.get("seconds", 12.0))))
            return f"{junction} held all-red"
        if action in ("resolve", "driver_ready", "wreck_loaded", "dispatch_extra", "service_stop_done"):
            incident = self.incidents.get(str(command.get("incident", "")))
            if incident is None:
                return "unknown incident"
            if action == "resolve":
                incident.request_resolve()
                return f"{incident.id} resolving"
            if action == "dispatch_extra":
                kind = str(command.get("kind", "police"))
                stops = incident.scene.get("planned_stops") or [{"edge": incident.location.get("edge", ""), "pos": 30.0}]
                incident.request_unit(kind, str(command.get("role", "support")), stops[-1])
                return f"{kind} requested for {incident.id}"
            return incident.acknowledge(action, command)
        if action == "resolve_all":
            for incident in self.incidents.values():
                incident.request_resolve()
            return f"resolving {len(self.incidents)} incidents"
        if action == "send_unit":
            unit = self.fleet.units.get(str(command.get("unit", "")))
            if unit is None:
                kind = str(command.get("kind", "police"))
                target = edge_point(str(command.get("edge", "")), float(command.get("pos", 30.0))) or (380.0, 380.0)
                unit = self.fleet.select_unit(kind, target)
            if unit is None or not unit.is_available:
                return "no available unit"
            self.fleet.dispatch(unit, {"edge": str(command.get("edge", "")), "pos": float(command.get("pos", 30.0))}, str(command.get("incident", "manual")), self.now)
            return f"{unit.id} dispatched"
        if action == "lockdown":
            return self.road_blocks.lock_zone(str(command.get("zone", "")), str(command.get("reason", "emergency")))
        if action == "reopen":
            return self.road_blocks.reopen_zone(str(command.get("zone", "")), str(command.get("reason", "")))
        if action == "recall_unit":
            unit = self.fleet.units.get(str(command.get("unit", "")))
            if unit is None:
                return "unknown unit"
            self.fleet.release(unit, self.now)
            return f"{unit.id} released"
        return "unknown action"

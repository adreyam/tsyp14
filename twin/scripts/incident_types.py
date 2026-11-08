"""Incident definitions: selection, choreography, scene control and clearance.

Each incident is a small state machine driven once per bridge step. The
participants are real SUMO vehicles (all rendered), manipulated only through
TraCI speed/lane/stop commands, so every other road user reacts to exactly
what is drawn. Every waiting phase has a timeout with a legal fallback.
"""

from __future__ import annotations

import math
import random

from incident_physics import Body, Box, contact_point, damage_zone, heading_vector, overlap, resolve_impact

WORLD = 380.0
CIVILIAN_TYPES = ("car", "taxi", "bus")
ARM_RELAX_SECONDS = 12.0
ARM_FALLBACK_SECONDS = 30.0
# A breakdown asked for a place (near: Godot x, z) picks a car within this radius for its first seconds.
NEAR_PICK_RADIUS = 110.0
NEAR_PICK_SECONDS = 40.0
# Cars of the surrounding streets routed into a rush-hour breakdown's queue (param jam).
JAM_FEED_RADIUS = 380.0
JAM_CAR_SPACING = 7.0       # metres of street per queued car (length + gap)
JAM_RELEASE_METRES = 25.0   # a queued car this close to the junction before a full jam street goes round instead
# A trigger must always produce its incident: a pinned car or junction that
# never offers a safe geometry is released and the nearest feasible one used.
ARM_UNPIN_SECONDS = 75.0
OVERTAKE_CUT_IN_BY = 46.0
# The unsafe overtaker is a speeding driver (72 km/h in a 50 zone): enough
# speed advantage to be past the overtaken car within about two seconds.
OVERTAKE_MAX_SPEED = 20.0
OVERTAKE_SPEED_ADVANTAGE = 9.0
# A crash scene starts at least this far into its edge: the response block
# (police, ambulance) forms behind it on the same edge, never on the
# approach to the junction where no flagger can manage it.
SCENE_MIN_POSITION = 26.0
# ...and ends early enough for a tow slot ahead of it and a flagger line that
# holds oncoming cars clear of the junction (78 - 48.5 - 16 >= 12 m).
SCENE_MAX_POSITION = 48.0
COAST_MAX_DECEL = 4.5
# A non-drivable vehicle with no tow on scene this long after the police
# report is driven clear under escort (the scene must always clear).
TOW_UNREACHABLE_SECONDS = 150.0
TOW_STUCK_SECONDS = 90.0
LANE_LENGTH = 78.0
# A responder held in a stationary queue close to its stop is on scene where it
# stands and its crew walks in (traffic is jammed behind the wreck).
QUEUED_ARRIVAL_SECONDS = 20.0
QUEUED_ARRIVAL_METRES = 70.0
# A request the local fleet cannot serve for this long (every unit of the kind
# busy, or trapped behind the very scene that needs it) is answered by a
# contracted mutual-aid unit that drives in through a perimeter gate.
MUTUAL_AID_AFTER_SECONDS = 90.0
CHOREOGRAPHY_TIMEOUT = 22.0
UNIT_NEAR_SCENE_METRES = 38.0
UNIT_NEAR_SCENE_WAIT = 8.0
DRIVER_RETURN_TIMEOUT = 30.0
TOW_LOAD_TIMEOUT = 12.0
# Live caps on Godot acknowledgements (see incident_engine): a driver who
# cannot get back to the car or a winch that never reports done delays the
# scene but never freezes it.
DRIVER_RETURN_LIVE_LIMIT = 90.0
TOW_LOAD_LIVE_LIMIT = 45.0
POLICE_REPORT_SECONDS = 12.0
TREATMENT_SECONDS = 15.0


def godot_xy(x: float, y: float) -> list[float]:
    return [round(x - WORLD, 2), round(y - WORLD, 2)]


def is_interior_edge(edge: str) -> bool:
    if not edge.startswith("E_r"):
        return False
    try:
        head, tail = edge[2:].split("_")
        rows = [int(head[1:].split("c")[0]), int(tail[1:].split("c")[0])]
        columns = [int(head.split("c")[1]), int(tail.split("c")[1])]
    except (ValueError, IndexError):
        return False
    return all(2 <= value <= 8 for value in rows + columns)


def reverse_edge(edge: str) -> str:
    head, tail = edge[2:].split("_")
    return f"E_{tail}_{head}"


def edge_to_junction(edge: str) -> str:
    tail = edge[2:].split("_")[1]
    row, column = tail[1:].split("c")
    return f"Junction_r{row}_c{column}"


def vehicle_box(data: dict) -> Box:
    from incident_physics import body_centre
    x, y = body_centre(data["front"], data["angle"], data["length"])
    return Box(x, y, data["angle"], data["length"], data["width"])


class Incident:
    kind = "incident"
    title = "Incident"
    category = "road"

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        self.engine = engine
        self.traci = engine.traci
        self.id = incident_id
        self.params = params
        self.rng = random.Random(engine.seed_for(incident_id))
        self.phase = "arming"
        self.created = engine.now
        self.phase_since = engine.now
        self.participants: dict[str, dict] = {}
        self.units: dict[str, dict] = {}
        self.unit_requests: list[dict] = []
        self.location: dict = {}
        self.timeline: list[dict] = []
        self.impact: dict | None = None
        self.scene: dict = {}
        self.severity = "minor"
        self.resolve_requested = False
        self.note(f"{self.title} reported")

    # -- shared helpers -------------------------------------------------------------

    @property
    def now(self) -> float:
        return self.engine.now

    def note(self, text: str) -> None:
        self.timeline.append({"t": round(self.engine.now, 1), "text": text})
        self.engine.events.push(self.engine.now, {"kind": "incident_note", "incident": self.id, "text": text})

    def set_phase(self, phase: str) -> None:
        if phase != self.phase:
            self.phase = phase
            self.phase_since = self.now
            self.engine.events.push(self.now, {"kind": "incident_phase", "incident": self.id, "phase": phase})

    def in_phase(self) -> float:
        return self.now - self.phase_since

    def controlled_ids(self) -> set[str]:
        return {vehicle_id for vehicle_id, record in self.participants.items() if record.get("status") not in ("released", "towed")}

    def hold_vehicle(self, vehicle_id: str, stop: bool = True) -> None:
        """Keep a vehicle stationary in its lane as a real obstacle."""
        try:
            self.traci.vehicle.setSpeedMode(vehicle_id, 0)
            self.traci.vehicle.setLaneChangeMode(vehicle_id, 0)
            self.traci.vehicle.setSpeed(vehicle_id, 0.0)
            road = self.traci.vehicle.getRoadID(vehicle_id)
            if stop and not road.startswith(":") and road in self.traci.vehicle.getRoute(vehicle_id) and self.traci.vehicle.getSpeed(vehicle_id) < 0.05:
                position = self.traci.vehicle.getLanePosition(vehicle_id)
                self.traci.vehicle.setStop(vehicle_id, road, pos=position, laneIndex=self.traci.vehicle.getLaneIndex(vehicle_id), duration=100000.0)
                self.traci.vehicle.setSpeed(vehicle_id, -1)
                self.traci.vehicle.setSpeedMode(vehicle_id, 31)
        except self.traci.exceptions.TraCIException:
            pass

    def release_vehicle(self, vehicle_id: str) -> None:
        """Return a participant to normal, law-abiding driving."""
        record = self.participants.get(vehicle_id, {})
        self.engine.pose_offsets.pop(vehicle_id, None)
        try:
            self.engine.fleet._clear_stops(vehicle_id)
            self.traci.vehicle.setSpeed(vehicle_id, -1)
            self.traci.vehicle.setSpeedMode(vehicle_id, 31)
            self.traci.vehicle.setLaneChangeMode(vehicle_id, 1621)
            road = self.traci.vehicle.getRoadID(vehicle_id)
            if not road.startswith(":") and road not in self.traci.vehicle.getRoute(vehicle_id):
                # Left in the oncoming lane: it is held still and steered back
                # only when SUMO confirms the move is safe (engine supervises).
                self.engine.rejoin_own_lane(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass
        self.engine.watch_released(vehicle_id)
        record["status"] = "released"

    def request_unit(self, kind: str, role: str, stop: dict, response: bool = True) -> None:
        self.unit_requests.append({"kind": kind, "role": role, "stop": stop, "response": response, "unit": "", "since": self.now})

    def _service_unit_requests(self) -> None:
        location = (self.location.get("sx", WORLD), self.location.get("sy", WORLD))
        for request in self.unit_requests:
            if request["unit"]:
                continue
            exclude = set(self.units)
            unit = self.engine.fleet.select_unit(request["kind"], location, exclude)
            if unit is None and self.now - float(request.get("since", self.now)) >= MUTUAL_AID_AFTER_SECONDS:
                unit = self.engine.fleet.call_mutual_aid(request["kind"], location, self.now)
                if unit is not None:
                    self.note(f"No local {request['kind']} unit free; mutual-aid {unit.id} called in from outside the city")
            if unit is None:
                continue
            lights = {"police": 1, "ambulance": 1, "fire": 1 | 16, "tow": 8 | 16, "utility": 8 | 16, "garbage": 8}.get(request["kind"], 1)
            if request["kind"] not in ("tow", "garbage") and self.engine.fleet.can_stage_from_bay(unit, request["stop"], location):
                self.engine.fleet.stage_from_bay(unit, request["stop"], self.id, self.now, on_scene_lights=lights)
                self.note(f"{unit.id} works the scene from its station bay next door (crew on foot)")
            else:
                self.engine.fleet.dispatch(unit, request["stop"], self.id, self.now, response=request["response"], on_scene_lights=lights)
            request["unit"] = unit.id
            self.units[unit.id] = {"role": request["role"], "kind": request["kind"], "stop": request["stop"], "since": self.now, "near_since": -1.0}
            self.note(f"{unit.id} dispatched ({request['role']})")

    def unit_status(self, unit_id: str) -> str:
        unit = self.engine.fleet.units.get(unit_id)
        return unit.status if unit else ""

    def units_on_scene(self, role: str | None = None) -> list[str]:
        return [unit_id for unit_id, record in self.units.items() if (role is None or record["role"] == role) and self.unit_status(unit_id) == "on_scene"]

    def maintain_block(self, snapshot: dict) -> None:
        """Keeps the scene a contiguous block in the closed lane:
        * a unit stopped right behind a scene vehicle is on scene;
        * an on-scene unit closes any gap that opens ahead of it;
        * the closure's extent and its no-overtake set follow the block."""
        edge = self.location.get("edge", "")
        if not edge or getattr(self, "junction", ""):
            return
        scene_ids = {vehicle_id for vehicle_id, record in self.participants.items() if record.get("status") not in ("released", "towed")}
        unit_vehicles = {}
        for unit_id in self.units:
            unit = self.engine.fleet.units.get(unit_id)
            if unit is not None and unit.vehicle_id:
                unit_vehicles[unit.vehicle_id] = unit
                if unit.status == "on_scene":
                    scene_ids.add(unit.vehicle_id)
        for vehicle_id, unit in unit_vehicles.items():
            data = snapshot.get(vehicle_id)
            if data is None or data["road"] != edge:
                continue
            try:
                leader = self.traci.vehicle.getLeader(vehicle_id, 12.0)
            except self.traci.exceptions.TraCIException:
                continue
            record = self.units[unit.id]
            if self._stop_beyond_block(record, snapshot):
                # Heading for a slot ahead of the wrecks (a tow): the flagger
                # waves it past; it never stops short behind the scene.
                continue
            if unit.status == "en_route" and leader is not None and leader[0] not in scene_ids:
                self._arrive_from_queue(unit, record, data, leader[0], snapshot)
                continue
            if leader is None or leader[0] not in scene_ids:
                continue
            if unit.status == "en_route" and data["speed"] < 0.1 and leader[1] < 4.0:
                if record.get("adjacent_since", -1.0) < 0.0:
                    record["adjacent_since"] = self.now
                elif self.now - record["adjacent_since"] >= 2.0:
                    self.engine.fleet.arrive_in_place(unit, self.now)
                    self.note(f"{unit.id} on scene")
            elif unit.status == "on_scene" and leader[1] > 3.2 and data["speed"] < 0.1:
                # Close up behind the block (the vehicle ahead left).
                try:
                    self.engine.fleet._clear_stops(vehicle_id)
                    target = snapshot[vehicle_id]["lane_position"] + leader[1] - 1.8
                    self.traci.vehicle.setStop(vehicle_id, edge, pos=target, laneIndex=0, duration=100000.0)
                except self.traci.exceptions.TraCIException:
                    pass
        # The block is the wrecks plus the on-scene units contiguous with them;
        # a unit that stopped further back (crew walked in) is not part of it,
        # so traffic queued between stays in the flagger's queue.
        block = {vehicle_id for vehicle_id in scene_ids if vehicle_id not in unit_vehicles}
        grown = True
        while grown:
            grown = False
            for vehicle_id in list(block):
                # Ahead: a unit in its slot just beyond the wrecks (a tow).
                try:
                    ahead = self.traci.vehicle.getLeader(vehicle_id, 6.0)
                except self.traci.exceptions.TraCIException:
                    ahead = None
                if ahead is not None and ahead[0] in scene_ids and ahead[0] not in block:
                    block.add(ahead[0])
                    grown = True
            for vehicle_id in scene_ids - block:
                try:
                    leader = self.traci.vehicle.getLeader(vehicle_id, 6.0)
                except self.traci.exceptions.TraCIException:
                    continue
                if leader is not None and leader[0] in block:
                    block.add(vehicle_id)
                    grown = True
        start, end = math.inf, -math.inf
        for vehicle_id in block:
            data = snapshot.get(vehicle_id)
            if data is None or data["road"] != edge:
                continue
            if self.engine.road._off_route(vehicle_id, data):
                # Facing the wrong way (head-on wreck): body beyond its front.
                start = min(start, data["lane_position"] - 0.5)
                end = max(end, data["lane_position"] + data["length"] + 0.5)
            else:
                start = min(start, data["lane_position"] - data["length"] - 0.5)
                end = max(end, data["lane_position"] + 0.5)
        if start < end:
            stay = {vehicle_id for vehicle_id, unit in unit_vehicles.items() if float(self.units[unit.id]["stop"].get("pos", 0.0)) < start + 1.0}
            self.engine.road.set_extent(self.id, edge, start, end, stay)
        self.scene["flagger"] = self.engine.road.flagger_public(self.id)

    def _tow_progressing(self) -> bool:
        """An assigned tow that is still on its way (not stuck) is waited for:
        the escorted drive-away is only for a recovery that cannot arrive."""
        for unit_id, record in self.units.items():
            if record["role"] != "recovery":
                continue
            unit = self.engine.fleet.units.get(unit_id)
            if unit is None:
                continue
            if unit.status in ("mobilizing", "inserting"):
                return True
            data = self.engine.last_snapshot.get(unit.vehicle_id)
            if unit.status == "en_route" and data is not None and data["waiting"] < TOW_STUCK_SECONDS:
                return True
        return False

    def _stop_beyond_block(self, record: dict, snapshot: dict) -> bool:
        edge = self.location.get("edge", "")
        stop = record.get("stop", {})
        if stop.get("edge") != edge:
            return False
        fronts = [snapshot[vehicle_id]["lane_position"] for vehicle_id, participant in self.participants.items()
                  if participant.get("status") not in ("released", "towed") and snapshot.get(vehicle_id, {}).get("road") == edge]
        return bool(fronts) and float(stop.get("pos", 0.0)) > max(fronts) + 1.0

    def _arrive_from_queue(self, unit, record: dict, data: dict, ahead: str, snapshot: dict) -> None:
        stop_pos = float(record["stop"].get("pos", 0.0))
        queued = data["speed"] < 0.1 and snapshot.get(ahead, {}).get("speed", 1.0) < 0.1 and 0.0 <= stop_pos - data["lane_position"] <= QUEUED_ARRIVAL_METRES
        if not queued:
            record["queued_since"] = -1.0
            return
        if record.get("queued_since", -1.0) < 0.0:
            record["queued_since"] = self.now
        elif self.now - record["queued_since"] >= QUEUED_ARRIVAL_SECONDS:
            self.engine.fleet.arrive_in_place(unit, self.now)
            self.note(f"{unit.id} held in the queue; crew continues to the scene on foot")

    def _watch_units(self, snapshot: dict) -> None:
        """A unit stuck in queue near its stop is declared on scene in place."""
        if not getattr(self, "junction", "") and self.location.get("edge"):
            # Midblock scenes use maintain_block: queueing units are never on
            # scene until they reach the block itself.
            return
        for unit_id, record in self.units.items():
            unit = self.engine.fleet.units.get(unit_id)
            if unit is None or unit.status != "en_route" or unit.vehicle_id not in snapshot:
                continue
            data = snapshot[unit.vehicle_id]
            stop = record["stop"]
            target = stop.get("xy")
            if target is None:
                continue
            fx, fy = data["front"]
            near = math.hypot(fx - target[0], fy - target[1]) <= UNIT_NEAR_SCENE_METRES
            if near and data["speed"] < 0.2:
                if record["near_since"] < 0.0:
                    record["near_since"] = self.now
                elif self.now - record["near_since"] >= UNIT_NEAR_SCENE_WAIT:
                    self.engine.fleet.arrive_in_place(unit, self.now)
                    self.note(f"{unit_id} on scene (stopped short of its planned position)")
            else:
                record["near_since"] = -1.0

    def release_units(self) -> None:
        for unit_id in list(self.units):
            unit = self.engine.fleet.units.get(unit_id)
            if unit is not None and unit.incident_id == self.id:
                self.engine.fleet.release(unit, self.now)
        for request in self.unit_requests:
            request["unit"] = request["unit"] or "cancelled"

    def step(self, snapshot: dict) -> None:
        raise NotImplementedError

    def request_resolve(self) -> None:
        self.resolve_requested = True
        self.note("Operator requested immediate resolution")

    def acknowledge(self, action: str, command: dict) -> str:
        return "ignored"

    def public(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "category": self.category,
            "phase": self.phase, "severity": self.severity, "created": round(self.created, 1),
            "location": {key: value for key, value in self.location.items() if key not in ("sx", "sy")},
            "participants": [dict(record, id=vehicle_id) for vehicle_id, record in self.participants.items()],
            "units": [{"unit": unit_id, "role": record["role"], "kind": record["kind"], "status": self.unit_status(unit_id)} for unit_id, record in self.units.items()],
            "impact": self.impact, "scene": self.scene, "timeline": self.timeline[-14:],
        }

    def finished(self) -> bool:
        return self.phase == "resolved"


# ---------------------------------------------------------------------------------
# Collisions
# ---------------------------------------------------------------------------------

class CrashIncident(Incident):
    """Shared impact → rest → scene → response → clearance flow."""

    category = "collision"

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        super().__init__(engine, incident_id, params)
        self.bodies: dict[str, Body] = {}
        self.boxes_at_impact: dict[str, Box] = {}
        self.junction = ""
        self.closed_edges: list[str] = []
        self.report_done_at = -1.0
        self.treatment_done_at = -1.0

    # Subclasses implement select() → bool and choreograph(snapshot) → bool (impact happened).

    def select(self, snapshot: dict, relaxed: bool) -> bool:
        raise NotImplementedError

    def choreograph(self, snapshot: dict) -> bool:
        raise NotImplementedError

    def first_contact(self, subjects: list[str], snapshot: dict) -> bool:
        """Registers the first overlap between a choreographed vehicle and ANY
        vehicle (scripted target or a third party) as the impact."""
        for subject in subjects:
            subject_data = snapshot.get(subject)
            if subject_data is None:
                continue
            subject_box = vehicle_box(subject_data)
            for other, other_data in snapshot.items():
                if other == subject:
                    continue
                if abs(other_data["front"][0] - subject_data["front"][0]) > 14.0 or abs(other_data["front"][1] - subject_data["front"][1]) > 14.0:
                    continue
                other_box = vehicle_box(other_data)
                hit, normal, _depth = overlap(subject_box, other_box)
                if hit:
                    if other not in self.participants:
                        self.participants[other] = {"role": "struck"}
                        try:
                            self.traci.vehicle.setSpeedMode(other, 0)
                            self.traci.vehicle.setLaneChangeMode(other, 0)
                        except self.traci.exceptions.TraCIException:
                            pass
                    for vehicle_id in list(self.participants):
                        if vehicle_id not in (subject, other):
                            # A choreographed vehicle not involved in the
                            # contact goes back to normal driving.
                            self.release_vehicle(vehicle_id)
                            self.participants.pop(vehicle_id, None)
                    self.register_impact(subject, other, snapshot, subject_box, other_box, normal)
                    return True
        return False

    def abort_choreography(self) -> None:
        # Hand participants back with a firm but normal deceleration so a car
        # that was being sped up never arrives at a queue at choreography speed.
        for vehicle_id in list(self.participants):
            try:
                speed = self.traci.vehicle.getSpeed(vehicle_id)
                self.release_vehicle(vehicle_id)
                if speed > 8.0:
                    self.traci.vehicle.slowDown(vehicle_id, 7.0, 2.0)
            except self.traci.exceptions.TraCIException:
                continue
        self.participants.clear()

    def step(self, snapshot: dict) -> None:
        if self.resolve_requested and self.phase in ("arming", "approach"):
            self.abort_choreography()
            self.note("Cancelled before impact")
            self.set_phase("resolved")
            return
        if self.phase == "arming":
            relaxed = self.in_phase() >= ARM_RELAX_SECONDS
            if self.select(snapshot, relaxed):
                self.set_phase("approach")
                self.engine.events.push(self.now, {"kind": "incident_armed", "incident": self.id, "participants": list(self.participants)})
            elif self.in_phase() >= ARM_FALLBACK_SECONDS:
                pinned = [key for key in ("vehicle", "junction") if self.params.get(key)]
                if pinned and self.now - self.created >= ARM_UNPIN_SECONDS:
                    released = ", ".join(str(self.params.pop(key)) for key in pinned)
                    self.note(f"{released} never offered a safe geometry; staging at the nearest feasible location instead")
                else:
                    self.note("No safe geometry for this collision type yet; still searching with relaxed constraints")
                self.phase_since = self.now - ARM_RELAX_SECONDS
        elif self.phase == "approach":
            if any(vehicle_id not in snapshot for vehicle_id in self.participants):
                self.abort_choreography()
                self.set_phase("arming")
            elif self.choreograph(snapshot):
                self.set_phase("sliding")
            elif getattr(self, "choreography_failed", False):
                self.choreography_failed = False
                self.abort_choreography()
                self.set_phase("arming")
            elif self.in_phase() >= CHOREOGRAPHY_TIMEOUT:
                self.abort_choreography()
                self.note("Participants diverged before contact; selecting new vehicles")
                self.set_phase("arming")
        elif self.phase == "sliding":
            self._step_slide(snapshot)
        elif self.phase in ("responding", "working", "clearing"):
            self._step_scene(snapshot)

    # -- impact -----------------------------------------------------------------------

    def register_impact(self, first: str, second: str, snapshot: dict, box_a: Box, box_b: Box, normal: tuple[float, float]) -> None:
        data_a, data_b = snapshot[first], snapshot[second]
        body_a = Body.for_vehicle(first, data_a["length"], data_a["width"], data_a["speed"], data_a["angle"])
        body_b = Body.for_vehicle(second, data_b["length"], data_b["width"], data_b["speed"], data_b["angle"])
        contact = contact_point(box_a, box_b)
        delta_v = resolve_impact(body_a, box_a, body_b, box_b, normal, contact)
        closing = max(data_a["speed"], data_b["speed"])
        self.severity = "severe" if delta_v >= 6.0 else ("moderate" if delta_v >= 3.0 else "minor")
        self.bodies = {first: body_a, second: body_b}
        self.boxes_at_impact = {first: box_a, second: box_b}
        # A wreck inside a junction can never be re-seated legally in SUMO, so
        # it is always recovered by a tow truck rather than driven off.
        in_junction = data_a["road"].startswith(":") or data_b["road"].startswith(":")
        for vehicle_id, box in ((first, box_a), (second, box_b)):
            record = self.participants.setdefault(vehicle_id, {})
            record.update({
                "zone": damage_zone(box, contact), "level": round(min(1.0, delta_v / 9.0 + 0.15), 2),
                "status": "wreck", "drivable": self.severity == "minor" and not in_junction, "injured": self.severity != "minor",
                "impact_speed": round(snapshot[vehicle_id]["speed"], 2),
            })
            try:
                # Freeze the SUMO bodies at the contact pose; the slide is the
                # bounded pose offset streamed to Godot.
                self.traci.vehicle.setSpeedMode(vehicle_id, 0)
                self.traci.vehicle.setLaneChangeMode(vehicle_id, 0)
                self.traci.vehicle.setSpeed(vehicle_id, 0.0)
            except self.traci.exceptions.TraCIException:
                pass
        self.impact = {
            "t": round(self.now, 2), "point": godot_xy(*contact), "speed": round(closing, 2), "delta_v": round(delta_v, 2),
            "vehicles": [first, second], "normal": [round(normal[0], 3), round(normal[1], 3)],
        }
        self.location.update({"x": godot_xy(*contact)[0], "z": godot_xy(*contact)[1], "sx": contact[0], "sy": contact[1]})
        self.engine.events.push(self.now, {"kind": "impact", "incident": self.id, "point": self.impact["point"], "vehicles": [first, second], "delta_v": round(delta_v, 2), "severity": self.severity})
        self.note(f"Impact at {closing * 3.6:.0f} km/h ({self.severity})")

    def _step_slide(self, snapshot: dict) -> None:
        dt = self.engine.step_length
        for vehicle_id, body in self.bodies.items():
            previous = (body.dx, body.dy, body.dyaw)
            body.step(dt)
            if self._slide_blocked(vehicle_id, snapshot):
                # A sliding wreck stops against any third vehicle instead of
                # passing through it.
                body.dx, body.dy, body.dyaw = previous
                body.vx = body.vy = body.spin = 0.0
                body.resting = True
            self.engine.pose_offsets[vehicle_id] = [round(body.dx, 3), round(body.dy, 3), round(body.dyaw, 4)]
            try:
                self.traci.vehicle.setSpeed(vehicle_id, 0.0)
            except self.traci.exceptions.TraCIException:
                pass
        if all(body.resting for body in self.bodies.values()):
            self._settle(snapshot)

    def _slide_blocked(self, vehicle_id: str, snapshot: dict) -> bool:
        moved = self._rest_box(vehicle_id)
        for other_id, other in snapshot.items():
            if other_id in self.bodies:
                continue
            if abs(other["front"][0] - moved.x) > 12.0 or abs(other["front"][1] - moved.y) > 12.0:
                continue
            hit, _normal, depth = overlap(moved, vehicle_box(other))
            if hit and depth > 0.02:
                return True
        return False

    def _rest_box(self, vehicle_id: str) -> Box:
        box = self.boxes_at_impact[vehicle_id]
        body = self.bodies[vehicle_id]
        return Box(box.x + body.dx, box.y + body.dy, box.angle + math.degrees(body.dyaw), box.length, box.width)

    def _settle(self, snapshot: dict) -> None:
        # Move SUMO bodies on normal lanes to their rest position along the lane
        # (the visual stays where it is: the offset shrinks by the same amount),
        # so queued traffic stops behind the real wreck, not its impact point.
        for vehicle_id, body in self.bodies.items():
            try:
                road = self.traci.vehicle.getRoadID(vehicle_id)
                if road.startswith(":") or road not in self.traci.vehicle.getRoute(vehicle_id):
                    # Never moveTo an opposite-driving body (SUMO corrupts the
                    # lane occupancy); it stays exactly where it came to rest.
                    continue
                hx, hy = heading_vector(snapshot[vehicle_id]["angle"])
                along = body.dx * hx + body.dy * hy
                lane = self.traci.vehicle.getLaneID(vehicle_id)
                lane_length = self.traci.lane.getLength(lane)
                position = self.traci.vehicle.getLanePosition(vehicle_id)
                if position - snapshot[vehicle_id]["length"] < 0.3:
                    # Its rear still straddles the junction lane: never moveTo
                    # a partially-occupying vehicle (SUMO asserts on it).
                    continue
                target = max(snapshot[vehicle_id]["length"] + 0.1, min(lane_length - 0.1, position + along))
                # Never move a body into the vehicle ahead or behind on its lane.
                for other_id, other in snapshot.items():
                    if other_id == vehicle_id or self.traci.vehicle.getLaneID(other_id) != lane:
                        continue
                    if other["road"] not in self.traci.vehicle.getRoute(other_id):
                        # Driving this lane the wrong way (a head-on partner):
                        # its body lies the other side of its front, never "behind".
                        continue
                    if other["lane_position"] > position and target > other["lane_position"] - other["length"] - 0.3:
                        target = min(target, other["lane_position"] - other["length"] - 0.3)
                    elif other["lane_position"] <= position and target - snapshot[vehicle_id]["length"] < other["lane_position"] + 0.3:
                        target = max(target, other["lane_position"] + snapshot[vehicle_id]["length"] + 0.3)
                target = max(snapshot[vehicle_id]["length"] + 0.1, min(lane_length - 0.1, target))
                moved = target - position
                if abs(moved) > 0.05:
                    self.traci.vehicle.moveTo(vehicle_id, lane, target)
                    body.dx -= hx * moved
                    body.dy -= hy * moved
                    self.boxes_at_impact[vehicle_id] = Box(self.boxes_at_impact[vehicle_id].x + hx * moved, self.boxes_at_impact[vehicle_id].y + hy * moved, self.boxes_at_impact[vehicle_id].angle, self.boxes_at_impact[vehicle_id].length, self.boxes_at_impact[vehicle_id].width)
                    # moveTo takes effect at SUMO's next step: the matching
                    # offset is published with that step's positions, never
                    # with this step's old ones (the wreck would jump back).
                    self.engine.pending_pose_offsets[vehicle_id] = [round(body.dx, 3), round(body.dy, 3), round(body.dyaw, 4)]
            except self.traci.exceptions.TraCIException:
                continue
        for vehicle_id in self.bodies:
            self.hold_vehicle(vehicle_id)
        self._secure_scene(snapshot)
        self._plan_response()
        self.set_phase("responding")

    def wreck_boxes(self) -> list[Box]:
        return [self._rest_box(vehicle_id) for vehicle_id in self.bodies]

    def _secure_scene(self, snapshot: dict) -> None:
        roads = {vehicle_id: snapshot.get(vehicle_id, {}).get("road", "") for vehicle_id in self.bodies}
        internal = [road for road in roads.values() if road.startswith(":")]
        if internal or self.junction:
            junction = self.junction or internal[0][1:].rsplit("_", 1)[0]
            self.junction = junction
            if junction in self.traci.trafficlight.getIDList():
                self.engine.road.take_junction(junction, "police", self.wreck_boxes(), self.now)
                self.note(f"Junction {junction} under incident signal control: only paths clear of the wreck receive green")
            incoming = [edge for edge in self.traci.junction.getIncomingEdges(junction) if not edge.startswith(":")] if hasattr(self.traci.junction, "getIncomingEdges") else []
            self.engine.road.penalise_edges(self.id, incoming, 240.0)
            self.location.setdefault("junction", junction)
        settled_edge = False
        for vehicle_id, road in roads.items():
            if road and not road.startswith(":"):
                position = snapshot[vehicle_id]["lane_position"]
                length = snapshot[vehicle_id]["length"]
                if road not in self.traci.vehicle.getRoute(vehicle_id):
                    # Stopped facing the wrong way (a head-on in the oncoming
                    # lane): its body lies beyond its front along this lane.
                    self.engine.road.close_lane(self.id, road, position - 1.0, position + length + 1.0, block_entry=True)
                else:
                    self.engine.road.close_lane(self.id, road, position - length - 1.0, position + 1.0, block_entry=True)
                self.closed_edges.append(road)
                if not settled_edge:
                    # Where the wrecks came to rest, not where the manoeuvre
                    # began (an overtake can end in the oncoming lane).
                    self.location["edge"] = road
                    self.location["pos"] = round(position, 1)
                    settled_edge = True

    def _plan_response(self) -> None:
        stops = self.engine.scene_planner.stops_for(self, self.wreck_boxes())
        self.scene["planned_stops"] = stops
        police_count = 2 if self.junction or self.severity == "severe" else 1
        plan = [("police", "traffic_control")] + ([("police", "investigation")] if police_count > 1 else [])
        if self.severity != "minor":
            plan.append(("ambulance", "medical"))
        if self.severity == "severe":
            plan.append(("fire", "fire_safety"))
        non_drivable = [vehicle_id for vehicle_id, record in self.participants.items() if not record.get("drivable")]
        for _wreck in non_drivable[:2]:
            plan.append(("tow", "recovery"))
        recovery_index = 0
        for index, (kind, role) in enumerate(plan):
            if self.junction:
                stop = stops[index % len(stops)] if stops else {"edge": self.location.get("edge", ""), "pos": 60.0}
                if stops and index >= len(stops):
                    # A second unit on the same approach parks behind the first.
                    stop = dict(self.engine.scene_planner.stop(stop["edge"], float(stop["pos"]) - 9.0 * (index // len(stops))), park=True)
            elif role == "recovery" and len(stops) > 1:
                recovery_index += 1
                stop = stops[min(recovery_index, len(stops) - 1)]
            else:
                stop = stops[0] if stops else {"edge": self.location.get("edge", ""), "pos": 30.0}
            self.request_unit(kind, role, stop, response=True)
        self.note(f"Response plan: {', '.join(role for _kind, role in plan)}")

    # -- scene work --------------------------------------------------------------------

    def _step_scene(self, snapshot: dict) -> None:
        if self.junction:
            self.scene["junction_control"] = self.engine.road.junction_public(self.junction)
        self.maintain_block(snapshot)
        self._service_unit_requests()
        self._watch_units(snapshot)
        police = self.units_on_scene("traffic_control") + self.units_on_scene("investigation")
        if self.phase == "responding" and self.resolve_requested:
            # Operator resolution never waits for units: the same legal
            # clearing path runs now (drivers clear drivable cars, tows on
            # scene load the rest).
            self.set_phase("working")
        if self.phase == "responding" and police:
            self.set_phase("working")
            self.note("Police on scene: traffic control established")
        if self.phase == "working":
            if self.report_done_at < 0.0 and police:
                self.report_done_at = self.now + POLICE_REPORT_SECONDS
            medical_needed = any(record.get("injured") for record in self.participants.values())
            if medical_needed and self.treatment_done_at < 0.0 and self.units_on_scene("medical"):
                self.treatment_done_at = self.now + TREATMENT_SECONDS
                self.note("Paramedics treating occupants")
            report_done = self.report_done_at >= 0.0 and self.now >= self.report_done_at
            treatment_done = not medical_needed or (self.treatment_done_at >= 0.0 and self.now >= self.treatment_done_at)
            if treatment_done and medical_needed and not self.scene.get("patient_transport"):
                self.scene["patient_transport"] = True
                for unit_id in self.units_on_scene("medical"):
                    self.note(f"{unit_id} transporting patient to hospital")
                    unit = self.engine.fleet.units[unit_id]
                    unit.response = True
                    self.engine.fleet.release(unit, self.now, keep_response=True)
            self._step_wrecks(report_done, bool(police))
            if self.resolve_requested:
                self._force_clear()
            if all(record.get("status") in ("released", "towed") for record in self.participants.values()):
                self.set_phase("clearing")
        if self.phase == "clearing":
            self.engine.road.release(self.id)
            if self.junction:
                self.engine.road.release_junction(self.junction)
            self.release_units()
            self.note("Scene cleared; lanes reopened")
            self.set_phase("resolved")

    def _step_wrecks(self, report_done: bool, scene_secured: bool = False) -> None:
        # Frontmost wreck first: the tow waits ahead of the scene, so the one
        # nearest to it is winched first and no wreck is dragged through
        # another on its way to the flatbed.
        snapshot = self.engine.last_snapshot
        ordered = sorted(self.participants.items(), key=lambda item: -snapshot.get(item[0], {}).get("lane_position", 0.0))
        for vehicle_id, record in ordered:
            status = record.get("status")
            if status == "wreck" and record.get("drivable") and report_done:
                record["status"] = "driver_returning"
                record["since"] = self.now
                self.note(f"{vehicle_id} cleared to drive away")
            elif status == "driver_returning":
                if record.get("driver_ready") or (self.engine.fleet.godot_silent(self.now) and self.now - record["since"] >= DRIVER_RETURN_TIMEOUT) or self.now - record["since"] >= DRIVER_RETURN_LIVE_LIMIT:
                    if self._clear_to_drive(vehicle_id):
                        self.release_vehicle(vehicle_id)
                    else:
                        record["status"] = "wreck"
                        record["drivable"] = False
                        if not any(request["kind"] == "tow" and request["unit"] != "cancelled" for request in self.unit_requests):
                            self.note(f"{vehicle_id} cannot be driven clear safely; recovery requested")
                            self.request_unit("tow", "recovery", (self.scene.get("planned_stops") or [{"edge": self.location.get("edge", ""), "pos": 40.0}])[-1], response=False)
            elif status == "wreck" and not record.get("drivable") and (report_done or scene_secured):
                # A tow works as soon as the police have secured the scene; the
                # paperwork carries on beside it instead of holding it up.
                tow = self._tow_for(vehicle_id)
                if not tow and not self.units_on_scene("recovery") and self.now - self.report_done_at >= TOW_UNREACHABLE_SECONDS and not self._tow_progressing():
                    # Last resort so a scene always clears: the same legal path
                    # an operator resolve takes.
                    record["drivable"] = True
                    self.note(f"No tow can reach the scene; {vehicle_id} is driven clear under police escort")
                    continue
                if tow:
                    record["status"] = "loading"
                    record["tow"] = tow
                    record["since"] = self.now
                    self.note(f"{tow} winching {vehicle_id} onto the flatbed")
            elif status == "loading":
                if record.get("loaded") or (self.engine.fleet.godot_silent(self.now) and self.now - record["since"] >= TOW_LOAD_TIMEOUT) or self.now - record["since"] >= TOW_LOAD_LIVE_LIMIT:
                    tow = record["tow"]
                    self.engine.handoffs.push(self.now, {"id": vehicle_id, "reason": "towed", "unit": tow, "incident": self.id})
                    self.engine.fleet.remove_vehicle(vehicle_id)
                    self.engine.pose_offsets.pop(vehicle_id, None)
                    record["status"] = "towed"
                    unit = self.engine.fleet.units.get(tow)
                    if unit is not None:
                        unit.cargo = vehicle_id
                        self.engine.fleet.release(unit, self.now)
                    self.note(f"{tow} departing with {vehicle_id}")

    def _clear_to_drive(self, vehicle_id: str) -> bool:
        snapshot = self.engine.last_snapshot
        data = snapshot.get(vehicle_id)
        if data is None or data["road"].startswith(":"):
            return False
        box = vehicle_box(data)
        hx, hy = heading_vector(data["angle"])
        for other_id, other in snapshot.items():
            if other_id == vehicle_id or abs(other["front"][0] - box.x) > 10.0 or abs(other["front"][1] - box.y) > 10.0:
                continue
            other_box = vehicle_box(other)
            hit, _normal, _depth = overlap(box.grown(0.15), other_box)
            if hit and (other_box.x - box.x) * hx + (other_box.y - box.y) * hy > 0.0:
                # Something ahead: driving forward would push into it. Contact
                # only behind (the other wreck of a pair) parts as it drives off.
                return False
        return True

    def _tow_for(self, vehicle_id: str) -> str:
        busy = {record.get("tow") for record in self.participants.values() if record.get("status") == "loading"}
        for unit_id in self.units_on_scene("recovery"):
            if unit_id not in busy and not getattr(self.engine.fleet.units[unit_id], "cargo", ""):
                return unit_id
        return ""

    def _force_clear(self) -> None:
        """Resolve now: fast-forward the same legal path — never delete."""
        self.report_done_at = min(self.report_done_at, self.now) if self.report_done_at >= 0.0 else self.now
        self.treatment_done_at = self.now
        for vehicle_id, record in self.participants.items():
            if record.get("status") == "wreck":
                # Without a tow on scene the wreck is pushed clear by its driver.
                record["drivable"] = record.get("drivable") or not self.units_on_scene("recovery")

    def acknowledge(self, action: str, command: dict) -> str:
        vehicle_id = str(command.get("vehicle", ""))
        record = self.participants.get(vehicle_id)
        if record is None:
            return "unknown vehicle"
        if action == "driver_ready":
            record["driver_ready"] = True
            return "ok"
        if action == "wreck_loaded":
            record["loaded"] = True
            return "ok"
        return "ignored"


class RearEndIncident(CrashIncident):
    kind = "rear_end"
    title = "Rear-end collision"

    def select(self, snapshot: dict, relaxed: bool) -> bool:
        target = str(self.params.get("vehicle", ""))
        candidates = []
        for follower, data in snapshot.items():
            if data["type"] not in CIVILIAN_TYPES or follower in self.engine.busy_vehicles():
                continue
            if target and follower != target:
                continue
            road = data["road"]
            if not is_interior_edge(road) or data["speed"] < (4.0 if relaxed else 6.0):
                continue
            position = data["lane_position"]
            if not (6.0 <= position <= (38.0 if relaxed else 30.0)):
                continue
            try:
                leader = self.traci.vehicle.getLeader(follower, 45.0)
            except self.traci.exceptions.TraCIException:
                continue
            if not leader or leader[0] not in snapshot or leader[0] in self.engine.busy_vehicles():
                continue
            leader_data = snapshot[leader[0]]
            if leader_data["road"] != road or leader_data["type"] not in CIVILIAN_TYPES:
                continue
            gap = leader[1]
            if not (5.0 <= gap <= (40.0 if relaxed else 28.0)):
                continue
            # Leave room downstream for responders to stop ahead of the wreck,
            # and upstream for the response block behind it on the same edge.
            if not (SCENE_MIN_POSITION <= leader_data["lane_position"] <= SCENE_MAX_POSITION):
                continue
            candidates.append((abs(gap - 15.0), follower, leader[0]))
        if not candidates:
            return False
        _score, follower, leader = sorted(candidates)[0]
        self.participants = {follower: {"role": "striking"}, leader: {"role": "struck"}}
        self.follower, self.leader = follower, leader
        self.leader_brake = 6.2 + self.rng.random() * 1.2
        for vehicle_id in (follower, leader):
            self.traci.vehicle.setSpeedMode(vehicle_id, 0)
            self.traci.vehicle.setLaneChangeMode(vehicle_id, 0)
        road = snapshot[follower]["road"]
        self.location = {"edge": road, "pos": round(snapshot[leader]["lane_position"], 1)}
        self.note(f"{leader} brakes suddenly; following driver in {follower} is distracted")
        return True

    def choreograph(self, snapshot: dict) -> bool:
        dt = self.engine.step_length
        lead, follow = snapshot[self.leader], snapshot[self.follower]
        if lead["road"] != follow["road"] and self.first_contact([self.follower], snapshot):
            return True
        if lead["road"] != follow["road"]:
            return False
        leader_speed = max(0.0, lead["speed"] - self.leader_brake * dt)
        self.traci.vehicle.setSpeed(self.leader, leader_speed)
        gap = lead["lane_position"] - lead["length"] - follow["lane_position"]
        speed = follow["speed"]
        # The distracted driver keeps speed until braking can no longer avoid
        # contact, then brakes hard: impact speed ≈ 30–55 % of travel speed.
        if gap <= speed * speed / (2.0 * 9.0) * 0.55 or self.scene.get("braking"):
            self.scene["braking"] = True
            speed = max(2.5, speed - 7.5 * dt)
        self.traci.vehicle.setSpeed(self.follower, speed)
        if gap <= speed * dt + 0.02:
            lane = self.traci.vehicle.getLaneID(self.follower)
            self.traci.vehicle.moveTo(self.follower, lane, lead["lane_position"] - lead["length"] - 0.02)
            follow = dict(follow)
            follow["lane_position"] = lead["lane_position"] - lead["length"] - 0.02
            hx, hy = heading_vector(follow["angle"])
            fx = lead["front"][0] - hx * (lead["length"] + 0.02)
            fy = lead["front"][1] - hy * (lead["length"] + 0.02)
            follow["front"] = (fx, fy)
            follow["speed"] = speed
            box_f = vehicle_box(follow)
            box_l = vehicle_box(lead)
            # Slight lateral offset of the contact makes the struck car yaw.
            box_f.x += hy * self.rng.uniform(-0.25, 0.25)
            box_f.y -= hx * self.rng.uniform(-0.25, 0.25)
            snapshot_copy = dict(snapshot)
            snapshot_copy[self.follower] = follow
            self.register_impact(self.follower, self.leader, snapshot_copy, box_f, box_l, heading_vector(follow["angle"]))
            return True
        return False


class RedLightIncident(CrashIncident):
    kind = "red_light_tbone"
    title = "Red-light collision"

    def select(self, snapshot: dict, relaxed: bool) -> bool:
        busy = self.engine.busy_vehicles()
        approaching: dict[str, list] = {}
        for vehicle_id, data in snapshot.items():
            if data["type"] not in CIVILIAN_TYPES or vehicle_id in busy or not is_interior_edge(data["road"]):
                continue
            try:
                tls = self.traci.vehicle.getNextTLS(vehicle_id)
                route = self.traci.vehicle.getRoute(vehicle_id)
                index = self.traci.vehicle.getRouteIndex(vehicle_id)
            except self.traci.exceptions.TraCIException:
                continue
            if not tls or index + 1 >= len(route):
                continue
            junction, _link, distance, state = tls[0]
            if junction != edge_to_junction(data["road"]):
                continue
            straight = self._goes_straight(data["road"], route[index + 1])
            approaching.setdefault(junction, []).append((vehicle_id, distance, str(state), straight, data))
        candidates = []
        for junction, entries in approaching.items():
            if self.params.get("junction") and junction != self.params["junction"]:
                continue
            for runner, runner_distance, runner_state, runner_straight, runner_data in entries:
                if runner_state.lower() not in ("r", "y") or not runner_straight or runner_data["speed"] < 2.0:
                    continue
                if not (15.0 <= runner_distance <= (110.0 if relaxed else 80.0)):
                    continue
                for crosser, crosser_distance, crosser_state, crosser_straight, crosser_data in entries:
                    if crosser == runner or not crosser_straight:
                        continue
                    if self._axis(runner_data["road"]) == self._axis(crosser_data["road"]):
                        continue
                    if crosser_state not in ("G", "g"):
                        # Late red-runner versus a car pulling away on a fresh
                        # green: the waiting car's green start is read from the
                        # signal program, its launch uses its real acceleration.
                        to_green = self._seconds_to_green(junction, crosser)
                        if crosser_distance > 9.0 or crosser_data["speed"] > 0.5 or not (1.5 <= to_green <= 16.0):
                            continue
                        launch = to_green + math.sqrt(2.0 * (crosser_distance + 10.0 + crosser_data["length"] * 0.5) / 2.4)
                        needed = (runner_distance + 10.0) / launch
                        if not (4.5 <= needed <= 15.0):
                            continue
                        candidates.append((abs(needed - 10.0), runner, crosser, junction))
                        continue
                    if crosser_data["speed"] < 1.5 or not (5.0 <= crosser_distance <= (95.0 if relaxed else 70.0)):
                        continue
                    # Feasible with both speeds adapted: crossing car 3–14 m/s,
                    # runner 4–16 m/s (the conflict lies ~10 m past each line).
                    runner_path = runner_distance + 10.0
                    flank = crosser_distance + 10.0 + crosser_data["length"] * 0.5
                    crosser_speed = max(3.0, min(14.0, flank * 10.0 / runner_path))
                    needed = runner_path / max(flank / crosser_speed, 0.3)
                    if not (4.5 <= needed <= 15.5):
                        continue
                    candidates.append((abs(needed - 10.0) + abs(crosser_speed - crosser_data["speed"]) * 0.5, runner, crosser, junction))
        if not candidates:
            return False
        for _score, runner, crosser, junction in sorted(candidates):
            # A vehicle already inside the junction is not excluded: if it is
            # struck, first_contact makes it part of the same collision.
            if self._first_in_line(runner, snapshot) and self._first_in_line(crosser, snapshot):
                break
        else:
            return False
        self.runner, self.crosser, self.junction = runner, crosser, junction
        self.participants = {runner: {"role": "striking"}, crosser: {"role": "struck"}}
        # The runner is scripted from here (speed mode 6: physical acceleration
        # limits kept; safe gap, right of way and red-light braking ignored).
        self.traci.vehicle.setSpeedMode(runner, 6)
        self.traci.vehicle.setLaneChangeMode(runner, 0)
        self.crosser_speed = max(8.0, min(13.0, snapshot[crosser]["speed"]))
        self.location = {"junction": junction, "edge": snapshot[runner]["road"]}
        self.note(f"{runner} approaches {junction} on red without slowing; {crosser} is crossing on green")
        return True

    def _seconds_to_green(self, junction: str, vehicle_id: str) -> float:
        """Time until vehicle_id's own signal link turns green, from the
        controller's active program (inf if it does not within one cycle)."""
        try:
            link_index = self.traci.vehicle.getNextTLS(vehicle_id)[0][1]
            logic = self.traci.trafficlight.getAllProgramLogics(junction)[0]
            phase = self.traci.trafficlight.getPhase(junction)
            remaining = self.traci.trafficlight.getNextSwitch(junction) - self.traci.simulation.getTime()
        except (self.traci.exceptions.TraCIException, IndexError):
            return math.inf
        elapsed = max(0.0, remaining)
        phases = logic.phases
        for step in range(1, len(phases) + 1):
            candidate = phases[(phase + step) % len(phases)]
            if link_index < len(candidate.state) and candidate.state[link_index] in ("G", "g"):
                return elapsed
            elapsed += candidate.duration
        return math.inf

    def _first_in_line(self, vehicle_id: str, snapshot: dict) -> bool:
        """No vehicle between this one and the junction (it would be hit)."""
        try:
            tls = self.traci.vehicle.getNextTLS(vehicle_id)
            leader = self.traci.vehicle.getLeader(vehicle_id, (tls[0][2] if tls else 40.0) + 20.0)
        except self.traci.exceptions.TraCIException:
            return False
        return leader is None

    def _junction_occupied(self, junction: str, snapshot: dict, exclude: set[str]) -> bool:
        prefix = f":{junction}_"
        return any(data["road"].startswith(prefix) for vehicle_id, data in snapshot.items() if vehicle_id not in exclude)

    @staticmethod
    def _axis(edge: str) -> str:
        head, tail = edge[2:].split("_")
        return "ns" if head.split("c")[1] == tail.split("c")[1] else "ew"

    @staticmethod
    def _goes_straight(edge: str, next_edge: str) -> bool:
        try:
            head, tail = edge[2:].split("_")
            next_head, next_tail = next_edge[2:].split("_")
            a = [int(value) for value in head[1:].split("c")]
            b = [int(value) for value in tail[1:].split("c")]
            c = [int(value) for value in next_tail[1:].split("c")]
        except (ValueError, IndexError):
            return False
        return (b[0] - a[0], b[1] - a[1]) == (c[0] - b[0], c[1] - b[1])

    def _conflict(self, runner: dict, crosser: dict) -> tuple[float, float]:
        rx, ry = runner["front"]
        rhx, rhy = heading_vector(runner["angle"])
        cx, cy = crosser["front"]
        chx, chy = heading_vector(crosser["angle"])
        # Intersection of the two straight paths (perpendicular grid roads).
        denominator = rhx * chy - rhy * chx
        if abs(denominator) < 0.2:
            return math.inf, math.inf
        t = ((cx - rx) * chy - (cy - ry) * chx) / denominator
        u = ((cx - rx) * rhy - (cy - ry) * rhx) / denominator
        return t, u

    def choreograph(self, snapshot: dict) -> bool:
        if self.first_contact([self.runner, self.crosser], snapshot):
            return True
        runner, crosser = snapshot[self.runner], snapshot[self.crosser]
        runner_to_conflict, crosser_to_conflict = self._conflict(runner, crosser)
        if crosser_to_conflict < -crosser["length"] or runner_to_conflict < -2.0:
            return False
        flank = crosser_to_conflict + crosser["length"] * 0.5
        distance = runner_to_conflict - crosser["width"] * 0.5
        try:
            next_signal = self.traci.vehicle.getNextTLS(self.runner)[0]
            to_line = next_signal[2] if next_signal[0] == self.junction else 0.0
        except (self.traci.exceptions.TraCIException, IndexError):
            to_line = 0.0
        braking_distance = runner["speed"] * runner["speed"] / (2.0 * 4.0) + 3.0
        # Inside the junction (or past its signal) the runner is committed.
        committed = runner["road"].startswith(":") or to_line <= braking_distance
        # The crossing driver is only scripted once its own light is green (it
        # then keeps going instead of braking for the runner); before that it
        # waits at the line under SUMO's normal control.
        crosser_green = crosser["speed"] > 0.5 or crosser["road"].startswith(":")
        if not crosser_green:
            try:
                crosser_green = str(self.traci.vehicle.getNextTLS(self.crosser)[0][3]) in ("G", "g")
            except (self.traci.exceptions.TraCIException, IndexError):
                crosser_green = False
        if crosser_green and not self.scene.get("crosser_scripted"):
            self.scene["crosser_scripted"] = True
            self.traci.vehicle.setSpeedMode(self.crosser, 6)
            self.traci.vehicle.setLaneChangeMode(self.crosser, 0)
        if committed and flank <= 0.5:
            # The crossing car's centre is already past: strike its rear quarter.
            self.traci.vehicle.setSpeed(self.runner, max(7.0, runner["speed"]))
            return False
        if crosser_green:
            self.traci.vehicle.setSpeed(self.crosser, self.crosser_speed)
            speed = max(crosser["speed"], 0.1)
            # Remaining time under constant acceleration toward crosser_speed.
            if speed < self.crosser_speed - 0.5:
                acceleration = 2.4
                time_to_cruise = (self.crosser_speed - speed) / acceleration
                cruise_distance = speed * time_to_cruise + 0.5 * acceleration * time_to_cruise ** 2
                arrival = (math.sqrt(speed * speed + 2.0 * acceleration * flank) - speed) / acceleration if flank <= cruise_distance else time_to_cruise + (flank - cruise_distance) / self.crosser_speed
            else:
                arrival = flank / speed
        else:
            to_green = self._seconds_to_green(self.junction, self.crosser)
            arrival = (to_green if math.isfinite(to_green) else 30.0) + math.sqrt(2.0 * max(flank, 0.1) / 2.4)
        desired = distance / max(arrival, 0.3)
        if not 2.5 <= desired <= 16.0 and not committed:
            # Still able to stop for the red: abandon cleanly and early.
            self.choreography_failed = True
            return False
        self.traci.vehicle.setSpeed(self.runner, max(4.0 if committed else 2.5, min(16.0, desired)))
        return False


class OvertakeIncident(CrashIncident):
    """A driver stuck behind a slower car pulls into the oncoming lane to pass
    and meets an oncoming car head-on: both drivers brake, far too late."""

    kind = "unsafe_overtake"
    title = "Unsafe overtaking collision"
    # Where on the edge the two meet (inside the response scene window), and
    # how long the pass takes from pulling out to contact.
    MEETING_POSITION = 38.0
    PASS_SECONDS = 2.6
    LATE_BRAKE_SECONDS = 0.7
    ONCOMING_BRAKE_SECONDS = 1.1

    def select(self, snapshot: dict, relaxed: bool) -> bool:
        busy = self.engine.busy_vehicles()
        candidates = []
        for overtaker, data in snapshot.items():
            if data["type"] not in ("car", "taxi") or overtaker in busy or not is_interior_edge(data["road"]):
                continue
            if data["lane_position"] > (24.0 if relaxed else 18.0) or data["speed"] < 3.0:
                continue
            try:
                leader = self.traci.vehicle.getLeader(overtaker, 30.0)
            except self.traci.exceptions.TraCIException:
                continue
            if not leader or leader[0] not in snapshot or leader[0] in busy or leader[1] > 25.0:
                continue
            slow = snapshot[leader[0]]
            if slow["type"] not in ("car", "taxi") or slow["road"] != data["road"]:
                continue
            oncoming = self._oncoming_partner(data, snapshot, busy, relaxed)
            if oncoming is None:
                continue
            candidates.append((leader[1], overtaker, leader[0], oncoming))
        if not candidates:
            return False
        _gap, overtaker, slow, oncoming = sorted(candidates)[0]
        self.overtaker, self.slow, self.oncoming = overtaker, slow, oncoming
        self.participants = {overtaker: {"role": "striking"}, oncoming: {"role": "struck"}}
        for vehicle_id in (overtaker, oncoming):
            self.traci.vehicle.setSpeedMode(vehicle_id, 0)
            self.traci.vehicle.setLaneChangeMode(vehicle_id, 0)
        self.traci.vehicle.changeLaneRelative(overtaker, 1, 2.0)
        self.state = "passing"
        self.pass_started = self.now
        self.location = {"edge": snapshot[slow]["road"]}
        self.note(f"{overtaker} pulls into the oncoming lane to pass {slow} while {oncoming} is approaching")
        return True

    def _oncoming_partner(self, data: dict, snapshot: dict, busy: set, relaxed: bool) -> str | None:
        """An oncoming car on the opposite lane of the same block, far enough
        to meet mid-block within the pass, with nothing else between them."""
        opposite = reverse_edge(data["road"])
        best = None
        for other_id, other in snapshot.items():
            if other_id in busy or other["road"] != opposite or other["type"] not in ("car", "taxi"):
                continue
            # Its distance from the overtaker's edge start, measured the same way.
            position = LANE_LENGTH - other["lane_position"]
            gap = position - data["lane_position"]
            if not (38.0 <= gap <= (70.0 if relaxed else 64.0)) or other["speed"] < 2.0:
                continue
            between = [v for v_id, v in snapshot.items() if v_id != other_id and v["road"] == opposite and LANE_LENGTH - v["lane_position"] < position]
            if between:
                continue
            if best is None or gap < best[0]:
                best = (gap, other_id)
        return best[1] if best else None

    def choreograph(self, snapshot: dict) -> bool:
        if self.first_contact([self.overtaker, self.oncoming], snapshot):
            return True
        if self.oncoming not in snapshot:
            self.choreography_failed = True
            return False
        overtaker, oncoming = snapshot[self.overtaker], snapshot[self.oncoming]
        elapsed = self.now - self.pass_started
        remaining = max(self.PASS_SECONDS - elapsed, 0.25)
        # Positions along the overtaker's edge (the oncoming car runs the
        # reverse edge, so its position is mirrored).
        own_position = overtaker["lane_position"] if overtaker["road"] == self.location["edge"] else LANE_LENGTH - overtaker["lane_position"]
        oncoming_position = LANE_LENGTH - oncoming["lane_position"] if oncoming["road"] == reverse_edge(self.location["edge"]) else oncoming["lane_position"]
        meeting = max(self.MEETING_POSITION, own_position + 4.0)
        overtaker_speed = max(8.0, min(OVERTAKE_MAX_SPEED, (meeting - own_position) / remaining))
        oncoming_speed = max(6.0, min(16.0, (oncoming_position - meeting) / remaining))
        if remaining <= self.ONCOMING_BRAKE_SECONDS:
            # The oncoming driver sees it too late: hard braking, not enough.
            oncoming_speed = max(5.5, oncoming["speed"] - 6.0 * self.engine.step_length)
        if remaining <= self.LATE_BRAKE_SECONDS:
            # The overtaker sees the oncoming car far too late and stamps on the
            # brakes in the oncoming lane (a swerve back would need room it no
            # longer has): both still meet front to front.
            overtaker_speed = max(9.0, overtaker["speed"] - 5.0 * self.engine.step_length)
            if self.state == "passing":
                self.state = "braking"
                self.note(f"{self.overtaker} brakes too late in the oncoming lane")
        self.traci.vehicle.setSpeed(self.overtaker, overtaker_speed)
        self.traci.vehicle.setSpeed(self.oncoming, oncoming_speed)
        if elapsed > self.PASS_SECONDS + 3.0:
            self.note(f"{self.overtaker} and the oncoming car passed without contact")
            self.choreography_failed = True
        return False

    def _plan_response(self) -> None:
        super()._plan_response()
        edge = self.location.get("edge", "")
        if not edge or getattr(self, "junction", ""):
            return
        # Both wrecks block the one lane; the overtaker's own lane runs free
        # beside them. Recovery comes down that lane and stops just past the
        # wrecks (no tow could get through the blocked lane to a slot beyond).
        from incident_engine import TOW_LENGTH, TOW_SLOT_CLEARANCE
        planner = self.engine.scene_planner
        own = reverse_edge(edge)
        front = max(planner.lane_position_of(own, x, y) for box in self.wreck_boxes() for x, y in box.corners())
        slot = min(LANE_LENGTH - 3.0, front + TOW_SLOT_CLEARANCE + TOW_LENGTH)
        for request in self.unit_requests:
            if request["kind"] == "tow" and not request["unit"]:
                request["stop"] = planner.stop(own, slot)
        # Recovery uses that lane, so nobody is waved past the wrecks through
        # it any more (the flagger stops when both lanes are taken): a passer
        # could otherwise meet an arriving tow head-on.
        self.engine.road.close_lane(self.id, own, max(0.0, slot - TOW_LENGTH - 3.0), slot + 1.0)


class SecondaryCollisionIncident(CrashIncident):
    """Any unscripted contact becomes a coherent incident: the cars stop as
    wrecks (separated along the contact normal) and receive a response, so no
    two vehicles ever overlap and drive on."""

    kind = "secondary_collision"
    title = "Secondary collision"

    def select(self, snapshot: dict, relaxed: bool) -> bool:
        first, second = str(self.params.get("collider", "")), str(self.params.get("victim", ""))
        if first not in snapshot or second not in snapshot:
            self.set_phase("resolved")
            return False
        if snapshot[first]["speed"] < 0.3 and snapshot[second]["speed"] < 0.3 and self._nudge_apart(first, second, snapshot):
            # Two stationary bodies touching by centimetres (no energy): they
            # are separated in-lane and no response is needed.
            self.note(f"{first} and {second} separated (stationary contact)")
            self.set_phase("resolved")
            return False
        self.participants = {first: {"role": "striking"}, second: {"role": "struck"}}
        self.location = {"edge": snapshot[first]["road"] if not snapshot[first]["road"].startswith(":") else snapshot[second]["road"]}
        return True

    def _nudge_apart(self, first: str, second: str, snapshot: dict) -> bool:
        a, b = snapshot[first], snapshot[second]
        try:
            lane_a, lane_b = self.traci.vehicle.getLaneID(first), self.traci.vehicle.getLaneID(second)
        except self.traci.exceptions.TraCIException:
            return False
        if lane_a != lane_b or lane_a.startswith(":"):
            return False
        rear, front = (first, second) if a["lane_position"] < b["lane_position"] else (second, first)
        overlap_length = snapshot[rear]["lane_position"] - (snapshot[front]["lane_position"] - snapshot[front]["length"])
        if overlap_length <= 0.0 or overlap_length > 1.2:
            return False
        target = snapshot[rear]["lane_position"] - overlap_length - 0.3
        if target < snapshot[rear]["length"]:
            return False
        try:
            if snapshot[rear]["road"] not in self.traci.vehicle.getRoute(rear):
                return False
            self.traci.vehicle.moveTo(rear, lane_a, target)
        except self.traci.exceptions.TraCIException:
            return False
        return True

    def choreograph(self, snapshot: dict) -> bool:
        first, second = list(self.participants)[:2]
        box_a, box_b = vehicle_box(snapshot[first]), vehicle_box(snapshot[second])
        hit, normal, depth = overlap(box_a, box_b)
        if not hit:
            dx, dy = box_b.x - box_a.x, box_b.y - box_a.y
            length = max(math.hypot(dx, dy), 0.01)
            normal, depth = (dx / length, dy / length), 0.0
        self.register_impact(first, second, snapshot, box_a, box_b, normal)
        # Start the slide from a touching pose, never an interpenetrating one.
        for vehicle_id, sign in ((first, -0.5), (second, 0.5)):
            body = self.bodies[vehicle_id]
            body.dx += normal[0] * depth * sign
            body.dy += normal[1] * depth * sign
        return True


# ---------------------------------------------------------------------------------
# Breakdowns, vehicle fire and lost load
# ---------------------------------------------------------------------------------

class BreakdownIncident(Incident):
    kind = "breakdown"
    title = "Vehicle breakdown"
    ignites = False

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        super().__init__(engine, incident_id, params)
        self.vehicle_id = ""
        self.fire = {"intensity": 0.0, "water": False}
        self.report_done_at = -1.0

    def controlled_ids(self) -> set[str]:
        return {self.vehicle_id} if self.vehicle_id and self.participants.get(self.vehicle_id, {}).get("status") not in ("released", "towed") else set()

    def _choose(self, snapshot: dict, types: tuple[str, ...]) -> bool:
        target = str(self.params.get("vehicle", ""))
        busy = self.engine.busy_vehicles()
        options = []
        for vehicle_id, data in snapshot.items():
            if data["type"] not in types or vehicle_id in busy or not is_interior_edge(data["road"]):
                continue
            if target and vehicle_id != target:
                continue
            if not (14.0 <= data["lane_position"] <= 42.0) or data["speed"] < 2.0:
                continue
            # Must be able to come to rest inside the scene window.
            if data["lane_position"] + data["speed"] ** 2 / (2.0 * COAST_MAX_DECEL) > SCENE_MAX_POSITION:
                continue
            near = self._near_point()
            if near is not None and self.now - self.created < NEAR_PICK_SECONDS:
                # A scenario asked for a place (Godot x, z): a car on a street near it, for the first seconds.
                distance = math.hypot(data["front"][0] - near[0], data["front"][1] - near[1])
                if distance > NEAR_PICK_RADIUS:
                    continue
                options.append((distance, vehicle_id))
                continue
            options.append((abs(data["lane_position"] - 26.0), vehicle_id))
        if not options:
            return False
        self.vehicle_id = sorted(options)[0][1]
        self.participants = {self.vehicle_id: {"role": "disabled", "status": "coasting", "zone": "front", "level": 0.0, "drivable": False, "injured": False}}
        self.traci.vehicle.setSpeedMode(self.vehicle_id, 0)
        self.traci.vehicle.setLaneChangeMode(self.vehicle_id, 0)
        data = snapshot[self.vehicle_id]
        self.location = {"edge": data["road"]}
        natural = data["lane_position"] + data["speed"] ** 2 / (2.0 * 2.2)
        self.coast_target = min(SCENE_MAX_POSITION, max(SCENE_MIN_POSITION + 4.0, natural))
        return True

    def _near_point(self) -> tuple[float, float] | None:
        near = self.params.get("near") if isinstance(self.params.get("near"), dict) else self.params
        try:
            return float(near["x"]) + WORLD, float(near["z"]) + WORLD
        except (KeyError, TypeError, ValueError):
            return None

    def _jam_size(self) -> int:
        try:
            return max(0, min(40, int(self.params.get("jam", 0) or 0)))
        except (TypeError, ValueError):
            return 0

    def jam_vehicle_ids(self) -> set[str]:
        """The queued cars that have not got past the broken car yet (afterwards they are ordinary traffic again,
        and the city keeps extending their trips)."""
        if self.phase == "resolved":
            return set()
        snapshot = self.engine.last_snapshot
        held = set()
        for vehicle_id, edges in getattr(self, "jam_paths", {}).items():
            road = snapshot.get(vehicle_id, {}).get("road", "")
            if road.startswith(":") or road in edges:
                held.add(vehicle_id)
        return held

    def _feed_jam(self, snapshot: dict) -> None:
        """A rush-hour breakdown (param jam: N): the nearby traffic is not sent round the block, and N cars of the
        surrounding streets are routed through the blocked lane, so a real queue builds up behind the broken car."""
        edge = self.location.get("edge", "")
        anchor = snapshot.get(self.vehicle_id)
        if not edge or anchor is None:
            return
        try:
            lane = self.traci.vehicle.getLaneID(self.vehicle_id)
            onward = [link[0].rsplit("_", 1)[0] for link in self.traci.lane.getLinks(lane) if not link[0].startswith(":")]
        except self.traci.exceptions.TraCIException:
            return
        busy = self.engine.busy_vehicles() | set(self.engine.fleet.vehicle_to_unit)
        candidates = []
        for vehicle_id, data in snapshot.items():
            if vehicle_id == self.vehicle_id or vehicle_id in busy or data["type"] not in ("car", "taxi") or data["road"].startswith(":"):
                continue
            distance = math.hypot(data["front"][0] - anchor["front"][0], data["front"][1] - anchor["front"][1])
            if data["road"] == edge and data["lane_position"] >= anchor["lane_position"]:
                continue                                     # already past the broken car
            if distance <= JAM_FEED_RADIUS:
                candidates.append((distance, vehicle_id, data["road"]))
        self.jam_feed = []
        self.jam_paths: dict[str, set[str]] = {}
        for _distance, vehicle_id, road in sorted(candidates):
            if len(self.jam_feed) >= self._jam_size():
                break
            try:
                if road == edge:
                    self.jam_feed.append(vehicle_id)     # already behind it: it simply stays in the queue
                    self.jam_paths[vehicle_id] = {edge}
                    continue
                approach = self.traci.simulation.findRoute(road, edge, vType="car").edges
                if not approach or len(approach) > 6:
                    continue
                beyond = onward[len(self.jam_feed) % len(onward)] if onward else ""
                route = list(approach) + ([beyond] if beyond else [])
                self.traci.vehicle.setRoute(vehicle_id, route)
                self.jam_feed.append(vehicle_id)
                self.jam_paths[vehicle_id] = set(approach)
            except self.traci.exceptions.TraCIException:
                continue
        if self.jam_feed:
            self.note(f"Rush hour: {len(self.jam_feed)} cars queue behind the broken-down car")

    def step(self, snapshot: dict) -> None:
        if self.phase == "arming":
            if self._choose(snapshot, CIVILIAN_TYPES):
                self.note(f"{self.vehicle_id} loses engine power and coasts to a stop")
                self.set_phase("coasting")
            return
        if self.vehicle_id not in snapshot and self.participants[self.vehicle_id]["status"] != "towed":
            self.set_phase("resolved")
            return
        if self.phase == "coasting":
            data = snapshot[self.vehicle_id]
            # Rolls to rest at its target inside the scene window, so the
            # response block, the flagger line and the tow slot all fit.
            remaining = getattr(self, "coast_target", data["lane_position"]) - data["lane_position"]
            if data["road"] != self.location.get("edge") or remaining <= 0.3:
                decel = COAST_MAX_DECEL
            else:
                decel = min(COAST_MAX_DECEL, max(0.5, data["speed"] ** 2 / (2.0 * remaining)))
            self.traci.vehicle.setSpeed(self.vehicle_id, max(0.0, data["speed"] - decel * self.engine.step_length))
            if data["speed"] < 0.05:
                self.hold_vehicle(self.vehicle_id)
                self.participants[self.vehicle_id]["status"] = "wreck"
                box = vehicle_box(data)
                self.location.update({"pos": round(data["lane_position"], 1), "x": godot_xy(box.x, box.y)[0], "z": godot_xy(box.x, box.y)[1], "sx": box.x, "sy": box.y})
                self.engine.road.close_lane(self.id, data["road"], data["lane_position"] - data["length"] - 1.0, data["lane_position"] + 1.0,
                                            reroute=not self._jam_size(), block_entry=True)
                if self._jam_size():
                    self._feed_jam(snapshot)
                self.engine.events.push(self.now, {"kind": "vehicle_disabled", "incident": self.id, "vehicle": self.vehicle_id})
                self.note("Vehicle stopped in lane with hazard lights; lane closed to through traffic")
                self._plan()
                self.set_phase("responding")
        elif self.phase in ("responding", "working"):
            self._step_work(snapshot)
        self.scene["fire"] = {"intensity": round(self.fire["intensity"], 3), "water": self.fire["water"]}

    def _plan(self) -> None:
        stops = self.engine.scene_planner.stops_for(self, [vehicle_box(self.engine.last_snapshot[self.vehicle_id])])
        self.scene["planned_stops"] = stops
        plan = [("police", "traffic_control"), ("tow", "recovery")]
        if self.ignites:
            plan = [("police", "traffic_control"), ("fire", "fire_suppression"), ("tow", "recovery")]
        for kind, role in plan:
            stop = stops[min(1, len(stops) - 1)] if role == "recovery" and len(stops) > 1 else stops[0]
            self.request_unit(kind, role, stop, response=kind not in ("tow",))
        self.note("Response plan: " + ", ".join(role for _kind, role in plan))

    def _release_overflow(self, snapshot: dict) -> None:
        """A rush-hour queue fills the broken-down car's street; once that street is full, a queued car that reaches
        the junction in front of it goes round the block instead of waiting there: in a single-lane street it would
        stop everyone behind it, whatever their direction, and the junction's green would move two or three cars."""
        paths = getattr(self, "jam_paths", None)
        edge = self.location.get("edge", "")
        if not paths or not edge or self.now < getattr(self, "_next_overflow_check", 0.0):
            return
        self._next_overflow_check = self.now + 1.0
        anchor = snapshot.get(self.vehicle_id)
        if anchor is None:
            return
        behind = [data for vehicle_id, data in snapshot.items() if data["road"] == edge and vehicle_id != self.vehicle_id
                  and data["lane_position"] < anchor["lane_position"]]
        room = max(0.0, anchor["lane_position"] - anchor["length"] - 2.0)
        if len(behind) < max(1, int(room / JAM_CAR_SPACING) - 1):
            return                                           # still room in the street: the queue keeps feeding it
        if not getattr(self, "_overflow_priced", False):
            from road_control import CLOSED_TRAVEL_TIME
            try:
                self.traci.edge.adaptTraveltime(edge, CLOSED_TRAVEL_TIME)
            except self.traci.exceptions.TraCIException:
                pass
            self._overflow_priced = True
        for vehicle_id in list(paths):
            data = snapshot.get(vehicle_id)
            if data is None or data["road"] == edge or data["road"].startswith(":"):
                continue
            try:
                route = self.traci.vehicle.getRoute(vehicle_id)
                index = self.traci.vehicle.getRouteIndex(vehicle_id)
                if index + 1 >= len(route) or route[index + 1] != edge:
                    continue
                if self.traci.lane.getLength(self.traci.vehicle.getLaneID(vehicle_id)) - data["lane_position"] > JAM_RELEASE_METRES:
                    continue
                paths.pop(vehicle_id, None)
                self.traci.vehicle.rerouteTraveltime(vehicle_id)
                self.released_overflow = getattr(self, "released_overflow", 0) + 1
            except self.traci.exceptions.TraCIException:
                continue

    def _step_work(self, snapshot: dict) -> None:
        self.maintain_block(snapshot)
        self._service_unit_requests()
        self._watch_units(snapshot)
        if self._jam_size():
            self._release_overflow(snapshot)
        dt = self.engine.step_length
        record = self.participants[self.vehicle_id]
        if self.ignites:
            elapsed = self.now - self.created
            if record["status"] == "wreck" and elapsed > 14.0 and self.fire["intensity"] <= 0.0 and not self.scene.get("extinguished"):
                self.fire["intensity"] = 0.15
                self.note("Smoke from the engine bay turns into an engine fire")
                self.engine.events.push(self.now, {"kind": "fire_ignition", "incident": self.id, "vehicle": self.vehicle_id})
            hose_ready = any(self.now - self.units[unit_id]["arrived"] >= 14.0 for unit_id in self.units_on_scene("fire_suppression") if "arrived" in self.units[unit_id])
            for unit_id in self.units_on_scene("fire_suppression"):
                self.units[unit_id].setdefault("arrived", self.now)
            self.fire["water"] = hose_ready and self.fire["intensity"] > 0.0
            if self.fire["intensity"] > 0.0:
                if self.fire["water"] or self.resolve_requested:
                    self.fire["intensity"] = max(0.0, self.fire["intensity"] - (0.035 if not self.resolve_requested else 0.2) * dt)
                    if self.fire["intensity"] == 0.0:
                        self.scene["extinguished"] = True
                        self.note("Fire extinguished; overhaul and cooling")
                else:
                    self.fire["intensity"] = min(1.0, self.fire["intensity"] + 0.02 * dt)
                record["level"] = round(max(record.get("level", 0.0), self.fire["intensity"]), 2)
        police = self.units_on_scene("traffic_control")
        if self.phase == "responding" and police:
            self.set_phase("working")
            self.note("Police on scene: traffic directed around the vehicle")
        fire_safe = not self.ignites or self.scene.get("extinguished")
        if self.report_done_at < 0.0 and police and fire_safe:
            self.report_done_at = self.now + (12.0 if not self.resolve_requested else 0.0)
        if self.resolve_requested and self.report_done_at > self.now:
            self.report_done_at = self.now
        if record["status"] == "wreck" and self.report_done_at >= 0.0 and self.now >= self.report_done_at:
            tows = self.units_on_scene("recovery")
            if tows:
                record.update({"status": "loading", "tow": tows[0], "since": self.now})
                self.note(f"{tows[0]} loading {self.vehicle_id}")
            elif (self.resolve_requested and not self.ignites) or (fire_safe and self.now - self.report_done_at >= TOW_UNREACHABLE_SECONDS and not self._tow_progressing()):
                if not self.resolve_requested:
                    self.note(f"No tow can reach the scene; {self.vehicle_id} is driven clear under police escort")
                record["status"] = "driver_returning"
                record["since"] = self.now
        elif record["status"] == "loading":
            if record.get("loaded") or (self.engine.fleet.godot_silent(self.now) and self.now - record["since"] >= TOW_LOAD_TIMEOUT) or self.now - record["since"] >= TOW_LOAD_LIVE_LIMIT:
                tow = record["tow"]
                self.engine.handoffs.push(self.now, {"id": self.vehicle_id, "reason": "towed", "unit": tow, "incident": self.id})
                self.engine.fleet.remove_vehicle(self.vehicle_id)
                record["status"] = "towed"
                unit = self.engine.fleet.units.get(tow)
                if unit is not None:
                    unit.cargo = self.vehicle_id
                    self.engine.fleet.release(unit, self.now)
                self.note(f"{tow} departing with {self.vehicle_id}")
        elif record["status"] == "driver_returning":
            if record.get("driver_ready") or (self.engine.fleet.godot_silent(self.now) and self.now - record["since"] >= DRIVER_RETURN_TIMEOUT) or self.now - record["since"] >= DRIVER_RETURN_LIVE_LIMIT:
                self.release_vehicle(self.vehicle_id)
        if record["status"] in ("towed", "released"):
            self.engine.road.release(self.id)
            self.release_units()
            self.note("Lane reopened")
            self.set_phase("resolved")

    def acknowledge(self, action: str, command: dict) -> str:
        record = self.participants.get(str(command.get("vehicle", "")))
        if record is None:
            return "unknown vehicle"
        if action == "wreck_loaded":
            record["loaded"] = True
            return "ok"
        if action == "driver_ready":
            record["driver_ready"] = True
            return "ok"
        return "ignored"


class VehicleFireIncident(BreakdownIncident):
    kind = "vehicle_fire"
    title = "Vehicle fire"
    category = "fire"
    ignites = True


class LostLoadIncident(BreakdownIncident):
    kind = "lost_load"
    title = "Lost load on carriageway"

    def step(self, snapshot: dict) -> None:
        if self.phase == "arming":
            if self._choose(snapshot, ("car", "taxi", "bus")):
                self.participants[self.vehicle_id]["role"] = "load_lost"
                self.scene["debris_seed"] = self.rng.randint(1, 10_000)
                self.note(f"{self.vehicle_id} sheds part of its load and stops")
                self.set_phase("coasting")
            return
        super().step(snapshot)


# ---------------------------------------------------------------------------------
# Building fire, water main, signal failure
# ---------------------------------------------------------------------------------

class BuildingFireIncident(Incident):
    kind = "building_fire"
    title = "Building fire"
    category = "fire"

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        super().__init__(engine, incident_id, params)
        self.fire = {"intensity": 0.2, "water": False}
        building = params.get("building", {})
        self.edge = str(building.get("edge", ""))
        self.pos = float(building.get("pos", 40.0))
        self.location = {"building": building.get("path", ""), "edge": self.edge, "pos": self.pos, "x": building.get("x", 0.0), "z": building.get("z", 0.0), "sx": float(building.get("x", 0.0)) + WORLD, "sy": float(building.get("z", 0.0)) + WORLD}
        self.scene["building"] = building
        self.hose_since = -1.0
        self.overhaul_until = -1.0

    def step(self, snapshot: dict) -> None:
        dt = self.engine.step_length
        if self.phase == "arming":
            if not self.edge:
                self.note("No building address supplied")
                self.set_phase("resolved")
                return
            stops = self.engine.scene_planner.building_stops(self)
            self.scene["planned_stops"] = stops
            for kind, role in [("fire", "fire_attack"), ("fire", "fire_support"), ("police", "traffic_control"), ("ambulance", "medical_standby")]:
                self.request_unit(kind, role, stops[0])
            self.engine.events.push(self.now, {"kind": "fire_ignition", "incident": self.id})
            self.note("Fire reported; apparatus responding")
            self.set_phase("responding")
        self.maintain_block(snapshot)
        self._service_unit_requests()
        self._watch_units(snapshot)
        engines = self.units_on_scene("fire_attack") + self.units_on_scene("fire_support")
        if self.units_on_scene() and not self.scene.get("lane_closed"):
            # The lane is closed once apparatus physically occupies it; the
            # block then follows the parked units (maintain_block).
            self.scene["lane_closed"] = True
            self.engine.road.close_lane(self.id, self.edge, max(0.0, self.pos - 6.0), min(78.0, self.pos + 8.0))
            self.note("Lane in front of the building closed; traffic passes under stop/go control")
        for unit_id in engines:
            self.units[unit_id].setdefault("arrived", self.now)
        if self.phase == "responding" and engines:
            self.set_phase("working")
            self.note("First engine on scene; crews laying hose")
        hoses = sum(1 for unit_id in engines if self.now - self.units[unit_id]["arrived"] >= 16.0)
        self.fire["water"] = hoses > 0 and self.fire["intensity"] > 0.0
        if self.fire["intensity"] > 0.0:
            if self.resolve_requested:
                self.fire["intensity"] = max(0.0, self.fire["intensity"] - 0.18 * dt)
            elif hoses:
                self.fire["intensity"] = max(0.0, self.fire["intensity"] - 0.016 * hoses * dt)
            else:
                self.fire["intensity"] = min(1.0, self.fire["intensity"] + 0.012 * dt)
            if self.fire["intensity"] == 0.0:
                self.overhaul_until = self.now + (25.0 if not self.resolve_requested else 3.0)
                self.note("Fire knocked down; overhaul and ventilation")
        elif self.overhaul_until >= 0.0 and self.now >= self.overhaul_until:
            self.engine.road.release(self.id)
            self.release_units()
            self.note("Fire out; lane reopened")
            self.set_phase("resolved")
        self.scene["fire"] = {"intensity": round(self.fire["intensity"], 3), "water": self.fire["water"], "hoses": hoses}


class WaterMainIncident(Incident):
    kind = "water_main_break"
    title = "Water main break"
    category = "utility"

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        super().__init__(engine, incident_id, params)
        self.flow = 1.0
        self.repair_until = -1.0

    def step(self, snapshot: dict) -> None:
        if self.phase == "arming":
            edge = str(self.params.get("edge", ""))
            if not edge:
                choices = sorted(edge for edge in {data["road"] for data in snapshot.values()} if is_interior_edge(edge))
                if not choices:
                    return
                edge = choices[self.rng.randrange(len(choices))]
            pos = float(self.params.get("pos", 20.0 + self.rng.random() * 36.0))
            self.edge, self.pos = edge, pos
            from incident_engine import edge_point
            x, y = edge_point(edge, pos, 5.2)
            self.location = {"edge": edge, "pos": round(pos, 1), "x": godot_xy(x, y)[0], "z": godot_xy(x, y)[1], "sx": x, "sy": y}
            # A kerb-side burst floods the lane: traffic slows through the water
            # until the crew coning the lane closes it physically.
            self.engine.road.slow_lane(edge, 4.5)
            self.engine.events.push(self.now, {"kind": "water_burst", "incident": self.id})
            stops = self.engine.scene_planner.point_stops(self, edge, pos)
            self.scene["planned_stops"] = stops
            for kind, role in [("utility", "repair"), ("police", "traffic_control")]:
                self.request_unit(kind, role, stops[0], response=kind == "police")
            self.note("Water erupting at the kerb; traffic slowed through the flooded lane")
            self.set_phase("responding")
        self.maintain_block(snapshot)
        self._service_unit_requests()
        self._watch_units(snapshot)
        crews = self.units_on_scene("repair")
        if self.units_on_scene() and not self.scene.get("lane_closed"):
            # The first unit stopped in the lane is an obstruction: the lane is
            # closed (and flagged) from then on, never left as a bare queue.
            self.engine.road.close_lane(self.id, self.edge, max(0.0, self.pos - 14.0), min(78.0, self.pos + 6.0))
            self.scene["lane_closed"] = True
        if self.phase == "responding" and crews:
            self.set_phase("working")
            self.repair_until = self.now + (70.0 if not self.resolve_requested else 4.0)
            self.note("Utility crew on scene: lane coned off, isolation valve located")
        if self.phase == "working":
            if self.resolve_requested:
                self.repair_until = min(self.repair_until, self.now + 4.0)
            remaining = max(0.0, self.repair_until - self.now)
            self.flow = max(0.0, min(1.0, remaining / 35.0))
            if self.now >= self.repair_until:
                self.engine.road.release(self.id)
                self.engine.road.release_slow_lane(self.edge)
                self.release_units()
                self.note("Main isolated and repaired; lane reopened")
                self.set_phase("resolved")
        elif self.phase == "responding" and self.resolve_requested:
            self.engine.road.release(self.id)
            self.engine.road.release_slow_lane(self.edge)
            self.release_units()
            self.set_phase("resolved")
        self.scene["water"] = {"flow": round(self.flow, 3), "kerb_offset": 5.2}


class SignalFailureIncident(Incident):
    kind = "signal_failure"
    title = "Traffic signal failure"
    category = "infrastructure"

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        super().__init__(engine, incident_id, params)
        self.junction = str(params.get("junction", ""))
        self.repair_until = -1.0

    def step(self, snapshot: dict) -> None:
        if self.phase == "arming":
            lights = sorted(self.traci.trafficlight.getIDList())
            busy = {incident.location.get("junction") for incident in self.engine.incidents.values() if incident is not self}
            if not self.junction:
                options = [tls for tls in lights if tls not in busy]
                self.junction = options[self.rng.randrange(len(options))]
            row, column = self.junction.split("_r")[1].split("_c")
            x, y = (int(column) - 1) * 95.0, (int(row) - 1) * 95.0
            self.location = {"junction": self.junction, "x": godot_xy(x, y)[0], "z": godot_xy(x, y)[1], "sx": x, "sy": y}
            self.engine.road.take_junction(self.junction, "dark", [], self.now)
            stops = self.engine.scene_planner.junction_stops(self, self.junction, [])
            self.scene["planned_stops"] = stops
            for index, (kind, role) in enumerate([("police", "traffic_control"), ("utility", "repair")]):
                self.request_unit(kind, role, stops[min(index, len(stops) - 1)], response=kind == "police")
            self.note("Controller fault: signals dark, drivers treating the junction as unsignalled")
            self.set_phase("responding")
        self._service_unit_requests()
        self._watch_units(snapshot)
        if self.units_on_scene("traffic_control") and self.engine.road.junctions.get(self.junction) and self.engine.road.junctions[self.junction].mode == "dark":
            self.engine.road.release_junction(self.junction)
            self.engine.road.take_junction(self.junction, "police", [], self.now)
            self.set_phase("working")
            self.note("Officer directing traffic manually")
        if self.units_on_scene("repair") and self.repair_until < 0.0:
            self.repair_until = self.now + (45.0 if not self.resolve_requested else 3.0)
            self.note("Technician resetting the signal controller")
        if self.resolve_requested and self.repair_until > self.now + 3.0:
            self.repair_until = self.now + 3.0
        if (self.repair_until >= 0.0 and self.now >= self.repair_until) or (self.resolve_requested and not self.units):
            self.engine.road.release_junction(self.junction)
            self.release_units()
            self.note("Signals restored to their normal program")
            self.set_phase("resolved")
        self.scene["junction_control"] = self.engine.road.junction_public(self.junction)


class GarbageCollectionIncident(Incident):
    """A municipal collection round: the refuse truck leaves the sanitation
    depot, stops beside each container on its route, the crew wheels the
    container to the hopper, tips it and returns it to the kerb, then the
    truck moves on; after the last stop it drives back to the depot."""

    kind = "garbage_collection"
    title = "Garbage collection round"
    category = "service"
    ROUND_STOPS = 3
    # Without Godot (headless) a stop takes this long once the truck is there.
    HEADLESS_STOP_SECONDS = 35.0

    def __init__(self, engine, incident_id: str, params: dict) -> None:
        super().__init__(engine, incident_id, params)
        self.route: list[dict] = []
        self.stop_index = -1
        self.arrived_at = -1.0
        self.stop_done = False
        self.collected: list[str] = []

    def _plan_round(self) -> None:
        bins = load_garbage_bins()
        if not bins:
            return
        zone = str(self.params.get("zone", "")).upper()
        if zone:
            # A round ordered for a zone stays in it: its containers only (the whole zone, up to 4 stops).
            in_zone = [entry for entry in bins if zone_of_xz(entry["x"], entry["z"]) == zone]
            if in_zone:
                bins = in_zone
        start = next((entry for entry in bins if entry["id"] == self.params.get("bin")), None)
        if start is None and zone:
            start = bins[0]
        if start is None:
            # Rounds start in the depot's own district: one of the containers
            # closest to the sanitation depot.
            depot = next((depot for depot in self.engine.fleet.depots if depot.get("id") == "SANITATION-DEPOT"), None)
            door = depot.get("door", [0.0, 0.0]) if depot else [0.0, 0.0]
            nearby = sorted(bins, key=lambda entry: math.hypot(entry["x"] - door[0], entry["z"] - door[1]))[:6]
            start = nearby[self.rng.randrange(len(nearby))]
        # Each next stop is the container the truck reaches soonest by road.
        route, remaining = [start], [entry for entry in bins if entry is not start]
        stops = min(4, len(bins)) if zone else self.ROUND_STOPS
        while len(route) < stops and remaining:
            last = route[-1]
            remaining.sort(key=lambda entry: math.hypot(entry["x"] - last["x"], entry["z"] - last["z"]))
            route.append(min(remaining[:8], key=lambda entry: self._drive_seconds(last, entry)))
            remaining.remove(route[-1])
        self.route = self._best_order(route, keep_start=bool(self.params.get("bin")) and not zone)

    def _best_order(self, chosen: list[dict], keep_start: bool) -> list[dict]:
        """The visiting order with the least total A* driving time, depot leg included (every order of the round's
        3-4 containers is tried; each leg is an A* search, cached). The containers stand on one side of their
        street, so the truck must arrive in that direction: the order decides how many blocks it has to loop."""
        router = getattr(self.engine, "astar", None)
        if router is None or len(chosen) < 2 or len(chosen) > 6:
            return chosen
        depot = next((depot for depot in self.engine.fleet.depots if depot.get("id") == "SANITATION-DEPOT"), {})
        depot_edge = str(depot.get("edge", ""))
        cache: dict[tuple[str, str], float] = {}

        def leg(origin_edge: str, target: dict) -> float:
            key = (origin_edge, target["id"])
            if key not in cache:
                found = router.route(origin_edge, target["edge"]) if origin_edge else None
                cache[key] = found.cost_s if found is not None else (0.0 if not origin_edge else 1e6)
            return cache[key]

        import itertools as _itertools
        first, rest = (chosen[:1], chosen[1:]) if keep_start else ([], chosen)
        best, best_cost = chosen, math.inf
        for order in _itertools.permutations(rest):
            sequence = first + list(order)
            cost = leg(depot_edge, sequence[0]) + sum(leg(a["edge"], b) for a, b in zip(sequence, sequence[1:]))
            if cost < best_cost:
                best, best_cost = sequence, cost
        return best

    def _drive_seconds(self, origin: dict, target: dict) -> float:
        router = getattr(self.engine, "astar", None)
        if router is not None:
            # The round's order is planned with the same A* the truck drives (astar_router.py).
            found = router.route(origin["edge"], target["edge"])
            return found.cost_s - (float(origin["stop_pos"]) - float(target["stop_pos"])) / 10.0 if found is not None else math.inf
        try:
            found = self.traci.simulation.findRoute(origin["edge"], target["edge"])
            return float(found.travelTime) - (float(origin["stop_pos"]) - float(target["stop_pos"])) / 10.0 if found.edges else math.inf
        except self.traci.exceptions.TraCIException:
            return math.inf

    def _note_astar_legs(self) -> None:
        """The A* plan of the round, leg by leg, in the incident's timeline (and the streets Dijkstra would have
        explored for the same leg, to show what the heuristic saves)."""
        router = getattr(self.engine, "astar", None)
        if router is None:
            return
        legs = []
        for origin, target in zip(self.route, self.route[1:]):
            found = router.route(origin["edge"], target["edge"])
            plain = router.route(origin["edge"], target["edge"], use_heuristic=False)
            if found is not None:
                legs.append(f"{origin['id']}->{target['id']} {len(found.edges)} streets, {found.turns} turn(s), {found.cost_s:.0f} s"
                            f" (A* explored {found.expanded} streets, Dijkstra {plain.expanded if plain else '?'})")
        if legs:
            self.note("A* route plan: " + "; ".join(legs))

    def _locate(self, entry: dict) -> None:
        from incident_engine import edge_point
        x, y = edge_point(entry["edge"], float(entry["pos"]), 4.4)
        self.location = {"edge": entry["edge"], "pos": entry["pos"], "x": entry["x"], "z": entry["z"], "sx": x, "sy": y, "bin": entry["id"]}

    def _stop_for(self, entry: dict) -> dict:
        return self.engine.scene_planner.stop(entry["edge"], float(entry["stop_pos"]))

    def step(self, snapshot: dict) -> None:
        if self.phase == "arming":
            self._plan_round()
            if not self.route:
                self.note("No refuse containers sited in the city")
                self.set_phase("resolved")
                return
            self.stop_index = 0
            self._locate(self.route[0])
            self.request_unit("garbage", "collection", self._stop_for(self.route[0]), response=False)
            self.note(f"Collection round of {len(self.route)} containers: {', '.join(entry['id'] for entry in self.route)}")
            self._note_astar_legs()
            self.set_phase("responding")
        self._service_unit_requests()
        unit = next((self.engine.fleet.units.get(unit_id) for unit_id in self.units), None)
        if unit is not None and self.phase != "arming" and unit.incident_id != self.id and unit.status in ("on_scene", "garaged", "patrol"):
            # The truck is no longer working this round (released elsewhere):
            # the round ends rather than waiting on it forever.
            self.engine.road.release(self.id)
            self.note("Round ended: the truck was released")
            self.set_phase("resolved")
            return
        if self.resolve_requested and (unit is None or unit.status != "on_scene"):
            self.release_units()
            self.engine.road.release(self.id)
            self.note("Round called off")
            self.set_phase("resolved")
        elif unit is not None and unit.status == "on_scene" and unit.incident_id == self.id:
            if self.arrived_at < 0.0:
                self.arrived_at = self.now
                self.stop_done = False
                self.set_phase("working")
                entry = self.route[self.stop_index]
                # The stopped truck closes its lane with room behind its tail
                # for the crew and the container; traffic is flagged past.
                tail = float(entry["stop_pos"]) - 8.6
                self.engine.road.close_lane(self.id, entry["edge"], max(0.0, tail - 7.0), float(entry["stop_pos"]) + 0.5)
                self.note(f"{unit.id} stopped at {entry['id']}; crew collecting")
            headless = (self.engine.fleet.godot_silent(self.now) and self.now - self.arrived_at >= self.HEADLESS_STOP_SECONDS) or self.now - self.arrived_at >= 150.0
            if self.stop_done or headless or self.resolve_requested:
                self._next_stop(unit)
        self.scene["collection"] = {
            "route": [entry["id"] for entry in self.route], "stop": self.stop_index,
            "bin": self.route[self.stop_index]["id"] if 0 <= self.stop_index < len(self.route) else "",
            "collected": list(self.collected),
        }

    def _next_stop(self, unit) -> None:
        self.engine.road.release(self.id)
        self.collected.append(self.route[self.stop_index]["id"])
        self.arrived_at = -1.0
        self.stop_index += 1
        if self.stop_index < len(self.route) and not self.resolve_requested:
            entry = self.route[self.stop_index]
            self._locate(entry)
            self.units[unit.id]["stop"] = self._stop_for(entry)
            self.engine.fleet.move_on(unit, self.units[unit.id]["stop"], self.now)
            self.set_phase("responding")
            self.note(f"{unit.id} moving on to {entry['id']}")
        else:
            self.stop_index = len(self.route) - 1
            self.release_units()
            self.note(f"Round complete: {len(self.collected)} containers emptied; {unit.id} returning to the depot")
            self.set_phase("resolved")

    def acknowledge(self, action: str, command: dict) -> str:
        if action == "service_stop_done" and str(command.get("bin", "")) == self.location.get("bin"):
            self.stop_done = True
            return "ok"
        return "ignored"


def zone_of_xz(x: float, z: float) -> str:
    """The city's zone letter (twin/scripts/city_zones.gd: 4 x 4 zones of 190 m, A at the north-west)."""
    column = min(3, max(0, int((x + 380.0) // 190.0)))
    row = min(3, max(0, int((z + 380.0) // 190.0)))
    return "ABCDEFGHIJKLMNOP"[row * 4 + column]


_GARBAGE_BINS: list[dict] | None = None


def load_garbage_bins() -> list[dict]:
    global _GARBAGE_BINS
    if _GARBAGE_BINS is None:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "sumo" / "garbage_bins.json"
        _GARBAGE_BINS = json.loads(path.read_text(encoding="utf-8")).get("bins", []) if path.is_file() else []
    return _GARBAGE_BINS


INCIDENT_TYPES = {
    cls.kind: cls for cls in (
        RearEndIncident, RedLightIncident, OvertakeIncident, SecondaryCollisionIncident, BreakdownIncident, VehicleFireIncident,
        LostLoadIncident, BuildingFireIncident, WaterMainIncident, SignalFailureIncident, GarbageCollectionIncident,
    )
}

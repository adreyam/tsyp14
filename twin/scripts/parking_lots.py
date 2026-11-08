"""Smart car parks for the SUMO bridge.

Cars choose a lot, drive to the kerb in front of its barrier and stop (a SUMO parking stop). The lot looks at its own occupancy:

* room and barrier not closed: the barrier opens, the car is handed to Godot (removed from SUMO) and drives in, parks in a
  bay, stays for a while, then drives out and is put back into SUMO at the same kerb spot with a route out of the city;
* lot full (or barrier closed): the barrier stays shut and the warning triangle shows; after a short hold the car carries on
  and is sent to the nearest other lot that still has room, or back into normal traffic when none has.

SUMO owns every moving car; this module owns the cars inside the lots. Godot only presents them: it receives, in each state
packet, every lot's occupancy and the phase and start time of each car (kerb queue / entering / alighting / parked /
boarding / leaving) and animates them from those times, so the picture always agrees with the counts. Everything here
runs in simulation time.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

LOTS_FILE = Path(__file__).resolve().parents[1] / "sumo" / "parking_lots.json"
CIVILIAN_TYPES = ("car", "taxi")
ENTER_SECONDS = 6.5
ALIGHT_SECONDS = 4.5
BOARD_SECONDS = 14.0
LEAVE_SECONDS = 6.5
MERGE_TURN_SECONDS = 2.8
ACCEPT_DELAY = 1.8           # barrier lifts, then the car is handed over
ENTRY_APPROACH_METRES = 6.0  # stop before the driveway so the car can steer into it
DENIAL_HOLD = 4.0            # red triangle shows while the car waits, then it drives on
DWELL_RANGE = (70.0, 200.0)
SPAWN_INTERVAL = 2.0
TARGET_OCCUPANCY = 0.58      # share of all bays the traffic tries to keep in use (parked + on the way)
MIN_DISTANCE, MAX_DISTANCE = 90.0, 460.0
HANDOFF_SECONDS = 4.0
REINSERT_TIMEOUT = 25.0
# Keep arrivals centred in their live lane until Godot owns the driveway turn.
# SUMO flag 1 is an off-road parking stop: on the final stopping tick it moves
# the body 3.2 m sideways to the shoulder.  That was the visible horizontal
# slide (and the renderer's compensating jitter) immediately before handoff.
STOP_FLAGS_PARKING = 0
MERGE_RETRY_SECONDS = 0.75
MERGE_HEADWAY_METRES = 19.0


class Lot:
    def __init__(self, data: dict) -> None:
        self.id = str(data["id"])
        self.zone = str(data.get("zone", ""))
        self.edge = str(data["edge"])
        self.pos = float(data["pos"])
        self.x = float(data["x"])
        self.z = float(data["z"])
        self.capacity = int(data["capacity"])
        self.popularity = float(data.get("popularity", 1.0))
        self.mode = "auto"                    # auto | open | closed
        self.evacuating = False               # its zone is closed (earthquake, flood): every car leaves, nobody enters
        self.mode_before_evacuation = "auto"
        self.cars: dict[int, dict] = {}      # slot -> car record
        self.entered_total = 0
        self.left_total = 0
        self.denied_total = 0
        self.redirected_total = 0
        self.last_denied_at = -1000.0

    def free_slot(self) -> int | None:
        for slot in range(self.capacity):
            if slot not in self.cars:
                return slot
        return None

    @property
    def occupied(self) -> int:
        return len(self.cars)

    def accepts(self) -> bool:
        return self.mode != "closed" and self.free_slot() is not None


class ParkingLots:
    def __init__(self, traci_module, outbound_gates: list, class_of, rng_seed: int = 6150, lots_file: Path = LOTS_FILE) -> None:
        self.traci = traci_module
        self.class_of = class_of
        self.rng = random.Random(rng_seed)
        self.gates = outbound_gates
        self.lots: dict[str, Lot] = {}
        if lots_file.is_file():
            for entry in json.loads(lots_file.read_text(encoding="utf-8")).get("lots", []):
                lot = Lot(entry)
                self.lots[lot.id] = lot
        self.intents: dict[str, dict] = {}        # vehicle id -> {"lot", "since", "stage"}
        self.reinserting: dict[str, dict] = {}    # vehicle id -> {"lot", "slot", "since", "type", "route_n"}
        self.removed_ids: set[str] = set()
        self.handoff_log: list[dict] = []
        self.sequence = 0
        self.next_spawn = 0.0
        self.route_counter = 0
        self.now = 0.0

    # ------------------------------------------------------------------ queries

    def intent_ids(self) -> set[str]:
        return set(self.intents) | set(self.reinserting)

    def consume_removed_ids(self) -> set[str]:
        removed, self.removed_ids = self.removed_ids, set()
        return removed

    def parked_total(self) -> int:
        return sum(lot.occupied for lot in self.lots.values())

    def capacity_total(self) -> int:
        return sum(lot.capacity for lot in self.lots.values())

    # ------------------------------------------------------------------ operator

    def set_mode(self, lot_id: str, mode: str) -> str:
        lot = self.lots.get(lot_id)
        if lot is None:
            return f"unknown lot {lot_id}"
        if mode not in ("auto", "open", "closed"):
            return f"unknown barrier mode {mode}"
        lot.mode = mode
        return f"{lot_id} barrier {mode}"

    def evacuate_zones(self, zones: set[str]) -> None:
        """The car parks of a closed zone (road_blocks.py lockdown) empty out: the barrier closes to arrivals, cars on
        their way there are sent to another car park, and every parked car leaves as soon as the driveway is free (its
        driver runs back to it: no stroll, no dwell time). When the zone reopens the barrier goes back to its mode."""
        for lot in self.lots.values():
            closed = lot.zone in zones
            if closed and not lot.evacuating:
                lot.evacuating = True
                lot.mode_before_evacuation = lot.mode
                lot.mode = "closed"
                for vehicle_id, intent in list(self.intents.items()):
                    if intent["lot"] == lot.id and intent["stage"] == "driving":
                        self._redirect(vehicle_id, lot, intent)
            elif not closed and lot.evacuating:
                lot.evacuating = False
                lot.mode = lot.mode_before_evacuation

    def _redirect(self, vehicle_id: str, lot: Lot, intent: dict) -> None:
        try:
            if self.traci.vehicle.getStops(vehicle_id, 1):
                self.traci.vehicle.replaceStop(vehicle_id, 0, "")
        except self.traci.exceptions.TraCIException:
            pass
        self._leave_denied(vehicle_id, lot, intent)

    # ------------------------------------------------------------------ step

    def step(self, snapshot: dict, controlled: set[str]) -> None:
        if not self.lots:
            return
        self.now = float(self.traci.simulation.getTime())
        self._step_cars(snapshot)
        self._step_reinserting(snapshot)
        self._step_intents(snapshot)
        self._spawn(snapshot, controlled)

    def _log_handoff(self, vehicle_id: str, lot: Lot, slot: int, reason: str, vehicle_class: str) -> None:
        self.sequence += 1
        self.handoff_log.append({"id": vehicle_id, "reason": reason, "lot": lot.id, "slot": slot, "cls": vehicle_class, "seq": self.sequence, "t": round(self.now, 2)})

    def _step_cars(self, _snapshot: dict) -> None:
        for lot in self.lots.values():
            # Only one car is allowed to use the narrow driveway at a time.
            # Keeping the next arrival/departure at its bay or the kerb avoids
            # the visual pile-up that used to occur at the barrier.
            for slot, car in list(lot.cars.items()):
                phase = car["phase"]
                if phase == "entering" and self.now >= car["t0"] + ENTER_SECONDS:
                    car["phase"], car["t0"] = "alighting", self.now
                elif phase == "alighting" and self.now >= car["t0"] + ALIGHT_SECONDS:
                    car["phase"], car["t0"] = "parked", self.now
                    car["leave_at"] = self.now + self.rng.uniform(*DWELL_RANGE)
                # returning/boarding are advanced by the embodied citizen in
                # Godot, not by a timer.  The car cannot leave without its
                # owner physically reaching it and completing the door motion.
                elif phase == "leaving" and self.now >= car["t0"] + LEAVE_SECONDS:
                    # The car has reversed to the barrier.  It holds there in
                    # its own lane until a real road gap is available, then
                    # performs the visible curved merge before SUMO owns it.
                    car["phase"], car["t0"] = "merge_wait", self.now
                elif phase == "merge_wait":
                    if self.now >= float(car.get("next_merge_at", 0.0)):
                        car["next_merge_at"] = self.now + MERGE_RETRY_SECONDS
                        if self._merge_gap_open(lot):
                            car["phase"], car["t0"] = "merge_turn", self.now
                elif phase == "merge_turn" and self.now >= car["t0"] + MERGE_TURN_SECONDS:
                    self._reinsert(lot, slot, car)

            if lot.evacuating:
                for car in lot.cars.values():
                    if car["phase"] in ("parked", "returning", "boarding"):
                        car["phase"], car["t0"] = "departure_wait", self.now
            moving_driveway = any(car["phase"] in ("entering", "leaving", "merge_wait", "merge_turn") for car in lot.cars.values())
            if moving_driveway:
                continue
            # Boarding completion is only a request to join the departure
            # queue. Driver events can arrive for several cars in adjacent
            # ticks; promoting them directly to `leaving` bypassed this lot
            # scheduler and put multiple bodies in the same driveway.
            ready = next((car for car in lot.cars.values() if car["phase"] == "departure_wait"), None)
            if ready is not None:
                ready["phase"], ready["t0"] = "leaving", self.now
                continue
            # Keep one complete return/boarding/departure cycle active per lot.
            # This also prevents a crowd of drivers and cars from converging on
            # the same aisle while the first owner is still boarding.
            if any(car["phase"] in ("returning", "boarding") for car in lot.cars.values()):
                continue
            # Departures get priority once their dwell time is up; this lets a
            # driver board before a new vehicle takes the shared driveway.
            due = next((car for car in lot.cars.values() if car["phase"] == "parked" and self.now >= car.get("leave_at", float("inf"))), None)
            if due is not None:
                due["phase"], due["t0"] = "returning", self.now
                continue
            waiting = next((car for car in lot.cars.values() if car["phase"] == "entry_wait"), None)
            if waiting is not None:
                waiting["phase"], waiting["t0"] = "entering", self.now

    def _reinsert(self, lot: Lot, slot: int, car: dict) -> None:
        vehicle_id = car["id"]
        target = self._exit_target(lot)
        try:
            route_edges = list(self.traci.simulation.findRoute(lot.edge, target, vType=car["type"]).edges)
            if len(route_edges) < 2:
                route_edges = [lot.edge]
            self.route_counter += 1
            route_id = f"park_route_{vehicle_id}_{self.route_counter}"
            self.traci.route.add(route_id, route_edges)
            # `max` makes the SUMO car join the moving lane rather than being
            # born stopped at the exact same kerb coordinate as the last car.
            self.traci.vehicle.add(vehicle_id, route_id, typeID=car["type"], depart="now", departLane="best", departPos=str(lot.pos), departSpeed="max")
        except self.traci.exceptions.TraCIException:
            car["t0"] = self.now - LEAVE_SECONDS + 1.0       # try again in a second; the car waits at the gate
            return
        del lot.cars[slot]
        lot.left_total += 1
        self.reinserting[vehicle_id] = {"lot": lot.id, "since": self.now}
        self._log_handoff(vehicle_id, lot, slot, "park_exit", car["cls"])

    def driver_event(self, vehicle_id: str, event: str) -> str:
        for lot in self.lots.values():
            for car in lot.cars.values():
                if str(car.get("id", "")) != vehicle_id:
                    continue
                phase = str(car.get("phase", ""))
                if event == "at_car" and phase == "returning":
                    car["phase"], car["t0"] = "boarding", self.now
                    return f"{vehicle_id} boarding"
                if event == "boarded" and phase == "boarding":
                    car["phase"], car["t0"] = "departure_wait", self.now
                    return f"{vehicle_id} queued to leave"
                return f"{vehicle_id} ignored {event} during {phase}"
        return f"unknown parking vehicle {vehicle_id}"

    def _merge_gap_open(self, lot: Lot) -> bool:
        """Do not insert a parking car into an occupied stretch of road.

        SUMO normally resolves car following after the insertion step, but an
        identical `departPos` for several releases makes that step visibly
        overlap.  Holding one car at the physical barrier until this buffer is
        clear provides both a real queue and a natural road merge.
        """
        try:
            # Do not allow two handoffs onto the same carriageway before the
            # first one has appeared in SUMO's live vehicle list.  The prior
            # code only examined already-simulated vehicles, so two parking
            # cars could be born at the same position in consecutive ticks.
            for record in self.reinserting.values():
                other = self.lots.get(str(record.get("lot", "")))
                if other is not None and other.edge == lot.edge:
                    return False
            vehicle_ids = self.traci.edge.getLastStepVehicleIDs(lot.edge)
            for vehicle_id in vehicle_ids:
                position = float(self.traci.vehicle.getLanePosition(vehicle_id))
                if abs(position - lot.pos) < MERGE_HEADWAY_METRES:
                    return False
        except self.traci.exceptions.TraCIException:
            # A transient edge query failure should not strand a car forever;
            # SUMO will still reject an unsafe insertion and we retry shortly.
            return True
        return True

    def _exit_target(self, lot: Lot) -> str:
        far = [gate for gate in self.gates if (gate[1] - (lot.x + 380.0)) ** 2 + (gate[2] - (lot.z + 380.0)) ** 2 >= 300.0 ** 2] or list(self.gates)
        return str(far[self.rng.randrange(len(far))][0])

    def _step_reinserting(self, snapshot: dict) -> None:
        for vehicle_id, record in list(self.reinserting.items()):
            if vehicle_id in snapshot or self.now - record["since"] > REINSERT_TIMEOUT:
                del self.reinserting[vehicle_id]

    def _step_intents(self, snapshot: dict) -> None:
        for vehicle_id, intent in list(self.intents.items()):
            lot = self.lots[intent["lot"]]
            if vehicle_id not in snapshot:
                if intent.get("stage") != "handed":
                    del self.intents[vehicle_id]
                continue
            stage = intent["stage"]
            if stage == "driving":
                if self._at_gate(vehicle_id, lot):
                    self._arrive(vehicle_id, lot, intent)
            elif stage == "accepting" and self.now >= intent["at"]:
                self._hand_over(vehicle_id, lot, intent)
            elif stage == "denied" and self.now >= intent["until"]:
                self._leave_denied(vehicle_id, lot, intent)

    def _at_gate(self, vehicle_id: str, lot: Lot) -> bool:
        try:
            return (
                self.traci.vehicle.getRoadID(vehicle_id) == lot.edge
                and self.traci.vehicle.isStopped(vehicle_id)
                and abs(self.traci.vehicle.getLanePosition(vehicle_id) - (lot.pos - ENTRY_APPROACH_METRES)) < 2.0
            )
        except self.traci.exceptions.TraCIException:
            return False

    def _arrive(self, vehicle_id: str, lot: Lot, intent: dict) -> None:
        slot = lot.free_slot()
        if lot.mode == "closed" or slot is None:
            lot.denied_total += 1
            lot.last_denied_at = self.now
            intent["stage"], intent["until"] = "denied", self.now + DENIAL_HOLD
            return
        # Reserve the bay now so two cars never get the same one.  The barrier
        # itself stays closed until the virtual car reaches its driveway.
        lot.cars[slot] = {"id": vehicle_id, "phase": "reserved", "t0": self.now, "type": intent["type"], "cls": intent["cls"]}
        intent.update(stage="accepting", at=self.now + ACCEPT_DELAY, slot=slot)

    def _hand_over(self, vehicle_id: str, lot: Lot, intent: dict) -> None:
        slot = intent["slot"]
        car = lot.cars.get(slot)
        if car is None or car["id"] != vehicle_id:
            del self.intents[vehicle_id]
            return
        self._log_handoff(vehicle_id, lot, slot, "park", intent["cls"])
        try:
            # A live subscription on a removed vehicle makes SUMO report an error every step.
            self.traci.vehicle.unsubscribe(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass
        try:
            self.traci.vehicle.remove(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass
        self.removed_ids.add(vehicle_id)
        # Cars may arrive together.  They wait at the kerb and are admitted to
        # the single-lane driveway one at a time by _step_cars().
        car["phase"], car["t0"] = "entry_wait", self.now
        lot.entered_total += 1
        intent["stage"] = "handed"
        del self.intents[vehicle_id]

    def _leave_denied(self, vehicle_id: str, lot: Lot, intent: dict) -> None:
        """The warning has been shown: the car pulls away and tries the nearest other lot with room, else rejoins traffic."""
        alternative = self._nearest_open_lot(vehicle_id, exclude=lot.id)
        try:
            self.traci.vehicle.resume(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass
        if alternative is None:
            del self.intents[vehicle_id]
            return
        lot.redirected_total += 1
        try:
            self.traci.vehicle.changeTarget(vehicle_id, alternative.edge)
            self.traci.vehicle.setStop(vehicle_id, alternative.edge, pos=alternative.pos - ENTRY_APPROACH_METRES, laneIndex=0, duration=3600.0, flags=STOP_FLAGS_PARKING)
        except self.traci.exceptions.TraCIException:
            del self.intents[vehicle_id]
            return
        intent.update(lot=alternative.id, stage="driving", since=self.now)

    def _nearest_open_lot(self, vehicle_id: str, exclude: str) -> Lot | None:
        try:
            x, y = self.traci.vehicle.getPosition(vehicle_id)
        except self.traci.exceptions.TraCIException:
            return None
        best, best_distance = None, 1e9
        spoken_for = {}
        for intent in self.intents.values():
            spoken_for[intent["lot"]] = spoken_for.get(intent["lot"], 0) + 1
        for lot in self.lots.values():
            if lot.id == exclude or lot.mode == "closed" or lot.occupied + spoken_for.get(lot.id, 0) >= lot.capacity:
                continue
            distance = math.hypot(lot.x + 380.0 - x, lot.z + 380.0 - y)
            if distance < best_distance:
                best, best_distance = lot, distance
        return best

    def _spawn(self, snapshot: dict, controlled: set[str]) -> None:
        if self.now < self.next_spawn:
            return
        self.next_spawn = self.now + SPAWN_INTERVAL
        wanted = TARGET_OCCUPANCY * self.capacity_total()
        if self.parked_total() + len(self.intents) >= wanted:
            return
        lots = [candidate for candidate in self.lots.values() if not candidate.evacuating]
        if not lots:
            return
        lot = self.rng.choices(lots, weights=[candidate.popularity for candidate in lots])[0]
        candidates = []
        for vehicle_id, data in snapshot.items():
            if vehicle_id in controlled or vehicle_id in self.intents or vehicle_id in self.reinserting:
                continue
            if str(data.get("type", "")) not in CIVILIAN_TYPES or float(data.get("speed", 0.0)) < 2.0:
                continue
            if str(data.get("road", "")).startswith(":"):
                continue
            front_x, front_y = data["front"]
            distance = math.hypot(front_x - (lot.x + 380.0), front_y - (lot.z + 380.0))
            if MIN_DISTANCE <= distance <= MAX_DISTANCE:
                candidates.append(vehicle_id)
        if not candidates:
            return
        vehicle_id = candidates[self.rng.randrange(len(candidates))]
        try:
            vehicle_type = str(self.traci.vehicle.getTypeID(vehicle_id))
            self.traci.vehicle.changeTarget(vehicle_id, lot.edge)
            # Stop one turning radius before the driveway. Stopping abreast of
            # the arm forced the render path to translate across the lane and
            # only then rotate. From this approach point the car can make one
            # continuous forward right turn into the gate.
            self.traci.vehicle.setStop(vehicle_id, lot.edge, pos=lot.pos - ENTRY_APPROACH_METRES, laneIndex=0, duration=3600.0, flags=STOP_FLAGS_PARKING)
        except self.traci.exceptions.TraCIException:
            return
        self.intents[vehicle_id] = {"lot": lot.id, "since": self.now, "stage": "driving", "type": vehicle_type, "cls": self.class_of(str(snapshot[vehicle_id]["type"]))}

    # ------------------------------------------------------------------ published state

    def _gate_open(self, lot: Lot) -> bool:
        """Open only while a vehicle is physically at the barrier.

        Boarding passengers and a car waiting at its bay must not hold a gate
        open.  The entry and exit timelines are measured along their visible
        driveway paths: the gate opens only for the last/first section nearest
        the barrier, then remains open while an exiting car waits or merges.
        """
        for car in lot.cars.values():
            phase = str(car.get("phase", ""))
            elapsed = max(0.0, self.now - float(car.get("t0", self.now)))
            # Both phases represent a real car stopped beside the entrance.
            # Waiting until 40% of `entering` opened the arm after the car had
            # already reached it and could leave admitted cars facing it shut.
            if phase in ("reserved", "entry_wait"):
                return True
            if phase == "entering" and elapsed <= ENTER_SECONDS * 0.72:
                return True
            if phase == "leaving" and elapsed >= LEAVE_SECONDS * 0.58:
                return True
            if phase in ("merge_wait", "merge_turn"):
                return True
        return False

    def handoffs(self) -> list[dict]:
        self.handoff_log = [entry for entry in self.handoff_log if self.now - float(entry["t"]) <= HANDOFF_SECONDS]
        return list(self.handoff_log)

    def public_state(self) -> dict:
        lots = []
        for lot in self.lots.values():
            waiting_denied = any(i["lot"] == lot.id and i["stage"] == "denied" for i in self.intents.values())
            lots.append({
                "id": lot.id, "zone": lot.zone, "edge": lot.edge, "capacity": lot.capacity, "occupied": lot.occupied,
                "barrier_mode": lot.mode, "evacuating": lot.evacuating, "gate_open": self._gate_open(lot),
                "warning": waiting_denied or self.now - lot.last_denied_at < DENIAL_HOLD + 0.5,
                "denied_total": lot.denied_total, "entered_total": lot.entered_total, "left_total": lot.left_total,
                "redirected_total": lot.redirected_total, "last_denied_at": round(lot.last_denied_at, 2),
                "incoming": sum(1 for i in self.intents.values() if i["lot"] == lot.id),
                "cars": [{"id": car["id"], "slot": slot, "phase": car["phase"], "t0": round(car["t0"], 2), "cls": car["cls"]} for slot, car in sorted(lot.cars.items()) if car["phase"] != "reserved"],
            })
        return {"time": round(self.now, 2), "lots": lots, "parked": self.parked_total(), "capacity": self.capacity_total(), "enter_s": ENTER_SECONDS, "alight_s": ALIGHT_SECONDS, "board_s": BOARD_SECONDS, "leave_s": LEAVE_SECONDS, "merge_s": MERGE_TURN_SECONDS}

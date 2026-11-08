"""Getting emergency units to a scene fast without breaking the world's rules.

Two aids for every unit on its way to an incident (lights-and-sirens
responders, and tow trucks called to a scene):

* Signal priority: a unit approaching a traffic light gets green for its own
  approach lane after a short amber for the crossing movements, so the queue
  in front of it clears with it; once it is through, the junction returns to
  its program from the matching phase (no conflicting green ever shows).
* Supervised overtaking: a unit stuck behind slower traffic mid-block passes
  through the oncoming lane, but only when that lane is empty for the whole
  manoeuvre plus a safety margin and the pass ends before the next junction.
  It steers back as soon as its own lane is free; the road controller's
  head-on guard stays armed as a last line of defence.
"""

from __future__ import annotations

# A steer-back request must expire within about a step: a longer one
# stays active after the vehicle is home and SUMO, which counts the
# oncoming lane as its neighbour, flips it back out every other step.
RETURN_REQUEST_SECONDS = 0.04

# Taken as soon as the unit is on the block before the light (blocks are 78 m), so the queue ahead clears with it.
PREEMPT_DISTANCE = 90.0
PREEMPT_AMBER_SECONDS = 2.5
# Amber plus all-red clearance never lasts longer than this.
PREEMPT_CLEARANCE_LIMIT = 7.0
PREEMPT_MAX_SECONDS = 35.0
# After a hold that timed out (the unit could not move: a blocked exit), the
# junction serves its own program for a while before it can be taken again.
PREEMPT_COOLDOWN_SECONDS = 25.0
LANE_LENGTH = 78.0
OVERTAKE_LEADER_GAP = 14.0
OVERTAKE_SLOW_SPEED = 7.0
OVERTAKE_CHAIN_GAP = 9.0
OVERTAKE_MERGE_ROOM = 6.0
OVERTAKE_STOP_LINE_ROOM = 9.0
OVERTAKE_SPEED = 15.0
OVERTAKE_START_SECONDS = 1.5
OVERTAKE_COOLDOWN_SECONDS = 4.0
# A unit waiting to pull out of its station: traffic coming up behind the
# driveway stops short of it, and a queue standing over it is cleared by a
# brief priority green at the junction ahead.
EXIT_WAIT_SECONDS = 1.0
EXIT_YIELD_METRES = 35.0
EXIT_GREEN_SECONDS = 12.0


def reverse_edge(edge: str) -> str:
    head, tail = edge[2:].split("_")
    return f"E_{tail}_{head}"


class EmergencyPriority:
    def __init__(self, traci_module, fleet, road) -> None:
        self.traci = traci_module
        self.fleet = fleet
        self.road = road
        # tls id -> {"unit", "lane", "green", "phase", "since", "program"}
        self.preempted: dict[str, dict] = {}
        # vehicle id -> overtake record
        self.passing: dict[str, dict] = {}
        self.cooldown: dict[str, float] = {}
        # Vehicles stopped to let a unit out of its station (until it is in).
        self.yielding: dict[str, str] = {}
        self.signal_cooldown: dict[str, float] = {}
        self._links: dict[str, list] = {}
        self._logics: dict[str, list] = {}
        self.preemptions = 0
        self.overtakes = 0

    # -- public ---------------------------------------------------------------

    def closed_links(self, tls: str) -> frozenset:
        """Set by the incident engine (smart_signals.SmartSignals._closed_links)."""
        return frozenset()

    def priority_units(self) -> list:
        # Every police car, ambulance and fire engine sent somewhere (an incident, a support call, a police post sent
        # by the Brain) gets green at the lights on its way, as well as tow trucks called to a scene.
        return [unit for unit in self.fleet.units.values()
                if unit.status == "en_route" and unit.vehicle_id and unit.incident_id
                and (unit.response or unit.kind in ("tow", "police", "ambulance", "fire"))]

    def opposite_vehicles(self) -> set[str]:
        return {vehicle_id for vehicle_id, record in self.passing.items() if record.get("out")}

    def step(self, now: float, snapshot: dict, protected: set[str]) -> None:
        units = [unit for unit in self.priority_units() if unit.vehicle_id in snapshot]
        active = {unit.vehicle_id for unit in units}
        for unit in units:
            data = snapshot[unit.vehicle_id]
            try:
                self._preempt(unit, data, now, protected)
                self._overtake(unit, data, snapshot, now)
            except self.traci.exceptions.TraCIException:
                continue
        for unit in self.fleet.units.values():
            if unit.status == "inserting" and unit.vehicle_id and unit.vehicle_id not in snapshot and now - unit.status_since >= EXIT_WAIT_SECONDS:
                try:
                    self._clear_exit(unit, snapshot, now, protected)
                except self.traci.exceptions.TraCIException:
                    pass
        self._release_yielders(snapshot)
        for vehicle_id in list(self.passing):
            if vehicle_id not in active:
                # Arrived, released or gone mid-pass: finish the return safely.
                self._step_pass(vehicle_id, snapshot, now, force_return=True)
        self._release_signals(now, snapshot, protected)

    # -- station exits ------------------------------------------------------------

    def _clear_exit(self, unit, snapshot: dict, now: float, protected: set[str]) -> None:
        edge = unit.depot.get("edge", "")
        if not edge or not unit.bay:
            return
        exit_pos = float(unit.bay["exit_pos"])
        length = float(unit.bay.get("length", 6.0))
        queue_over_exit = False
        for vehicle_id, data in snapshot.items():
            if data["road"] != edge or vehicle_id in self.yielding:
                continue
            front, rear = data["lane_position"], data["lane_position"] - data["length"]
            if rear < exit_pos + 2.0 and front > exit_pos - length - 2.0:
                queue_over_exit = True
            elif front <= exit_pos - length - 2.0 and front >= exit_pos - length - EXIT_YIELD_METRES:
                # Coming up behind the driveway: stop short and let it out.
                stop_room = exit_pos - length - 3.0 - front
                if stop_room < data["speed"] ** 2 / 6.0:
                    continue  # too close to stop comfortably: it drives on by
                # Held at zero (SUMO brakes within its comfortable limits) until
                # the unit is on the road; slowDown alone would let it go again.
                self.traci.vehicle.setSpeed(vehicle_id, 0.0)
                self.yielding[vehicle_id] = unit.vehicle_id
        if queue_over_exit:
            self._green_for_lane(f"{edge}_0", unit, now, protected)

    def _release_yielders(self, snapshot: dict) -> None:
        for vehicle_id, unit_vehicle in list(self.yielding.items()):
            if unit_vehicle in snapshot or vehicle_id not in snapshot or not any(
                    unit.vehicle_id == unit_vehicle and unit.status == "inserting" for unit in self.fleet.units.values()):
                self.yielding.pop(vehicle_id, None)
                try:
                    self.traci.vehicle.setSpeed(vehicle_id, -1)
                except self.traci.exceptions.TraCIException:
                    pass

    def _green_for_lane(self, lane: str, unit, now: float, protected: set[str]) -> None:
        links = self.traci.lane.getLinks(lane)
        tls_ids = [tls for tls in self.traci.trafficlight.getIDList() if any(group and group[0][0] == lane for group in self._controlled(tls))]
        if not links or not tls_ids:
            return
        tls = tls_ids[0]
        if tls in protected or tls in self.preempted or now < self.signal_cooldown.get(tls, -1.0):
            return
        green = {index for index, group in enumerate(self._controlled(tls)) if group and group[0][0] == lane}
        green -= set(self.closed_links(tls))
        current = self.traci.trafficlight.getRedYellowGreenState(tls)
        amber = "".join("y" if current[index] in "Gg" and index not in green else current[index] for index in range(len(current)))
        self.traci.trafficlight.setRedYellowGreenState(tls, amber)
        self.preempted[tls] = {"unit": unit.id, "vehicle": "", "lane": lane, "green": green, "phase": "amber", "since": now,
                               "program": self.traci.trafficlight.getProgram(tls), "until": now + EXIT_GREEN_SECONDS}
        self.preemptions += 1

    # -- signal priority ---------------------------------------------------------

    def _preempt(self, unit, data: dict, now: float, protected: set[str]) -> None:
        if data["road"].startswith(":"):
            return
        upcoming = self.traci.vehicle.getNextTLS(unit.vehicle_id)
        if not upcoming:
            return
        tls, link_index, distance, _state = upcoming[0]
        if distance > PREEMPT_DISTANCE or tls in protected or now < self.signal_cooldown.get(tls, -1.0):
            return
        record = self.preempted.get(tls)
        if record is not None:
            # Another unit already holds this junction: it keeps it; a unit on
            # the same approach simply rides the same green.
            return
        links = self._controlled(tls)
        if link_index >= len(links) or not links[link_index]:
            return
        lane = links[link_index][0][0]
        green = {index for index, group in enumerate(links) if group and group[0][0] == lane}
        # Movements into a closed street (road_blocks.py) stay red for the cars sharing the unit's lane; only the
        # unit's own movement opens (a tow truck driving into the accident street).
        green -= set(self.closed_links(tls)) - {link_index}
        current = self.traci.trafficlight.getRedYellowGreenState(tls)
        program = self.traci.trafficlight.getProgram(tls)
        if all(current[index] in "Gg" for index in green):
            # Already green: hold it until the unit is through.
            target = "".join("G" if index in green else ("r" if current[index] in "yY" else current[index]) for index in range(len(current)))
            target = self._conflict_free(tls, green, target)
            self.traci.trafficlight.setRedYellowGreenState(tls, target)
            phase = "green"
        else:
            amber = "".join("y" if current[index] in "Gg" and index not in green else current[index] for index in range(len(current)))
            self.traci.trafficlight.setRedYellowGreenState(tls, amber)
            phase = "amber"
        self.preempted[tls] = {"unit": unit.id, "vehicle": unit.vehicle_id, "lane": lane, "green": green, "phase": phase, "since": now, "program": program}
        self.preemptions += 1

    def _conflict_free(self, tls: str, green: set[int], state: str) -> str:
        """Only movements that some program phase shows together with the
        unit's approach may stay green alongside it."""
        compatible: set[int] = set(green)
        for phase_state in self._phase_states(tls):
            if all(phase_state[index] in "Gg" for index in green):
                compatible |= {index for index, signal in enumerate(phase_state) if signal in "Gg"}
                break
        return "".join(signal if index in compatible or signal not in "Gg" else "r" for index, signal in enumerate(state))

    def _release_signals(self, now: float, snapshot: dict, protected: set[str]) -> None:
        for tls, record in list(self.preempted.items()):
            if tls in protected:
                # An incident took the junction over: it owns the signals now.
                del self.preempted[tls]
                continue
            if record["phase"] == "amber" and now - record["since"] >= PREEMPT_CLEARANCE_LIMIT:
                # The box never cleared (a vehicle stuck inside it): give the
                # junction back to its program rather than hold it red.
                self._restore(tls, record)
                del self.preempted[tls]
                self.signal_cooldown[tls] = now + PREEMPT_COOLDOWN_SECONDS
                continue
            if record["phase"] == "amber" and now - record["since"] >= PREEMPT_AMBER_SECONDS:
                if not self._box_clear(tls, record["green"], snapshot):
                    # All red until crossing traffic has left the junction box.
                    try:
                        self.traci.trafficlight.setRedYellowGreenState(tls, "r" * len(self._controlled(tls)))
                    except self.traci.exceptions.TraCIException:
                        pass
                    continue
                state = "".join("G" if index in record["green"] else "r" for index in range(len(self._controlled(tls))))
                try:
                    self.traci.trafficlight.setRedYellowGreenState(tls, state)
                except self.traci.exceptions.TraCIException:
                    pass
                record["phase"] = "green"
                record["green_since"] = now
            if record["phase"] != "green":
                continue
            timed_out = now - record["since"] > PREEMPT_MAX_SECONDS
            if "until" in record:
                done = now >= float(record["until"])
            else:
                done = not self._still_approaching(record["vehicle"], tls, snapshot) or timed_out
            if done:
                self._restore(tls, record)
                del self.preempted[tls]
                if timed_out:
                    self.signal_cooldown[tls] = now + PREEMPT_COOLDOWN_SECONDS

    def _box_clear(self, tls: str, green: set[int], snapshot: dict) -> bool:
        allowed = {group[0][2] for index, group in enumerate(self._controlled(tls)) if group and index in green}
        prefix = f":{tls}_"
        for vehicle_id, data in snapshot.items():
            if not data["road"].startswith(prefix):
                continue
            try:
                lane = self.traci.vehicle.getLaneID(vehicle_id)
            except self.traci.exceptions.TraCIException:
                continue
            if lane not in allowed:
                return False
        return True

    def _still_approaching(self, vehicle_id: str, tls: str, snapshot: dict) -> bool:
        data = snapshot.get(vehicle_id)
        if data is None:
            return False
        if data["road"].startswith(f":{tls}_"):
            return True
        try:
            upcoming = self.traci.vehicle.getNextTLS(vehicle_id)
        except self.traci.exceptions.TraCIException:
            return False
        return bool(upcoming) and upcoming[0][0] == tls and upcoming[0][2] <= PREEMPT_DISTANCE + 20.0

    def _restore(self, tls: str, record: dict) -> None:
        # Resume the program at a phase that already shows the approach that
        # just had priority: nothing that was green turns into a conflict.
        try:
            states = self._phase_states(tls)
            phase = next((index for index, state in enumerate(states) if all(state[link] in "Gg" for link in record["green"])), 0)
            # Always the junction's base program: the program active when it
            # was taken may itself be an override ("online"), and restoring
            # that would freeze its lights in one state for good.
            self.traci.trafficlight.setProgram(tls, "0")
            self.traci.trafficlight.setPhase(tls, phase)
        except self.traci.exceptions.TraCIException:
            pass

    def _controlled(self, tls: str) -> list:
        if tls not in self._links:
            self._links[tls] = self.traci.trafficlight.getControlledLinks(tls)
        return self._links[tls]

    def _phase_states(self, tls: str) -> list[str]:
        if tls not in self._logics:
            # the base program "0" (a closed street adds a "closure" copy with some movements red)
            logics = [logic for logic in self.traci.trafficlight.getAllProgramLogics(tls) if logic.programID == "0"] or self.traci.trafficlight.getAllProgramLogics(tls)
            self._logics[tls] = [phase.state for phase in logics[0].phases] if logics else []
        return self._logics[tls]

    # -- overtaking ------------------------------------------------------------------

    def _overtake(self, unit, data: dict, snapshot: dict, now: float) -> None:
        vehicle_id = unit.vehicle_id
        if vehicle_id in self.passing:
            self._step_pass(vehicle_id, snapshot, now)
            return
        if now < self.cooldown.get(vehicle_id, -1.0):
            return
        road = data["road"]
        if not road.startswith("E_") or unit.guard_hold:
            return
        route = self.traci.vehicle.getRoute(vehicle_id)
        index = self.traci.vehicle.getRouteIndex(vehicle_id)
        if road not in route:
            return
        leader = self.traci.vehicle.getLeader(vehicle_id, 30.0)
        if not leader or leader[1] > OVERTAKE_LEADER_GAP or leader[0] not in snapshot:
            return
        if snapshot[leader[0]]["speed"] > OVERTAKE_SLOW_SPEED or snapshot[leader[0]]["road"] != road:
            return
        # The pass must continue straight over the next junction (SUMO only
        # drives the oncoming lane then) and must not run past the unit's stop.
        straight = self._straight_successor(road)
        if index + 1 < len(route) and route[index + 1] != straight:
            return
        stops = self.traci.vehicle.getStops(vehicle_id, 1)
        if stops and stops[0].lane.rsplit("_", 1)[0] == road:
            return
        if any(closure.edge in (road, reverse_edge(road)) and closure.start >= 0.0 for closures in self.road.closures.values() for closure in closures):
            return
        own = data["lane_position"]
        # The queue to pass: consecutive vehicles ahead with small gaps.
        ahead = sorted((other["lane_position"], other["length"], other_id) for other_id, other in snapshot.items()
                       if other_id != vehicle_id and other["road"] == road and other["lane_position"] > own)
        chain_front = own
        for position, length, _other in ahead:
            if position - length - chain_front > OVERTAKE_CHAIN_GAP and chain_front > own:
                break
            chain_front = position
        merge_front = chain_front + OVERTAKE_MERGE_ROOM + data["length"]
        if merge_front > LANE_LENGTH - OVERTAKE_STOP_LINE_ROOM:
            # The queue reaches the junction (a red light): signal priority
            # clears it instead.
            return
        if not self._oncoming_clear(road, own, merge_front, max(data["speed"], 4.0), snapshot):
            return
        record = {"edge": road, "merge": merge_front, "since": now, "out": False,
                  "lane_change_mode": self.traci.vehicle.getLaneChangeMode(vehicle_id),
                  "speed_mode": self.traci.vehicle.getSpeedMode(vehicle_id)}
        self.traci.vehicle.setLaneChangeMode(vehicle_id, 0)
        self.traci.vehicle.setSpeedMode(vehicle_id, 7)
        self.traci.vehicle.changeLaneRelative(vehicle_id, 1, 1.0)
        self.traci.vehicle.setSpeed(vehicle_id, OVERTAKE_SPEED)
        self.passing[vehicle_id] = record
        self.overtakes += 1

    def _oncoming_clear(self, road: str, own: float, merge_front: float, speed: float, snapshot: dict) -> bool:
        opposite = reverse_edge(road)
        # Anyone already out in an oncoming lane on this street (another unit's
        # overtake, a flagger's guided pass) rules a pass out entirely.
        for vehicle_id in self.road.opposite_lane_vehicles() | set(self.passing):
            other = snapshot.get(vehicle_id)
            if other is not None and other["road"] in (road, opposite):
                return False
        far_junction = road[2:].split("_")[1]
        pass_seconds = (merge_front - own) / max((speed + OVERTAKE_SPEED) * 0.5, 4.0) + 2.0
        for other in snapshot.values():
            other_road = other["road"]
            if other_road == opposite:
                # Its distance along our edge (it drives towards us).
                position = LANE_LENGTH - other["lane_position"]
                if position < own - 3.0:
                    continue
                # Judged at passing speed (the unit accelerates into the pass),
                # with more margin than the head-on guard ever asks for.
                closing = OVERTAKE_SPEED + other["speed"]
                if position - own < closing * 2.6 + 16.0 or position - other["speed"] * pass_seconds < merge_front + 14.0:
                    return False
            elif other_road.startswith(f":Junction_{far_junction.replace('c', '_c')}_"):
                return False
            elif other_road.endswith(f"_{far_junction}") and other_road != road and other["lane_position"] > LANE_LENGTH - 40.0:
                # About to come out of the far junction into the oncoming lane.
                return False
        return True

    def _step_pass(self, vehicle_id: str, snapshot: dict, now: float, force_return: bool = False) -> None:
        record = self.passing[vehicle_id]
        data = snapshot.get(vehicle_id)
        if data is None:
            self.passing.pop(vehicle_id, None)
            return
        road = data["road"]
        opposite = reverse_edge(record["edge"])
        if road == opposite:
            record["out"] = True
            own = LANE_LENGTH - data["lane_position"]
            rear = own - data["length"]
            # Never run on toward the junction in the oncoming lane.
            # (10 m short of the end: closer, SUMO forces the vehicle back into its own lane whether or not
            # someone is there; a second tow coming up beside it was hit that way)
            if own + data["speed"] ** 2 / 6.0 > LANE_LENGTH - 10.0:
                self.traci.vehicle.slowDown(vehicle_id, 0.0, max(0.3, data["speed"] / 4.0))
            free_until = own + 3.0 + data["speed"] * 1.0
            own_lane_free = not any(
                other_id != vehicle_id and other["road"] == record["edge"]
                and other["lane_position"] > rear - 3.0 and other["lane_position"] - other["length"] < free_until
                for other_id, other in snapshot.items())
            if own_lane_free and (own >= record["merge"] - 1.0 or force_return or self._oncoming_threat(own, data, record["edge"], snapshot)):
                self.traci.vehicle.changeLaneRelative(vehicle_id, -1, RETURN_REQUEST_SECONDS)
            return
        if record["out"] or now - record["since"] > OVERTAKE_START_SECONDS or force_return:
            # Back in its own lane (or SUMO never took the oncoming lane).
            self.passing.pop(vehicle_id, None)
            self.cooldown[vehicle_id] = now + OVERTAKE_COOLDOWN_SECONDS
            try:
                self.traci.vehicle.setSpeed(vehicle_id, -1)
                self.traci.vehicle.setSpeedMode(vehicle_id, record["speed_mode"])
                self.traci.vehicle.setLaneChangeMode(vehicle_id, record["lane_change_mode"])
            except self.traci.exceptions.TraCIException:
                pass

    def _oncoming_threat(self, own: float, data: dict, edge: str, snapshot: dict) -> bool:
        opposite = reverse_edge(edge)
        for other_id, other in snapshot.items():
            if other["road"] != opposite or other["speed"] < 0.1:
                continue
            position = LANE_LENGTH - other["lane_position"]
            if position > own and position - own < (data["speed"] + other["speed"]) * 3.0 + 10.0:
                return True
        return False

    def _straight_successor(self, edge: str) -> str:
        try:
            for link in self.traci.lane.getLinks(f"{edge}_0"):
                if str(link[6]) == "s" and not str(link[0]).startswith(":"):
                    return str(link[0]).rsplit("_", 1)[0]
        except self.traci.exceptions.TraCIException:
            pass
        return ""

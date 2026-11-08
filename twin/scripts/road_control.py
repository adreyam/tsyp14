"""Legal traffic management around incidents (bridge side, TraCI only).

* Lane closures re-cost the edge for routing and reroute vehicles that have
  not reached it yet; vehicles already queued behind the blockage are allowed
  to overtake through the oncoming lane — SUMO only does so when that lane is
  clear for the whole manoeuvre — and lose the permission once past.
* Junction police control replaces a signal program with a two-axis program
  in which only movements whose swept internal-lane path keeps clear of every
  wreck footprint ever receive green.
* Signal failure switches a controller dark ("O": vehicles fall back to the
  junction's right-of-way rules).
"""

from __future__ import annotations

import math

from incident_physics import Box, polyline_hits_box

# A steer-back request must expire within about a step: a longer one
# stays active after the vehicle is home and SUMO, which counts the
# oncoming lane as its neighbour, flips it back out every other step.
RETURN_REQUEST_SECONDS = 0.04

DEFAULT_TRAVEL_TIME = 78.0 / 13.89
CLOSED_TRAVEL_TIME = 900.0
POLICE_GREEN_SECONDS = 12.0
POLICE_YELLOW_SECONDS = 3.0
POLICE_ALL_RED_SECONDS = 2.0
JUNCTION_RELEASE_ALL_RED_SECONDS = 4.0
# Path centreline to wreck: half a vehicle width (1.4) + a 1.3 m crew zone
# around the wreck + 0.5 m margin, so crews beside a wreck are never on a
# movement that receives green.
WRECK_PATH_CLEARANCE = 3.2


def approach_axis(lane_id: str) -> str | None:
    """'ns' or 'ew' for an incoming lane id E_rXcY_rZcW_0."""
    try:
        head, tail = lane_id[2:].rsplit("_", 1)[0].split("_")
        from_row, from_column = head[1:].split("c")
        to_row, to_column = tail[1:].split("c")
    except ValueError:
        return None
    if from_column == to_column:
        return "ns"
    if from_row == to_row:
        return "ew"
    return None


FLAGGER_RELEASE_SECONDS = 12.0
FLAGGER_CLEARING_TIMEOUT = 25.0
FLAGGER_GO_SECONDS = 18.0
FLAGGER_TIGHT_GO_SECONDS = 9.0
FLAGGER_VERY_TIGHT_GO_SECONDS = 6.0
FLAGGER_DRAIN_TIMEOUT = 12.0
# No pass starts this close to the end of a go phase.
FLAGGER_LAST_WAVE_SECONDS = 3.0
# Oncoming traffic is held far enough beyond the scene for the longest passer
# (a 9 m bus: rear 3 m past the scene, so its front at +12 m) to return and
# still stop 4 m short of the held car.
FLAGGER_HOLD_MARGIN = 16.0
FLAGGER_PASSER_STOP_SHORT = 4.0
FLAGGER_MIN_HOLD_ROOM = 8.0      # metres of oncoming lane needed before the flagger line to stop oncoming cars
FLAGGER_HOLD_DECEL = 3.5
# Waved-through vehicles pass the scene in the held oncoming lane at this
# speed and return once their rear is this far beyond the scene.
FLAGGER_PASS_SPEED = 7.0
# Beyond SUMO's minimum gap (2.5 m), which it treats as a collision.
FLAGGER_RETURN_MARGIN = 3.0
BLOCK = 95.0
JUNCTION_HALF = 8.5


def own_lane_position(edge_id: str, x: float, y: float) -> float:
    """Lane position along edge_id of the point (x, y) (SUMO frame)."""
    try:
        head, tail = edge_id[2:].split("_")
        from_row, from_column = (int(value) for value in head[1:].split("c"))
        to_row, to_column = (int(value) for value in tail[1:].split("c"))
    except ValueError:
        return 0.0
    fx, fy = (from_column - 1) * BLOCK, (from_row - 1) * BLOCK
    tx, ty = (to_column - 1) * BLOCK, (to_row - 1) * BLOCK
    return ((x - fx) * (tx - fx) + (y - fy) * (ty - fy)) / BLOCK - JUNCTION_HALF


class LaneClosure:
    """A blocked stretch of one lane, managed like a real stop/go closure:
    oncoming traffic is held at a flagger line beyond the scene, the queued
    traffic passes through the guaranteed-empty opposite lane, then the
    oncoming traffic is released."""

    def __init__(self, edge: str, start: float, end: float) -> None:
        self.edge = edge
        self.start = start
        self.end = end
        self.permitted: set[str] = set()
        self.phase = "release"
        self.phase_since = -1.0
        self.held: set[str] = set()
        # Vehicles that must not pass (units whose stop is behind the scene).
        self.stay: set[str] = set()
        # Vehicles the flagger is currently guiding past in the oncoming lane.
        self.passing: set[str] = set()
        # Set each step: no room for through passers, only a unit to its slot.
        self.units_only = False
        # A rush-hour closure (breakdown with a jam): short, rare waves.
        self.rush = False
        self.lane_change_modes: dict[str, int] = {}
        self.speed_modes: dict[str, int] = {}
        self.lifted_stops: dict[str, tuple] = {}
        self.saved_routes: dict[str, list[str]] = {}
        self.saved_types: dict[str, str] = {}
        self.pass_started: dict[str, float] = {}

    @property
    def reverse(self) -> str:
        head, tail = self.edge[2:].split("_")
        return f"E_{tail}_{head}"

    def hold_line(self) -> float:
        """Flagger line in the reverse lane's own coordinates."""
        return max(1.0, 78.0 - self.end - FLAGGER_HOLD_MARGIN)


class JunctionControl:
    def __init__(self, tls_id: str, mode: str, wrecks: list[Box]) -> None:
        self.tls_id = tls_id
        self.mode = mode  # "police" or "dark"
        self.wrecks = wrecks
        self.started = -1.0
        self.allowed: dict[int, str] = {}
        self.axis_of_link: dict[int, str] = {}
        self.link_count = 0
        self.phase = ""
        # Responders crossing live paths: every movement held at red.
        self.hold_until = -1.0
        # incoming lane -> {outgoing edge: movement open under police control}
        self.exits: dict[str, dict[str, bool]] = {}
        self.next_detour_check = -1.0
        self.detours = 0


class RoadControl:
    def __init__(self, traci_module) -> None:
        self.traci = traci_module
        self.closures: dict[str, list[LaneClosure]] = {}
        # Released closures whose guided passers are not back in lane yet.
        self.retiring: list[LaneClosure] = []
        self.junctions: dict[str, JunctionControl] = {}
        # Junctions handed back to their program after an all-red clearance.
        self.releasing: dict[str, float] = {}
        self.slow_lanes: dict[str, float] = {}
        self._links_cache: dict[str, list] = {}
        self._now = 0.0
        self.guard_interventions = 0
        # Vehicles whose opposite-lane manoeuvre is scripted by an incident
        # (the unsafe overtake) are exempt from the head-on guard.
        self.guard_exempt: set[str] = set()
        # vehicle -> (road, road is on its route). Whether a vehicle drives in
        # an oncoming lane can only change when its road changes (moving into
        # the oncoming lane is a change onto the reverse edge), so the route
        # is fetched once per road instead of once per vehicle per step.
        self._on_route_cache: dict[str, tuple[str, bool]] = {}
        # Set by the incident engine: hard closures (road_blocks.RoadBlocks.block / unblock).
        self.on_block = None
        self.on_unblock = None

    # -- lane closures ----------------------------------------------------------

    def close_lane(self, owner: str, edge: str, start: float, end: float, reroute: bool = True, block_entry: bool = False) -> None:
        if block_entry and reroute and self.on_block is not None:
            # An accident: the street is closed at the junction before it (red into it) until the tow has taken
            # the car (road_blocks.py), so no more cars drive up behind the scene and in the way of police and tow.
            self.on_block(owner, edge)
        for existing in self.closures.get(owner, []):
            if existing.edge == edge and existing.start >= 0.0:
                # One closure (one flagger) per closed lane.
                existing.start = min(existing.start, max(0.0, start))
                existing.end = max(existing.end, end)
                return
        closure = LaneClosure(edge, max(0.0, start), end)
        closure.rush = not reroute
        self.closures.setdefault(owner, []).append(closure)
        if not reroute:
            # Rush-hour breakdown: the traffic already on its way keeps coming and queues (no detour advice).
            return
        try:
            self.traci.edge.adaptTraveltime(edge, CLOSED_TRAVEL_TIME)
        except self.traci.exceptions.TraCIException:
            return
        self._reroute_around({edge})

    def penalise_edges(self, owner: str, edges: list[str], travel_time: float) -> None:
        for edge in edges:
            closure = LaneClosure(edge, -1.0, -1.0)
            self.closures.setdefault(owner, []).append(closure)
            try:
                self.traci.edge.adaptTraveltime(edge, travel_time)
            except self.traci.exceptions.TraCIException:
                continue
        self._reroute_around(set(edges))

    def _reroute_around(self, edges: set[str]) -> None:
        for vehicle_id in self.traci.vehicle.getIDList():
            try:
                route = self.traci.vehicle.getRoute(vehicle_id)
                index = self.traci.vehicle.getRouteIndex(vehicle_id)
                road = self.traci.vehicle.getRoadID(vehicle_id)
            except self.traci.exceptions.TraCIException:
                continue
            # Only vehicles that have not entered the closed edge can avoid it;
            # one driving an oncoming lane is never re-routed (SUMO crashes).
            if road in edges or "#" in vehicle_id or road not in route:
                continue
            if any(edge in edges for edge in route[index + 1:]):
                try:
                    self.traci.vehicle.rerouteTraveltime(vehicle_id)
                except self.traci.exceptions.TraCIException:
                    continue

    def slow_lane(self, edge: str, speed: float) -> None:
        lane = f"{edge}_0"
        if lane not in self.slow_lanes:
            try:
                self.slow_lanes[lane] = self.traci.lane.getMaxSpeed(lane)
                self.traci.lane.setMaxSpeed(lane, speed)
            except self.traci.exceptions.TraCIException:
                pass

    def release(self, owner: str) -> None:
        if self.on_unblock is not None:
            self.on_unblock(owner)
        for closure in self.closures.pop(owner, []):
            if closure.passing:
                # A guided passer is still out: the flagger keeps holding the
                # oncoming lane until it is back (stepped as a retiring closure).
                self.retiring.append(closure)
                continue
            self._retire(closure)

    def _retire(self, closure: LaneClosure) -> None:
        self._release_held(closure)
        still_closed = any(other.edge == closure.edge for closures in self.closures.values() for other in closures)
        if not still_closed:
            try:
                self.traci.edge.adaptTraveltime(closure.edge, DEFAULT_TRAVEL_TIME)
            except self.traci.exceptions.TraCIException:
                pass

    def _step_retiring(self, snapshot: dict) -> None:
        for closure in list(self.retiring):
            self._hold_oncoming(closure, snapshot)
            self._return_passers(closure, snapshot, scene_cleared=True)
            if not closure.passing:
                self.retiring.remove(closure)
                self._retire(closure)

    def release_slow_lane(self, edge: str) -> None:
        lane = f"{edge}_0"
        if lane in self.slow_lanes:
            try:
                self.traci.lane.setMaxSpeed(lane, self.slow_lanes.pop(lane))
            except self.traci.exceptions.TraCIException:
                pass

    def step_closures(self, snapshot: dict, excluded: set[str], now: float = 0.0) -> None:
        """Runs every closure's stop/go cycle and supervises any vehicle in an
        oncoming lane (head-on guard)."""
        self._now = now
        self._head_on_guard(snapshot)
        self._step_retiring(snapshot)
        for closures in self.closures.values():
            for closure in closures:
                if closure.start >= 0.0:
                    self._step_flagger(closure, snapshot, excluded)

    def _queue(self, closure: LaneClosure, snapshot: dict, excluded: set[str]) -> list[str]:
        """Vehicles in the closed lane, behind the scene, that want to pass."""
        output = []
        for vehicle_id, data in snapshot.items():
            # A car pulled right up to the block's rear has its nose a little
            # past the start line: it is still waiting to pass, not inside.
            if data["road"] != closure.edge or data["lane_position"] >= closure.start + 1.5:
                continue
            if vehicle_id in closure.stay or (vehicle_id in excluded and "#" not in vehicle_id):
                continue
            try:
                if data["road"] not in self.traci.vehicle.getRoute(vehicle_id):
                    continue
                # A vehicle whose next stop is at the scene joins the block
                # behind it; only through traffic (or a unit heading beyond
                # the scene) is waved past.
                stops = self.traci.vehicle.getStops(vehicle_id, 1) if "#" in vehicle_id else ()
            except self.traci.exceptions.TraCIException:
                continue
            if stops and stops[0].lane.rsplit("_", 1)[0] == closure.edge and stops[0].endPos <= closure.end + 1.0:
                continue
            output.append(vehicle_id)
        return output

    def _region_clear(self, closure: LaneClosure, snapshot: dict) -> bool:
        """Nothing in the opposite lane alongside the scene or between it and
        the flagger line (in either direction of travel)."""
        far = closure.hold_line() - 0.5
        near = 78.0 - closure.start + 6.0
        for data in snapshot.values():
            if data["road"] == closure.reverse and far <= data["lane_position"] <= near:
                return False
        return True

    def _hold_oncoming(self, closure: LaneClosure, snapshot: dict) -> None:
        line = closure.hold_line()
        candidates = []
        for vehicle_id, data in snapshot.items():
            if data["road"] != closure.reverse or vehicle_id in closure.held or vehicle_id in closure.passing:
                continue
            if not self._on_route_cache.get(vehicle_id, (data["road"], True))[1]:
                # A passer driving the oncoming lane is not oncoming traffic.
                continue
            distance = line - data["lane_position"]
            braking = data["speed"] * data["speed"] / (2.0 * 3.5) + 1.0
            if distance > braking:
                candidates.append((distance, vehicle_id))
        if not candidates:
            return
        # Only the first vehicle short of the line gets the flagger stop;
        # the rest queue behind it naturally.
        if any(snapshot.get(vehicle_id, {}).get("road") == closure.reverse for vehicle_id in closure.held):
            return
        _distance, vehicle_id = min(candidates)
        try:
            self.traci.vehicle.setStop(vehicle_id, closure.reverse, pos=line, laneIndex=0, duration=600.0)
            closure.held.add(vehicle_id)
        except self.traci.exceptions.TraCIException:
            pass

    def _release_held(self, closure: LaneClosure) -> None:
        for vehicle_id in closure.held:
            try:
                if self.traci.vehicle.isStopped(vehicle_id):
                    self.traci.vehicle.resume(vehicle_id)
                elif self.traci.vehicle.getStops(vehicle_id):
                    self.traci.vehicle.replaceStop(vehicle_id, 0, "")
            except self.traci.exceptions.TraCIException:
                continue
        closure.held.clear()

    def _step_flagger(self, closure: LaneClosure, snapshot: dict, excluded: set[str]) -> None:
        now = self._now
        if closure.phase_since < 0.0:
            closure.phase_since = now
        elapsed = now - closure.phase_since
        queue = self._queue(closure, snapshot, excluded)
        # Room for passers to merge back, and for held oncoming cars to stand
        # clear of the junction they came out of.
        # Held oncoming cars need room to stand clear of the junction they came
        # out of. Short of the comfortable 12 m the flagger still runs, with
        # brief go phases so only a car or two is ever held near the junction:
        # never waving at all froze scenes near a junction, and the queues
        # behind them gridlocked whole districts.
        tight = closure.hold_line() < 12.0
        room = closure.end < 78.0 - 12.0 and closure.hold_line() >= 1.0
        go_seconds = FLAGGER_GO_SECONDS if not tight else (FLAGGER_TIGHT_GO_SECONDS if closure.hold_line() >= 6.0 else FLAGGER_VERY_TIGHT_GO_SECONDS)
        release_seconds = FLAGGER_RELEASE_SECONDS
        if closure.rush:
            # Rush hour (a breakdown with a jam): the oncoming stream is heavy, so the flagger waves the queue
            # through in short batches with long waits; the queue behind the broken car keeps growing.
            release_seconds, go_seconds = FLAGGER_RELEASE_SECONDS * 2.5, min(go_seconds, FLAGGER_TIGHT_GO_SECONDS)
        # A scene close to the far junction leaves no room to hold oncoming
        # traffic for a stream of passers, but a single short pass by a unit
        # heading for its slot beyond the wrecks (a tow) needs little: it is
        # still waved through, alone.
        closure.units_only = not room and closure.end < 78.0 - 6.0 and closure.hold_line() >= 6.0 and any(
            "#" in vehicle_id and self._stop_beyond(closure, vehicle_id) for vehicle_id in queue)
        if closure.phase == "release":
            if queue and (room or closure.units_only) and elapsed >= release_seconds:
                closure.phase, closure.phase_since = "clearing", now
        elif closure.phase == "clearing":
            self._hold_oncoming(closure, snapshot)
            if self._region_clear(closure, snapshot):
                closure.phase, closure.phase_since = "go", now
            elif elapsed >= FLAGGER_CLEARING_TIMEOUT and not closure.passing:
                self._release_held(closure)
                closure.phase, closure.phase_since = "release", now
        elif closure.phase == "go":
            self._hold_oncoming(closure, snapshot)
            # Only the flagger moves anyone into the oncoming lane (one guided,
            # checked pass at a time); SUMO's own opposite overtaking is never
            # enabled, it would pass at full speed with no controlled return.
            closure.permitted.update(queue)
            if elapsed < go_seconds - FLAGGER_LAST_WAVE_SECONDS:
                self._wave_through(closure, queue, snapshot)
            if (not queue and not closure.passing) or elapsed >= go_seconds:
                self._revoke(closure, snapshot)
                closure.phase, closure.phase_since = "draining", now
        elif closure.phase == "draining":
            self._hold_oncoming(closure, snapshot)
            overtaking = any(data["road"] == closure.reverse and vehicle_id not in closure.held and self._off_route(vehicle_id, data) for vehicle_id, data in snapshot.items())
            # Oncoming traffic is never released while a guided passer is still
            # on its pass (whatever the timeout): it would meet it head-on.
            if not closure.passing and (not overtaking or elapsed >= FLAGGER_DRAIN_TIMEOUT):
                self._release_held(closure)
                closure.phase, closure.phase_since = "release", now
        self._return_passers(closure, snapshot)
        for vehicle_id in list(closure.permitted):
            data = snapshot.get(vehicle_id)
            if data is None:
                closure.permitted.discard(vehicle_id)
            elif data["road"] != closure.edge and data["road"] != closure.reverse:
                closure.permitted.discard(vehicle_id)

    def _wave_through(self, closure: LaneClosure, queue: list[str], snapshot: dict) -> None:
        """The flagger sends the first queued vehicle into the held oncoming
        lane (SUMO's own opposite-overtaking rarely starts from a standstill
        behind a stopped block); the next follows once the entry is clear."""
        if not queue:
            return
        if any(other.edge == closure.reverse and other.start >= 0.0 for closures in self.closures.values() for other in closures):
            # Both lanes are closed (wrecks in each direction): the road is
            # blocked, nobody is waved through, the queue waits for clearing.
            return
        for passer_id in closure.passing:
            data = snapshot.get(passer_id)
            if data is not None and data["road"] == closure.reverse and own_lane_position(closure.edge, *data["front"]) - data["length"] < closure.start + 2.0:
                return
            if "#" in passer_id and self._stop_beyond(closure, passer_id):
                # A unit is still on its way to a slot past the scene: the
                # block is about to grow there, so nobody else is sent out to
                # merge back into that stretch.
                return
        head = max(queue, key=lambda vehicle_id: snapshot[vehicle_id]["lane_position"])
        if 78.0 - closure.end - FLAGGER_HOLD_MARGIN < FLAGGER_MIN_HOLD_ROOM and "#" not in head:
            # The scene reaches almost to the far junction: there is no room in the oncoming lane to stop the cars
            # coming out of that junction before they meet a passer. No car is waved through; the queue waits for the
            # tow (a responder still is: the oncoming check below waits until the lane is clear for it).
            return
        if getattr(closure, "units_only", False) and not ("#" in head and self._stop_beyond(closure, head)):
            return
        data = snapshot[head]
        if head in closure.passing or closure.start - data["lane_position"] > 12.0:
            return
        # The whole manoeuvre must be in an empty oncoming lane (from the
        # flagger line back to 5 m behind the passer), and nothing coming may
        # be unable to stop at the flagger line.
        entry = 78.0 - (data["lane_position"] - data["length"]) + 5.0
        if self._oncoming_conflict(closure, snapshot, head, entry):
            return
        if not self._slot_free(closure, head, snapshot) or not self._room_beyond(closure, head, snapshot):
            return
        straight = self._straight_successor(closure.edge)
        if not straight:
            # No straight continuation (T-junction or city edge): SUMO cannot
            # overtake here, the queue waits for the scene to clear.
            return
        try:
            # The flagger has cleared the oncoming lane: SUMO's own gap checks
            # (which refuse a standing start behind the block) are bypassed for
            # the guided pass only, then restored.
            closure.lane_change_modes[head] = self.traci.vehicle.getLaneChangeMode(head)
            closure.speed_modes[head] = self.traci.vehicle.getSpeedMode(head)
            self.traci.vehicle.setLaneChangeMode(head, 0)
            # Safe speed and acceleration limits stay on (no vehicle can run
            # into the block); only the junction bits that veto an opposite
            # change are dropped while the flagger holds the lane.
            self.traci.vehicle.setSpeedMode(head, 7)
            # SUMO never overtakes past its own next stop: a unit heading for a
            # slot beyond the scene has it lifted for the pass and restored.
            stops = self.traci.vehicle.getStops(head, 1)
            if stops and stops[0].lane.rsplit("_", 1)[0] == closure.edge:
                closure.lifted_stops[head] = (stops[0].lane.rsplit("_", 1)[0], float(stops[0].endPos), int(stops[0].lane.rsplit("_", 1)[1]), float(stops[0].duration))
                self.traci.vehicle.replaceStop(head, 0, "")
            # SUMO only overtakes through the opposite lane when the route
            # continues straight on: for the pass the route is the scene edge
            # plus its straight successor, restored once back in lane.
            route = self.traci.vehicle.getRoute(head)
            index = self.traci.vehicle.getRouteIndex(head)
            closure.saved_routes[head] = list(route[index:])
            self.traci.vehicle.setRoute(head, [closure.edge, straight])
            # The response vTypes (red-light clearance) never change into the
            # opposite lane: a responder passes as its base type, restored
            # once back in lane (lights follow the unit, not the type).
            type_id = self.traci.vehicle.getTypeID(head)
            if type_id.endswith("_response"):
                closure.saved_types[head] = type_id
                self.traci.vehicle.setType(head, type_id[: -len("_response")])
            self.traci.vehicle.setSpeed(head, FLAGGER_PASS_SPEED)
            self.traci.vehicle.changeLaneRelative(head, 1, 1.0)
            closure.pass_started[head] = self._now
        except self.traci.exceptions.TraCIException:
            self._end_pass(closure, head)
            return
        closure.passing.add(head)

    def opposite_lane_vehicles(self) -> set[str]:
        """Vehicles driving in an oncoming lane right now (guided passers and
        scripted overtakers). No route command (reroute, changeTarget,
        setRoute) or moveTo may touch them: SUMO corrupts an opposite-driving
        vehicle's lane state and crashes."""
        driving_opposite = {vehicle_id for vehicle_id, (_road, on_route) in self._on_route_cache.items() if not on_route}
        for closure in [closure for closures in self.closures.values() for closure in closures] + self.retiring:
            driving_opposite |= closure.passing
        return driving_opposite

    def _straight_successor(self, edge: str) -> str:
        try:
            for link in self.traci.lane.getLinks(f"{edge}_0"):
                if str(link[6]) == "s" and not str(link[0]).startswith(":"):
                    return str(link[0]).rsplit("_", 1)[0]
        except self.traci.exceptions.TraCIException:
            pass
        return ""

    def _room_beyond(self, closure: LaneClosure, head: str, snapshot: dict) -> bool:
        """A flagger waves a car only when there is room for it beyond the
        scene: the own lane must be empty from the scene to the flagger line
        (a queue backing up from the next red light fills it)."""
        if self._stop_beyond(closure, head):
            return True  # a unit returns into its own (checked) slot
        limit = closure.end + FLAGGER_HOLD_MARGIN
        return not any(
            other_id != head and other["road"] == closure.edge and other_id not in closure.passing
            and other["lane_position"] > closure.end and other["lane_position"] - other["length"] < limit
            for other_id, other in snapshot.items()
        )

    def _stop_beyond(self, closure: LaneClosure, vehicle_id: str) -> bool:
        try:
            stops = self.traci.vehicle.getStops(vehicle_id, 1)
        except self.traci.exceptions.TraCIException:
            return False
        return bool(stops) and stops[0].lane.rsplit("_", 1)[0] == closure.edge and stops[0].endPos > closure.end

    def _slot_free(self, closure: LaneClosure, head: str, snapshot: dict) -> bool:
        """A unit heading for a slot beyond the scene (a tow) is waved only
        when that slot is empty: it can then always return into it."""
        try:
            stops = self.traci.vehicle.getStops(head, 1)
        except self.traci.exceptions.TraCIException:
            return False
        if not stops or stops[0].lane.rsplit("_", 1)[0] != closure.edge:
            return True
        front = float(stops[0].endPos)
        rear = front - snapshot[head]["length"]
        return not any(
            other_id != head and other["road"] == closure.edge
            and other["lane_position"] > rear - FLAGGER_RETURN_MARGIN and other["lane_position"] - other["length"] < front + FLAGGER_RETURN_MARGIN
            for other_id, other in snapshot.items()
        )

    def _oncoming_conflict(self, closure: LaneClosure, snapshot: dict, head: str, entry: float) -> bool:
        line = closure.hold_line()
        # Oncoming traffic enters the reverse edge from the junction at the end
        # of the closed edge: that junction's internal lanes and every edge
        # feeding it count as approaches.
        junction = closure.edge[2:].split("_")[1]
        for other_id, other in snapshot.items():
            if other_id == head or other_id in closure.passing or other_id in closure.held:
                continue
            road = other["road"]
            braking = other["speed"] * other["speed"] / (2.0 * FLAGGER_HOLD_DECEL) + 1.0
            if "#" in other_id and (road == closure.reverse or road.startswith(f":Junction_{junction.replace('c', '_c')}_")
                                    or (road.endswith(f"_{junction}") and road != closure.edge and 78.0 - other["lane_position"] < 60.0)):
                # A responder (a second tow coming in from the far side, say) is never held by the flagger: while
                # one is in or coming into the oncoming lane, nobody is waved out into it.
                return True
            if road == closure.reverse:
                if other["lane_position"] - other["length"] - 1.0 > entry:
                    continue  # already behind the passer's pull-out point
                if other["lane_position"] >= line - 1.0 or other["lane_position"] + braking >= line - 1.0:
                    return True  # in the manoeuvre area or cannot stop short of it
            elif road.startswith(f":Junction_{junction.replace('c', '_c')}_") and other["speed"] > 0.5:
                if braking >= line:
                    return True
            elif road.endswith(f"_{junction}") and road != closure.edge and other["speed"] > 0.5:
                if (78.0 - other["lane_position"]) + line < braking:
                    return True
        return False

    def _return_passers(self, closure: LaneClosure, snapshot: dict, scene_cleared: bool = False) -> None:
        for vehicle_id in list(closure.passing):
            data = snapshot.get(vehicle_id)
            if data is None:
                closure.passing.discard(vehicle_id)
                continue
            if data["road"] == closure.reverse:
                front = own_lane_position(closure.edge, *data["front"])
                rear = front - data["length"]
                # First, always: never run on towards the held oncoming car
                # (braking estimated conservatively, stopping 4 m short).
                stop_at = closure.end + FLAGGER_HOLD_MARGIN - FLAGGER_PASSER_STOP_SHORT
                if front + data["speed"] ** 2 / (2.0 * 3.0) > stop_at:
                    try:
                        self.traci.vehicle.slowDown(vehicle_id, 0.0, max(0.3, data["speed"] / FLAGGER_HOLD_DECEL))
                    except self.traci.exceptions.TraCIException:
                        pass
                # Once the scene is cleared there is nothing left to pass: the
                # passer steers back wherever its own lane is free.
                if rear <= closure.end + FLAGGER_RETURN_MARGIN and not scene_cleared:
                    continue
                # Ahead it looks one second further: the passer keeps moving
                # while the change executes and must not land inside the gap.
                ahead = front + FLAGGER_RETURN_MARGIN + data["speed"] * 1.0
                own_lane_free = not any(
                    other_id != vehicle_id and other["road"] == closure.edge
                    and other["lane_position"] > rear - FLAGGER_RETURN_MARGIN and other["lane_position"] - other["length"] < ahead
                    for other_id, other in snapshot.items()
                )
                if own_lane_free:
                    try:
                        self.traci.vehicle.changeLaneRelative(vehicle_id, -1, RETURN_REQUEST_SECONDS)
                    except self.traci.exceptions.TraCIException:
                        pass
            elif data["road"] != closure.edge or data["lane_position"] > closure.end + 1.0:
                # Back in its own lane beyond the scene (or gone): done.
                self._end_pass(closure, vehicle_id)
            elif self._now - closure.pass_started.get(vehicle_id, self._now) > 1.5:
                # SUMO did not take the oncoming lane: abort, it stays queued.
                self._end_pass(closure, vehicle_id)

    def _end_pass(self, closure: LaneClosure, vehicle_id: str) -> None:
        closure.passing.discard(vehicle_id)
        closure.pass_started.pop(vehicle_id, None)
        lifted = closure.lifted_stops.pop(vehicle_id, None)
        try:
            saved_type = closure.saved_types.pop(vehicle_id, None)
            if saved_type:
                self.traci.vehicle.setType(vehicle_id, saved_type)
            saved = closure.saved_routes.pop(vehicle_id, None)
            road = self.traci.vehicle.getRoadID(vehicle_id)
            in_own_lane = road.startswith(":") or road in self.traci.vehicle.getRoute(vehicle_id)
            if saved and in_own_lane and not road.startswith(":"):
                if road == saved[0]:
                    self.traci.vehicle.setRoute(vehicle_id, saved)
                else:
                    self.traci.vehicle.changeTarget(vehicle_id, saved[-1])
            if lifted is not None and in_own_lane:
                edge, position, lane_index, duration = lifted
                if road == edge:
                    # The return can end at or past the slot: the stop moves to
                    # the first spot it can still brake for (never behind it,
                    # which SUMO rejects and the unit would drive off the map).
                    speed = self.traci.vehicle.getSpeed(vehicle_id)
                    reachable = self.traci.vehicle.getLanePosition(vehicle_id) + speed * speed / (2.0 * 3.0) + 1.0
                    position = min(max(position, reachable), self.traci.lane.getLength(f"{edge}_{lane_index}") - 0.5)
                self.traci.vehicle.setStop(vehicle_id, edge, pos=position, laneIndex=lane_index, duration=duration)
        except self.traci.exceptions.TraCIException:
            pass
        try:
            self.traci.vehicle.setSpeed(vehicle_id, -1)
            if vehicle_id in closure.speed_modes:
                self.traci.vehicle.setSpeedMode(vehicle_id, closure.speed_modes.pop(vehicle_id))
            if vehicle_id in closure.lane_change_modes:
                self.traci.vehicle.setLaneChangeMode(vehicle_id, closure.lane_change_modes.pop(vehicle_id))
        except self.traci.exceptions.TraCIException:
            closure.lane_change_modes.pop(vehicle_id, None)
            closure.speed_modes.pop(vehicle_id, None)

    def _off_route(self, vehicle_id: str, data: dict) -> bool:
        try:
            return data["road"] not in self.traci.vehicle.getRoute(vehicle_id)
        except self.traci.exceptions.TraCIException:
            return False

    def _revoke(self, closure: LaneClosure, snapshot: dict) -> None:
        # Vehicles already out in the opposite lane finish their manoeuvre;
        # those still in their lane lose the permission.
        for vehicle_id in list(closure.permitted):
            data = snapshot.get(vehicle_id)
            if data is not None and not self._off_route(vehicle_id, data):
                closure.permitted.discard(vehicle_id)

    def set_extent(self, owner: str, edge: str, start: float, end: float, stay: set[str]) -> None:
        for closure in self.closures.get(owner, []):
            if closure.edge == edge and closure.start >= 0.0:
                closure.start = max(0.0, start)
                closure.end = end
                closure.stay = set(stay)

    def flagger_public(self, owner: str) -> list[dict]:
        return [{"edge": closure.edge, "phase": closure.phase, "held": len(closure.held), "passing": len(closure.permitted)} for closure in self.closures.get(owner, []) if closure.start >= 0.0]

    def _head_on_guard(self, snapshot: dict) -> None:
        """Any vehicle driving in an oncoming lane is checked against traffic
        approaching it in that lane; the manoeuvre is aborted (steer back) or,
        if the own lane is occupied, both brake before contact."""
        if len(self._on_route_cache) > len(snapshot) * 2:
            self._on_route_cache = {key: value for key, value in self._on_route_cache.items() if key in snapshot}
        guided = {vehicle_id for closure in [c for closures in self.closures.values() for c in closures] + self.retiring for vehicle_id in closure.passing}
        for vehicle_id, data in snapshot.items():
            road = data["road"]
            if road.startswith(":") or "_" not in road or vehicle_id in self.guard_exempt or vehicle_id in guided:
                continue
            cached = self._on_route_cache.get(vehicle_id)
            if cached is None or cached[0] != road:
                try:
                    cached = (road, road in self.traci.vehicle.getRoute(vehicle_id))
                except self.traci.exceptions.TraCIException:
                    continue
                self._on_route_cache[vehicle_id] = cached
            if cached[1]:
                continue
            # vehicle_id is in the oncoming lane of its route edge.
            hx, hy = math.sin(math.radians(data["angle"])), math.cos(math.radians(data["angle"]))
            for other_id, other in snapshot.items():
                if other_id == vehicle_id or other["road"] != road:
                    continue
                dx, dy = other["front"][0] - data["front"][0], other["front"][1] - data["front"][1]
                ahead = dx * hx + dy * hy
                if ahead <= 0.0 or abs(dx * hy - dy * hx) > 3.0:
                    continue
                closing = data["speed"] + other["speed"]
                if ahead < closing * 2.6 + 9.0:
                    # Abort the manoeuvre: steer back only into free space,
                    # never onto the vehicle being overtaken.
                    try:
                        if self.traci.vehicle.couldChangeLane(vehicle_id, -1):
                            self.traci.vehicle.changeLaneRelative(vehicle_id, -1, RETURN_REQUEST_SECONDS)
                    except self.traci.exceptions.TraCIException:
                        pass
                    if ahead < closing * 1.4 + 6.0:
                        # Brake both at a physical deceleration (≤ 7.5 m/s²) so
                        # the guard can never cause a rear-end behind them.
                        for braking, speed in ((vehicle_id, data["speed"]), (other_id, other["speed"])):
                            try:
                                self.traci.vehicle.slowDown(braking, 0.0, max(0.4, speed / 7.5))
                            except self.traci.exceptions.TraCIException:
                                pass
                    self.guard_interventions += 1
                    break

    def closures_public(self) -> list[dict]:
        output = []
        for owner, closures in self.closures.items():
            for closure in closures:
                if closure.start >= 0.0:
                    output.append({"owner": owner, "edge": closure.edge, "start": round(closure.start, 2), "end": round(closure.end, 2)})
        return output

    # -- junction control -------------------------------------------------------------

    def _links(self, tls_id: str) -> list:
        if tls_id not in self._links_cache:
            self._links_cache[tls_id] = self.traci.trafficlight.getControlledLinks(tls_id)
        return self._links_cache[tls_id]

    def take_junction(self, tls_id: str, mode: str, wrecks: list[Box], now: float) -> JunctionControl:
        control = JunctionControl(tls_id, mode, wrecks)
        control.started = now
        links = self._links(tls_id)
        control.link_count = len(links)
        for index, group in enumerate(links):
            if not group:
                continue
            incoming, _outgoing, via = group[0]
            axis = approach_axis(incoming)
            if axis is None:
                continue
            control.axis_of_link[index] = axis
            try:
                shape = [tuple(point) for point in self.traci.lane.getShape(via)]
            except self.traci.exceptions.TraCIException:
                continue
            if any(polyline_hits_box(shape, wreck, WRECK_PATH_CLEARANCE) for wreck in wrecks):
                continue
            direction = ""
            try:
                for link in self.traci.lane.getLinks(incoming):
                    if link[4] == via:
                        direction = str(link[6])
                        break
            except self.traci.exceptions.TraCIException:
                pass
            if direction in ("l", "L", "t"):
                # No left turns or U-turns under police control: a yielding
                # left-turner waits inside the junction box for the whole
                # incident and then completes its turn into the opposing one.
                # Left-turn traffic is waved onto an open movement instead.
                continue
            control.allowed[index] = "G"
        for index, group in enumerate(links):
            for incoming, outgoing, _via in group:
                exits = control.exits.setdefault(incoming, {})
                out_edge = outgoing.rsplit("_", 1)[0]
                exits[out_edge] = exits.get(out_edge, False) or index in control.allowed
        self.junctions[tls_id] = control
        self._apply_junction(control, now)
        return control

    def update_wrecks(self, tls_id: str, wrecks: list[Box], now: float) -> None:
        if tls_id in self.junctions:
            control = self.junctions.pop(tls_id)
            self.take_junction(tls_id, control.mode, wrecks, now)

    def _apply_junction(self, control: JunctionControl, now: float) -> None:
        if now < control.hold_until:
            state = "r" * control.link_count
            control.phase = "hold_all_red"
        elif control.mode == "dark":
            # A dark signal is an all-way stop: every approach stops, then
            # proceeds when no foe is crossing.
            state = "s" * control.link_count
            control.phase = "dark"
        if now >= control.hold_until and control.mode != "dark":
            cycle = (POLICE_GREEN_SECONDS + POLICE_YELLOW_SECONDS + POLICE_ALL_RED_SECONDS) * 2.0
            t = (now - control.started) % cycle
            half = cycle / 2.0
            axis = "ns" if t < half else "ew"
            local = t if t < half else t - half
            if local < POLICE_GREEN_SECONDS:
                light = "green"
            elif local < POLICE_GREEN_SECONDS + POLICE_YELLOW_SECONDS:
                light = "yellow"
            else:
                light = "red"
            characters = []
            for index in range(control.link_count):
                if light == "red" or control.axis_of_link.get(index) != axis or index not in control.allowed:
                    characters.append("r")
                elif light == "yellow":
                    characters.append("y")
                else:
                    characters.append(control.allowed[index])
            state = "".join(characters)
            control.phase = f"{axis}_{light}"
        try:
            if self.traci.trafficlight.getRedYellowGreenState(control.tls_id) != state:
                self.traci.trafficlight.setRedYellowGreenState(control.tls_id, state)
        except self.traci.exceptions.TraCIException:
            pass

    def hold_junction(self, tls_id: str, until: float) -> None:
        control = self.junctions.get(tls_id)
        if control is not None:
            control.hold_until = until

    def step_junctions(self, now: float, controlled: set[str] | None = None) -> None:
        self._step_releasing(now)
        for control in self.junctions.values():
            self._apply_junction(control, now)
            if control.mode == "police" and now >= control.next_detour_check:
                control.next_detour_check = now + 1.0
                self._direct_blocked_movements(control, controlled or set())

    def _direct_blocked_movements(self, control: JunctionControl, controlled: set[str]) -> None:
        """An officer waves the first vehicle of an approach whose movement is
        closed onto an open one and it continues to its destination from
        there. Without this a vehicle wanting a closed movement waits at the
        line forever and freezes its whole lane (including units heading home
        to the depot beside the scene)."""
        for incoming, exits in control.exits.items():
            open_exits = [edge for edge, is_open in exits.items() if is_open]
            if not open_exits:
                continue
            try:
                queued = self.traci.lane.getLastStepVehicleIDs(incoming)
            except self.traci.exceptions.TraCIException:
                continue
            if not queued:
                continue
            try:
                head = max(queued, key=self.traci.vehicle.getLanePosition)
                if head in controlled:
                    continue
                route = self.traci.vehicle.getRoute(head)
                index = self.traci.vehicle.getRouteIndex(head)
                type_id = self.traci.vehicle.getTypeID(head)
            except self.traci.exceptions.TraCIException:
                continue
            if index + 1 >= len(route) or exits.get(route[index + 1], True):
                continue
            detour = self._detour_route(control, route[index], open_exits, route[-1], type_id)
            if not detour:
                continue
            try:
                self.traci.vehicle.setRoute(head, detour)
                control.detours += 1
            except self.traci.exceptions.TraCIException:
                continue

    def _detour_route(self, control: JunctionControl, current: str, open_exits: list[str], destination: str, type_id: str) -> list[str]:
        """Cheapest route current -> open exit -> destination; a route that
        comes back through this junction must enter it on an open movement."""
        best: list[str] = []
        best_cost = math.inf
        for exit_edge in open_exits:
            if exit_edge == destination:
                return [current, exit_edge]
            try:
                found = self.traci.simulation.findRoute(exit_edge, destination, vType=type_id)
            except self.traci.exceptions.TraCIException:
                continue
            edges = list(found.edges)
            if not edges:
                continue
            cost = float(found.travelTime)
            for first, second in zip(edges, edges[1:]):
                if any(lane.rsplit("_", 1)[0] == first and not exits.get(second, True) for lane, exits in control.exits.items()):
                    # Re-enters on a closed movement: usable only if nothing better.
                    cost += 10000.0
            if cost < best_cost:
                best_cost = cost
                best = [current] + edges
        return best

    def release_junction(self, tls_id: str) -> None:
        """Signals return to their program through an all-red clearance, so
        nothing still crossing on a police-released movement meets traffic
        released by the program's first green."""
        if self.junctions.pop(tls_id, None) is None:
            return
        self.releasing[tls_id] = self._now + JUNCTION_RELEASE_ALL_RED_SECONDS
        try:
            links = self._links(tls_id)
            self.traci.trafficlight.setRedYellowGreenState(tls_id, "r" * len(links))
        except self.traci.exceptions.TraCIException:
            pass

    def _step_releasing(self, now: float) -> None:
        for tls_id, until in list(self.releasing.items()):
            if now < until:
                continue
            del self.releasing[tls_id]
            try:
                self.traci.trafficlight.setProgram(tls_id, "0")
                self.traci.trafficlight.setPhase(tls_id, 0)
            except self.traci.exceptions.TraCIException:
                pass

    def protected_signals(self) -> set[str]:
        return set(self.junctions) | set(self.releasing)

    def junction_public(self, tls_id: str) -> dict:
        control = self.junctions.get(tls_id)
        if control is None:
            return {}
        return {"tls": tls_id, "mode": control.mode, "phase": control.phase, "allowed_links": len(control.allowed), "links": control.link_count}

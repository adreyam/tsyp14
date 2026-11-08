"""SMART-SIG on the live Twin: queue-adaptive traffic lights for every junction.

The city's signal programs are fixed and short (6 s of green per direction): a long queue got exactly the same
green as an empty street, and a Brain command ("ns_green" to drain a jam, a green wave for a fire engine) only jumped
the light to a 6-second phase that was over before the queue moved. This controller runs on top of those programs:

* Adaptive green (every junction on its base program): a green phase is extended, a couple of seconds at a time,
  while its approaches still hold the longer queue and that queue is actually moving; it ends as soon as it has
  served its queue, the cross queue is clearly longer, the traffic beyond is blocked (nobody moves on green) or it
  reaches MAX_GREEN. The program's own amber and all-red phases run unchanged, so no conflicting green ever shows.
* Brain holds: ns_green / ew_green hold that direction green for up to HOLD_SECONDS (the light goes through its
  amber first when the other direction is green); afterwards the junction stays biased towards it (its queue counts
  double) until reset_cycle. all_red holds all red (up to ALL_RED_SECONDS).
* Hands off: junctions under incident control, junctions an emergency unit has taken (emergency_priority.py,
  "online" program), and anything not on its base program "0" are left alone.
"""

from __future__ import annotations

MIN_GREEN = 6.0
MAX_GREEN = 50.0                # as the axis engine's sumo-rl signals (max_green=50)
SECONDS_PER_CAR = 1.2   # extra green per waiting car, weighted by its share of the junction
CROSS_RATIO = 1.5       # yield early when the cross street holds this many times the pressure
MIN_TURN_GREEN = 3.0
MAX_TURN_GREEN = 12.0
EXTEND_SECONDS = 2.0
CHECK_SECONDS = 0.5
HOLD_SECONDS = 45.0
ALL_RED_SECONDS = 90.0
# A green whose vehicles do not move (blocked exit) ends after this long.
STALLED_SECONDS = 4.0
NS_GREEN, ALL_RED, EW_GREEN = 0, 2, 6
EVACUATION_HOLD_SECONDS = 8.0
RESERVE_MIN_QUEUE = 3     # a road still holding this many cars after its left-turn phase gets its straight green again
EVACUATION_HOLD_MAX = 20.0


class SmartSignals:
    def __init__(self, traci_module) -> None:
        self.traci = traci_module
        self.holds: dict[str, dict] = {}        # tls -> {"phase", "until", "bias"}
        self.bias: dict[str, str] = {}           # tls -> "ns" | "ew"
        self.phase_seen: dict[str, tuple[int, float]] = {}
        self.stalled_since: dict[str, float] = {}
        self._lanes: dict[str, list[str]] = {}
        self._links: dict[str, list] = {}
        self._states: dict[str, list[str]] = {}
        self._next_check = 0.0
        # Closed streets (road_blocks.py): tls -> owner -> link indices that must stay red (movements into the street).
        self.closed: dict[str, dict[str, set[int]]] = {}
        self._closure_built: dict[str, frozenset] = {}
        self.extensions = 0
        self.left_cut = 0
        self.reserves = 0
        self.deserved: dict[str, float] = {}     # tls -> green the current phase has earned (s), for display
        self.enabled = True
        self._evacuation_turn: dict[str, tuple[int, float]] = {}   # tls -> (phase, end) of its last evacuation hold
        self._reserved: dict[str, int] = {}      # tls -> main green re-served after its left-turn phase (this cycle)

    # -- Brain / operator commands ---------------------------------------------------------

    def command(self, tls: str, name: str, now: float) -> None:
        if name in ("ns_green", "ew_green"):
            phase = NS_GREEN if name == "ns_green" else EW_GREEN
            self.holds[tls] = {"phase": phase, "until": now + HOLD_SECONDS}
            self.bias[tls] = name[:2]
            self._steer(tls, phase, now)
        elif name == "all_red":
            self.holds[tls] = {"phase": ALL_RED, "until": now + ALL_RED_SECONDS}
            self._to_all_red(tls, now)
        elif name == "reset_cycle":
            self.holds.pop(tls, None)
            self.bias.pop(tls, None)
            if self._phase(tls) == ALL_RED:
                self.traci.trafficlight.setPhase(tls, ALL_RED)      # an all-red hold ends now (1 s clearance)
        else:                                                      # ambers: a direct jump, as before
            self.holds.pop(tls, None)

    def evacuation_green(self, tls: str, link_index: int, now: float, seconds: float = EVACUATION_HOLD_SECONDS) -> bool:
        """Cars leaving a closed zone (road_blocks.py) get the green for their movement, as a short hold through the
        normal amber; a hold the Brain or the operator asked for is left alone."""
        hold = self.holds.get(tls)
        if hold is not None and not hold.get("evacuation"):
            return False
        try:
            if self.traci.trafficlight.getProgram(tls) not in ("0", "closure"):
                return False                                       # emergency priority has this junction now
        except self.traci.exceptions.TraCIException:
            return False
        states = self._phase_states(tls)
        target = next((phase for phase in (NS_GREEN, EW_GREEN) if phase < len(states) and link_index < len(states[phase])
                       and states[phase][link_index] in "Gg"), None)
        if target is None:
            target = next((phase for phase, state in enumerate(states) if link_index < len(state) and state[link_index] in "Gg"), None)
        if target is None:
            return False
        if hold is not None and now < hold["until"]:
            if hold["phase"] != target:
                return False                                       # another exit's turn first; this one is next
            if now - hold["since"] >= EVACUATION_HOLD_MAX:
                return False                                       # long enough: the others get their turn
            hold["until"] = min(max(hold["until"], now + seconds), hold["since"] + EVACUATION_HOLD_MAX)
            self._evacuation_turn[tls] = (target, hold["until"])
            self._steer_safely(tls, target, now)
            return True
        last = self._evacuation_turn.get(tls)
        if last is not None and last[0] == target and now - last[1] < seconds:
            return False                                       # it just had its turn: let the other directions move
        self.holds[tls] = {"phase": target, "until": now + seconds, "since": now, "evacuation": True}
        self._evacuation_turn[tls] = (target, now + seconds)
        return self._steer_safely(tls, target, now)

    def _steer_safely(self, tls: str, target: int, now: float) -> bool:
        try:
            self._steer(tls, target, now)
        except self.traci.exceptions.TraCIException:
            self.holds.pop(tls, None)
            return False
        return True

    def _to_all_red(self, tls: str, now: float) -> None:
        """All red through the amber of whatever is green (never green straight to red)."""
        phase = self._phase(tls)
        states = self._phase_states(tls)
        state = states[phase] if 0 <= phase < len(states) else ""
        if any(c in "Gg" for c in state) and phase + 1 < len(states):
            self.traci.trafficlight.setPhase(tls, phase + 1)
        elif "y" in state:
            return
        else:
            if phase != ALL_RED:
                self.traci.trafficlight.setPhase(tls, ALL_RED)
            self.traci.trafficlight.setPhaseDuration(tls, max(0.5, self.holds[tls]["until"] - now))

    def _steer(self, tls: str, target: int, now: float) -> None:
        """Towards a held green: kept if already showing, through the amber if the other direction is green."""
        phase = self._phase(tls)
        states = self._phase_states(tls)
        if phase == target:
            self.traci.trafficlight.setPhaseDuration(tls, max(0.5, self.holds[tls]["until"] - now))
        elif 0 <= phase < len(states) and any(c in "Gg" for c in states[phase]) and phase + 1 < len(states):
            self.traci.trafficlight.setPhase(tls, phase + 1)       # the green that is on goes amber first
        elif 0 <= phase < len(states) and not any(c in "Gg" for c in states[phase]):
            next_phase = (phase + 1) % len(states)
            if next_phase < len(states) and any(c in "Gg" for c in states[next_phase]) and next_phase != target:
                # an amber / all-red about to open the wrong green: open the held one instead
                if all(c in "ry" for c in states[phase]) and "y" not in states[phase]:
                    self.traci.trafficlight.setPhase(tls, target)
                    self.traci.trafficlight.setPhaseDuration(tls, max(0.5, self.holds[tls]["until"] - now))

    # -- closed streets -----------------------------------------------------------------------

    def close_into(self, owner: str, edge: str) -> None:
        """Red, for as long as the street is closed, for every movement that turns into it."""
        for tls in self.traci.trafficlight.getIDList():
            links = self._controlled_links(tls)
            indices = {index for index, group in enumerate(links)
                       if group and group[0][1].rsplit("_", 1)[0] == edge}
            if indices:
                self.closed.setdefault(tls, {}).setdefault(owner, set()).update(indices)
                self._apply_closure(tls)

    def open_owner(self, owner: str) -> None:
        for tls in [tls for tls, owners in self.closed.items() if owner in owners]:
            self.closed[tls].pop(owner, None)
            if not self.closed[tls]:
                del self.closed[tls]
            self._apply_closure(tls)

    def _closed_links(self, tls: str) -> frozenset:
        return frozenset(index for indices in self.closed.get(tls, {}).values() for index in indices)

    def _apply_closure(self, tls: str) -> None:
        """Runs the junction on a copy of its program with the closed movements red in every phase (program
        "closure"), or back on its program "0" when nothing is closed. Left alone while another controller (incident
        control, emergency priority: any other program) has the junction; re-applied by step() once it is back."""
        try:
            program = self.traci.trafficlight.getProgram(tls)
            if program not in ("0", "closure"):
                return
            phase = self._phase(tls)
            closed = self._closed_links(tls)
            if not closed:
                if program == "closure":
                    self.traci.trafficlight.setProgram(tls, "0")
                    self.traci.trafficlight.setPhase(tls, phase)
                self._closure_built.pop(tls, None)
                return
            if self._closure_built.get(tls) != closed:
                base = [logic for logic in self.traci.trafficlight.getAllProgramLogics(tls) if logic.programID == "0"][0]
                phases = []
                for item in base.phases:
                    state = "".join("r" if index in closed else signal for index, signal in enumerate(item.state))
                    phases.append(self.traci.trafficlight.Phase(item.duration, state, item.minDur, item.maxDur))
                self.traci.trafficlight.setProgramLogic(tls, self.traci.trafficlight.Logic("closure", base.type, phase, phases))
                self._closure_built[tls] = closed
            if program != "closure":
                self.traci.trafficlight.setProgram(tls, "closure")
            self.traci.trafficlight.setPhase(tls, phase)
        except (self.traci.exceptions.TraCIException, IndexError):
            pass

    # -- every step ------------------------------------------------------------------------

    def step(self, now: float, skip: set[str]) -> None:
        if not self.enabled or now < self._next_check:
            return
        self._next_check = now + CHECK_SECONDS
        for tls in self.traci.trafficlight.getIDList():
            try:
                self._step_one(tls, now, skip)
            except self.traci.exceptions.TraCIException:
                continue

    def _step_one(self, tls: str, now: float, skip: set[str]) -> None:
        if tls in skip:
            self.phase_seen.pop(tls, None)
            return
        program = self.traci.trafficlight.getProgram(tls)
        if program == "0" and tls in self.closed:
            self._apply_closure(tls)                                # given back by priority / incident control
            program = self.traci.trafficlight.getProgram(tls)
        elif program == "closure" and tls not in self.closed:
            self._apply_closure(tls)
            program = self.traci.trafficlight.getProgram(tls)
        if program not in ("0", "closure"):
            self.phase_seen.pop(tls, None)
            return
        phase = self._phase(tls)
        seen = self.phase_seen.get(tls)
        if seen is None or seen[0] != phase:
            self.phase_seen[tls] = (phase, now)
            self.stalled_since.pop(tls, None)
            seen = self.phase_seen[tls]
            if phase in (NS_GREEN, EW_GREEN) and self._reserved.get(tls, phase) != phase:
                self._reserved.pop(tls, None)                     # the cross street's turn: a new cycle
        elapsed = now - seen[1]
        hold = self.holds.get(tls)
        if hold is not None:
            if now < hold["until"]:
                if hold["phase"] == ALL_RED:
                    self._to_all_red(tls, now)
                else:
                    self._steer(tls, hold["phase"], now)
                return
            self.holds.pop(tls, None)                              # the hold is over; a green bias may remain
        states = self._phase_states(tls)
        if phase in (NS_GREEN + 5, EW_GREEN + 5) and phase < len(states) and not any(c in "Gg" for c in states[phase]):
            self._maybe_reserve(tls, phase - 5, states)            # end of a left-turn phase: same road again?
            return
        if not (0 <= phase < len(states)) or not any(c in "Gg" for c in states[phase]):
            return                                                 # amber / all red: the program's timing
        # Max-pressure: a movement's pressure is the queue waiting to use it minus the queue already standing on the
        # lane it leads to. A green is worth keeping while its pressure is the higher one; a green whose exit lanes
        # are full (no pressure: the cars could not move anyway) hands over to the cross street.
        links = self._controlled_links(tls)
        halting = {}

        def queue(lane: str) -> int:
            if lane not in halting:
                halting[lane] = self.traci.lane.getLastStepHaltingNumber(lane)
            return halting[lane]

        own_pressure, cross_pressure, own_queue = 0.0, 0.0, 0
        counted_in: set[tuple[str, bool]] = set()
        for index, group in enumerate(links):
            if not group or index >= len(states[phase]):
                continue
            incoming, outgoing = group[0][0], group[0][1]
            if index in self._closed_links(tls):
                continue                                           # a closed street: nobody may go there
            green = states[phase][index] in "Gg"
            pressure = queue(incoming) - queue(outgoing) * 0.8
            if green:
                own_pressure += pressure
                if (incoming, True) not in counted_in:
                    own_queue += queue(incoming)
                    counted_in.add((incoming, True))
            else:
                cross_pressure += pressure
        own = {group[0][0] for index, group in enumerate(links) if group and index < len(states[phase]) and states[phase][index] in "Gg"}
        direction = "ns" if phase < EW_GREEN else "ew"
        if self.bias.get(tls) == direction:
            own_pressure = own_pressure * 2 + 2
        elif tls in self.bias:
            cross_pressure = cross_pressure * 2 + 2
        turn = sum(1 for c in states[phase] if c in "Gg") <= 2
        min_green, max_green = (MIN_TURN_GREEN, MAX_TURN_GREEN) if turn else (MIN_GREEN, MAX_GREEN)
        moving = any(self.traci.lane.getLastStepMeanSpeed(lane) > 1.0 for lane in own if self.traci.lane.getLastStepVehicleNumber(lane))
        # (own: the approach lanes this green serves)
        if own_queue and not moving and elapsed >= min_green:
            stalled = self.stalled_since.setdefault(tls, now)
        else:
            self.stalled_since.pop(tls, None)
            stalled = now
        blocked = now - stalled >= STALLED_SECONDS
        # Longer green for the road with more cars: the green this phase deserves grows with the cars on its
        # approaches (SECONDS_PER_CAR each, as they discharge) weighted by their share of all the cars waiting at the
        # junction, so two equal streets alternate briskly while a jammed street keeps its green up to MAX_GREEN.
        # Recomputed every half second: the green ends once its cars have gone, when the cross street becomes the
        # longer one, or when its exit is blocked (cars could not move anyway).
        own_lanes = {group[0][0] for index, group in enumerate(links) if group and index < len(states[phase])
                     and states[phase][index] in "Gg" and index not in self._closed_links(tls)}
        cross_lanes = {group[0][0] for group in links if group} - own_lanes
        own_cars = sum(self.traci.lane.getLastStepVehicleNumber(lane) for lane in own_lanes)
        cross_cars = sum(self.traci.lane.getLastStepVehicleNumber(lane) for lane in cross_lanes)
        if self.bias.get(tls) == direction:
            own_cars *= 2                                          # the Brain asked for this road (a jam)
        elif tls in self.bias:
            cross_cars *= 2
        deserved = min(max_green, min_green + SECONDS_PER_CAR * own_cars * own_cars / (own_cars + cross_cars + 1.0))
        self.deserved[tls] = round(deserved, 1)
        extend = (own_cars >= 1 and own_pressure > 0 and not blocked and elapsed < deserved
                  and not (elapsed >= min_green + 4.0 and cross_pressure > CROSS_RATIO * max(own_pressure, 1.0)))
        if not turn and elapsed >= 3.0 and self._left_turner_blocks(tls, phase, states, links, own_lanes):
            # The car at the head of a single-lane approach waits to turn left on red: nobody behind it can use this
            # green. Go to the left-turn phase now (the straight green comes back after it, see _maybe_reserve).
            self.traci.trafficlight.setPhaseDuration(tls, 0.1)
            self.left_cut += 1
            return
        if extend and elapsed + 0.01 >= min_green - EXTEND_SECONDS:
            self.traci.trafficlight.setPhaseDuration(tls, EXTEND_SECONDS)
            self.extensions += 1
        elif not turn and not extend and elapsed >= 3.0 and cross_pressure >= 2 and (own_pressure <= 0 or blocked):
            # nothing (or nothing that can move) to serve here while the cross street waits: hand over now
            self.traci.trafficlight.setPhaseDuration(tls, 0.1)

    def _left_turner_blocks(self, tls: str, phase: int, states: list[str], links: list, own_lanes: set[str]) -> bool:
        turn_phase = phase + 3
        if turn_phase >= len(states):
            return False
        for lane in own_lanes:
            try:
                vehicles = self.traci.lane.getLastStepVehicleIDs(lane)
                if len(vehicles) < 2:
                    continue                                       # nobody stuck behind it
                head = vehicles[-1]
                if self.traci.vehicle.getSpeed(head) > 0.3:
                    continue
                upcoming = self.traci.vehicle.getNextTLS(head)
                if not upcoming or upcoming[0][0] != tls or upcoming[0][2] > 6.0:
                    continue
                index = upcoming[0][1]
                if states[phase][index] in "rR" and states[turn_phase][index] in "Gg":
                    return True
            except self.traci.exceptions.TraCIException:
                continue
        return False

    def _maybe_reserve(self, tls: str, main: int, states: list[str]) -> None:
        """After a road's left-turn phase: if that road still has the longer queue (its straight cars were stuck
        behind left-turners), it gets its straight green again before the cross street, once per cycle."""
        if self._reserved.get(tls) == main:
            return
        cross = EW_GREEN if main == NS_GREEN else NS_GREEN
        links = self._controlled_links(tls)

        def cars(phase: int) -> int:
            lanes = {group[0][0] for index, group in enumerate(links) if group and index < len(states[phase])
                     and states[phase][index] in "Gg" and index not in self._closed_links(tls)}
            return max([self.traci.lane.getLastStepVehicleNumber(lane) for lane in lanes] or [0])

        own, other = cars(main), cars(cross)
        if own >= RESERVE_MIN_QUEUE and own > other:
            self._reserved[tls] = main
            self.traci.trafficlight.setPhase(tls, main)
            self.reserves += 1

    # -- network data ---------------------------------------------------------------------

    def _phase(self, tls: str) -> int:
        return int(self.traci.trafficlight.getPhase(tls))

    def _controlled_links(self, tls: str) -> list:
        if tls not in self._links:
            self._links[tls] = list(self.traci.trafficlight.getControlledLinks(tls))
        return self._links[tls]

    def _controlled_lanes(self, tls: str) -> list[str]:
        if tls not in self._lanes:
            self._lanes[tls] = list(self.traci.trafficlight.getControlledLanes(tls))
        return self._lanes[tls]

    def _phase_states(self, tls: str) -> list[str]:
        if tls not in self._states:
            logics = [logic for logic in self.traci.trafficlight.getAllProgramLogics(tls) if logic.programID == "0"]
            self._states[tls] = [phase.state for phase in logics[0].phases] if logics else []
        return self._states[tls]

    def public(self) -> dict:
        return {"holds": {tls: {"phase": hold["phase"], "until": round(hold["until"], 1)} for tls, hold in self.holds.items()},
                "bias": dict(self.bias), "extensions": self.extensions}

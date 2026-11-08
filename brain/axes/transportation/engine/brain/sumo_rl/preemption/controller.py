"""Signal-side controller: turns a priority request into a safe green and gives the signal back afterwards."""

from typing import Dict, List, Optional

from .config import PreemptionConfig
from .messages import PriorityRequest
from .receiver import RoadsideReceiver

IDLE, TRANSITION, HOLD, RECOVERY = "IDLE", "TRANSITION", "HOLD", "RECOVERY"
GREEN = "Gg"


class PreemptionController:
    """One per intersection. It only uses phases that already exist in the signal program, and every green that
    stops is shown yellow for `yellow_time` first, so no conflicting greens are ever shown together.

        IDLE --(request closer than lead_time)--> [yellow] TRANSITION --> HOLD --(requests gone)--> [yellow] RECOVERY --> IDLE
                                                                           \\--(another approach waits)--> TRANSITION
    """

    def __init__(self, ts, sumo, receiver: RoadsideReceiver, config: PreemptionConfig, fixed_ts: bool, log):
        self.ts = ts
        self.id = ts.id
        self.sumo = sumo
        self.receiver = receiver
        self.config = config
        self.fixed_ts = fixed_ts
        self.log = log
        self.yellow_time = float(ts.yellow_time)

        tl = sumo.trafficlight
        current = tl.getProgram(self.id)
        # after setRedYellowGreenState SUMO runs a one-phase "online" program: never use that one as the schedule
        logics = [lg for lg in tl.getAllProgramLogics(self.id) if lg.programID != "online"] or tl.getAllProgramLogics(self.id)
        logic = next((lg for lg in logics if lg.programID == current), max(logics, key=lambda lg: len(lg.phases)))
        self.program_id = logic.programID
        self.phase_states = [ph.state for ph in logic.phases]
        self.phase_durations = [float(ph.duration) for ph in logic.phases]
        self.cycle = sum(self.phase_durations)
        self.cycle_pos0 = 0.0  # where the background program was in its cycle when the preemption began
        self.t_start = 0.0
        self.green_states: List[str] = []
        self.green_phase_index: Dict[str, int] = {}
        for i, ph in enumerate(logic.phases):
            s = ph.state
            if "y" in s.lower() or not any(c in GREEN for c in s):
                continue
            if s not in self.green_states:
                self.green_states.append(s)
                self.green_phase_index[s] = i

        links = tl.getControlledLinks(self.id)
        self.link_lane = [lk[0][0] if lk else None for lk in links]

        self.mode = IDLE
        self.target: Optional[str] = None
        self.resume_state: Optional[str] = None
        self.t0 = 0.0
        self.hold_start = 0.0
        self.last_needed = 0.0
        self.count = 0  # number of preemptions
        self.total_hold = 0.0

    # ------------------------------------------------------------------ helpers
    def _state(self) -> str:
        return self.sumo.trafficlight.getRedYellowGreenState(self.id)

    def _set(self, state: str) -> None:
        self.sumo.trafficlight.setRedYellowGreenState(self.id, state)

    def _pick_target(self, link_index: int) -> Optional[str]:
        """The existing green phase that gives green to the link and the most other links."""
        ok = [s for s in self.green_states if link_index < len(s) and s[link_index] in GREEN]
        return max(ok, key=lambda s: sum(c in GREEN for c in s)) if ok else None

    @staticmethod
    def _yellow_between(cur: str, tgt: str) -> str:
        """Links that are green now and not green in the target turn yellow; the others keep their colour."""
        return "".join("y" if c in GREEN and t not in GREEN else c for c, t in zip(cur, tgt))

    def _queue_delay(self, link_index: int) -> float:
        """Seconds the queue standing in front of the stop line needs to discharge once the signal is green."""
        lane = self.link_lane[link_index] if link_index < len(self.link_lane) else None
        if lane is None:
            return 0.0
        try:
            halting = self.sumo.lane.getLastStepHaltingNumber(lane)
        except Exception:
            return 0.0
        return halting * self.config.discharge_headway

    def _ready(self, now: float, factor: float = 1.0) -> List[PriorityRequest]:
        """Requests that must be served now: the vehicle is close enough that the green has to start.

        Start time = yellow + queue discharge + safety margin before the vehicle reaches the stop line.
        factor > 1 widens the window; it is used to keep an already given green while the vehicle slows down."""
        reqs = [
            r
            for r in self.receiver.active_requests(now)
            if r.eta <= factor * (self.config.lead_time + self.yellow_time + self._queue_delay(r.link_index))
        ]
        return sorted(reqs, key=lambda r: (-r.priority, r.eta))

    def _covered(self, req: PriorityRequest, state: str) -> bool:
        return req.link_index < len(state) and state[req.link_index] in GREEN

    # ---------------------------------------------------------------------- step
    def step(self, now: float) -> None:
        if self.mode == IDLE:
            self._step_idle(now)
        elif self.mode == TRANSITION:
            if now - self.t0 >= self.yellow_time:
                self._set(self.target)
                self.mode, self.hold_start, self.last_needed = HOLD, now, now
                self.log(now, self.id, "GREEN_HELD", state=self.target)
        elif self.mode == HOLD:
            self._step_hold(now)
        elif self.mode == RECOVERY:
            if now - self.t0 >= self.yellow_time:
                self._handover(now)

    def _step_idle(self, now: float) -> None:
        ready = self._ready(now)
        if not ready:
            return
        cur = self._state()
        if "y" in cur.lower():  # let a running yellow finish first
            return
        req = ready[0]
        target = self._pick_target(req.link_index)
        if target is None:
            return
        self.ts.preempted = True
        self.count += 1
        self.resume_state = self._resume_state_normal()
        self.t_start, self.cycle_pos0 = now, self._program_cycle_position(now)
        self.log(now, self.id, "PREEMPT_START", vehicle=req.vehicle_id, eta=round(req.eta, 1), distance=round(req.distance, 1))
        if cur[req.link_index] in GREEN:
            self.target = cur  # already green for the vehicle: just keep it
            self._set(cur)
            self.mode, self.hold_start, self.last_needed = HOLD, now, now
            self.log(now, self.id, "GREEN_HELD", state=cur, note="already green")
        else:
            self._begin_transition(now, cur, target)

    def _begin_transition(self, now: float, cur: str, target: str) -> None:
        self.target = target
        self._set(self._yellow_between(cur, target))
        self.mode, self.t0 = TRANSITION, now
        self.log(now, self.id, "YELLOW_CLEARING", frm=cur, to=target)

    def _step_hold(self, now: float) -> None:
        reqs = self._ready(now, factor=2.0)
        if any(self._covered(r, self.target) for r in reqs):
            self.last_needed = now
        if now - self.last_needed < self.config.clearance_time and now - self.hold_start <= self.config.max_hold:
            return
        waiting = [r for r in reqs if not self._covered(r, self.target)]
        if waiting and now - self.hold_start <= self.config.max_hold:
            new_target = self._pick_target(waiting[0].link_index)
            if new_target is not None:
                self._begin_transition(now, self.target, new_target)
                return
        # release
        self.total_hold += now - self.hold_start
        resume = self.resume_state if self.resume_state is not None else self.target
        if self.fixed_ts:
            # rejoin the background program exactly where it would be now (keeps the coordination with the
            # neighbouring signals); links that are green now and not green there first show yellow
            idx, _ = self._background_position(now)
            resume = self.phase_states[idx]
            if self._yellow_between(self.target, resume) == self.target:
                self._handover(now)
                return
        if resume != self.target:
            self._set(self._yellow_between(self.target, resume))
            self.mode, self.t0 = RECOVERY, now
            self.resume_state = resume
            self.log(now, self.id, "YELLOW_RECOVERY", frm=self.target, to=resume)
        else:
            self._handover(now)

    def _program_cycle_position(self, now: float) -> float:
        """Seconds into the signal program's cycle at time `now` (read from the running program)."""
        tl = self.sumo.trafficlight
        try:
            idx = tl.getPhase(self.id)
            elapsed = self.phase_durations[idx] - (tl.getNextSwitch(self.id) - now)
            return sum(self.phase_durations[:idx]) + max(0.0, min(elapsed, self.phase_durations[idx]))
        except Exception:
            return 0.0

    def _background_position(self, now: float):
        """(phase index, seconds left in it) where the program would be now had it never been interrupted."""
        pos = (self.cycle_pos0 + (now - self.t_start)) % self.cycle
        acc = 0.0
        for i, d in enumerate(self.phase_durations):
            if pos < acc + d:
                return i, acc + d - pos
            acc += d
        return 0, self.phase_durations[0]

    def _resume_state_normal(self) -> Optional[str]:
        ts = self.ts
        if not self.fixed_ts and hasattr(ts, "all_phases"):
            return ts.all_phases[ts.green_phase].state
        return None

    def _handover(self, now: float) -> None:
        ts = self.ts
        if self.fixed_ts:
            idx, left = self._background_position(now)
            tl = self.sumo.trafficlight
            tl.setProgram(self.id, self.program_id)
            tl.setPhase(self.id, idx)
            tl.setPhaseDuration(self.id, left)
        else:
            self._set(self.resume_state if self.resume_state is not None else self.target)
            ts.is_yellow = False
            ts.time_since_last_phase_change = 0
        ts.preempted = False
        self.mode, self.target, self.resume_state = IDLE, None, None
        self.log(now, self.id, "PREEMPT_END")

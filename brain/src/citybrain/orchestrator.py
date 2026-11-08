"""
Orchestrator: one entry point for every input, whatever the source.

    ingest(event)
      1. classify      which agents want it (by Twin family or incident kind); several axes may fire on one event;
                       an event no agent handles is logged as "unrouted" once per device and dropped
      2. trust gate    ask the Guardian (mock or real). trusted/released -> normal; suspect or no verdict -> degraded
                       (only reversible actions); untrusted/quarantined -> the event is not used, the reason is logged,
                       and the device is listed as blocked on the dashboard
      3. agents        each wanted agent runs detect -> decide -> act -> explain
      4. situations    findings open, update or close situations (the dashboard top bar)
      5. arbitration   all actions of this event are checked together:
                         - an action on a quarantined actuator is refused
                         - load shedding in a zone with an active emergency is vetoed (emergency need wins); a power cut
                           that serves the emergency (feeder isolation for firefighters) is allowed
                         - two axes on the same resource: policy.axis_priority decides; a resource commanded by a
                           higher-priority axis stays locked for policy.resource_lock_s
                         - the same command on the same actuator is not repeated within policy.cooldown_s
                         - in degraded mode, irreversible actions are withheld
      6. act + audit   the surviving actions go to the TwinSource; the Twin's answer comes back on the same trace
    A2A                agents ask each other through self.a2a (citybrain/a2a.py): a task is delivered to its receiver in
                       the same pass, its plans join the same arbitration, and an action that carries the task id
                       (parameters.a2a_task) completes or fails the task with the Twin's answer
    tick()             every Brain loop: the fleet view is refreshed and each agent's tick() runs (green waves follow
                       the units), on a "heartbeat" event of the Brain itself
    ingest_raw(data)   validates untrusted input (pushed over HTTP) and answers with a readable error
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .agents import AGENT_CLASSES
from .a2a import A2ABus
from .agents.base import CityView, Finding, Situation, zone_from_service_id
from .audit import Audit
from .config import Config
from .contracts import AXES, SEVERITY_RANK, Action, Event, Origin, Verdict, explain_validation, iso, utc_now
from .guardian import GuardianSource
from .sources import TwinSource

UNDO_COMMANDS = {"restore_power", "restore_water", "auto", "reset_cycle", "open"}
STATUS_RANK = {"active": 0, "contained": 1, "blocked": 2, "resolved": 3}


class Orchestrator:
    def __init__(self, cfg: Config, source: TwinSource, guardian: GuardianSource, audit: Audit):
        self.cfg = cfg
        self.source = source
        self.guardian = guardian
        self.audit = audit
        self.city = CityView(cfg)
        self.agents = {a: cls(cfg) for a, cls in AGENT_CLASSES.items()}
        self.a2a = A2ABus(audit, source.name)
        for ag in self.agents.values():
            self.a2a.register(ag)
            ag.view = self.city
        self.priority = {a: i for i, a in enumerate(cfg.policy.axis_priority)}
        self.sent: Dict[tuple, float] = {}            # action key -> monotonic time sent
        self.desired: Dict[str, tuple] = {}           # resource -> key of the last command sent (idempotence)
        self.locks: Dict[str, Tuple[str, float, str]] = {}   # resource -> (axis, until, situation)
        self.unrouted: Dict[str, float] = {}
        self.blocked: Dict[str, float] = {}
        self.actions: List[dict] = []                 # recent actions (map)
        self.events: List[dict] = []                  # recent notable events (map)
        self.stats = {"events": 0, "used": 0, "ignored": 0, "malformed": 0, "decisions": 0, "actions": 0,
                      "refused": 0, "conflicts": 0, "by_axis": {a: 0 for a in AXES}}
        self.scenario_id: Optional[str] = None

    def fresh_start(self) -> None:
        """A Test Book run starts from a clean Brain: new agents (nothing remembered from an earlier run, e.g. a flood
        still marked as handled), a new A2A bus, no situation, lock, cooldown or desired state left over. The city view
        (readings, registry, fleet) and the audit log are kept."""
        self.agents = {a: cls(self.cfg) for a, cls in AGENT_CLASSES.items()}
        self.a2a = A2ABus(self.audit, self.source.name)
        for ag in self.agents.values():
            self.a2a.register(ag)
            ag.view = self.city
        self.city.situations.clear()
        self.city.owned.clear()
        self.sent.clear()
        self.desired.clear()
        self.locks.clear()
        self.blocked.clear()

    # ------------------------------------------------------------------ input validation
    def ingest_raw(self, data: Any, pushed: bool = True) -> Tuple[bool, str]:
        """Validate untrusted input. Returns (accepted, message)."""
        if isinstance(data, (bytes, str)):
            try:
                data = json.loads(data)
            except (ValueError, UnicodeDecodeError) as e:
                return self._malformed(f"not JSON: {e}")
        if isinstance(data, dict) and data.get("schema") == "citybrain.verdict/1":
            try:
                v = Verdict(**data)
            except ValidationError as e:
                return self._malformed(explain_validation(e, "verdict"))
            self.guardian.push(v)
            self.audit.write("verdict", v.trace_id, v.source, f"Guardian verdict pushed: {v.device_id} {v.status}"
                             + (f" ({v.reason})" if v.reason else ""), severity="watch" if v.status != "trusted" else "info",
                             payload=v.dump(), scenario_id=self.scenario_id)
            return True, "verdict accepted"
        if not isinstance(data, dict):
            return self._malformed("an event must be a JSON object")
        if data.get("schema") not in (None, "citybrain.event/1"):
            return self._malformed(f"unknown schema '{data.get('schema')}' (expected citybrain.event/1 or citybrain.verdict/1)")
        try:
            ev = Event(**data)
        except ValidationError as e:
            return self._malformed(explain_validation(e, "event"))
        self.ingest(ev, pushed=pushed)
        return True, f"event {ev.event_id} accepted"

    def _malformed(self, why: str) -> Tuple[bool, str]:
        self.stats["malformed"] += 1
        self.audit.write("error", None, self.source.name, f"Malformed input refused: {why}", severity="watch",
                         payload={"problem": why}, scenario_id=self.scenario_id)
        return False, why

    # ------------------------------------------------------------------ main path
    def ingest(self, ev: Event, pushed: bool = False) -> List[Action]:
        self.stats["events"] += 1
        if ev.scenario_id is None and self.scenario_id:
            ev = ev.model_copy(update={"scenario_id": self.scenario_id})
        wanted = [a for a, ag in self.agents.items() if ag.wants(ev)]
        if not wanted:
            if ev.type == "reading":
                self.city.remember(ev)          # still on the map (actuator states)
            if ev.type in ("reading", "incident") and ev.origin.family not in self._known_families():
                last = self.unrouted.get(ev.origin.device_id, 0)
                if time.monotonic() - last > 120:
                    self.unrouted[ev.origin.device_id] = time.monotonic()
                    self.audit.write("ignored", ev.trace_id, ev.source,
                                     f"Unrouted: no agent handles family '{ev.origin.family}' ({ev.origin.device_id})",
                                     payload={"event": ev.dump()}, scenario_id=ev.scenario_id)
            return []
        verdict = self.guardian.verdict(ev, pushed=pushed)
        mode = self._mode(verdict)
        if mode == "blocked":
            self._block(ev, verdict, wanted)
            return []
        self.city.remember(ev)
        self.city.quarantine = dict(self.guardian.status().get("quarantined", {}))
        self.stats["used"] += 1
        batch: List[Tuple[str, Finding, List[Action]]] = []
        self._context(ev)
        for axis in wanted:
            for finding, plans, actions in self.agents[axis].handle(ev, self.city):
                batch.append((axis, finding, actions))
        batch += self._drain(ev)
        if not batch:
            return []
        self._log_input(ev, verdict, mode)
        return self._process(ev, batch, mode)

    # ------------------------------------------------------------------ A2A and ticks
    def _context(self, ev: Event) -> None:
        self.a2a.scenario_id = ev.scenario_id or self.scenario_id
        for ag in self.agents.values():
            ag.ctx = {"trace_id": ev.trace_id, "scenario_id": ev.scenario_id or self.scenario_id}

    def _drain(self, ev: Event) -> List[Tuple[str, Finding, List[Action]]]:
        """Deliver the A2A tasks and state changes waiting on the bus; their plans join this pass."""
        out: List[Tuple[str, Finding, List[Action]]] = []
        for _ in range(200):                       # a task can cause another task: bounded, never a loop
            if self.a2a.inbox:
                task = self.a2a.inbox.popleft()
                if not task.open:
                    continue
                ag = self.agents[task.to_agent]
                tev = self._system_event("AGENTS", "a2a", task.trace_id, task.scenario_id) if task.trace_id != ev.trace_id else ev
                self._context(tev)
                try:
                    results = ag.on_task(task, self.city)
                except Exception as e:             # a bad task must not stop the Brain
                    self.a2a.update(task, "failed", f"{task.to_agent} could not handle it: {type(e).__name__}: {e}")
                    results = []
                if task.state == "submitted":
                    self.a2a.update(task, "working", f"{task.to_agent} accepted")
                for f, plans in results:
                    out.append((task.to_agent, f, ag.act(tev, f, plans)))
            elif self.a2a.updates:
                task, old = self.a2a.updates.popleft()
                sender = self.agents.get(task.from_agent)
                if sender is None:
                    continue                       # a partner over HTTP: it polls GET /v1/a2a/tasks
                tev = self._system_event("AGENTS", "a2a", task.trace_id, task.scenario_id) if task.trace_id != ev.trace_id else ev
                self._context(tev)
                for f, plans in sender.on_task_update(task, old, self.city):
                    out.append((sender.axis, f, sender.act(tev, f, plans)))
            else:
                break
        self._context(ev)
        return out

    def _system_event(self, device: str, family: str, trace_id: Optional[str] = None, scenario_id: Optional[str] = None) -> Event:
        kw = {"trace_id": trace_id} if trace_id else {}
        return Event(source=self.source.name, type="heartbeat", scenario_id=scenario_id or self.scenario_id,
                     origin=Origin(layer="brain", device_id=device, family=family, kind="system"), **kw)

    def tick(self) -> List[Action]:
        """Every Brain loop: refresh the fleet view, run each agent's tick and deliver A2A tasks sent from outside."""
        snap = self.source.snapshot() or {}
        self.city.update_units(snap.get("units") or [], snap.get("vehicles") or [], snap.get("incidents") or [])
        ev = self._system_event("FLEET", "fleet")
        self._context(ev)
        batch: List[Tuple[str, Finding, List[Action]]] = []
        for axis, ag in self.agents.items():
            for f, plans in ag.tick(self.city):
                batch.append((axis, f, ag.act(ev, f, plans)))
        batch += self._drain(ev)
        if not batch:
            return []
        return self._process(ev, batch, "normal")

    def _process(self, ev: Event, batch: List[Tuple[str, Finding, List[Action]]], mode: str) -> List[Action]:
        for axis, finding, actions in batch:
            self._situation(axis, finding, ev)
            self.stats["decisions"] += 1
            self.audit.write("decision", actions[0].trace_id if actions else ev.trace_id, ev.source,
                             f"[{axis}] {finding.title}: {finding.reason}"
                             + ("" if actions else (" -> situation closed" if finding.resolved else " -> no action needed")),
                             axis=axis, severity=finding.severity,
                             payload={"situation": finding.key, "confidence": finding.confidence, "resolved": finding.resolved,
                                      "planned": [a.dump() for a in actions], "evidence": finding.evidence},
                             scenario_id=ev.scenario_id)
        return self._arbitrate_and_send(ev, [a for _, _, acts in batch for a in acts], mode)

    def _known_families(self) -> set:
        fams = set()
        for ag in self.agents.values():
            fams |= ag.families
        return fams | {"incident", "signal", "water_valve", "service_zone", "parking", "power_switch", "lighting"}

    def _mode(self, v: Optional[Verdict]) -> str:
        if v is None:
            return {"degraded": "degraded", "ignore": "blocked", "trust": "normal"}[self.cfg.guardian.on_missing]
        if v.status in ("untrusted", "quarantined"):
            return "blocked"
        if v.status == "suspect":
            return "degraded"
        return "normal"

    def _log_input(self, ev: Event, v: Optional[Verdict], mode: str) -> None:
        where = f" zone {ev.location.zone}" if ev.location else ""
        if ev.type == "incident":
            text = f"Incident {ev.incident.id} ({ev.incident.kind}, {ev.incident.phase}){where}"
        else:
            text = f"Reading {ev.origin.device_id}{where}: " + ", ".join(f"{m} {r.value:g} {r.unit}" for m, r in list(ev.readings.items())[:4])
        self.audit.write("event", ev.trace_id, ev.source, text, payload={"event": ev.dump()}, scenario_id=ev.scenario_id)
        self._remember_map("event", ev.dump(), ev)
        if v is not None:
            self.audit.write("verdict", ev.trace_id, v.source, f"Guardian: {ev.origin.device_id} {v.status} ({v.confidence:.2f})"
                             + (f": {v.reason}" if v.reason else ""), severity="info" if mode == "normal" else "watch",
                             payload=v.dump(), scenario_id=ev.scenario_id)
        else:
            self.audit.write("verdict", ev.trace_id, ev.source,
                             f"Guardian: no verdict for {ev.origin.device_id} ({self.guardian.status().get('last_error') or 'unavailable'}): "
                             f"policy '{self.cfg.guardian.on_missing}' -> {mode}", severity="watch", payload={},
                             scenario_id=ev.scenario_id)

    def _block(self, ev: Event, v: Optional[Verdict], axes: List[str]) -> None:
        self.stats["ignored"] += 1
        dev = ev.origin.device_id
        reason = v.reason if v else f"no Guardian verdict and policy on_missing=ignore"
        status = v.status if v else "no verdict"
        if time.monotonic() - self.blocked.get(dev, 0) < 30:
            return
        self.blocked[dev] = time.monotonic()
        self.audit.write("event", ev.trace_id, ev.source,
                         f"Reading {dev} zone {ev.location.zone if ev.location else '?'}: "
                         + ", ".join(f"{m} {r.value:g} {r.unit}" for m, r in list(ev.readings.items())[:3]),
                         payload={"event": ev.dump()}, scenario_id=ev.scenario_id)
        self._remember_map("event", ev.dump(), ev, blocked=True)
        if v:
            self.audit.write("verdict", ev.trace_id, v.source, f"Guardian: {dev} {v.status} ({v.confidence:.2f}): {v.reason}",
                             severity="alert", payload=v.dump(), scenario_id=ev.scenario_id)
        self.audit.write("ignored", ev.trace_id, ev.source,
                         f"Ignored {dev} ({status}): {reason}. No decision is taken on this data.",
                         axis=axes[0], severity="alert", payload={"device": dev, "status": status, "reason": reason},
                         scenario_id=ev.scenario_id)
        key = f"{axes[0]}:untrusted:{dev}"
        loc = ev.location.model_dump() if ev.location else None
        now = iso(utc_now())
        s = self.city.situations.get(key)
        if s is None:
            self.city.situations[key] = Situation(key, axes[0], f"Untrusted data from {dev}", "untrusted", "watch", "blocked",
                                                  ev.location.zone if ev.location else None, loc, now, now, ev.trace_id,
                                                  f"{status}: {reason}", ev.source, scenario_id=ev.scenario_id)
        else:
            s.updated, s.status, s.reason, s.trace_id = now, "blocked", f"{status}: {reason}", ev.trace_id

    # ------------------------------------------------------------------ situations
    def _situation(self, axis: str, f: Finding, ev: Event) -> None:
        now = iso(utc_now())
        loc = f.location.model_dump() if f.location else None
        s = self.city.situations.get(f.key)
        if s is None:
            if f.resolved:
                return
            s = Situation(f.key, axis, f.title, f.kind, f.severity, "active", f.location.zone if f.location else None, loc,
                          now, now, ev.trace_id, f.reason, ev.source, scenario_id=ev.scenario_id)
            self.city.situations[f.key] = s
            self.audit.write("situation", ev.trace_id, ev.source, f"Opened [{axis}] {f.title} ({f.severity})", axis=axis,
                             severity=f.severity, payload=s.view(), scenario_id=ev.scenario_id)
            return
        changed = s.severity != f.severity or s.status == "resolved" or f.resolved
        s.title, s.updated, s.reason, s.trace_id, s.source = f.title, now, f.reason, ev.trace_id, ev.source
        if loc:
            s.location, s.zone = loc, f.location.zone
        if f.resolved:
            s.status = "resolved"
        else:
            if s.status == "resolved":
                s.status = "active"
            s.severity = f.severity if not (s.status == "contained" and SEVERITY_RANK[f.severity] <= SEVERITY_RANK[s.severity]) else s.severity
        if changed:
            self.audit.write("situation", ev.trace_id, ev.source,
                             f"{'Closed' if f.resolved else 'Updated'} [{axis}] {f.title} ({s.status}, {s.severity})",
                             axis=axis, severity=s.severity, payload=s.view(), scenario_id=ev.scenario_id)

    def top(self, per_axis: int = 3) -> Dict[str, List[dict]]:
        """The documented importance rule: unresolved before resolved, then severity, then most recent.
        Resolved situations stay visible for 10 minutes."""
        cutoff = time.time() - 600
        out: Dict[str, List[dict]] = {a: [] for a in AXES}
        for s in self.city.situations.values():
            if s.status == "resolved":
                try:
                    from datetime import datetime
                    if datetime.fromisoformat(s.updated.replace("Z", "+00:00")).timestamp() < cutoff:
                        continue
                except ValueError:
                    pass
            out[s.axis].append(s.view())
        for a in out:
            out[a].sort(key=lambda s: (STATUS_RANK.get(s["status"], 9), -SEVERITY_RANK[s["severity"]], _neg_time(s["updated"])))
            out[a] = out[a][:per_axis]
        return out

    # ------------------------------------------------------------------ arbitration and sending
    def _arbitrate_and_send(self, ev: Event, actions: List[Action], mode: str) -> List[Action]:
        now = time.monotonic()
        actions.sort(key=lambda a: (self.priority[a.agent], a.priority, -SEVERITY_RANK[a.severity]))
        claimed: Dict[str, Action] = {}
        out: List[Action] = []
        for a in actions:
            res = a.resource()
            if self.desired.get(res) == a.key():
                continue                              # already in that state: nothing to send, nothing to log
            why = self._refuse(a, res, mode, now)
            if why:
                self.stats["refused"] += 1
                self.audit.write("conflict" if why.startswith("conflict") else "ignored", a.trace_id, a.source,
                                 f"[{a.agent}] {a.action_type} on {a.target.actuator_id} not sent: {why}", axis=a.agent,
                                 severity="watch", payload={"action": a.dump(), "why": why}, scenario_id=ev.scenario_id)
                if why.startswith("conflict"):
                    self.stats["conflicts"] += 1
                if not why.startswith("same command"):
                    self.agents[a.agent].on_refused(a, why, self.city)
                    self._settle(a, False, f"not sent: {why}")
                continue
            winner = claimed.get(res)
            if winner is not None:
                self.stats["conflicts"] += 1
                self.audit.write("conflict", a.trace_id, a.source,
                                 f"conflict on {res}: {winner.agent} ({winner.target.command}) wins over {a.agent} "
                                 f"({a.target.command}) by axis priority {self.cfg.policy.axis_priority}",
                                 axis=a.agent, severity="watch", payload={"kept": winner.dump(), "dropped": a.dump()},
                                 scenario_id=ev.scenario_id)
                self.agents[a.agent].on_refused(a, f"conflict with {winner.agent}", self.city)
                self._settle(a, False, f"conflict with {winner.agent} on {res}")
                continue
            claimed[res] = a
            out.append(a)
        for a in out:
            self._send(a, ev)
        return out

    def _refuse(self, a: Action, res: str, mode: str, now: float) -> Optional[str]:
        q = self.city.quarantine.get(a.target.actuator_id)
        if q:
            return f"the Guardian quarantined {a.target.actuator_id} ({q})"
        if mode == "degraded" and not a.reversible:
            return "degraded trust (suspect data or no Guardian verdict): irreversible actions are withheld"
        if a.action_type == "load_shed":
            zone = zone_from_service_id(a.target.actuator_id)
            if zone and self.city.active("emergency", zone):
                needs = ", ".join(s.title for s in self.city.active("emergency", zone))
                return f"conflict: emergency need in zone {zone} ({needs}) vetoes a load-shedding cut by {a.agent}"
        lock = self.locks.get(res)
        if lock and lock[1] > now and lock[0] != a.agent and self.priority[lock[0]] < self.priority[a.agent] \
                and a.target.command not in UNDO_COMMANDS:
            return f"conflict: {res} is held by {lock[0]} ({lock[2]}) for {lock[1] - now:.0f} s more"
        last = self.sent.get(a.key())
        if last is not None and now - last < self.cfg.policy.cooldown_s and a.action_type != "green_wave":
            # (a green wave follows a moving unit and releases each junction itself: its own hysteresis)
            return f"same command sent {now - last:.0f} s ago (cooldown {self.cfg.policy.cooldown_s:g} s)"
        return None

    def _send(self, a: Action, ev: Event) -> None:
        now = time.monotonic()
        self.sent[a.key()] = now
        res = a.resource()
        self.desired[res] = a.key()
        if a.target.command in UNDO_COMMANDS:
            self.locks.pop(res, None)
            self.city.owned.pop(res, None)
        else:
            self.locks[res] = (a.agent, now + self.cfg.policy.resource_lock_s, a.situation_id or "")
            if a.situation_id:
                self.city.owned[res] = a.situation_id
        self.stats["actions"] += 1
        self.stats["by_axis"][a.agent] += 1
        self.audit.write("action", a.trace_id, a.source,
                         f"[{a.agent}] {a.action_type}: {a.target.actuator_id} {a.target.command}"
                         + (f" {json.dumps(a.target.value)}" if a.target.value is not None else "") + f" because {a.reason}",
                         axis=a.agent, severity=a.severity, payload=a.dump(), scenario_id=ev.scenario_id)
        fb = self.source.send(a)
        ok = bool(fb.state.get("ok"))
        if not ok:
            self.desired.pop(res, None)
        self.audit.write("feedback", fb.trace_id, fb.source,
                         f"Twin {'applied' if ok else 'refused'} {a.target.actuator_id} {a.target.command}: "
                         + (str(fb.state.get("applied")) if ok else str((fb.state.get("error") or {}).get("message"))),
                         axis=a.agent, severity="info" if ok else "alert", payload=fb.dump(), scenario_id=ev.scenario_id)
        self._settle(a, ok, f"Twin {'applied' if ok else 'refused'} {a.target.actuator_id} {a.target.command}: "
                     + (str(fb.state.get("applied")) if ok else str((fb.state.get("error") or {}).get("message"))))
        rec = a.dump()
        rec["feedback"] = {"ok": ok, "applied": fb.state.get("applied"), "error": fb.state.get("error")}
        rec["event_location"] = ev.location.model_dump() if ev.location else None
        rec["event_id"] = ev.event_id
        self._remember_map("action", rec, ev)
        s = self.city.situations.get(a.situation_id or "")
        if s is not None:
            s.actions += 1
            if ok and s.status == "active" and a.target.command not in UNDO_COMMANDS:
                s.status = "contained"

    def _settle(self, a: Action, ok: bool, note: str) -> None:
        """An action planned for an A2A task completes or fails that task (the sender is told)."""
        tid = a.parameters.get("a2a_task")
        task = self.a2a.get(str(tid)) if tid else None
        if task is not None and task.open:
            self.a2a.update(task, "completed" if ok else "failed", note,
                            {"actuator": a.target.actuator_id, "command": a.target.command, "action_id": a.action_id})

    def _remember_map(self, kind: str, rec: dict, ev: Event, blocked: bool = False) -> None:
        item = {"kind": kind, "seq": self.audit.last_seq, "blocked": blocked, **({"action": rec} if kind == "action" else {"event": rec})}
        (self.actions if kind == "action" else self.events).append(item)
        del self.actions[:-400]
        del self.events[:-400]


def _neg_time(ts: str) -> float:
    try:
        from datetime import datetime
        return -datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0

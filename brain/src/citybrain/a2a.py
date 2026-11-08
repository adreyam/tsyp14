"""
A2A: how the axis agents talk to each other (agent-to-agent).

An agent that needs another axis to continue a scenario does not command that axis's actuators itself: it sends
an A2A task to the agent that owns them, and that agent decides how to do it. Examples:

    emergency  -> transportation   green_wave        a fire engine / ambulance / police car is on its way: give it green
    emergency  -> transportation   stop_traffic      confirmed earthquake: every signal red (resume_traffic undoes it)
    emergency  -> energy           isolate_feeder    building fire: de-energise the building for the firefighters
    emergency  -> energy           cut_zone_power    critical flood: cut the zone's power (electrical safety)
    emergency  -> transportation   close_car_parks   flood: no more cars into the water
    pollution  -> transportation   close_car_parks   bad air: fewer cars drive into the zone

The model follows the A2A task model: every agent publishes an agent card (its skills), a task has a skill, an
input, a reason, and a state that moves submitted -> working -> completed | rejected | failed | canceled, with a
note at each change. The sender is told about every change (Agent.on_task_update), so it can react (a rejected
request, a failed command). Every message is one "a2a" line in the audit log, on the trace of the event that caused
it, so a scenario can be followed from one axis to the next on the dashboard.

The orchestrator stays the only way to the Twin: the actions an agent plans for a task go through the same trust
gate, arbitration and audit as any other action. Partners (an axis engine, an operator tool) can send tasks over
HTTP: POST /v1/a2a/tasks (see brain/INTERFACE.md).
"""
from __future__ import annotations

from collections import OrderedDict, deque
from typing import Any, Dict, List, Optional

from .contracts import AXES, A2ATask, iso, utc_now

KEEP_TASKS = 500


class A2ABus:
    def __init__(self, audit, source_name: str):
        self.audit = audit
        self.source_name = source_name
        self.agents: Dict[str, Any] = {}
        self.tasks: "OrderedDict[str, A2ATask]" = OrderedDict()
        self.inbox: deque = deque()        # tasks to deliver to their receiver
        self.updates: deque = deque()      # (task, old_state) changes to tell the sender
        self.scenario_id: Optional[str] = None
        self.stats = {"sent": 0, "completed": 0, "rejected": 0, "failed": 0, "canceled": 0}

    # ------------------------------------------------------------------ cards
    def register(self, agent) -> None:
        self.agents[agent.axis] = agent
        agent.a2a = self

    def cards(self) -> List[dict]:
        out = []
        for axis, ag in self.agents.items():
            doc = (ag.__class__.__doc__ or ag.__class__.__module__ or "").strip().splitlines()
            out.append({"name": f"citybrain-{axis}", "axis": axis, "description": doc[0] if doc else axis,
                        "protocol": "citybrain.a2a/1 (task: submitted -> working -> completed | rejected | failed | canceled)",
                        "skills": [{"id": k, "description": v} for k, v in sorted(ag.skills.items())]})
        return out

    # ------------------------------------------------------------------ sending
    def send(self, from_agent: str, to_agent: str, skill: str, input: Optional[dict], reason: str,
             trace_id: Optional[str] = None, context_id: Optional[str] = None, scenario_id: Optional[str] = None) -> A2ATask:
        kw = {"trace_id": trace_id} if trace_id else {}
        task = A2ATask(from_agent=from_agent, to_agent=to_agent, skill=skill, input=dict(input or {}), reason=reason,
                       context_id=context_id, scenario_id=scenario_id or self.scenario_id, **kw)
        return self.submit(task)

    def submit(self, task: A2ATask) -> A2ATask:
        """Accept a task (from an agent or from a partner over HTTP). An unknown skill is rejected at once."""
        self.stats["sent"] += 1
        task.history.append({"state": "submitted", "ts": iso(utc_now()), "note": task.reason})
        self.tasks[task.task_id] = task
        while len(self.tasks) > KEEP_TASKS:
            self.tasks.popitem(last=False)
        self._log(task, f"A2A {task.from_agent} → {task.to_agent}: {task.skill}{_brief(task.input)} because {task.reason}",
                  axis=task.from_agent if task.from_agent in AXES else task.to_agent)
        receiver = self.agents.get(task.to_agent)
        if receiver is None or task.skill not in receiver.skills:
            known = ", ".join(sorted(receiver.skills)) if receiver else "none"
            self.update(task, "rejected", f"{task.to_agent} has no skill '{task.skill}' (skills: {known})")
            return task
        self.inbox.append(task)
        return task

    def update(self, task: A2ATask, state: str, note: str, result: Optional[dict] = None) -> None:
        if task.state == state and not result and note == task.note:
            return
        old = task.state
        task.state, task.note, task.updated = state, note[:600], utc_now()
        if result:
            task.result.update(result)
        task.history.append({"state": state, "ts": iso(task.updated), "note": task.note})
        del task.history[:-40]
        if state in self.stats:
            self.stats[state] += 1
        if state != old:
            sev = "watch" if state in ("rejected", "failed") else "info"
            self._log(task, f"A2A {task.to_agent} → {task.from_agent}: {task.skill} {state}: {task.note}",
                      axis=task.to_agent, severity=sev)
            self.updates.append((task, old))

    def cancel(self, task: A2ATask, by: str, note: str) -> None:
        if task.open:
            self.update(task, "canceled", f"canceled by {by}: {note}")

    # ------------------------------------------------------------------ queries
    def open_tasks(self, to_agent: Optional[str] = None, skill: Optional[str] = None,
                   from_agent: Optional[str] = None) -> List[A2ATask]:
        return [t for t in self.tasks.values() if t.open and (to_agent is None or t.to_agent == to_agent)
                and (skill is None or t.skill == skill) and (from_agent is None or t.from_agent == from_agent)]

    def get(self, task_id: str) -> Optional[A2ATask]:
        return self.tasks.get(task_id)

    def recent(self, limit: int = 100) -> List[dict]:
        return [t.dump() for t in list(self.tasks.values())[-limit:]][::-1]

    def view(self) -> dict:
        return {"agents": self.cards(), "stats": dict(self.stats), "open": len(self.open_tasks()), "tasks": self.recent(60)}

    # ------------------------------------------------------------------ audit
    def _log(self, task: A2ATask, summary: str, axis: str, severity: str = "info") -> None:
        self.audit.write("a2a", task.trace_id, self.source_name, summary, axis=axis if axis in AXES else None,
                         severity=severity, payload=task.dump(), scenario_id=task.scenario_id)


def _brief(inp: dict) -> str:
    keys = [k for k in ("unit", "incident", "zone", "kind") if inp.get(k) not in (None, "")]
    return (" (" + ", ".join(f"{k} {inp[k]}" for k in keys) + ")") if keys else ""

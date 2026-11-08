"""
The pattern every agent follows: detect -> decide -> act -> explain.

    detect(event, city)   -> [Finding]     what is happening (or what stopped happening), with evidence
    decide(finding, city) -> [Plan]        which Twin actuator to use, and why (an empty list is a decision too)
    act(...)              -> [Action]      validated Action messages on the event's trace
    explain               every Finding and Plan carries a plain-language reason; the orchestrator logs it

Agents only read the city through `CityView` (latest trusted readings, device registry, situations,
quarantine list, emergency units on the move). They never talk to the Twin or to the Guardian themselves: the
orchestrator does.

Two more entry points, both returning [(Finding, [Plan])] that go through the same orchestrator path:
    tick(city)            called on every Brain loop, for things that move by themselves (units driving to a scene)
    on_task(task, city)   an A2A task another agent sent to this one (see citybrain/a2a.py); the receiver moves the
                          task to working / completed / rejected / failed with a note
and one way out: self.ask(to, skill, input, reason) sends an A2A task to another axis. on_task_update tells the
sender when one of its tasks changed state.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..contracts import Action, ActionTarget, Event, Location, SEVERITY_RANK, ZONES, zone_centre

BANDS = ["North", "NorthCentral", "SouthCentral", "South"]
# Twin emergency fleet statuses (twin/scripts/incident_engine.py EmergencyFleet): a unit on the road network.
MOVING = {"inserting", "en_route", "returning"}
_JUNCTION = re.compile(r"^SIG-Junction_r(\d+)_c(\d+)$")


GRID = [-380.0 + 95.0 * k for k in range(9)]     # road centre lines (x and z), as in the Twin


def road_segment(x: float, z: float) -> dict:
    """The road segment nearest to a point: its axis ("ew" along x / "ns" along z) and the junctions at both ends,
    as Twin grid indices (r = row along z, c = column along x, 1..9)."""
    ix = min(range(9), key=lambda i: abs(GRID[i] - x))
    iz = min(range(9), key=lambda i: abs(GRID[i] - z))
    if abs(GRID[iz] - z) <= abs(GRID[ix] - x):           # on an east-west road (z fixed)
        c0 = max(0, min(7, int((x + 380.0) // 95.0)))
        return {"axis": "ew", "a": (iz + 1, c0 + 1), "b": (iz + 1, c0 + 2)}
    r0 = max(0, min(7, int((z + 380.0) // 95.0)))
    return {"axis": "ns", "a": (r0 + 1, ix + 1), "b": (r0 + 2, ix + 1)}


def edge_id(frm: tuple, to: tuple) -> str:
    """The Twin's SUMO edge from one junction to its neighbour (E_r{r}c{c}_r{r}c{c})."""
    return f"E_r{frm[0]}c{frm[1]}_r{to[0]}c{to[1]}"


def junction_signal(rc: tuple) -> str:
    return f"SIG-Junction_r{rc[0]}_c{rc[1]}"


def junction_point(rc: tuple) -> tuple:
    return ((rc[1] - 1) * 95.0 - 380.0, (rc[0] - 1) * 95.0 - 380.0)


def junction_xz(signal_id: str) -> Optional[tuple]:
    """Centre of the junction a signal controls (Twin grid: 95 m blocks from -380 m)."""
    m = _JUNCTION.match(signal_id)
    if not m:
        return None
    return ((int(m.group(2)) - 1) * 95.0 - 380.0, (int(m.group(1)) - 1) * 95.0 - 380.0)


def service_zone_id(zone: str) -> str:
    """Twin service cluster of a zone letter (twin/scripts/utility_resource_system.gd)."""
    i = ZONES.index(zone)
    return f"ZONE-Zone_{BANDS[i // 4]}_{i % 4 + 1}"


_SERVICE = re.compile(r"^ZONE-Zone_(North|NorthCentral|SouthCentral|South)_([1-4])$")


def zone_from_service_id(device_id: str) -> Optional[str]:
    m = _SERVICE.match(device_id)
    return ZONES[BANDS.index(m.group(1)) * 4 + int(m.group(2)) - 1] if m else None


@dataclass
class Finding:
    key: str                      # situation id, e.g. "emergency:flood:K"
    title: str                    # e.g. "Flood in zone K"
    kind: str                     # flood, earthquake, incident, jam, air, overload ...
    severity: str                 # info | watch | alert | critical
    confidence: float
    reason: str                   # the explanation shown everywhere
    location: Optional[Location] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    resolved: bool = False


@dataclass
class Plan:
    actuator_id: str
    command: str
    action_type: str
    reason: str
    value: Any = None
    priority: int = 1
    reversible: bool = True
    location: Optional[Location] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Situation:
    id: str
    axis: str
    title: str
    kind: str
    severity: str
    status: str                   # active | contained | resolved
    zone: Optional[str]
    location: Optional[dict]
    started: str
    updated: str
    trace_id: str
    reason: str
    source: str
    actions: int = 0
    scenario_id: Optional[str] = None

    def view(self) -> dict:
        return dict(self.__dict__)


class CityView:
    """What agents may read. Filled by the orchestrator with trusted data only."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.latest: Dict[str, Event] = {}          # device -> last trusted event
        self.registry: Dict[str, dict] = {}         # device -> {family, zone, x, z}
        self.situations: Dict[str, Situation] = {}
        self.quarantine: Dict[str, str] = {}        # device -> reason (Guardian)
        self.owned: Dict[str, str] = {}             # resource -> situation that commanded it (to undo on close)
        self.units: Dict[str, dict] = {}            # emergency unit -> {kind, status, incident, response, x, z, dx, dz, speed}
        self.incidents: Dict[str, dict] = {}        # open Twin incident -> {kind, x, z, phase}

    def update_units(self, units: List[dict], vehicles: List[dict], incidents: List[dict]) -> None:
        """Fleet view from the Twin (list_emergency_units + list_vehicles): position, status and travel direction."""
        live = {str(v.get("unit")): v for v in vehicles or [] if isinstance(v, dict) and v.get("unit")}
        seen = set()
        for u in units or []:
            if not isinstance(u, dict) or not u.get("unit"):
                continue
            uid = str(u["unit"])
            seen.add(uid)
            loc = (live.get(uid) or {}).get("location") or u.get("location") or {}
            try:
                x, z = float(loc.get("x")), float(loc.get("z"))
            except (TypeError, ValueError):
                x = z = None
            old = self.units.get(uid, {})
            dx, dz = old.get("dx", 0.0), old.get("dz", 0.0)
            if x is not None and old.get("x") is not None:
                mx, mz = x - old["x"], z - old["z"]
                if math.hypot(mx, mz) >= 1.5:                    # moved: that is the direction of travel
                    n = math.hypot(mx, mz)
                    dx, dz = mx / n, mz / n
            if x is None:
                x, z = old.get("x"), old.get("z")
            speed = (live.get(uid) or {}).get("speed_mps", u.get("speed_mps"))
            self.units[uid] = {"unit": uid, "kind": str(u.get("kind", "")), "status": str(u.get("status", "")),
                               "incident": str(u.get("incident") or ""), "response": bool(u.get("response", False)),
                               "depot": u.get("depot"), "x": x, "z": z, "dx": dx, "dz": dz,
                               "speed": float(speed) if isinstance(speed, (int, float)) else None,
                               "moved": old.get("moved", 0.0) if (dx, dz) == (old.get("dx"), old.get("dz")) else time.monotonic()}
        for uid in list(self.units):
            if uid not in seen:
                del self.units[uid]
        self.incidents = {}
        for inc in incidents or []:
            if isinstance(inc, dict) and inc.get("id") and str(inc.get("phase") or "") != "resolved":
                loc = inc.get("location") or {}
                try:
                    self.incidents[str(inc["id"])] = {"kind": str(inc.get("kind", "")), "x": float(loc.get("x")),
                                                      "z": float(loc.get("z")), "phase": str(inc.get("phase") or "")}
                except (TypeError, ValueError):
                    pass

    def signals(self) -> Dict[str, tuple]:
        """Every signal the Brain knows -> its junction centre."""
        out = {}
        for d in self.family("signal"):
            c = junction_xz(d)
            if c:
                out[d] = c
        return out

    def remember(self, ev: Event) -> None:
        dev = ev.origin.device_id
        self.latest[dev] = ev
        if ev.location is not None:
            self.registry[dev] = {"family": ev.origin.family, "kind": ev.origin.kind, "zone": ev.location.zone,
                                  "x": ev.location.x, "z": ev.location.z}

    def value(self, device: str, metric: str, default: Optional[float] = None) -> Optional[float]:
        ev = self.latest.get(device)
        return ev.value(metric, default) if ev else default

    def family(self, family: str) -> Dict[str, dict]:
        return {d: r for d, r in self.registry.items() if r["family"] == family}

    def in_zone(self, family: str, zone: str) -> List[str]:
        return sorted(d for d, r in self.family(family).items() if r["zone"] == zone)

    def nearest(self, family: str, x: float, z: float, exclude: Optional[set] = None, usable: bool = True) -> Optional[str]:
        best, bd = None, float("inf")
        for d, r in self.family(family).items():
            if exclude and d in exclude:
                continue
            if usable and d in self.quarantine:
                continue
            dist = math.hypot(r["x"] - x, r["z"] - z)
            if dist < bd:
                best, bd = d, dist
        return best

    def active(self, axis: Optional[str] = None, zone: Optional[str] = None) -> List[Situation]:
        return [s for s in self.situations.values() if s.status != "resolved"
                and (axis is None or s.axis == axis) and (zone is None or s.zone == zone)]


def loc_of(view: CityView, device: str) -> Optional[Location]:
    r = view.registry.get(device)
    if r:
        return Location(zone=r["zone"], x=r["x"], z=r["z"])
    if device.startswith("ZONE-"):
        z = zone_from_service_id(device)
        if z:
            cx, cz = zone_centre(z)
            return Location(zone=z, x=cx, z=cz)
    return None


class Agent:
    axis = "?"
    families: set = set()          # Twin families this agent reads
    incident_kinds: set = set()    # Twin incident kinds this agent handles

    skills: Dict[str, str] = {}    # A2A skills other agents may ask for: id -> what it does

    def __init__(self, cfg):
        self.cfg = cfg
        self.p = cfg.agent(self.axis)
        self.a2a = None                # the orchestrator's A2ABus (set when registered)
        self.view: Optional["CityView"] = None   # the orchestrator's city view (set when registered)
        self.ctx: Dict[str, Any] = {}  # trace and scenario of the event being handled (set by the orchestrator)

    def wants(self, ev: Event) -> bool:
        if ev.type == "incident":
            return ev.incident is not None and ev.incident.kind in self.incident_kinds
        return ev.type == "reading" and ev.origin.family in self.families

    # -- the four steps -------------------------------------------------------
    def detect(self, ev: Event, city: CityView) -> List[Finding]:
        raise NotImplementedError

    def decide(self, f: Finding, city: CityView) -> List[Plan]:
        raise NotImplementedError

    def act(self, ev: Event, f: Finding, plans: List[Plan]) -> List[Action]:
        out = []
        for p in plans:
            out.append(Action(
                trace_id=ev.trace_id, caused_by=[ev.event_id], source=ev.source, agent=self.axis,
                action_type=p.action_type, target=ActionTarget(actuator_id=p.actuator_id, command=p.command, value=p.value),
                # where it happens: the plan's place, else the actuator's own place, else the finding's
                location=p.location or (loc_of(self.view, p.actuator_id) if self.view is not None else None) or f.location,
                severity=f.severity, priority=p.priority,
                parameters={**p.parameters, "situation": f.key}, reason=p.reason, confidence=f.confidence,
                reversible=p.reversible, situation_id=f.key))
        return out

    def handle(self, ev: Event, city: CityView) -> List[tuple]:
        """Run the four steps on one event. Returns [(finding, plans, actions)]."""
        result = []
        for f in self.detect(ev, city):
            plans = self.decide(f, city)
            result.append((f, plans, self.act(ev, f, plans)))
        return result

    def on_refused(self, action: Action, why: str, city: "CityView") -> None:
        """Called by the orchestrator when one of this agent's actions is vetoed or refused."""

    # -- A2A ---------------------------------------------------------------------
    def ask(self, to: str, skill: str, input: Optional[dict], reason: str, context: Optional[str] = None):
        """Send an A2A task to another axis agent; its plans come back through the orchestrator."""
        return self.a2a.send(self.axis, to, skill, input, reason, trace_id=self.ctx.get("trace_id"),
                             context_id=context, scenario_id=self.ctx.get("scenario_id"))

    def on_task(self, task, city: "CityView") -> List[Tuple[Finding, List[Plan]]]:
        """An A2A task for this agent. Default: rejected (the agent has no skills)."""
        self.a2a.update(task, "rejected", f"{self.axis} does not handle '{task.skill}'")
        return []

    def on_task_update(self, task, old_state: str, city: "CityView") -> List[Tuple[Finding, List[Plan]]]:
        """One of this agent's tasks changed state (completed, rejected, failed ...). Default: nothing to do."""
        return []

    def tick(self, city: "CityView") -> List[Tuple[Finding, List[Plan]]]:
        """Called on every Brain loop, without an event. Default: nothing."""
        return []

    def pending(self, city: "CityView") -> List[Finding]:
        """Findings an agent wants to re-decide without a new event (e.g. after a veto). Default: none."""
        return []

    # -- helpers ----------------------------------------------------------------
    def num(self, key: str, default: float) -> float:
        try:
            return float(self.p.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def worse(a: str, b: str) -> str:
        return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b

    @staticmethod
    def now() -> float:
        return time.monotonic()

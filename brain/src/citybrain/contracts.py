"""
Message contracts of the City Brain (v1).

Every message that enters or leaves the Brain is one of these pydantic models:

    Event    Twin -> Brain      a reading, an incident, an actuator answer or a heartbeat
    Verdict  Guardian -> Brain  how far the Brain can trust a device's data
    Action   Brain -> Twin      one actuator command, with the agent, the reason and the trace

The live source and the mock source build the same models, so the orchestrator and the
agents never know which one is active. Unknown fields are refused (extra="forbid"), so a
partner that sends something unexpected gets a clear error instead of a silent guess.
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_EVENT = "citybrain.event/1"
SCHEMA_VERDICT = "citybrain.verdict/1"
SCHEMA_ACTION = "citybrain.action/1"

Source = Literal["live", "mock"]
Axis = Literal["emergency", "transportation", "pollution", "energy"]
AXES: tuple = ("emergency", "transportation", "pollution", "energy")
Severity = Literal["info", "watch", "alert", "critical"]
SEVERITY_RANK = {"info": 0, "watch": 1, "alert": 2, "critical": 3}

ZONES = tuple("ABCDEFGHIJKLMNOP")
CITY_HALF_EXTENT_M = 380.0
ZONE_SIZE_M = 190.0

# Twin device ids: FAMILY-PREFIX "-" suffix (TRAF-01, SIG-Junction_r5_c5, ZONE-Zone_North_1, WGT-T001 ...)
DEVICE_ID_RE = re.compile(r"^[A-Z]{2,10}(-[A-Za-z0-9_]{1,40})?$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """Short, sortable, unique id: prefix-<ms since epoch base36>-<random>."""
    ms = int(time.time() * 1000)
    digits, n = "", ms
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    while n:
        n, r = divmod(n, 36)
        digits = alphabet[r] + digits
    return f"{prefix}-{digits}{secrets.token_hex(2)}"


def zone_of(x: float, z: float) -> str:
    """Same rule as the Twin (twin/scripts/city_zones.gd): 4 x 4 zones of 190 m, A north-west."""
    col = min(3, max(0, int((x + CITY_HALF_EXTENT_M) // ZONE_SIZE_M)))
    row = min(3, max(0, int((z + CITY_HALF_EXTENT_M) // ZONE_SIZE_M)))
    return ZONES[row * 4 + col]


def zone_centre(zone: str) -> tuple:
    i = ZONES.index(zone)
    return (-CITY_HALF_EXTENT_M + (i % 4 + 0.5) * ZONE_SIZE_M, -CITY_HALF_EXTENT_M + (i // 4 + 0.5) * ZONE_SIZE_M)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Location(Strict):
    zone: str = Field(pattern=r"^[A-P]$", description="Twin zone A..P")
    x: float = Field(ge=-450, le=450, description="Twin metres, west -> east")
    z: float = Field(ge=-450, le=450, description="Twin metres, north -> south")
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)

    @classmethod
    def at(cls, x: float, z: float) -> "Location":
        return cls(zone=zone_of(x, z), x=round(float(x), 2), z=round(float(z), 2))


class Reading(Strict):
    value: float
    unit: str = Field(default="", max_length=16)

    @field_validator("value")
    @classmethod
    def finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("value must be a finite number")
        return v


class Origin(Strict):
    layer: Literal["twin", "guardian", "brain", "operator"]
    device_id: str = Field(min_length=2, max_length=48)
    family: str = Field(min_length=2, max_length=24)
    kind: Literal["sensor", "actuator", "system"]

    @field_validator("device_id")
    @classmethod
    def twin_style_id(cls, v: str) -> str:
        if not DEVICE_ID_RE.match(v):
            raise ValueError(f"device_id '{v}' is not a Twin device id (expected e.g. FLOOD-03, SIG-Junction_r5_c5)")
        return v


class Incident(Strict):
    id: str = Field(min_length=1, max_length=48)
    kind: str = Field(min_length=2, max_length=40)
    status: str = Field(default="active", max_length=24)
    phase: Optional[str] = Field(default=None, max_length=48)
    title: Optional[str] = Field(default=None, max_length=120)


class Event(Strict):
    schema_: Literal["citybrain.event/1"] = Field(default=SCHEMA_EVENT, alias="schema")
    event_id: str = Field(default_factory=lambda: new_id("ev"), max_length=48)
    trace_id: str = Field(default_factory=lambda: new_id("tr"), max_length=48)
    ts: datetime = Field(default_factory=utc_now)
    source: Source
    scenario_id: Optional[str] = Field(default=None, max_length=16)
    type: Literal["reading", "incident", "actuator_feedback", "heartbeat"]
    origin: Origin
    location: Optional[Location] = None
    readings: Dict[str, Reading] = Field(default_factory=dict)
    state: Dict[str, Any] = Field(default_factory=dict)
    incident: Optional[Incident] = None
    twin_clock: Optional[str] = Field(default=None, max_length=16)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def coherent(self) -> "Event":
        if self.type == "reading" and not self.readings:
            raise ValueError("a 'reading' event needs at least one entry in 'readings'")
        if self.type == "incident" and self.incident is None:
            raise ValueError("an 'incident' event needs the 'incident' object")
        if len(self.readings) > 32:
            raise ValueError("at most 32 metrics per event")
        return self

    def value(self, metric: str, default: Optional[float] = None) -> Optional[float]:
        r = self.readings.get(metric)
        return r.value if r is not None else default

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


VerdictStatus = Literal["trusted", "suspect", "untrusted", "quarantined", "released"]


class Verdict(Strict):
    schema_: Literal["citybrain.verdict/1"] = Field(default=SCHEMA_VERDICT, alias="schema")
    verdict_id: str = Field(default_factory=lambda: new_id("vd"), max_length=48)
    trace_id: Optional[str] = Field(default=None, max_length=48)
    ts: datetime = Field(default_factory=utc_now)
    source: Source
    device_id: str = Field(min_length=2, max_length=48)
    event_id: Optional[str] = Field(default=None, max_length=48)
    status: VerdictStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reason: Optional[str] = Field(default=None, max_length=400)
    valid_for_s: float = Field(default=60.0, gt=0, le=86400)
    containment: Optional[Literal["none", "isolate", "quarantine", "rollback"]] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def reason_when_not_trusted(self) -> "Verdict":
        if self.status in ("suspect", "untrusted", "quarantined") and not self.reason:
            raise ValueError(f"a '{self.status}' verdict must give a reason")
        return self

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


class ActionTarget(Strict):
    actuator_id: str = Field(min_length=2, max_length=48, description="Twin id: SIG-..., VLV-..., ZONE-..., INCIDENTS, SIGNALS ...")
    command: str = Field(min_length=1, max_length=32, description="Twin command name, sent unchanged")
    value: Any = None

    @field_validator("actuator_id")
    @classmethod
    def twin_style_id(cls, v: str) -> str:
        if not DEVICE_ID_RE.match(v):
            raise ValueError(f"actuator_id '{v}' is not a Twin id")
        return v


class Action(Strict):
    schema_: Literal["citybrain.action/1"] = Field(default=SCHEMA_ACTION, alias="schema")
    action_id: str = Field(default_factory=lambda: new_id("act"), max_length=48)
    trace_id: str = Field(max_length=48)
    caused_by: List[str] = Field(default_factory=list, max_length=32)
    ts: datetime = Field(default_factory=utc_now)
    source: Source
    agent: Axis
    action_type: str = Field(min_length=2, max_length=40)
    target: ActionTarget
    location: Optional[Location] = None
    severity: Severity
    priority: int = Field(ge=0, le=3, description="0 immediate .. 3 low")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    reversible: bool = True
    situation_id: Optional[str] = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def key(self) -> tuple:
        """What the action does to the city: used for de-duplication and cooldowns."""
        return (self.target.actuator_id, self.target.command, repr(self.target.value))

    def resource(self) -> str:
        """What the action claims: two actions on the same resource conflict."""
        t = self.target
        if t.actuator_id == "INCIDENTS" and t.command == "send_unit" and isinstance(t.value, dict):
            return f"INCIDENTS:send:{t.value.get('tag')}:{t.value.get('edge')}"
        if t.actuator_id == "INCIDENTS" and t.command == "recall_unit":
            return f"INCIDENTS:recall:{t.value}"
        if t.actuator_id == "INCIDENTS" and isinstance(t.value, dict):
            return f"INCIDENTS:{t.value.get('id')}:{t.value.get('kind')}"
        if t.actuator_id == "ZONES" and isinstance(t.value, dict):
            return f"ZONES:lockdown:{t.value.get('zone')}"
        if t.actuator_id.startswith("ZONE-"):
            return f"{t.actuator_id}:{'power' if 'power' in t.command else 'water'}"
        return t.actuator_id

    def twin_request(self) -> dict:
        """The exact line the Twin's device API expects (twin/scripts/sim_api.gd, method 'command')."""
        params = {"id": self.target.actuator_id, "command": self.target.command}
        if self.target.value is not None:
            params["value"] = self.target.value
        return {"id": self.action_id, "method": "command", "params": params}

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


SCHEMA_A2A = "citybrain.a2a/1"
TaskState = Literal["submitted", "working", "completed", "rejected", "failed", "canceled"]
OPEN_STATES = ("submitted", "working")
AGENT_NAME_RE = r"^[a-z][a-z0-9_-]{1,31}$"


class A2ATask(Strict):
    """One agent asking another to do something (A2A task model: a task with a skill, an input, a state that moves
    submitted -> working -> completed | rejected | failed | canceled, a result, and the history of its states).
    The four axis agents use it inside the Brain; partners (an axis engine, an operator tool) use it over HTTP."""
    schema_: Literal["citybrain.a2a/1"] = Field(default=SCHEMA_A2A, alias="schema")
    task_id: str = Field(default_factory=lambda: new_id("a2a"), max_length=48)
    trace_id: str = Field(default_factory=lambda: new_id("tr"), max_length=48)
    context_id: Optional[str] = Field(default=None, max_length=64, description="situation of the sender, if any")
    scenario_id: Optional[str] = Field(default=None, max_length=16)
    created: datetime = Field(default_factory=utc_now)
    updated: datetime = Field(default_factory=utc_now)
    from_agent: str = Field(pattern=AGENT_NAME_RE)
    to_agent: Axis
    skill: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    input: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=600, description="why the sender asks, in plain words")
    state: TaskState = "submitted"
    note: Optional[str] = Field(default=None, max_length=600, description="what the receiver said at the last state change")
    result: Dict[str, Any] = Field(default_factory=dict)
    history: List[Dict[str, Any]] = Field(default_factory=list, max_length=40)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("input")
    @classmethod
    def small_input(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if len(v) > 24 or len(repr(v)) > 4000:
            raise ValueError("input must stay small (at most 24 keys, 4000 characters)")
        return v

    @property
    def open(self) -> bool:
        return self.state in OPEN_STATES

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


def explain_validation(err: ValidationError, what: str) -> str:
    """One readable sentence per problem: 'event.origin.device_id: device_id 'x' is not a Twin device id'."""
    parts = []
    for e in err.errors()[:8]:
        loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
        msg = e.get("msg", "invalid").replace("Value error, ", "")
        parts.append(f"{what}.{loc}: {msg}")
    more = len(err.errors()) - 8
    if more > 0:
        parts.append(f"... and {more} more")
    return "; ".join(parts)


def json_schemas() -> dict:
    """JSON Schema of the messages, served at /v1/schemas for partners."""
    return {
        "event": Event.model_json_schema(by_alias=True),
        "verdict": Verdict.model_json_schema(by_alias=True),
        "action": Action.model_json_schema(by_alias=True),
        "a2a_task": A2ATask.model_json_schema(by_alias=True),
    }

"""Canonical energy schemas.

All Brain logic works ONLY on these models. Partner-specific formats are
translated at the edge by adapters (see energy_brain/adapters). Models are
strict: unknown fields are rejected and every timestamp must carry a timezone,
so a malformed partner message fails loudly at the boundary instead of
silently corrupting the energy state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def _tz_aware(cls, v: Any) -> Any:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("timestamp must include a timezone (use ISO-8601 with 'Z' or an offset)")
        return v


# --------------------------------------------------------------------------- units

PowerUnit = Literal["W", "kW", "MW"]
_TO_KW = {"W": 1e-3, "kW": 1.0, "MW": 1e3}


def to_kw(value: float, unit: str) -> float:
    try:
        return value * _TO_KW[unit]
    except KeyError as exc:
        raise ValueError(f"unsupported power unit '{unit}' (expected one of {sorted(_TO_KW)})") from exc


# --------------------------------------------------------------------------- city description

class ZoneTag(str, Enum):
    hospital = "hospital"
    flood_pump = "flood_pump"
    traffic_signals = "traffic_signals"
    residential = "residential"
    commercial = "commercial"
    industrial = "industrial"


class Zone(Strict):
    zone_id: str = Field(min_length=1)
    capacity_kw: Optional[float] = Field(default=None, gt=0, description="None = capacity unknown (L0 for this zone)")
    tags: list[ZoneTag] = Field(default_factory=list)

    @property
    def is_priority(self) -> bool:
        return any(t in (ZoneTag.hospital, ZoneTag.flood_pump, ZoneTag.traffic_signals) for t in self.tags)


class SwitchKind(str, Enum):
    tie = "tie"          # normally open; closing transfers load from_zone -> to_zone
    feeder = "feeder"    # normally closed; opening de-energizes from_zone


class Switch(Strict):
    switch_id: str = Field(min_length=1)
    kind: Optional[SwitchKind] = None
    from_zone: Optional[str] = None
    to_zone: Optional[str] = None
    transfer_fraction: Optional[float] = Field(default=None, ge=0, le=1)

    @property
    def effect_known(self) -> bool:
        if self.kind == SwitchKind.feeder:
            return self.from_zone is not None
        return None not in (self.kind, self.from_zone, self.to_zone, self.transfer_fraction)


class AggregateMeter(Strict):
    """A meter that measures the sum of several zones (e.g. a substation or feeder-head meter).
    Optional. When present, the Brain checks power balance: meter ~= sum of its zones' sensors."""
    sensor_id: str = Field(min_length=1)
    zones: list[str] = Field(min_length=1)


class CityDescriptor(Strict):
    """What the Brain knows about a city. Every field beyond IDs is optional:
    missing knowledge lowers the Brain's autonomy level, it never crashes it."""
    city_id: str = "unknown-city"
    zones: list[Zone]
    switches: list[Switch] = Field(default_factory=list)
    aggregate_meters: list[AggregateMeter] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs(self) -> "CityDescriptor":
        ids = [z.zone_id for z in self.zones]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate zone_id in city descriptor")
        known = set(ids)
        for s in self.switches:
            for ref in (s.from_zone, s.to_zone):
                if ref is not None and ref not in known:
                    raise ValueError(f"switch '{s.switch_id}' references unknown zone '{ref}'")
        for m in self.aggregate_meters:
            bad = [z for z in m.zones if z not in known]
            if bad:
                raise ValueError(f"aggregate meter '{m.sensor_id}' references unknown zone(s) {bad}")
        return self


# --------------------------------------------------------------------------- telemetry

class PowerReading(Strict):
    type: Literal["power_reading"] = "power_reading"
    sensor_id: str = Field(min_length=1)
    zone_id: str = Field(min_length=1)
    ts: datetime
    value: float = Field(ge=0)
    unit: PowerUnit = "kW"
    seq: Optional[int] = Field(default=None, ge=0)

    @property
    def kw(self) -> float:
        return to_kw(self.value, self.unit)


class SwitchState(str, Enum):
    open = "open"
    closed = "closed"


class SwitchStatus(Strict):
    type: Literal["switch_status"] = "switch_status"
    switch_id: str = Field(min_length=1)
    state: SwitchState
    available: bool = True
    ts: datetime


# --------------------------------------------------------------------------- trust

class Verdict(str, Enum):
    trusted = "trusted"
    untrusted = "untrusted"


class TrustVerdict(Strict):
    type: Literal["trust_verdict"] = "trust_verdict"
    verdict_id: str = Field(default_factory=lambda: new_id("v"))
    subject_id: str = Field(min_length=1, description="sensor_id or switch_id the verdict is about")
    verdict: Verdict
    ts: datetime
    quarantined: bool = False
    reason: Optional[str] = None


# --------------------------------------------------------------------------- actions

class SwitchOperation(str, Enum):
    open = "open"
    close = "close"


class SwitchCommand(Strict):
    type: Literal["switch_command"] = "switch_command"
    command_id: str = Field(default_factory=lambda: new_id("cmd"))
    switch_id: str
    operation: SwitchOperation
    issued_at: datetime = Field(default_factory=utcnow)
    decision_id: str
    reason: str
    verdict_refs: list[str] = Field(default_factory=list)
    degraded: bool = False


class AckStatus(str, Enum):
    accepted = "accepted"
    rejected = "rejected"
    duplicate = "duplicate"


class CommandAck(Strict):
    type: Literal["command_ack"] = "command_ack"
    command_id: str
    status: AckStatus
    ts: datetime
    detail: Optional[str] = None


# --------------------------------------------------------------------------- cross-domain

class PriorityHint(Strict):
    """Lets other Brain domains (flood, traffic, fire) protect a zone's power."""
    type: Literal["priority_hint"] = "priority_hint"
    zone_id: str
    source: str
    reason: str
    expires_at: datetime


# --------------------------------------------------------------------------- outbound events (timeline + audit)

EventKind = Literal[
    "reading_rejected", "reading_suspect", "verdict_change", "knowledge_level", "learned_effect",
    "forecast", "risk_detected", "risk_cleared", "candidates_evaluated",
    "decision", "gate_passed", "gate_blocked", "command_sent", "command_ack", "verification",
    "actuator_anomaly", "degraded_mode", "operator_alert", "partner_error", "scenario_marker",
    "priority_hint", "plan_deviation", "verdict_flapping", "sequence_reset", "restoration", "security",
    "proposal", "orchestrator", "plan", "consistency",
]


class BrainEvent(Strict):
    type: Literal["brain_event"] = "brain_event"
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    ts: datetime = Field(default_factory=utcnow)
    layer: Literal["brain"] = "brain"
    domain: Literal["energy"] = "energy"
    kind: EventKind
    correlation_id: Optional[str] = None
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)

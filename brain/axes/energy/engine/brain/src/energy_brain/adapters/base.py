"""Ports: the only contract Brain logic sees. Swap a partner = swap an adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..schemas import (BrainEvent, CityDescriptor, CommandAck, PowerReading, SwitchCommand,
                       SwitchStatus, TrustVerdict)


class PartnerError(RuntimeError):
    """A partner was unreachable or sent something we refuse to use."""

    def __init__(self, partner: str, operation: str, detail: str, retryable: bool = True):
        super().__init__(f"[{partner}] {operation}: {detail}")
        self.partner, self.operation, self.detail, self.retryable = partner, operation, detail, retryable


@dataclass
class ReadingsBatch:
    valid: list[PowerReading] = field(default_factory=list)
    rejected: list[tuple[Any, str]] = field(default_factory=list)  # (raw item, reason)


class TwinPort(Protocol):
    def get_city(self) -> CityDescriptor: ...
    def get_readings(self) -> ReadingsBatch: ...
    def get_switches(self) -> list[SwitchStatus]: ...
    def send_command(self, cmd: SwitchCommand) -> CommandAck: ...


class GuardianPort(Protocol):
    def get_verdicts(self, subject_ids: list[str]) -> list[TrustVerdict]: ...
    def report(self, event: BrainEvent) -> None: ...

"""Action Manager + Result Verification.

An 'accepted' ack only means the Twin received the request. Success is decided
from what the sensors show afterwards:
  SUCCESS       switch reached the requested state and the load moved as predicted
  MISMATCH      switch moved but the load did not move as predicted -> model or data problem
  FAILED        switch did not reach the requested state before the deadline -> actuator failure
  UNVERIFIABLE  switch moved but the load evidence is untrusted or missing
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .adapters import PartnerError
from .decision import Candidate
from .schemas import AckStatus, CommandAck, SwitchCommand, SwitchOperation, SwitchState


@dataclass
class Pending:
    cmd: SwitchCommand
    candidate: Candidate
    sent_t: float
    deadline_t: float
    before: dict[str, float]
    moved_t: Optional[float] = None


@dataclass
class Verification:
    pending: Pending
    outcome: str
    detail: str
    observed: dict[str, float] = field(default_factory=dict)


def send_with_retry(twin, cmd: SwitchCommand, attempts: int) -> tuple[Optional[CommandAck], Optional[str]]:
    """Retries on transport errors with the SAME command_id, so the Twin's
    duplicate protection guarantees at most one execution."""
    last = None
    for _ in range(attempts):
        try:
            ack = twin.send_command(cmd)
        except PartnerError as e:
            last = str(e)
            if not e.retryable:
                break
            continue
        if ack.status in (AckStatus.accepted, AckStatus.duplicate):
            return ack, None
        return ack, f"rejected by Twin: {ack.detail or 'no detail'}"
    return None, last


NO_EFFECT_FRAC = 0.2   # observed shift below 20% of the predicted one on every zone = the switch did not act


def verify(p: Pending, state, t: float, tolerance: float) -> Optional[Verification]:
    """Returns a Verification when the outcome is known, None while still waiting."""
    st = state.switches.get(p.cmd.switch_id)
    target = SwitchState.closed if p.cmd.operation == SwitchOperation.close else SwitchState.open
    if st is not None and st.state == target:
        if p.moved_t is None:
            p.moved_t = t
            return None  # give the loads one sample to settle
        observed, errors, unverifiable = {}, [], []
        for z, predicted in p.candidate.shift_now.items():
            sensor = state.sensor_of(z)
            if sensor is None or state.verdict_status(sensor) == "untrusted" or state.zones[z].last_kw is None:
                unverifiable.append(z)
                continue
            obs = state.zones[z].last_kw - p.before[z]
            observed[z] = round(obs, 1)
            if abs(predicted) > 1e-6:
                errors.append(abs(obs - predicted) / abs(predicted))
        if not errors:
            return Verification(p, "UNVERIFIABLE", f"switch moved; no trusted load evidence for {unverifiable}", observed)
        worst = max(errors)
        pred = {z: round(v, 1) for z, v in p.candidate.shift_now.items()}
        moved = [abs(observed[z]) / abs(p.candidate.shift_now[z]) for z in observed
                 if abs(p.candidate.shift_now[z]) > 1e-6]
        if moved and max(moved) < NO_EFFECT_FRAC:
            # the status says it moved, the power did not: a spoofed status or a breaker that did not operate
            return Verification(p, "FAILED", f"{p.cmd.switch_id} reports {target.value} but no load moved "
                                f"(shift {observed} vs predicted {pred}): status not believed, switch treated as "
                                f"still {('open' if target == SwitchState.closed else 'closed')}", observed)
        if worst <= tolerance:
            return Verification(p, "SUCCESS", f"switch {target.value}; load shift {observed} vs predicted {pred}", observed)
        return Verification(p, "MISMATCH", f"switch {target.value} but load shift {observed} vs predicted {pred} "
                            f"(error {100 * worst:.0f}% > {100 * tolerance:.0f}%)", observed)
    if t >= p.deadline_t:
        state_txt = st.state.value if st else "unknown"
        return Verification(p, "FAILED", f"{p.cmd.switch_id} still {state_txt} {t - p.sent_t:.0f} s after command "
                            f"(expected {target.value})")
    return None

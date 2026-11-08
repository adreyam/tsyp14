"""Learning switch effects by observation (never by experiment).

Anti-poisoning: the Brain only offers an observation when every zone involved
has a Guardian-trusted sensor, holds it for `confirm_s`, and drops it if any of
those sensors turns untrusted or suspect meanwhile (see EnergyBrain._learn).

When a switch whose effect is unknown changes state (operator, scenario, another
system), compare zone loads just before and just after:
  - one zone drops, another rises by a similar amount -> tie switch, from -> to
  - one zone drops to ~0 and nothing rises            -> feeder switch for that zone
After `min_observations` consistent observations the effect is adopted and the
knowledge level is re-assessed. The Brain never toggles a switch to learn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Optional

from .config import LearningConfig
from .schemas import Switch, SwitchKind, SwitchState


@dataclass
class Observation:
    kind: SwitchKind
    from_zone: str
    to_zone: Optional[str]
    fraction: float


@dataclass
class EffectLearner:
    cfg: LearningConfig
    observations: dict[str, list[Observation]] = field(default_factory=dict)

    def observe(self, switch_id: str, new_state: SwitchState, before: dict[str, float],
                after: dict[str, float], commit: bool = True) -> tuple[Optional[Observation], str]:
        zones = [z for z in before if z in after]
        delta = {z: after[z] - before[z] for z in zones}
        sig = {z: d for z, d in delta.items() if abs(d) > self.cfg.min_shift_frac * max(before[z], after[z], 1.0)}
        down = [z for z, d in sig.items() if d < 0]
        up = [z for z, d in sig.items() if d > 0]
        obs = None
        if len(down) == 1 and len(up) == 1:
            moved = min(-sig[down[0]], sig[up[0]])
            if abs(-sig[down[0]] - sig[up[0]]) > 0.3 * max(-sig[down[0]], sig[up[0]]):
                return None, f"shift not balanced ({sig})"
            if new_state == SwitchState.closed:   # load left `down` for `up`
                obs = Observation(SwitchKind.tie, down[0], up[0], moved / max(before[down[0]], 1e-6))
            else:                                  # opening: load came back to `up`
                obs = Observation(SwitchKind.tie, up[0], down[0], moved / max(after[up[0]], 1e-6))
        elif len(down) == 1 and not up and after[down[0]] < 0.05 * before[down[0]] and new_state == SwitchState.open:
            obs = Observation(SwitchKind.feeder, down[0], None, 1.0)
        elif len(up) == 1 and not down and before[up[0]] < 0.05 * after[up[0]] and new_state == SwitchState.closed:
            obs = Observation(SwitchKind.feeder, up[0], None, 1.0)
        if obs is None:
            return None, f"inconclusive load change {({z: round(d) for z, d in delta.items()})}"
        if commit:
            self.commit(switch_id, obs)
        return obs, f"{obs.kind.value} {obs.from_zone}->{obs.to_zone or '(off)'} fraction {obs.fraction:.2f}"

    def commit(self, switch_id: str, obs: Observation) -> None:
        self.observations.setdefault(switch_id, []).append(obs)

    def learned(self, switch_id: str) -> Optional[Switch]:
        obs = self.observations.get(switch_id, [])
        if len(obs) < self.cfg.min_observations:
            return None
        recent = obs[-self.cfg.min_observations:]
        first = recent[0]
        if any((o.kind, o.from_zone, o.to_zone) != (first.kind, first.from_zone, first.to_zone) for o in recent):
            return None
        fr = [o.fraction for o in recent]
        if max(fr) - min(fr) > self.cfg.fraction_tolerance:
            return None
        return Switch(switch_id=switch_id, kind=first.kind, from_zone=first.from_zone, to_zone=first.to_zone,
                      transfer_fraction=round(mean(fr), 3) if first.kind == SwitchKind.tie else None)

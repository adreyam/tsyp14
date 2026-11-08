"""City knowledge: merge what the Twin advertises with an optional local file,
then decide how much autonomy the Brain is allowed in this city.

  L0 blind    no capacity known                    -> alerts only, never switch
  L1 bounded  capacities known, no usable switch   -> forecast overload, alert
  L2 mapped   >=1 switch with known effect whose    -> full predict/decide/act loop
              zones all have known capacity            (only on those switches)

Learned switch effects (from observed switching) plug in here in a later step.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .schemas import CityDescriptor, Switch, Zone


def merge(twin: CityDescriptor, local: Optional[CityDescriptor]) -> CityDescriptor:
    """Twin metadata wins on conflicts; the local file only fills gaps.
    Zones/switches the Twin does not advertise are ignored (we cannot read or act on them)."""
    if local is None:
        return twin
    lz = {z.zone_id: z for z in local.zones}
    ls = {s.switch_id: s for s in local.switches}
    zones = []
    for z in twin.zones:
        l = lz.get(z.zone_id)
        zones.append(Zone(zone_id=z.zone_id,
                          capacity_kw=z.capacity_kw if z.capacity_kw is not None else (l.capacity_kw if l else None),
                          tags=z.tags or (l.tags if l else [])))
    switches = []
    for s in twin.switches:
        l = ls.get(s.switch_id)
        pick = lambda a, b: a if a is not None else b  # noqa: E731
        switches.append(Switch(
            switch_id=s.switch_id,
            kind=pick(s.kind, l.kind if l else None),
            from_zone=pick(s.from_zone, l.from_zone if l else None),
            to_zone=pick(s.to_zone, l.to_zone if l else None),
            transfer_fraction=pick(s.transfer_fraction, l.transfer_fraction if l else None),
        ))
    meters = twin.aggregate_meters or local.aggregate_meters
    return CityDescriptor(city_id=twin.city_id, zones=zones, switches=switches, aggregate_meters=meters)


@dataclass(frozen=True)
class KnowledgeReport:
    level: str                       # "L0" | "L1" | "L2"
    zones_with_capacity: list[str]
    zones_blind: list[str]
    actionable_switches: list[str]
    unusable_switches: dict[str, str]  # switch_id -> why

    def as_payload(self) -> dict:
        return self.__dict__.copy()


def assess(city: CityDescriptor) -> KnowledgeReport:
    cap = {z.zone_id for z in city.zones if z.capacity_kw is not None}
    actionable, unusable = [], {}
    for s in city.switches:
        if not s.effect_known:
            unusable[s.switch_id] = "effect unknown (not advertised, not configured, not learned)"
            continue
        needed = [z for z in (s.from_zone, s.to_zone) if z]
        missing = [z for z in needed if z not in cap]
        if missing:
            unusable[s.switch_id] = f"capacity unknown for {', '.join(missing)}"
        else:
            actionable.append(s.switch_id)
    level = "L2" if actionable else ("L1" if cap else "L0")
    return KnowledgeReport(level=level,
                           zones_with_capacity=sorted(cap),
                           zones_blind=sorted(z.zone_id for z in city.zones if z.zone_id not in cap),
                           actionable_switches=sorted(actionable),
                           unusable_switches=unusable)

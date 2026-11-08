"""
Energy agent: grid reserve, load shedding, voltage, switch faults.

detect   reserve: GRID-01 energy_level under grid_alert_pct (alert) and under grid_shed_pct (critical: shed load);
                  back to normal over grid_restore_pct (hysteresis, so it does not flap)
         voltage: a utility gateway under voltage_low_v while its zone is supplied (watch)
         switch fault: a closed power switch that reads powered 0 % (alert)
         outage: a zone without power that the Brain did not cut (the Twin's own protection when the reserve is
               empty) -> restored as soon as the reserve allows
decide   alert: dim every street light to `dim_level` (the cheapest saving), back to auto when the reserve recovers
         shed: cut power to the zone with the highest load that is not protected (hospital zones in
               policy.protected_zones) and not flooded (its drainage pumps must keep running). The energy agent does not judge other axes: the orchestrator vetoes a cut
               in a zone with an active emergency, and then this agent chooses the next zone (on_refused + pending).
         restore: every zone it cut, once the reserve is back over grid_restore_pct.

A2A skills (the energy axis owns the feeders, the zone power and the street lights; other axes ask it)
    isolate_feeder      {incident, zone}  building fire: de-energise the zone's feeder for the firefighters (rejected in a
                        protected hospital zone: the fire crews isolate locally); restore_feeder {incident} re-energises it
    cut_zone_power      {zone, reason}    electrical safety (critical flood); rejected in a protected zone;
                        restore_zone_power {zone} restores it
    street_lights_on    every street light on (storm visibility); wins over dimming while it lasts.
                        street_lights_auto: back to the schedule (or to the dimmed level if the reserve is still low)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from citybrain.contracts import Action, Event
from citybrain.agents.base import Agent, CityView, Finding, Plan, loc_of, service_zone_id, zone_from_service_id


class EnergyAgent(Agent):
    """Energy: grid reserve, load shedding, feeders, zone power and street lights."""
    axis = "energy"
    families = {"grid", "utility", "power_switch", "service_zone", "power_meter"}
    incident_kinds: set = set()          # fires reach energy as A2A isolate_feeder requests from the emergency agent
    skills = {
        "isolate_feeder": "de-energise the feeder of a burning building's zone (input: incident, zone)",
        "restore_feeder": "re-energise a feeder isolated for a fire (input: incident)",
        "cut_zone_power": "cut a zone's power for electrical safety (input: zone, reason)",
        "restore_zone_power": "restore a zone's power cut for another axis (input: zone)",
        "street_lights_on": "every street light on (input: reason)",
        "street_lights_auto": "street lights back to their schedule",
    }

    def __init__(self, cfg):
        super().__init__(cfg)
        self.reserve = "ok"
        self.level = 100.0
        self.shed: List[str] = []           # service zone ids cut by this agent
        self.vetoed: dict = {}              # zone -> why the orchestrator refused
        self.retry: Optional[Finding] = None
        self._retrying = False
        self.low_v: set = set()
        self.faults: set = set()
        self.dimmed = False
        self.isolated: dict = {}            # incident -> service zone id cut for firefighters
        self.outages: set = set()
        self.cut_for: Dict[str, str] = {}   # service zone id -> axis that asked for the cut (A2A)
        self.lights_on: set = set()         # axes that asked for every street light on

    def detect(self, ev: Event, city: CityView) -> List[Finding]:
        fam, dev = ev.origin.family, ev.origin.device_id
        if fam == "grid":
            return self._reserve(ev)
        if fam == "service_zone":
            return self._outage(ev, city)
        if fam == "utility":
            v = ev.value("voltage", 230.0)
            low = self.num("voltage_low_v", 205)
            powered = city.value(service_zone_id(ev.location.zone), "power_supplied", 100.0)
            if v < low and powered and dev not in self.low_v:
                self.low_v.add(dev)
                return [Finding(f"energy:voltage:{dev}", f"Low voltage, zone {ev.location.zone}", "voltage", "watch", 0.8,
                                f"{dev}: {v:.0f} V (limit {low:g} V) while zone {ev.location.zone} is supplied", ev.location)]
            if v >= low + 5 and dev in self.low_v:
                self.low_v.discard(dev)
                return [Finding(f"energy:voltage:{dev}", f"Low voltage, zone {ev.location.zone}", "voltage", "info", 0.8,
                                f"{dev} back to {v:.0f} V", ev.location, resolved=True)]
            return []
        if fam == "power_switch":
            closed, powered = ev.value("switch_closed", 100.0), ev.value("powered", 100.0)
            if closed > 50 and powered < 50 and dev not in self.faults:
                self.faults.add(dev)
                return [Finding(f"energy:switch:{dev}", f"Feeder fault at {dev}", "switch", "alert", 0.85,
                                f"{dev} is closed but its feeder reads unpowered: upstream fault, crew needed", ev.location)]
            if powered > 50 and dev in self.faults:
                self.faults.discard(dev)
                return [Finding(f"energy:switch:{dev}", f"Feeder fault at {dev}", "switch", "info", 0.85,
                                f"{dev} powered again", ev.location, resolved=True)]
        return []

    def _outage(self, ev: Event, city: CityView) -> List[Finding]:
        dev = ev.origin.device_id
        supplied = ev.value("power_supplied", 100.0)
        zone = zone_from_service_id(dev)
        ours = dev in self.shed or dev in self.isolated.values() or dev in self.cut_for or f"{dev}:power" in city.owned
        if supplied < 50 and not ours and dev not in self.outages:
            self.outages.add(dev)
            return [Finding(f"energy:outage:{dev}", f"Power outage, zone {zone}", "outage", "alert", 0.9,
                            f"{dev} reads power_supplied {supplied:.0f} % and the Brain did not cut it "
                            f"(the Twin's own protection or a fault); reserve {self.level:.0f} %", ev.location, {"device": dev})]
        if supplied >= 50 and dev in self.outages:
            self.outages.discard(dev)
            return [Finding(f"energy:outage:{dev}", f"Power outage, zone {zone}", "outage", "info", 0.9,
                            f"{dev} supplied again", ev.location, {"device": dev}, resolved=True)]
        if dev in self.outages and self.level >= self.num("grid_restore_pct", 35):
            return [Finding(f"energy:outage:{dev}", f"Power outage, zone {zone}", "outage", "alert", 0.9,
                            f"reserve back to {self.level:.0f} %: zone {zone} can be restored", ev.location,
                            {"device": dev, "restore": True})]
        return []

    def _reserve(self, ev: Event) -> List[Finding]:
        lvl = ev.value("energy_level", 100.0)
        self.level = lvl
        alert, shed, restore = self.num("grid_alert_pct", 25), self.num("grid_shed_pct", 15), self.num("grid_restore_pct", 35)
        if lvl < shed:
            new = "shed"
        elif lvl < alert:
            new = "alert" if self.reserve in ("ok", "alert") else self.reserve
        elif lvl >= restore:
            new = "ok"
        else:
            new = self.reserve
        if new == self.reserve:
            if self.retry is not None and new == "shed":
                f, self.retry, self._retrying = self.retry, None, True
                f.reason = f"GRID-01 reserve {lvl:.0f} %: previous choice vetoed ({'; '.join(f'{z}: {w}' for z, w in self.vetoed.items())})"
                f.evidence["level"] = lvl
                return [f]
            return []
        old, self.reserve = self.reserve, new
        if new == "ok":
            self.vetoed.clear()
            return [Finding("energy:reserve", "Grid reserve low", "reserve", "info", 0.95,
                            f"GRID-01 reserve back to {lvl:.0f} % (restore level {restore:g} %)", ev.location, {"level": lvl}, resolved=True)]
        sev = "critical" if new == "shed" else "alert"
        return [Finding("energy:reserve", "Grid reserve low", "reserve", sev, 0.95,
                        f"GRID-01 reserve {lvl:.0f} % (alert {alert:g} %, shed {shed:g} %), demand "
                        f"{ev.value('grid_demand', 0):.0f} kW", ev.location, {"level": lvl, "previous": old})]

    def decide(self, f: Finding, city: CityView) -> List[Plan]:
        if f.kind == "isolation":
            return self._isolate(f, city)
        if f.kind == "outage":
            if f.evidence.get("restore"):
                dev = f.evidence["device"]
                return [Plan(dev, "restore_power", "restore_power", location=loc_of(city, dev),
                             reason=f"{f.reason}; restore power to {dev}")]
            return []
        if f.kind != "reserve":
            return []
        if f.resolved:
            plans = [Plan(z, "restore_power", "restore_power", location=loc_of(city, z), reason=f"reserve recovered: restore power to {z}") for z in self.shed]
            if self.dimmed:
                self.dimmed = False
                if not self.lights_on:
                    plans.append(Plan("LIGHTS", "auto", "street_lights", reason="reserve recovered: street lights back to the adaptive schedule"))
            self.shed = []
            return plans
        plans: List[Plan] = []
        if self.lights_on:
            f.reason += f"; street lights stay on (asked by {', '.join(sorted(self.lights_on))} for safety): no dimming"
        elif not self.dimmed:
            self.dimmed = True
            dim = self.num("dim_level", 0.6)
            plans.append(Plan("LIGHTS", "dim", "street_lights", value=dim, priority=2,
                              reason=f"reserve {f.evidence.get('level', self.level):.0f} %: dim every street light to {dim:.0%} "
                                     f"(the cheapest saving, no service lost)"))
        if f.severity != "critical":
            return plans
        return plans + self._shed_if_needed(f, city)

    def _isolate(self, f: Finding, city: CityView) -> List[Plan]:
        iid = f.evidence["incident"]
        if f.resolved:
            dev = self.isolated.pop(iid, None)
            return [Plan(dev, "restore_power", "restore_power", location=loc_of(city, dev),
                         reason=f"fire {iid} out: re-energise {dev}")] if dev else []
        zone = f.evidence["zone"]
        if zone in self.cfg.policy.protected_zones:
            self.isolated[iid] = None
            f.reason += f"; zone {zone} is protected (hospital): power kept on, the fire crews isolate locally"
            return []
        dev = service_zone_id(zone)
        self.isolated[iid] = dev
        return [Plan(dev, "cut_power", "isolate_feeder", priority=1, location=loc_of(city, dev), reversible=False,
                     reason=f"{f.reason}: isolate the feeder of zone {zone} ({dev})", parameters={"incident_id": iid})]

    # ------------------------------------------------------------------ A2A
    def on_task(self, task, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        inp, sk, who = task.input, task.skill, task.from_agent
        if sk == "isolate_feeder":
            iid, zone = str(inp.get("incident", "")), str(inp.get("zone", "")).upper()
            if not iid or zone not in "ABCDEFGHIJKLMNOP" or len(zone) != 1:
                self.a2a.update(task, "rejected", "isolate_feeder needs an incident id and a zone A..P")
                return []
            f = Finding(f"energy:isolation:{iid}", f"Feeder isolation, zone {zone}", "isolation", "alert", 0.9,
                        f"{who} asked (A2A): {task.reason}", loc_of(city, service_zone_id(zone)), {"incident": iid, "zone": zone})
            if iid in self.isolated:
                self.a2a.update(task, "completed", f"already handled for {iid}")
                return []
            plans = self._isolate(f, city)
            if not plans:
                self.a2a.update(task, "rejected", f"zone {zone} is protected (hospital): power kept on, the fire crews isolate locally")
                return [(f, [])]
            plans[0].parameters["a2a_task"] = task.task_id
            return [(f, plans)]
        if sk == "restore_feeder":
            iid = str(inp.get("incident", ""))
            dev = self.isolated.get(iid)
            f = Finding(f"energy:isolation:{iid}", f"Feeder isolation, zone {zone_from_service_id(dev) if dev else '?'}", "isolation",
                        "info", 0.95, f"{who} asked (A2A): {task.reason}", loc_of(city, dev) if dev else None,
                        {"incident": iid}, resolved=True)
            plans = self._isolate(f, city)
            if not plans:
                self.a2a.update(task, "completed", f"nothing was isolated for {iid}: nothing to restore")
                return [(f, [])]
            plans[0].parameters["a2a_task"] = task.task_id
            return [(f, plans)]
        if sk in ("cut_zone_power", "restore_zone_power"):
            zone = str(inp.get("zone", "")).upper()
            if len(zone) != 1 or zone not in "ABCDEFGHIJKLMNOP":
                self.a2a.update(task, "rejected", f"{sk} needs a zone A..P")
                return []
            dev = service_zone_id(zone)
            key = f"energy:safety_cut:{zone}"
            if sk == "cut_zone_power":
                if zone in self.cfg.policy.protected_zones:
                    self.a2a.update(task, "rejected", f"zone {zone} is protected (hospital): its power is never cut")
                    return []
                if dev in self.cut_for:
                    self.a2a.update(task, "completed", f"{dev} already cut (for {self.cut_for[dev]})")
                    return []
                self.cut_for[dev] = who
                f = Finding(key, f"Safety power cut, zone {zone}", "safety_cut", "critical", 0.9,
                            f"{who} asked (A2A): {task.reason}", loc_of(city, dev), {"zone": zone})
                return [(f, [Plan(dev, "cut_power", "cut_power", priority=0, location=loc_of(city, dev), reversible=False,
                                  reason=f"{who} asks (A2A): {task.reason}: cut the power of zone {zone} ({dev})",
                                  parameters={"a2a_task": task.task_id, "zone": zone})])]
            if self.cut_for.pop(dev, None) is None:
                self.a2a.update(task, "completed", f"{dev} was not cut for {who}: nothing to restore")
                return []
            f = Finding(key, f"Safety power cut, zone {zone}", "safety_cut", "info", 0.9, f"{who} asked (A2A): {task.reason}",
                        loc_of(city, dev), {"zone": zone}, resolved=True)
            if dev in self.shed:
                self.a2a.update(task, "completed", f"{dev} stays off: it is also shed for the low reserve")
                return [(f, [])]
            return [(f, [Plan(dev, "restore_power", "restore_power", location=loc_of(city, dev),
                              reason=f"{who} asks (A2A): {task.reason}: restore the power of zone {zone}",
                              parameters={"a2a_task": task.task_id})])]
        if sk == "street_lights_on":
            first = not self.lights_on
            self.lights_on.add(who)
            f = Finding("energy:lights_on", "Street lights forced on", "lights", "watch", 0.9, f"{who} asked (A2A): {task.reason}")
            if not first:
                self.a2a.update(task, "completed", "already on")
                return [(f, [])]
            return [(f, [Plan("LIGHTS", "on", "street_lights", priority=1,
                              reason=f"{who} asks (A2A): {task.reason}: every street light on"
                                     + (" (instead of dimmed: safety first)" if self.dimmed else ""),
                              parameters={"a2a_task": task.task_id})])]
        if sk == "street_lights_auto":
            self.lights_on.discard(who)
            f = Finding("energy:lights_on", "Street lights forced on", "lights", "info", 0.9, f"{who} asked (A2A): {task.reason}",
                        resolved=not self.lights_on)
            if self.lights_on:
                self.a2a.update(task, "completed", f"kept on: {', '.join(sorted(self.lights_on))} still needs them on")
                return [(f, [])]
            if self.reserve in ("alert", "shed"):
                self.dimmed = True
                dim = self.num("dim_level", 0.6)
                return [(f, [Plan("LIGHTS", "dim", "street_lights", value=dim,
                                  reason=f"{who} asks (A2A): {task.reason}; the reserve is still low ({self.level:.0f} %): "
                                         f"dimmed to {dim:.0%} rather than the schedule", parameters={"a2a_task": task.task_id})])]
            return [(f, [Plan("LIGHTS", "auto", "street_lights", reason=f"{who} asks (A2A): {task.reason}: street lights back to the adaptive schedule",
                              parameters={"a2a_task": task.task_id})])]
        self.a2a.update(task, "rejected", f"unknown skill {sk}")
        return []

    def _shed_if_needed(self, f: Finding, city: CityView) -> List[Plan]:
        if self.shed and not self._retrying:
            return []                       # a zone is already shed for this low reserve
        if self._retrying:
            # The zone it chose was vetoed (an emergency there): it does not cut another neighbourhood instead (the
            # situation stays in its zone); it saves energy on the street lights, unless they are forced on for safety.
            self._retrying = False
            low = self.num("dim_level_low", 0.35)
            f.reason += f"; no other neighbourhood is cut: street lights dimmed further to {low:.0%} instead"
            if self.lights_on:
                f.reason += f" (not possible: street lights forced on by {', '.join(sorted(self.lights_on))})"
                return []
            self.dimmed = True
            return [Plan("LIGHTS", "dim", "street_lights", value=low, priority=1,
                         reason=f"reserve {self.level:.0f} %: the zone chosen for load shedding was vetoed (an emergency there); "
                                f"instead of cutting another neighbourhood, street lights dimmed to {low:.0%}")]
        return self._shed(f, city)

    def _shed(self, f: Finding, city: CityView) -> List[Plan]:
        loads = []
        for dev in city.family("service_zone"):
            if dev in self.shed:
                continue
            loads.append((city.value(dev, "zone_power", 0.0) or 0.0, dev, zone_from_service_id(dev)))
        skipped = []
        for kw, dev, zone in sorted(loads, reverse=True):
            if any(s.kind == "flood" for s in city.active("emergency", zone)):
                # cross-domain priority: the drainage pumps of a flooded zone are never de-energised
                skipped.append(f"{zone} flood pumps running")
                continue
            if zone in self.cfg.policy.protected_zones:
                skipped.append(f"{zone} protected (hospital)")
                continue
            if zone in self.vetoed:
                skipped.append(f"{zone} vetoed")
                continue
            if (city.value(dev, "power_supplied", 100.0) or 0) < 50:
                continue
            self.shed.append(dev)
            why = f"; skipped {', '.join(skipped)}" if skipped else ""
            return [Plan(dev, "cut_power", "load_shed", priority=1, location=loc_of(city, dev), reversible=False,
                         reason=f"reserve {f.evidence.get('level', self.level):.0f} %: shed zone {zone}, the largest load "
                                f"({kw:.0f} kW) that may be cut{why}",
                         parameters={"zone": zone, "load_kw": round(kw, 1), "skipped": skipped})]
        return []

    def on_refused(self, action: Action, why: str, city: CityView) -> None:
        if action.action_type != "load_shed":
            return
        zone = zone_from_service_id(action.target.actuator_id)
        if action.target.actuator_id in self.shed:
            self.shed.remove(action.target.actuator_id)
        if zone:
            self.vetoed[zone] = why
        self.retry = Finding("energy:reserve", "Grid reserve low", "reserve", "critical", 0.95,
                             f"choice of zone {zone} vetoed: {why}", action.location, {"level": self.level})

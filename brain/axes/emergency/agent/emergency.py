"""
Emergency agent (template for the other three): incidents, earthquakes, floods, storms.

detect
    incident   every Twin incident (fire, collision, water-main break ...) opens a situation; it closes when the Twin resolves it
    earthquake shaking_intensity (MMI) above the alert level on at least `seismic_confirm_sensors` stations within
               `seismic_window_s` (two-station confirmation, as the PLUM method of the emergency axis); one station alone
               is "pending confirmation" and never triggers an action
    flood      surface_water_depth above the alert level and still rising (or above the critical level)
    storm      wind or rainfall above the alert level at any weather station
decide
    incident   send the extra units the Twin has not already sent (INCIDENTS dispatch {id, kind}); for a water-main
               break, close the nearest water valve the Guardian has not quarantined
    earthquake critical: cut water in the epicentre zone; undone when the shaking stops
    the rest is asked of the axis that owns the actuators (A2A, citybrain/a2a.py), and that axis does it its own way:
    units      every fire engine, ambulance and police car on its way to a scene (and an ambulance carrying a patient to
               hospital) -> transportation green_wave, so the signals ahead of it turn green as it drives
    fire       building fire -> energy isolate_feeder (firefighters on a de-energised building); restore_feeder when out
    earthquake critical and confirmed -> transportation stop_traffic; resume_traffic when the shaking stops
    flood      -> transportation close_car_parks and detour_traffic (police cars to the junctions at both ends of the
               flooded street, cross traffic kept moving); an ambulance sent to stand by near the water (INCIDENTS
               send_unit); critical -> energy cut_zone_power (electrical safety); all undone and recalled when over
    one zone   a scenario stays in its zone: the epicentre of a quake is fixed once confirmed (the strongest station
               then), the water is cut and the traffic stopped only there
    storm      -> energy street_lights_on; street_lights_auto when the storm ends
explain
    every finding and plan says which reading, which threshold and which device, in plain words
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from citybrain.contracts import Event, Location
from citybrain.agents.base import (MOVING, Agent, CityView, Finding, Plan, edge_id, junction_point, loc_of, road_segment,
                                   service_zone_id)

EXTRA_UNITS = {
    "building_fire": ["fire", "ambulance", "police"], "vehicle_fire": ["fire", "ambulance"],
    "red_light_tbone": ["ambulance", "police"], "unsafe_overtake": ["ambulance", "police"],
    "rear_end": ["police"], "breakdown": ["tow"], "lost_load": ["tow"], "water_main_break": ["police"],
}
CRITICAL_INCIDENTS = {"building_fire", "vehicle_fire", "red_light_tbone", "unsafe_overtake"}
# Hospital entrances (twin/runtime/sumo/emergency_depots.json, the "door" of each EMS station): where an
# ambulance with a patient is going, so its green wave stops at the right road.
HOSPITALS = {"HOSPITAL-CENTRAL": (-37.0, -15.9), "HOSPITAL-WEST": (-218.0, -117.3)}


class EmergencyAgent(Agent):
    """Emergency: incidents, earthquakes, floods and storms; asks the other axes for green waves, power and car parks."""
    axis = "emergency"
    families = {"seismic", "flood", "weather"}
    incident_kinds = set(EXTRA_UNITS)
    skills = {"dispatch_unit": "send one more unit to an open Twin incident (input: incident, kind police|ambulance|fire|tow)"}

    def __init__(self, cfg):
        super().__init__(cfg)
        self.shake: Dict[str, tuple] = {}            # station -> (mmi, t, zone, x, z)
        self.quake_open = False
        self.flood_open: Dict[str, bool] = {}
        self.storm_open = False
        self.sent_units: Dict[str, set] = {}         # incident -> unit kinds the Brain asked for
        self.waves: Dict[tuple, str] = {}            # (unit, incident, leg) -> green_wave task id
        self.asked: Dict[str, str] = {}              # what this agent asked another axis for -> task id
        self.epicentre: Location = None              # fixed once a quake is confirmed and settled: the scenario stays there
        self.epi_since = 0.0                         # when the quake was confirmed
        self.epi_mmi = 0.0
        self.tags: Dict[str, dict] = {}              # tag of units sent without an incident (FLOOD-J) -> {x, z, zone}

    # ------------------------------------------------------------------ detect
    def detect(self, ev: Event, city: CityView) -> List[Finding]:
        if ev.type == "incident":
            return self._incident(ev)
        fam = ev.origin.family
        if fam == "seismic":
            return self._seismic(ev)
        if fam == "flood":
            return self._flood(ev)
        if fam == "weather":
            return self._storm(ev, city)
        return []

    def _incident(self, ev: Event) -> List[Finding]:
        inc = ev.incident
        sev = "critical" if inc.kind in CRITICAL_INCIDENTS else "alert"
        where = f" in zone {ev.location.zone}" if ev.location else ""
        title = f"{(inc.title or inc.kind.replace('_', ' ')).capitalize()}{where}"
        if inc.status == "resolved":
            return [Finding(f"emergency:incident:{inc.id}", title, "incident", sev, 0.99,
                            f"the Twin reports {inc.id} resolved", ev.location, {"incident": inc.id}, resolved=True)]
        on_scene = [u for u in (ev.state.get("units") or []) if u]
        return [Finding(f"emergency:incident:{inc.id}", title, "incident", sev, 0.97,
                        f"Twin incident {inc.id} ({inc.kind}, phase {inc.phase or 'reported'}){where}; units already sent: "
                        f"{', '.join(on_scene) or 'none'}", ev.location,
                        {"incident": inc.id, "kind": inc.kind, "units": on_scene})]

    def _seismic(self, ev: Event) -> List[Finding]:
        mmi = ev.value("shaking_intensity", 1.0) or 1.0
        alert, critical = self.num("seismic_mmi_alert", 5.0), self.num("seismic_mmi_critical", 6.5)
        window, need = self.num("seismic_window_s", 6), int(self.num("seismic_confirm_sensors", 2))
        now = self.now()
        dev = ev.origin.device_id
        if mmi >= alert:
            self.shake[dev] = (mmi, now, ev.location.zone, ev.location.x, ev.location.z)
        else:
            self.shake.pop(dev, None)
        recent = {d: s for d, s in self.shake.items() if now - s[1] <= window}
        self.shake = recent
        if not recent:
            if self.quake_open:
                self.quake_open = False
                self.epicentre = None
                return [Finding("emergency:earthquake", "Earthquake", "earthquake", "alert", 0.95,
                                f"shaking back below MMI {alert:g} on every station", ev.location, resolved=True)]
            return []
        top_dev, top = max(recent.items(), key=lambda kv: kv[1][0])
        loc = Location(zone=top[2], x=top[3], z=top[4])
        stations = ", ".join(f"{d} MMI {s[0]:.1f}" for d, s in sorted(recent.items(), key=lambda kv: -kv[1][0])[:4])
        if len(recent) < need:
            return [Finding("emergency:earthquake", f"Seismic trigger in zone {top[2]} (unconfirmed)", "earthquake", "watch", 0.4,
                            f"{stations}: only {len(recent)} station(s) above MMI {alert:g}, {need} needed to confirm; "
                            f"no action until a second station agrees", loc, {"stations": list(recent), "confirmed": False})]
        self.quake_open = True
        settle = self.num("seismic_settle_s", 1.5)
        if self.epicentre is None:
            self.epicentre, self.epi_since, self.epi_mmi = loc, now, top[0]
        elif now - self.epi_since < settle and top[0] > self.epi_mmi:
            self.epicentre, self.epi_mmi = loc, top[0]   # still settling: the strongest station so far
        settled = now - self.epi_since >= settle      # then fixed: the water cut and the traffic stop go there only
        loc = self.epicentre
        sev = "critical" if top[0] >= critical else "alert"
        return [Finding("emergency:earthquake", f"Earthquake, epicentre near zone {loc.zone}", "earthquake", sev,
                        min(0.99, 0.7 + 0.1 * len(recent)),
                        f"confirmed by {len(recent)} stations ({stations}); strongest {top_dev}", loc,
                        {"stations": list(recent), "confirmed": True, "mmi": top[0], "settled": settled})]

    def _flood(self, ev: Event) -> List[Finding]:
        depth, rise = ev.value("surface_water_depth", 0.0), ev.value("rise_rate", 0.0)
        alert, crit = self.num("flood_depth_alert_mm", 150), self.num("flood_depth_critical_mm", 300)
        zone = ev.location.zone
        key = f"emergency:flood:{zone}"
        if depth >= crit or (depth >= alert and rise >= -0.5):
            self.flood_open[zone] = True
            sev = "critical" if depth >= crit else "alert"
            return [Finding(key, f"Flood in zone {zone}", "flood", sev, 0.9,
                            f"{ev.origin.device_id} reads {depth:.0f} mm of water (alert {alert:g}, critical {crit:g}), "
                            f"rising {rise:+.1f} mm/min", ev.location, {"device": ev.origin.device_id, "depth_mm": depth})]
        if self.flood_open.get(zone) and depth < alert * 0.6:
            self.flood_open[zone] = False
            return [Finding(key, f"Flood in zone {zone}", "flood", "watch", 0.9,
                            f"{ev.origin.device_id} back to {depth:.0f} mm (below {alert * 0.6:.0f} mm)", ev.location, resolved=True)]
        return []

    def _storm(self, ev: Event, city: CityView) -> List[Finding]:
        wind, rain = ev.value("wind_speed", 0.0), ev.value("rainfall", 0.0)
        wa, ra = self.num("wind_alert_mps", 15), self.num("rain_alert_mm_h", 10)
        if wind >= wa or rain >= ra:
            self.storm_open = True
            return [Finding("emergency:storm", "Storm over the city", "storm", "watch", 0.85,
                            f"{ev.origin.device_id}: wind {wind:.1f} m/s (alert {wa:g}), rain {rain:.1f} mm/h (alert {ra:g})",
                            ev.location, {"wind": wind, "rain": rain})]
        if self.storm_open:
            others = [d for d in city.family("weather") if d != ev.origin.device_id
                      and ((city.value(d, "wind_speed", 0) or 0) >= wa or (city.value(d, "rainfall", 0) or 0) >= ra)]
            if not others:
                self.storm_open = False
                return [Finding("emergency:storm", "Storm over the city", "storm", "watch", 0.85,
                                "every weather station back below the storm thresholds", ev.location, resolved=True)]
        return []

    # ------------------------------------------------------------------ decide
    def decide(self, f: Finding, city: CityView) -> List[Plan]:
        if f.kind == "incident":
            return self._plan_incident(f, city)
        if f.kind == "earthquake":
            return self._plan_quake(f, city)
        if f.kind == "flood":
            return self._plan_flood(f, city)
        if f.kind == "storm":
            if f.resolved:
                if self.asked.pop("lights", None):
                    self.ask("energy", "street_lights_auto", {}, "the storm is over", context=f.key)
            elif "lights" not in self.asked:
                t = self.ask("energy", "street_lights_on", {}, "storm: visibility for drivers and responders", context=f.key)
                self.asked["lights"] = t.task_id
            return []
        return []

    def on_task(self, task, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        iid, kind = str(task.input.get("incident", "")), str(task.input.get("kind", ""))
        if task.skill != "dispatch_unit" or kind not in ("police", "ambulance", "fire", "tow"):
            self.a2a.update(task, "rejected", "dispatch_unit needs an incident id and kind police|ambulance|fire|tow")
            return []
        inc = city.incidents.get(iid)
        if inc is None:
            self.a2a.update(task, "rejected", f"{iid or '?'} is not an open incident of the Twin")
            return []
        f = Finding(f"emergency:incident:{iid}", f"{inc['kind'].replace('_', ' ').capitalize()}", "incident", "alert", 0.9,
                    f"{task.from_agent} asked (A2A): {task.reason}", Location.at(inc["x"], inc["z"]), {"incident": iid})
        return [(f, [Plan("INCIDENTS", "dispatch", "dispatch_unit", value={"id": iid, "kind": kind}, priority=0,
                          reason=f"{task.from_agent} asks (A2A) for a {kind} unit at {iid}: {task.reason}",
                          parameters={"unit_kind": kind, "incident_id": iid, "a2a_task": task.task_id})])]

    # ------------------------------------------------------------------ units on the move -> green waves (A2A)
    def tick(self, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        kinds = set(self.cfg.agent("transportation").get("green_wave_kinds") or ["fire", "ambulance", "police"])
        live = set()
        for u in city.units.values():
            if u["kind"] not in kinds:
                continue
            leg = None
            if u["incident"] and u["status"] in (MOVING | {"mobilizing"}) - {"returning"}:
                leg = "scene"
            elif u["status"] == "returning" and u["response"]:
                leg = "hospital"                          # a patient on board: lights and sirens back to hospital
            if leg is None:
                continue
            key = (u["unit"], u["incident"], leg)
            live.add(key)
            if key in self.waves:
                continue
            inc = city.incidents.get(u["incident"]) or self.tags.get(u["incident"]) or {}
            dest = {"x": inc["x"], "z": inc["z"]} if leg == "scene" and "x" in inc else None
            if leg == "hospital" and u.get("depot") in HOSPITALS:
                dest = dict(zip(("x", "z"), HOSPITALS[u["depot"]]))
            what = (inc.get("kind") or "incident").replace("_", " ")
            why = (f"{u['unit']} ({u['kind']}) is on its way to {u['incident']} ({what}): it needs green along its route"
                   if leg == "scene" else f"{u['unit']} carries a patient to hospital: it needs green along its route")
            t = self.ask("transportation", "green_wave", {"unit": u["unit"], "kind": u["kind"], "incident": u["incident"] or None,
                                                          "destination": dest, "leg": leg}, why,
                         context=f"emergency:incident:{u['incident']}" if u["incident"] else None)
            self.waves[key] = t.task_id
        for key in [k for k in self.waves if k not in live]:
            del self.waves[key]                       # that leg is over (on scene, back home): the wave closes itself
        return []

    def _plan_incident(self, f: Finding, city: CityView) -> List[Plan]:
        if f.resolved:
            iid = f.evidence.get("incident", "")
            self.sent_units.pop(iid, None)
            if self.asked.pop(f"isolate:{iid}", None):
                self.ask("energy", "restore_feeder", {"incident": iid}, f"fire {iid} is out: the building can be re-energised",
                         context=f.key)
            return []
        iid, kind = f.evidence["incident"], f.evidence["kind"]
        if kind == "building_fire" and f.location is not None and f"isolate:{iid}" not in self.asked:
            t = self.ask("energy", "isolate_feeder", {"incident": iid, "zone": f.location.zone},
                         f"building fire {iid} in zone {f.location.zone}: the firefighters need the building de-energised",
                         context=f.key)
            self.asked[f"isolate:{iid}"] = t.task_id
        present = set(f.evidence.get("units") or [])
        asked = self.sent_units.setdefault(iid, set())
        plans: List[Plan] = []
        for unit in EXTRA_UNITS.get(kind, []):
            if unit in asked or (unit in present and kind not in ("building_fire",)):
                continue
            asked.add(unit)
            plans.append(Plan("INCIDENTS", "dispatch", "dispatch_unit", value={"id": iid, "kind": unit}, priority=0,
                              reason=f"{kind.replace('_', ' ')} {iid}: send {'an' if unit[0] in 'aeiou' else 'a'} {unit} unit "
                                     f"({('every kind of emergency vehicle for a building fire: one more ' + unit) if kind == 'building_fire' else 'none on scene yet'})",
                              parameters={"unit_kind": unit, "incident_id": iid}))
        if kind == "water_main_break" and f.location is not None and "valve" not in asked:
            valve = city.nearest("water_valve", f.location.x, f.location.z)
            skipped = [d for d in city.family("water_valve") if d in city.quarantine]
            if valve:
                asked.add("valve")
                note = f"; skipped quarantined {', '.join(skipped)}" if skipped else ""
                plans.append(Plan(valve, "close", "close_valve", priority=0, location=loc_of(city, valve),
                                  reason=f"water-main break {iid}: close the nearest valve {valve} to stop the leak{note}",
                                  parameters={"incident_id": iid}))
        return plans

    def _plan_quake(self, f: Finding, city: CityView) -> List[Plan]:
        zone = f.location.zone if f.location else None
        if f.resolved:
            plans = []
            if self.asked.pop("stop_traffic", None):
                self.ask("transportation", "resume_traffic", {}, "the shaking stopped: traffic can move again", context=f.key)
            if zone and self.asked.get(f"lockdown:{zone}") == "quake":
                # (the zone stays closed until the Twin's clean-up reopens it; the next earthquake there closes it again)
                self.asked.pop(f"lockdown:{zone}", None)
            for res, sit in list(city.owned.items()):
                if sit == "emergency:earthquake" and res.endswith(":water"):
                    plans.append(Plan(res.rsplit(":", 1)[0], "restore_water", "restore_water", reason="shaking stopped: water supply restored"))
            return plans
        if f.severity != "critical" or not f.evidence.get("confirmed") or not f.evidence.get("settled"):
            return []
        lock: List[Plan] = []
        if zone and f"lockdown:{zone}" not in self.asked:
            # The epicentre zone is closed: its four entrances blocked (barriers, police cars) until the damage is
            # cleared in the Twin (which reopens it itself); not reopened when the shaking stops.
            self.asked[f"lockdown:{zone}"] = "quake"
            lock.append(Plan("ZONES", "lockdown", "zone_lockdown", value={"zone": zone, "reason": "earthquake"}, priority=0,
                             reason=f"confirmed earthquake, epicentre zone {zone}: close the zone's four entrances so no car "
                                    f"drives into the damaged streets", parameters={"zone": zone}))
        if "stop_traffic" not in self.asked:
            t = self.ask("transportation", "stop_traffic", {"zone": zone},
                         f"confirmed earthquake ({f.evidence.get('mmi', 0):.1f} MMI, epicentre near zone {zone}): stop all traffic "
                         f"until the shaking stops", context=f.key)
            self.asked["stop_traffic"] = t.task_id
        if f"{service_zone_id(zone)}:water" in city.owned:
            return lock
        return lock + [Plan(service_zone_id(zone), "cut_water", "cut_water", priority=0, location=loc_of(city, service_zone_id(zone)), reversible=False,
                     reason=f"epicentre zone {zone}: cut the water supply to limit damage from broken mains")]

    def _plan_flood(self, f: Finding, city: CityView) -> List[Plan]:
        zone = f.location.zone
        tag = f"FLOOD-{zone}"
        if f.resolved:
            plans: List[Plan] = []
            if self.asked.pop(f"detour:{zone}", None):
                self.ask("transportation", "end_detour", {"zone": zone, "tag": tag}, f"the flood in zone {zone} is over", context=f.key)
            if self.asked.pop(f"standby:{zone}", None):
                for u in city.units.values():
                    if u["incident"] == tag and u["kind"] == "ambulance":
                        plans.append(Plan("INCIDENTS", "recall_unit", "recall_unit", value=u["unit"],
                                          reason=f"the flood in zone {zone} is over: {u['unit']} back to its station",
                                          parameters={"unit": u["unit"], "tag": tag}))
                self.tags.pop(tag, None)
            if self.asked.pop(f"parks:{zone}", None):
                self.ask("transportation", "reopen_car_parks", {"zone": zone}, f"the flood in zone {zone} is over", context=f.key)
            if self.asked.pop(f"power:{zone}", None):
                self.ask("energy", "restore_zone_power", {"zone": zone}, f"the flood in zone {zone} is over", context=f.key)
            if self.asked.pop(f"lockdown:{zone}", None) == "flood":
                plans.append(Plan("ZONES", "reopen", "zone_reopen", value={"zone": zone}, priority=1,
                                  reason=f"the flood in zone {zone} is over: its entrances reopen", parameters={"zone": zone}))
            return plans
        plans = []
        if f"lockdown:{zone}" not in self.asked:
            self.asked[f"lockdown:{zone}"] = "flood"
            plans.append(Plan("ZONES", "lockdown", "zone_lockdown", value={"zone": zone, "reason": "flood"}, priority=0,
                              reason=f"flood in zone {zone} ({f.evidence.get('device')}, {f.evidence.get('depth_mm', 0):.0f} mm): "
                                     f"close the zone's four entrances so no car drives into the water", parameters={"zone": zone}))
        x = f.location.x if f.location.x is not None else 0.0
        zz = f.location.z if f.location.z is not None else 0.0
        if f"detour:{zone}" not in self.asked:
            t = self.ask("transportation", "detour_traffic", {"zone": zone, "x": round(x, 1), "z": round(zz, 1), "tag": tag},
                         f"flood in zone {zone} ({f.evidence.get('device')}, {f.evidence.get('depth_mm', 0):.0f} mm): police at the "
                         f"junctions of the flooded street to send the cars another way", context=f.key)
            self.asked[f"detour:{zone}"] = t.task_id
        if f"standby:{zone}" not in self.asked:
            seg = road_segment(x, zz)
            a = seg["a"]
            cross = (a[0] - 1, a[1]) if seg["axis"] == "ew" else (a[0], a[1] - 1)      # the cross street at one end
            if min(cross) < 1:
                cross = (a[0] + 1, a[1]) if seg["axis"] == "ew" else (a[0], a[1] + 1)
            self.asked[f"standby:{zone}"] = tag
            self.tags[tag] = {"x": x, "z": zz, "zone": zone}
            plans.append(Plan("INCIDENTS", "send_unit", "standby_unit", priority=1,
                              value={"kind": "ambulance", "edge": edge_id(cross, a), "pos": 70.0, "tag": tag},
                              location=Location.at(*junction_point(a)),
                              reason=f"flood in zone {zone}: an ambulance stands by at the junction next to the water "
                                     f"(on the cross street, out of the water) in case someone is caught",
                              parameters={"unit_kind": "ambulance", "tag": tag}))
        if f"parks:{zone}" not in self.asked:
            t = self.ask("transportation", "close_car_parks", {"zone": zone},
                         f"flood in zone {zone}: no more cars should drive into the water", context=f.key)
            self.asked[f"parks:{zone}"] = t.task_id
        if f.severity == "critical" and f"power:{zone}" not in self.asked and zone not in self.cfg.policy.protected_zones:
            t = self.ask("energy", "cut_zone_power", {"zone": zone},
                         f"water above {self.num('flood_depth_critical_mm', 300):g} mm in zone {zone}: cut the zone's power "
                         f"(electrical safety)", context=f.key)
            self.asked[f"power:{zone}"] = t.task_id
        return plans

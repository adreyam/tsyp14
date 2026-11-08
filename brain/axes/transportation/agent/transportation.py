"""
Transportation agent: jams, signals, car parks, incidents on the road, green waves for responding units.

detect   jam: a traffic detector above `jam_occupancy_pct` and below `jam_speed_kmh` for `jam_samples` readings
         in a row (a short peak is not a jam); it clears after `clear_samples` normal readings.
         road incident (collision, breakdown, signal failure, fire): opens a situation at the nearest junction.
decide   jam: give green to the congested road at the nearest junction (the detector's position says which road:
              east-west if it is further from the junction along x, north-south otherwise); reset the cycle when it clears
         collision: once the first responder is on scene, hold the nearest junction all red (the scene is protected,
              and the responders were not stopped on their way in); reset when resolved
         signal failure: restart the junction's normal cycle
         fire: no signal hold (the Twin closes the lane in front of the building); the engines get a green wave

A2A skills (the axis that owns the signals and the car parks does what the other axes ask, its own way)
    green_wave        {unit, incident?, destination?}  a responding unit (fire engine, ambulance, police car) drives
                      from its station to the scene along the roads: every junction up to `green_wave_ahead_m` ahead
                      of it on its road turns green in its direction of travel, so the queue in front of it clears
                      before it arrives; each junction goes back to its normal cycle as soon as the unit is
                      `green_wave_release_m` past it (or after `green_wave_max_hold_s` at most). Completed when the
                      unit is on scene. Junctions under a scene hold, or while traffic is stopped city-wide, are left
                      alone. The Twin adds its own last-80 m priority at each light (twin/scripts/emergency_priority.py).
    stop_traffic      {zone}  every signal of the zone red (confirmed earthquake: the scenario stays in its zone; without a
                      zone, every signal of the city); resume_traffic puts them back on their cycle
    detour_traffic    {zone, x, z, tag}  flood: a police car to each junction at the ends of the flooded street (INCIDENTS
                      send_unit, parked on the street leading into the water so no car turns into it), and those junctions
                      hold the cross street green so the traffic keeps moving around the water; end_detour recalls the
                      police cars (the units carrying the tag) and puts the junctions back on their cycle
    close_car_parks   {zone}  close the zone's car parks (flood, bad air); reopen_car_parks {zone} reopens them once
                      no other axis still needs them closed
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from citybrain.contracts import Event, Location
from citybrain.agents.base import (MOVING, Agent, CityView, Finding, Plan, edge_id, junction_point, junction_signal, loc_of,
                                   road_segment)

ROAD_INCIDENTS = {"rear_end", "red_light_tbone", "unsafe_overtake", "breakdown", "lost_load", "vehicle_fire",
                  "signal_failure", "building_fire"}
SCENE_HOLD = {"rear_end", "red_light_tbone", "unsafe_overtake", "breakdown", "lost_load"}   # collisions: all red once on scene
ARRIVED = {"on_scene", "departing", "garaged", "entering_depot", "patrol"}


class TransportationAgent(Agent):
    """Transportation: signals, car parks, jams, road incidents and green waves for responding units."""
    axis = "transportation"
    families = {"traffic", "parking"}
    incident_kinds = ROAD_INCIDENTS
    skills = {
        "green_wave": "give a responding unit green along its road, junction by junction, until it is on scene "
                      "(input: unit, incident, destination {x, z})",
        "stop_traffic": "every traffic signal of a zone red (input: zone; no zone: the whole city)",
        "resume_traffic": "the signals stopped by stop_traffic back to their normal cycle",
        "detour_traffic": "police cars to the junctions of a flooded street, cross traffic kept moving (input: zone, x, z, tag)",
        "end_detour": "recall the detour police cars and put the junctions back on their cycle (input: zone, tag)",
        "close_car_parks": "close the car parks of a zone (input: zone)",
        "reopen_car_parks": "reopen the car parks of a zone once no other axis needs them closed (input: zone)",
    }

    def __init__(self, cfg):
        super().__init__(cfg)
        self.bad: Dict[str, int] = {}
        self.good: Dict[str, int] = {}
        self.jammed: Dict[str, str] = {}          # detector -> junction it commanded
        self.held: Dict[str, str] = {}            # incident -> junction held all red for its scene
        self.hold_pending: Dict[str, tuple] = {}  # incident -> (junction, kind, location): held once a unit is on scene
        self.full_lots: set = set()
        self.waves: Dict[str, dict] = {}          # A2A task id -> green wave state
        self.wave_hold: Dict[str, str] = {}       # junction -> task id of the wave holding it
        self.stopped: Optional[str] = None        # task id of a stop_traffic in force
        self.parks: Dict[str, Dict[str, str]] = {}  # zone -> {close task id: who asked}
        self.stopped_signals: List[str] = []      # junctions held all red by stop_traffic (its zone only)
        self.detours: Dict[str, dict] = {}        # zone -> {tag, junctions}

    # ------------------------------------------------------------------ detect
    def detect(self, ev: Event, city: CityView) -> List[Finding]:
        if ev.type == "incident":
            inc = ev.incident
            key = f"transportation:incident:{inc.id}"
            title = f"{inc.kind.replace('_', ' ').capitalize()} on the road" + (f", zone {ev.location.zone}" if ev.location else "")
            if inc.status == "resolved":
                return [Finding(key, title, "road_incident", "watch", 0.95, f"{inc.id} resolved by the Twin", ev.location,
                                {"incident": inc.id, "kind": inc.kind}, resolved=True)]
            if ev.location is None:
                return []
            return [Finding(key, title, "road_incident", "alert", 0.9,
                            f"Twin incident {inc.id} ({inc.kind}) blocks traffic in zone {ev.location.zone}", ev.location,
                            {"incident": inc.id, "kind": inc.kind})]
        if ev.origin.family == "parking":
            pct = ev.value("occupancy_percent", 0.0)
            dev = ev.origin.device_id
            if pct >= 100 and dev not in self.full_lots:
                self.full_lots.add(dev)
                return [Finding(f"transportation:parking:{dev}", f"Car park {dev} full", "parking", "watch", 0.9,
                                f"{dev} at {pct:.0f} %: the Twin turns arriving cars away; no action needed", ev.location)]
            if pct < 85 and dev in self.full_lots:
                self.full_lots.discard(dev)
                return [Finding(f"transportation:parking:{dev}", f"Car park {dev} full", "parking", "info", 0.9,
                                f"{dev} back to {pct:.0f} %", ev.location, resolved=True)]
            return []
        return self._jam(ev, city)

    def _jam(self, ev: Event, city: CityView) -> List[Finding]:
        dev = ev.origin.device_id
        occ, speed = ev.value("occupancy", 0.0), ev.value("speed", 50.0)
        lim_o, lim_s = self.num("jam_occupancy_pct", 70), self.num("jam_speed_kmh", 12)
        need, clear = int(self.num("jam_samples", 3)), int(self.num("clear_samples", 3))
        key = f"transportation:jam:{dev}"
        if occ >= lim_o and speed <= lim_s:
            self.bad[dev] = self.bad.get(dev, 0) + 1
            self.good[dev] = 0
            if self.bad[dev] < need:
                return []
            return [Finding(key, f"Traffic jam at {dev}, zone {ev.location.zone}", "jam",
                            "alert" if occ >= 85 else "watch", min(0.95, 0.6 + 0.1 * self.bad[dev]),
                            f"{dev}: occupancy {occ:.0f} % (limit {lim_o:g}) and speed {speed:.0f} km/h (limit {lim_s:g}) "
                            f"for {self.bad[dev]} readings in a row", ev.location, {"device": dev})]
        self.bad[dev] = 0
        if dev in self.jammed:
            self.good[dev] = self.good.get(dev, 0) + 1
            if self.good[dev] >= clear:
                return [Finding(key, f"Traffic jam at {dev}, zone {ev.location.zone}", "jam", "info", 0.9,
                                f"{dev}: traffic flowing again ({speed:.0f} km/h, {occ:.0f} %) for {clear} readings",
                                ev.location, {"device": dev}, resolved=True)]
        return []

    # ------------------------------------------------------------------ decide
    def decide(self, f: Finding, city: CityView) -> List[Plan]:
        if f.kind == "jam":
            dev = f.evidence["device"]
            if f.resolved:
                sig = self.jammed.pop(dev, None)
                return [Plan(sig, "reset_cycle", "signal_phase", reason=f"jam at {dev} cleared: {sig} back to its normal cycle")] \
                    if sig and sig not in self.wave_hold else []
            if dev in self.jammed:
                return []
            sig = city.nearest("signal", f.location.x, f.location.z)
            if not sig or sig in self.wave_hold or self.stopped:
                return []
            s = city.registry[sig]
            road = "ew_green" if abs(f.location.x - s["x"]) > abs(f.location.z - s["z"]) else "ns_green"
            self.jammed[dev] = sig
            return [Plan(sig, road, "signal_phase", priority=2, location=loc_of(city, sig),
                         reason=f"jam at {dev}: {sig} is the nearest junction; the detector sits on its "
                                f"{'east-west' if road == 'ew_green' else 'north-south'} road, so hold that road green to drain the queue")]
        if f.kind == "road_incident":
            iid, kind = f.evidence["incident"], f.evidence["kind"]
            if f.resolved:
                self.hold_pending.pop(iid, None)
                sig = self.held.pop(iid, None)
                return [Plan(sig, "reset_cycle", "signal_phase", reason=f"{iid} resolved: {sig} back to its normal cycle")] if sig else []
            if iid in self.held or iid in self.hold_pending or f.location is None:
                return []
            sig = city.nearest("signal", f.location.x, f.location.z)
            if not sig:
                return []
            if kind == "signal_failure":
                self.held[iid] = sig
                return [Plan(sig, "reset_cycle", "signal_phase", priority=1, location=loc_of(city, sig),
                             reason=f"signal failure {iid}: restart the normal cycle of {sig}")]
            if kind in SCENE_HOLD:
                self.hold_pending[iid] = (sig, kind, f.location)
                f.reason += f"; {sig} will be held all red once the first responder is on scene (not before: the responders must get through)"
            else:
                f.reason += "; no signal hold for a fire (the Twin closes the lane in front of it): the engines get a green wave instead"
        return []

    # ------------------------------------------------------------------ A2A
    def on_task(self, task, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        if task.skill == "green_wave":
            return self._wave_start(task, city)
        if task.skill == "stop_traffic":
            zone = str(task.input.get("zone") or "").upper()
            self.stopped = task.task_id
            sigs = sorted(city.in_zone("signal", zone)) if zone else []
            for w in self.waves.values():                # those signals are going red: the waves leave them
                for sig in list(w["held"]):
                    if not zone or sig in sigs:
                        self.wave_hold.pop(sig, None)
                        w["held"].pop(sig, None)
            title = f"Traffic stopped in zone {zone}" if sigs else "All traffic stopped"
            f = Finding("transportation:stop_traffic", title, "stop_traffic", "critical", 0.95,
                        f"{task.from_agent} asked (A2A): {task.reason}", loc_of(city, sigs[0]) if sigs else None, {"zone": zone})
            if sigs:
                self.stopped_signals = sigs
                plans = [Plan(sig, "all_red", "stop_traffic", priority=0, location=loc_of(city, sig),
                              reason=f"{task.from_agent} asks to stop the traffic in zone {zone} ({task.reason}): {sig} all red")
                         for sig in sigs]
                plans[-1].parameters["a2a_task"] = task.task_id
                return [(f, plans)]
            return [(f, [Plan("SIGNALS", "phase", "signals_all", value="all_red", priority=0,
                              reason=f"{task.from_agent} asks to stop all traffic ({task.reason}): every signal red",
                              parameters={"a2a_task": task.task_id})])]
        if task.skill == "resume_traffic":
            was, sigs = self.stopped, self.stopped_signals
            self.stopped, self.stopped_signals = None, []
            f = Finding("transportation:stop_traffic", "Traffic stopped", "stop_traffic", "info", 0.95,
                        f"{task.from_agent} asked (A2A): {task.reason}", resolved=True)
            if was is None:
                self.a2a.update(task, "completed", "traffic was not stopped: nothing to undo")
                return [(f, [])]
            if sigs:
                plans = [Plan(sig, "reset_cycle", "stop_traffic", reason=f"{task.from_agent} asks to resume traffic ({task.reason}): "
                              f"{sig} back to its cycle") for sig in sigs]
                plans[-1].parameters["a2a_task"] = task.task_id
                return [(f, plans)]
            return [(f, [Plan("SIGNALS", "phase", "signals_all", value="reset_cycle", priority=0,
                              reason=f"{task.from_agent} asks to resume traffic ({task.reason}): every signal back to its cycle",
                              parameters={"a2a_task": task.task_id})])]
        if task.skill in ("detour_traffic", "end_detour"):
            return self._detour(task, city)
        if task.skill in ("close_car_parks", "reopen_car_parks"):
            return self._parks(task, city)
        self.a2a.update(task, "rejected", f"unknown skill {task.skill}")
        return []

    def _detour(self, task, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        zone = str(task.input.get("zone", "")).upper()
        tag = str(task.input.get("tag") or f"DETOUR-{zone}")
        key = f"transportation:detour:{zone}"
        if task.skill == "end_detour":
            d = self.detours.pop(zone, None)
            if d is None:
                self.a2a.update(task, "completed", f"no detour in zone {zone}: nothing to undo")
                return []
            plans = [Plan("INCIDENTS", "recall_unit", "police_detour", value=u["unit"],
                          reason=f"{task.from_agent} says {task.reason}: {u['unit']} back on patrol / to its station",
                          parameters={"unit": u["unit"], "tag": d["tag"]})
                     for u in city.units.values() if u["incident"] == d["tag"]]
            plans += [Plan(sig, "reset_cycle", "police_detour", reason=f"detour in zone {zone} over: {sig} back to its cycle")
                      for sig in d["signals"] if sig in city.registry]
            if plans:
                plans[-1].parameters["a2a_task"] = task.task_id
            else:
                self.a2a.update(task, "completed", "no police car or junction to put back")
            return [(Finding(key, f"Police detour around the flood, zone {zone}", "detour", "info", 0.9,
                             f"{task.from_agent} asked (A2A): {task.reason}", resolved=True), plans)]
        if zone in self.detours:
            self.a2a.update(task, "completed", f"detour already in place in zone {zone}")
            return []
        try:
            x, z = float(task.input["x"]), float(task.input["z"])
        except (KeyError, TypeError, ValueError):
            self.a2a.update(task, "rejected", "detour_traffic needs the flood's x and z")
            return []
        seg = road_segment(x, z)
        a, b = seg["a"], seg["b"]
        cross = "ns_green" if seg["axis"] == "ew" else "ew_green"
        street = f"{'east-west' if seg['axis'] == 'ew' else 'north-south'} street between junction r{a[0]}c{a[1]} and r{b[0]}c{b[1]}"
        signals = [junction_signal(j) for j in (a, b) if junction_signal(j) in city.registry]
        self.detours[zone] = {"tag": tag, "signals": signals, "junctions": [a, b]}
        loc = Location.at(x, z)
        plans: List[Plan] = []
        for frm, to in ((a, b), (b, a)):
            plans.append(Plan("INCIDENTS", "send_unit", "police_detour", priority=0,
                              value={"kind": "police", "edge": edge_id(frm, to), "pos": 8.0, "tag": tag},
                              location=Location.at(*junction_point(frm)),
                              reason=f"{task.from_agent} asks (A2A): {task.reason}; police car to junction r{frm[0]}c{frm[1]}, parked "
                                     f"at the entry of the flooded {street}, to turn the cars away from the water",
                              parameters={"unit_kind": "police", "tag": tag}))
        for sig in signals:
            if sig in self.wave_hold:
                continue
            plans.append(Plan(sig, cross, "police_detour", priority=1, location=loc_of(city, sig),
                              reason=f"detour around the flood: {sig} holds the cross street green "
                                     f"({'north-south' if cross == 'ns_green' else 'east-west'}) so the traffic keeps moving "
                                     f"around the flooded {street}"))
        plans[-1].parameters["a2a_task"] = task.task_id
        return [(Finding(key, f"Police detour around the flood, zone {zone}", "detour", "alert", 0.9,
                         f"{task.from_agent} asked (A2A): {task.reason}; the flooded {street} is closed by police at both ends",
                         loc, {"zone": zone, "tag": tag}), plans)]

    def _parks(self, task, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        zone = str(task.input.get("zone", "")).upper()
        lots = city.in_zone("parking", zone) if zone else []
        key = f"transportation:car_parks:{zone}"
        asked = self.parks.setdefault(zone, {})
        if task.skill == "close_car_parks":
            if not lots:
                self.a2a.update(task, "completed", f"zone {zone or '?'} has no car park: nothing to close")
                return []
            already = bool(asked)
            asked[task.task_id] = task.from_agent
            f = Finding(key, f"Car parks closed, zone {zone}", "car_parks", "watch", 0.9,
                        f"{task.from_agent} asked (A2A): {task.reason}", loc_of(city, lots[0]), {"zone": zone, "lots": lots})
            if already:
                self.a2a.update(task, "completed", f"{', '.join(lots)} already closed (also for {', '.join(sorted(set(asked.values())))})")
                return [(f, [])]
            plans = [Plan(l, "closed", "parking_barrier", priority=1, location=loc_of(city, l),
                          reason=f"{task.from_agent} asks (A2A): {task.reason}: close car park {l}") for l in lots]
            plans[-1].parameters["a2a_task"] = task.task_id
            return [(f, plans)]
        mine = [t for t, who in asked.items() if who == task.from_agent]
        for t in mine:
            del asked[t]
        if not mine:
            self.a2a.update(task, "completed", f"zone {zone}'s car parks were not closed for {task.from_agent}: nothing to reopen")
            return []
        if asked:
            self.a2a.update(task, "completed", f"kept closed: {', '.join(sorted(set(asked.values())))} still needs zone {zone}'s car parks closed")
            return []
        f = Finding(key, f"Car parks closed, zone {zone}", "car_parks", "info", 0.9,
                    f"{task.from_agent} asked (A2A): {task.reason}", resolved=True)
        plans = [Plan(l, "auto", "parking_barrier", reason=f"{task.from_agent} asks (A2A): {task.reason}: car park {l} back to auto")
                 for l in lots]
        plans[-1].parameters["a2a_task"] = task.task_id
        return [(f, plans)]

    # ------------------------------------------------------------------ green wave
    def _wave_start(self, task, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        unit = str(task.input.get("unit", ""))
        if not unit:
            self.a2a.update(task, "rejected", "green_wave needs the unit id")
            return []
        for tid, w in list(self.waves.items()):        # one wave per unit: a new leg replaces the old one
            if w["unit"] == unit:
                old = self.a2a.get(tid)
                if old is not None and old.open:
                    self.a2a.update(old, "completed", f"replaced by a new green wave for {unit}")
        dest = task.input.get("destination") or {}
        try:
            dest = (float(dest["x"]), float(dest["z"]))
        except (KeyError, TypeError, ValueError):
            dest = None
        self.waves[task.task_id] = {"unit": unit, "incident": task.input.get("incident"), "dest": dest, "held": {},
                                    "count": 0, "kind": task.input.get("kind", "unit"), "started": self.now(), "moved": False,
                                    "expired": set()}
        why = "traffic is stopped city-wide: the wave starts when it resumes" if self.stopped else \
            f"following {unit} from its station; junctions turn green ahead of it"
        self.a2a.update(task, "working", why)
        return []

    def tick(self, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        out: List[Tuple[Finding, List[Plan]]] = []
        now = self.now()
        for tid in list(self.waves):
            res = self._wave_step(tid, city, now)
            if res:
                out.append(res)
        out += self._scene_holds(city)
        return out

    def _wave_step(self, tid: str, city: CityView, now: float) -> Optional[Tuple[Finding, List[Plan]]]:
        w = self.waves[tid]
        task = self.a2a.get(tid)
        u = city.units.get(w["unit"])
        key = f"transportation:green_wave:{w['unit']}"
        title = f"Green wave for {w['unit']}" + (f" to {w['incident']}" if w.get("incident") else "")
        loc = Location.at(u["x"], u["z"]) if u and u.get("x") is not None else None
        done = None
        if task is None or not task.open:
            done = f"task {task.state if task else 'gone'}"
        elif u is None:
            done = f"{w['unit']} is no longer listed by the Twin"
        elif w["moved"] and u["status"] in ARRIVED:
            done = f"{w['unit']} arrived ({u['status']})"
        elif now - w["started"] > 900:
            done = "15 minutes without arriving: wave ended"
        if done:
            plans = [self._release(sig, w, f"green wave for {w['unit']} over ({done})") for sig in list(w["held"])]
            del self.waves[tid]
            if task is not None and task.open:
                self.a2a.update(task, "completed", f"{done}; {w['count']} junction(s) turned green on its way",
                                {"junctions": w["count"]})
            return (Finding(key, title, "green_wave", "watch", 0.9, f"{done}; {w['count']} junction(s) given green", loc,
                            {"unit": w["unit"], "task": tid}, resolved=True), plans)
        if u["status"] in MOVING:
            w["moved"] = True
        if w["dest"] is None and w.get("incident") in city.incidents:      # the incident was not in view yet when asked
            inc = city.incidents[w["incident"]]
            w["dest"] = (inc["x"], inc["z"])
        # on the road only: the pull-out from the bay to the kerb ("inserting") is not a direction of travel
        if (self.stopped and not self.stopped_signals) or u["status"] not in ("en_route", "returning") or (u["dx"], u["dz"]) == (0.0, 0.0) or u.get("x") is None:
            return None
        ew = abs(u["dx"]) >= abs(u["dz"])
        cmd = "ew_green" if ew else "ns_green"
        sgn = 1.0 if (u["dx"] if ew else u["dz"]) > 0 else -1.0
        ahead_m = self.num("green_wave_ahead_m", 250)
        if w["dest"]:
            d_along = ((w["dest"][0] - u["x"]) if ew else (w["dest"][1] - u["z"])) * sgn
            ahead_m = min(ahead_m, d_along + 15.0) if d_along > 0 else min(ahead_m, 60.0)   # it turns before going past it
        release_m, max_hold = self.num("green_wave_release_m", 12), self.num("green_wave_max_hold_s", 45)
        scene = set(self.held.values()) | set(self.stopped_signals) | {s for d in self.detours.values() for s in d["signals"]}
        signals = city.signals()
        want: Dict[str, float] = {}
        for sig, (jx, jz) in signals.items():
            lateral = (jz - u["z"]) if ew else (jx - u["x"])
            ahead = ((jx - u["x"]) if ew else (jz - u["z"])) * sgn
            if abs(lateral) <= 14 and 4.0 < ahead <= ahead_m and sig not in scene and sig not in city.quarantine \
                    and sig not in w["expired"]:
                want[sig] = ahead
        plans: List[Plan] = []
        dirname = ("east" if sgn > 0 else "west") if ew else ("south" if sgn > 0 else "north")
        for sig, ahead in sorted(want.items(), key=lambda kv: kv[1]):
            if w["held"].get(sig, (None,))[0] == cmd:
                continue
            other = self.wave_hold.get(sig)
            if other and other != tid and other in self.waves:
                continue                                  # first come, first served: another unit holds it
            if w["held"].get(sig, (None,))[0] is None:
                w["count"] += 1
            w["held"][sig] = (cmd, now)
            self.wave_hold[sig] = tid
            plans.append(Plan(sig, cmd, "green_wave", priority=0, location=loc_of(city, sig),
                              reason=f"{w['unit']} ({w['kind']}{', to ' + w['incident'] if w.get('incident') else ''}) is "
                                     f"{ahead:.0f} m from {sig}, heading {dirname}: "
                                     f"{'east-west' if ew else 'north-south'} green so the queue clears before it arrives",
                              parameters={"unit": w["unit"], "wave": tid}))
        for sig, (held_cmd, since) in list(w["held"].items()):
            if sig in want and held_cmd == cmd and now - since <= max_hold:
                continue
            jx, jz = signals.get(sig, (u["x"], u["z"]))
            lateral = (jz - u["z"]) if ew else (jx - u["x"])
            ahead = ((jx - u["x"]) if ew else (jz - u["z"])) * sgn
            on_road = abs(lateral) <= 14 and held_cmd == cmd
            if now - since > max_hold:
                w["expired"].add(sig)                     # not taken again by this unit: cross traffic gets its turn
                why = f"held {now - since:.0f} s, the most a wave may hold one junction"
            elif on_road and -release_m < ahead <= 4.0:
                continue                                  # the unit is crossing it now: keep it green until it is clear
            elif on_road and ahead <= -release_m:
                why = f"{w['unit']} is {-ahead:.0f} m past it"
            elif on_road:
                why = f"{w['unit']} turns before reaching it"
            else:
                why = f"{w['unit']} turned off that road"
            plans.append(self._release(sig, w, why))
        if not plans:
            return None
        return (Finding(key, title, "green_wave", "watch", 0.9,
                        f"{task.from_agent} asked (A2A) for {w['unit']} ({task.reason}); now at x {u['x']:.0f}, z {u['z']:.0f}, "
                        f"heading {dirname}, {u['status']}", loc, {"unit": w["unit"], "task": tid}), plans)

    def _release(self, sig: str, w: dict, why: str) -> Plan:
        w["held"].pop(sig, None)
        holder = self.wave_hold.get(sig)
        if holder is not None and self.waves.get(holder) is w:
            self.wave_hold.pop(sig, None)
        return Plan(sig, "reset_cycle", "green_wave", priority=1, reason=f"{why}: {sig} back to its normal cycle",
                    parameters={"unit": w["unit"]})

    def _scene_holds(self, city: CityView) -> List[Tuple[Finding, List[Plan]]]:
        out = []
        for iid, (sig, kind, loc) in list(self.hold_pending.items()):
            here = [u["unit"] for u in city.units.values() if u["incident"] == iid and u["status"] == "on_scene"]
            if not here or sig in self.wave_hold:
                continue
            del self.hold_pending[iid]
            self.held[iid] = sig
            f = Finding(f"transportation:incident:{iid}", f"{kind.replace('_', ' ').capitalize()} on the road, zone {loc.zone}",
                        "road_incident", "alert", 0.9, f"{', '.join(here)} on scene at {iid}", loc, {"incident": iid, "kind": kind})
            out.append((f, [Plan(sig, "all_red", "signal_phase", priority=1, location=loc_of(city, sig),
                                 reason=f"{kind.replace('_', ' ')} {iid}: {', '.join(here)} on scene; hold {sig} all red so "
                                        f"cross traffic stops around the scene")]))
        return out

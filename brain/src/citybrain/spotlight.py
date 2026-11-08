"""
Spotlight: what to show while a Test Book scenario runs, on the control center's City map and in the Twin itself.

While a scenario runs, the spotlight follows it and answers three questions, every Brain loop:

    where     the zones it touches: the scenario's own "focus" (zones, devices), the zones of the incidents it
              started (on the live Twin the Twin chooses the place, so they are found as they appear), the zones of
              the devices it plays with, and the zones of the actions the Brain takes for it
    what      the incidents it started and the devices it watches
    follow    the vehicle to keep in view: a unit of one of its incidents that is driving (fire engine, ambulance,
              police car, tow truck, garbage truck: the first one on the road, kept until it arrives, then the next
              one moving); with "follow": "traffic", a car driving towards the scenario's device (into the jam)

The control center reads it in GET /v1/map ("spotlight") and highlights those zones, frames them and keeps the
followed vehicle in the middle of the map. In the Twin (live or the stand-in), with scenario.twin_camera on, it
shows the same thing with the Twin's own commands (twin/scripts/sim_api.gd):

    ZONES show <letters>           the zones tinted on the ground          (ZONES hide ALL at the end)
    CAMERA follow <vehicle id>     the Twin's camera chases the vehicle    (CAMERA view overview at the end)
    FOCUS device <id>              the Twin frames a device when there is nothing to follow

Those are presentation only: never in the audit as actions, never undone by Stop, and a Twin without its control
room (the command answers "unavailable") is simply not driven.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from .contracts import zone_of

FOLLOW_ORDER = {"fire": 0, "ambulance": 1, "police": 2, "tow": 3, "garbage": 4, "utility": 5}
DRIVING = {"mobilizing", "inserting", "en_route", "returning", "entering_depot"}


class Spotlight:
    def __init__(self, brain, spec: dict, start_seq: int, incidents_before: set):
        self.brain = brain
        self.spec = spec
        f = spec.get("focus") or {}
        self.follow_mode = f.get("follow")                  # "units", "traffic" or None
        # one zone per scenario: its "zone" (or the first of "zones"); without one, the zone of its first incident,
        # else of its first action, else of its first device
        z0 = f.get("zone") or (f.get("zones") or [None])[0]
        self.static_zones = [z0] if isinstance(z0, str) and len(z0) == 1 else []
        self.devices = [d for d in f.get("devices", []) if isinstance(d, str)]
        for s in spec.get("steps", []):
            if s.get("device") and s["device"] not in self.devices and (not s.get("modes") or brain.source.name in s["modes"]):
                self.devices.append(s["device"])
        self.zones: List[str] = list(self.static_zones)
        self.incidents: Dict[str, dict] = {}
        self.before = set(incidents_before)
        self.seq = start_seq
        self.follow: Optional[dict] = None
        self.twin = {"zones": [], "camera": None, "note": None, "enabled": bool(brain.cfg.scenario_twin_camera())}
        self._twin_ok = True
        self._focused_device = None
        self._refused: Dict[str, float] = {}                # vehicle -> when the Twin would not follow it (asked again after 8 s)
        self._car_since = 0.0

    # ------------------------------------------------------------------ update (every Brain loop, under brain.lock)
    def update(self) -> None:
        b = self.brain
        snap = b.source.snapshot() or {}
        for inc in snap.get("incidents") or []:
            iid = str(inc.get("id", ""))
            if not iid or iid in self.before:
                continue
            loc = inc.get("location") or {}
            try:
                x, z = float(loc["x"]), float(loc["z"])
            except (KeyError, TypeError, ValueError):
                continue
            self.incidents[iid] = {"id": iid, "kind": inc.get("kind"), "x": x, "z": z, "zone": zone_of(x, z),
                                   "phase": inc.get("phase")}
        zones = list(self.static_zones)
        for d in self.devices:
            r = b.orch.city.registry.get(d)
            if r and r.get("zone"):
                zones.append(r["zone"])
        zones += [i["zone"] for i in self.incidents.values()]
        for rec in b.audit.since(self.seq, limit=2000):
            self.seq = max(self.seq, rec.get("seq", self.seq))
            if rec.get("kind") == "action" and rec.get("scenario_id") == self.spec["id"]:
                z = ((rec.get("payload") or {}).get("location") or {}).get("zone")
                if z and (rec.get("payload") or {}).get("action_type") not in ("green_wave",):
                    self._extra_zone(z)
        zones += getattr(self, "_extra", [])
        seen = []
        for z in zones:
            if z not in seen:
                seen.append(z)
        self.zones = seen[:1]                               # the scenario happens in one zone
        self.follow = self._pick_follow(snap)
        if self.twin["enabled"]:
            self._drive_twin()

    def _extra_zone(self, z: str) -> None:
        self._extra = getattr(self, "_extra", [])
        if z not in self._extra:
            self._extra.append(z)

    def _pick_follow(self, snap: dict) -> Optional[dict]:
        if self.follow_mode == "units":
            # its incidents' units, and the units sent without an incident during the scenario (a tag such as FLOOD-J:
            # the police cars of a flood detour, an ambulance on standby)
            rows = [u for u in snap.get("units") or [] if isinstance(u, dict)
                    and (str(u.get("incident") or "") in self.incidents
                         or (u.get("incident") and not str(u["incident"]).startswith("INC-") and str(u["incident"]) not in self.before))]
            moving = [u for u in rows if u.get("status") in DRIVING]
            cur = self.follow["unit"] if self.follow and self.follow.get("unit") else None
            pick = next((u for u in moving if u.get("unit") == cur), None)
            if pick is None and moving:
                pick = sorted(moving, key=lambda u: (u.get("status") == "returning", FOLLOW_ORDER.get(str(u.get("kind")), 9),
                                                     str(u.get("unit"))))[0]
            if pick is None:
                return None
            loc = self._live_loc(snap, pick.get("unit")) or pick.get("location") or {}
            # the Twin keys every unit's model by its unit id (FIRE-1, GRB-1), also while it is still in its bay:
            # CAMERA follow <unit id> works from the moment it is dispatched
            return {"kind": "unit", "unit": pick.get("unit"), "vehicle": pick.get("unit") or pick.get("vehicle"),
                    "type": pick.get("kind"), "status": pick.get("status"), "incident": pick.get("incident"),
                    "x": loc.get("x"), "z": loc.get("z")}
        if self.follow_mode == "traffic" and self.devices:
            r = self.brain.orch.city.registry.get(self.devices[0])
            if not r:
                return None
            cars = [v for v in snap.get("vehicles") or [] if isinstance(v, dict) and not v.get("unit")
                    and (v.get("location") or {}).get("x") is not None]
            cur = self.follow.get("vehicle") if self.follow else None
            keep = next((v for v in cars if v.get("id") == cur), None)
            if keep is not None:
                d = math.hypot(keep["location"]["x"] - r["x"], keep["location"]["z"] - r["z"])
                if d < 260 and time.monotonic() - self._car_since < 90:
                    return self._car(keep)
            near = [v for v in cars if 40 < math.hypot(v["location"]["x"] - r["x"], v["location"]["z"] - r["z"]) < 200
                    and float(v.get("speed_mps") or 0) > 1.0 and self._towards(v, r)]
            if not near:
                return self._car(keep) if keep else None
            v = min(near, key=lambda v: math.hypot(v["location"]["x"] - r["x"], v["location"]["z"] - r["z"]))
            self._car_since = time.monotonic()
            return self._car(v)
        return None

    @staticmethod
    def _towards(v: dict, r: dict) -> bool:
        h = math.radians(float(v.get("heading_deg") or 0.0))
        dx, dz = math.sin(h), -math.cos(h)                   # 0 = north (-z), 90 = east (+x)
        tx, tz = r["x"] - v["location"]["x"], r["z"] - v["location"]["z"]
        n = math.hypot(tx, tz) or 1.0
        return (dx * tx + dz * tz) / n > 0.7

    @staticmethod
    def _car(v: dict) -> dict:
        return {"kind": "car", "vehicle": v.get("id"), "unit": None, "type": v.get("type") or v.get("class"),
                "status": "stopped" if v.get("stopped") else "driving", "x": v["location"]["x"], "z": v["location"]["z"]}

    @staticmethod
    def _live_loc(snap: dict, unit) -> Optional[dict]:
        for v in snap.get("vehicles") or []:
            if isinstance(v, dict) and v.get("unit") == unit:
                return v.get("location")
        return None

    # ------------------------------------------------------------------ the Twin's own view
    def _cmd(self, target: str, command: str, value=None) -> Optional[dict]:
        if not self._twin_ok:
            return None
        try:
            return self.brain.source.client.request("command", {"id": target, "command": command,
                                                                **({"value": value} if value is not None else {})})
        except Exception as e:                               # no control room, unknown vehicle: presentation only
            msg = str(e)
            if "unavailable" in msg or "control room" in msg:
                self._twin_ok = False
                self.twin["note"] = "the Twin's control room is not attached: its camera is not driven"
            else:
                self.twin["note"] = msg[:160]
            return None

    def _drive_twin(self) -> None:
        if self.zones != self.twin["zones"]:
            if self.twin["zones"]:
                self._cmd("ZONES", "hide", "ALL")
            if self.zones:
                self._cmd("ZONES", "show", list(self.zones))
            self.twin["zones"] = list(self.zones)            # asked once per change, answered or not
        target = self.follow.get("vehicle") if self.follow else None
        if not target and self.twin["camera"] and "follows" in (self.twin["note"] or ""):
            self.twin["note"] = f"nothing on the move: the Twin's camera stays on {self.twin['camera']}"
        refused_at = self._refused.get(str(target)) if target else None
        if target and target != self.twin["camera"] and (refused_at is None or time.monotonic() - refused_at > 8.0):
            if self._cmd("CAMERA", "follow", str(target)) is None:
                self._refused[str(target)] = time.monotonic()
            else:
                self.twin["camera"] = target
                self.twin["note"] = f"the Twin's camera follows {self.follow.get('unit') or target}"
        elif not target and self.twin["camera"] is None and self.devices and self._focused_device != self.devices[0]:
            self._focused_device = self.devices[0]
            if self._cmd("FOCUS", "device", self.devices[0]) is not None:
                self.twin["note"] = f"the Twin frames {self.devices[0]}"

    def close(self) -> None:
        if self.twin["enabled"] and self._twin_ok:
            if self.twin["zones"]:
                self._cmd("ZONES", "hide", "ALL")
            if self.twin["camera"] or self._focused_device:
                self._cmd("CAMERA", "view", "overview")
        self.twin["zones"], self.twin["camera"] = [], None

    # ------------------------------------------------------------------ view
    def view(self) -> dict:
        return {"scenario": self.spec["id"], "name": self.spec.get("name"), "axis": self.spec.get("axis"),
                "zones": list(self.zones), "devices": list(self.devices), "incidents": list(self.incidents.values()),
                "follow": self.follow, "twin": dict(self.twin)}

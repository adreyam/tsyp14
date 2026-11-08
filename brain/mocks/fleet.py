"""
Emergency fleet of the Twin stand-in: units drive from their station to the scene along the roads, like the Twin.

The real Twin (twin/scripts/incident_engine.py EmergencyFleet, docs/INCIDENT_SYSTEM_ARCHITECTURE.md §3) never
teleports a unit: garaged -> mobilizing (crew walks out) -> inserting (bay to kerb) -> en_route (on the SUMO roads)
-> on_scene -> returning -> entering_depot -> garaged; police patrols drive around and answer from where they are.
This stand-in does the same, so the Brain (green waves, the map) sees the same thing on the mock source:

    stations and bays   the Twin's own (twin/runtime/sumo/emergency_depots.json)
    roads               the 9 x 9 grid of the city, 95 m blocks from -380 m; units drive on the right
    route               the shortest way along the roads from the kerb in front of the station to the kerb at the scene
    signals             a responding unit that meets a red light slows down and crosses after a short clearance
                        (the Twin: red-light clearance driving, 2.5 s amber of its own priority); on green it does not stop.
                        Other units (tow, utility, garbage) wait for green.
    mutual aid          if every unit of a kind is busy, an AID-<KIND>-n unit enters through the perimeter road nearest
                        the scene (the Twin's mutual aid), never inside the city
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

BLOCK = 95.0
EDGE = 380.0
LINES = [-EDGE + BLOCK * k for k in range(9)]
LANE = 3.0
RESPONSE_SPEED = 15.0          # m/s, lights and sirens
NORMAL_SPEED = 10.0
MOBILIZE_S = 4.0               # crew walks out and boards
INSERT_S = 3.0                 # bay -> kerb
CLEARANCE_S = 2.5              # a responding unit at a red light
RESPONSE_KINDS = {"fire", "ambulance", "police", "tow"}

# The Twin's stations (twin/runtime/sumo/emergency_depots.json): id -> bays (unit, kind, x, z)
STATIONS = {
    "POLICE-HQ": [("POL-3", "police", -13.6, -140.9), ("POL-4", "police", -13.6, -151.8)],
    "HOSPITAL-CENTRAL": [("AMB-1", "ambulance", -40.0, -13.6), ("AMB-2", "ambulance", -28.0, -13.6)],
    "HOSPITAL-WEST": [("AMB-3", "ambulance", -218.0, -108.6)],
    "FIRE-STATION": [("FIRE-1", "fire", 143.4, -81.4), ("FIRE-2", "fire", 129.8, -81.4)],
    "TOW-YARD": [("TOW-1", "tow", -176.4, 221.4), ("TOW-2", "tow", -176.4, 234.8)],
    "UTILITY-DEPOT": [("UTL-1", "utility", -41.2, -271.4), ("UTL-2", "utility", -53.4, -271.4)],
    "SANITATION-DEPOT": [("GRB-1", "garbage", 203.6, 135.6), ("GRB-2", "garbage", 203.6, 150.2)],
}
PATROLS = [("POL-1", "POLICE-HQ", [(0.0, -190.0), (190.0, -190.0), (190.0, 0.0), (0.0, 0.0)]),
           ("POL-2", "POLICE-HQ", [(-190.0, 95.0), (-190.0, 285.0), (95.0, 285.0), (95.0, 95.0)])]
PATIENT_INCIDENTS = {"red_light_tbone", "unsafe_overtake", "building_fire"}   # the ambulance takes a patient to hospital


def snap(x: float, z: float) -> Tuple[float, float]:
    """The nearest point on a road (the centre line of the grid)."""
    x, z = max(-EDGE, min(EDGE, x)), max(-EDGE, min(EDGE, z))
    lx = min(LINES, key=lambda l: abs(l - x))
    lz = min(LINES, key=lambda l: abs(l - z))
    return (lx, z) if abs(lx - x) <= abs(lz - z) else (x, lz)


def _ends(p: Tuple[float, float]) -> List[Tuple[float, float]]:
    """The junctions at both ends of the road segment a road point lies on."""
    x, z = p
    on_x = any(abs(x - l) < 0.01 for l in LINES)          # on a north-south road (x fixed)
    on_z = any(abs(z - l) < 0.01 for l in LINES)          # on an east-west road (z fixed)
    if on_x and on_z:
        return [p]
    if on_x:
        lo = max(l for l in LINES if l <= z)
        hi = min(l for l in LINES if l >= z)
        return [(x, lo), (x, hi)]
    lo = max(l for l in LINES if l <= x)
    hi = min(l for l in LINES if l >= x)
    return [(lo, z), (hi, z)]


def _same_segment(a, b) -> bool:
    return set(_ends(a)) == set(_ends(b)) or (len(_ends(a)) == 1 and _ends(a)[0] in _ends(b)) \
        or (len(_ends(b)) == 1 and _ends(b)[0] in _ends(a))


def route(a: Tuple[float, float], b: Tuple[float, float]) -> List[Tuple[float, float]]:
    """Shortest way along the roads from road point a to road point b: a list of points (a ... b)."""
    if _same_segment(a, b) and (abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01):
        return [a, b]
    best = None
    for n1 in _ends(a):
        for n2 in _ends(b):
            d = abs(a[0] - n1[0]) + abs(a[1] - n1[1]) + abs(n1[0] - n2[0]) + abs(n1[1] - n2[1]) + abs(n2[0] - b[0]) + abs(n2[1] - b[1])
            if best is None or d < best[0]:
                best = (d, n1, n2)
    _, n1, n2 = best
    # keep going along the road the unit is on: x first if it is on an east-west road
    if abs(a[1] - n1[1]) < 0.01 and abs(a[0] - n1[0]) > 0.01:
        corner = (n2[0], n1[1])
    elif abs(a[0] - n1[0]) < 0.01 and abs(a[1] - n1[1]) > 0.01:
        corner = (n1[0], n2[1])
    else:
        corner = (n2[0], n1[1])
    pts = [a, n1, corner, n2, b]
    out: List[Tuple[float, float]] = []
    for p in pts:
        if not out or abs(out[-1][0] - p[0]) + abs(out[-1][1] - p[1]) > 0.01:
            out.append(p)
    return out


def heading(dx: float, dz: float) -> float:
    """Degrees, as the stand-in's cars: 0 north (-z), 90 east (+x), 180 south (+z), 270 west."""
    if abs(dx) >= abs(dz):
        return 90.0 if dx > 0 else 270.0
    return 180.0 if dz > 0 else 0.0


class Unit:
    def __init__(self, uid: str, kind: str, depot: str, bay: Optional[Tuple[float, float]], patrol=None):
        self.id, self.kind, self.depot = uid, kind, depot
        self.bay = bay
        self.kerb = snap(*bay) if bay else None
        self.patrol = patrol
        self.status = "patrol" if patrol else "garaged"
        self.x, self.z = (bay if bay else patrol[0])
        self.path: List[Tuple[float, float]] = []
        self.incident = ""
        self.response = False
        self.since = 0.0
        self.wait = 0.0
        self.speed = 0.0
        self.dir = (0.0, -1.0)
        self.patrol_i = 0
        self.passed: set = set()      # junctions already crossed on this leg
        self.cargo = ""
        if patrol:
            self.path = route((self.x, self.z), patrol[1])

    def row(self) -> dict:
        ox, oz = self._lane()
        return {"unit": self.id, "kind": self.kind, "depot": self.depot, "status": self.status,
                "vehicle": self.id if self.status in ("inserting", "en_route", "on_scene", "returning", "patrol", "departing") else "",
                "incident": self.incident, "bay": self.id if self.bay else "", "response": self.response, "cargo": self.cargo,
                "location": {"x": round(self.x + ox, 2), "y": 0.0, "z": round(self.z + oz, 2)},
                "speed_mps": round(self.speed, 2)}

    def _lane(self) -> Tuple[float, float]:
        if self.status in ("garaged", "mobilizing"):
            return (0.0, 0.0)
        dx, dz = self.dir
        return (-dz * LANE, dx * LANE)                       # drive on the right


class Fleet:
    def __init__(self, city):
        self.city = city
        self.units: Dict[str, Unit] = {}
        for depot, bays in STATIONS.items():
            for uid, kind, x, z in bays:
                self.units[uid] = Unit(uid, kind, depot, (x, z))
        for uid, depot, loop in PATROLS:
            self.units[uid] = Unit(uid, "police", depot, None, patrol=loop)
        self.aid = 0
        self.stops: Dict[str, Tuple[float, float]] = {}   # "tag|unit" -> stop of a unit sent without an incident

    # ------------------------------------------------------------------ dispatch
    def available(self, kind: str, x: float, z: float) -> Optional[Unit]:
        free = [u for u in self.units.values() if u.kind == kind and not u.incident and u.status in ("garaged", "patrol")
                and not u.id.startswith("AID-")]
        if not free:
            return None
        return min(free, key=lambda u: abs(u.x - x) + abs(u.z - z) + (0 if u.status == "patrol" else 60))

    def dispatch(self, kind: str, iid: str, x: float, z: float, now: float) -> Unit:
        u = self.available(kind, x, z)
        if u is None:                                       # mutual aid: enters through the nearest perimeter road
            self.aid += 1
            gate = min([(-EDGE, l) for l in LINES] + [(EDGE, l) for l in LINES] + [(l, -EDGE) for l in LINES] + [(l, EDGE) for l in LINES],
                       key=lambda g: abs(g[0] - x) + abs(g[1] - z))
            u = Unit(f"AID-{kind.upper()}-{self.aid}", kind, "MUTUAL-AID", None, patrol=[gate, gate])
            u.patrol = None
            u.status = "en_route"
            self.units[u.id] = u
            u.incident, u.response, u.since = iid, kind in RESPONSE_KINDS, now
            u.path = route(gate, snap(x, z))
            u.passed = set()
            return u
        u.incident, u.response, u.since = iid, kind in RESPONSE_KINDS, now
        u.passed = set()
        if u.status == "patrol":                            # a patrol answers from where it is
            u.status = "en_route"
            u.path = route(snap(u.x, u.z), snap(x, z))
        else:
            u.status = "mobilizing"
        return u

    def send_to(self, kind: str, edge: str, pos: float, tag: str, now: float) -> Optional[Unit]:
        """INCIDENTS send_unit: a unit to a stop in a lane, without an incident (the Twin's engine send_unit). It drives
        there along the roads and stays until recalled."""
        import re
        m = re.match(r"^E_r(\d+)c(\d+)_r(\d+)c(\d+)$", edge)
        if not m:
            return None
        r1, c1, r2, c2 = (int(g) for g in m.groups())
        if abs(r1 - r2) + abs(c1 - c2) != 1 or not all(1 <= v <= 9 for v in (r1, c1, r2, c2)):
            return None
        x1, z1 = (c1 - 1) * BLOCK - EDGE, (r1 - 1) * BLOCK - EDGE
        x2, z2 = (c2 - 1) * BLOCK - EDGE, (r2 - 1) * BLOCK - EDGE
        f = max(0.0, min(1.0, pos / BLOCK))
        stop = (x1 + (x2 - x1) * f, z1 + (z2 - z1) * f)
        u = self.dispatch(kind, tag, stop[0], stop[1], now)
        self.stops[tag + "|" + u.id] = stop
        return u

    def recall(self, uid: str, now: float) -> bool:
        u = self.units.get(uid)
        if u is None or not u.incident:
            return False
        self.stops.pop(u.incident + "|" + u.id, None)
        self._go_home(u, now)
        return True

    def scene_of(self, u: Unit) -> Optional[Tuple[float, float]]:
        stop = self.stops.get(u.incident + "|" + u.id)
        if stop is not None:
            return stop
        inc = self.city.incidents.get(u.incident)
        return (inc["location"]["x"], inc["location"]["z"]) if inc else None

    # ------------------------------------------------------------------ driving
    def step(self, dt: float, now: float) -> None:
        if dt <= 0:
            return
        for u in list(self.units.values()):
            self._step(u, dt, now)

    def _step(self, u: Unit, dt: float, now: float) -> None:
        scene = self.scene_of(u) if u.incident else None
        if u.incident and scene is None and u.status in ("mobilizing", "inserting", "en_route", "on_scene"):
            self._go_home(u, now)                           # incident resolved: back to the station
            return
        if u.status == "mobilizing":
            u.speed = 0.0
            if now - u.since >= MOBILIZE_S:
                u.status, u.since = "inserting", now
                u.path = [(u.x, u.z), u.kerb]
        elif u.status == "inserting":
            self._drive(u, dt, 4.0)
            if not u.path:
                u.status, u.since = "en_route", now
                u.path = route(u.kerb, snap(*scene))
        elif u.status in ("en_route", "returning", "patrol"):
            self._drive(u, dt, RESPONSE_SPEED if u.response else NORMAL_SPEED)
            if not u.path:
                if u.status == "en_route":
                    u.status, u.since, u.speed = "on_scene", now, 0.0
                    inc = self.city.incidents.get(u.incident)
                    if inc is not None:
                        inc.setdefault("_first_on_scene", self.city.t)
                        for r in inc["units"]:
                            if r["unit"] == u.id:
                                r["status"] = "on_scene"
                elif u.status == "returning":
                    if u.bay:
                        u.status, u.since, u.response = "entering_depot", now, False
                        u.path = [(u.x, u.z), u.bay]
                    elif u.patrol:
                        u.status = "patrol"
                        u.patrol_i = 0
                        u.path = route((u.x, u.z), u.patrol[1])
                    else:                                   # mutual aid leaves the city
                        del self.units[u.id]
                else:                                       # patrol loop
                    u.patrol_i = (u.patrol_i + 1) % len(u.patrol)
                    u.path = route((u.x, u.z), u.patrol[(u.patrol_i + 1) % len(u.patrol)])
        elif u.status == "entering_depot":
            self._drive(u, dt, 4.0)
            if not u.path:
                u.status, u.speed, u.cargo = "garaged", 0.0, ""
        elif u.status == "on_scene":
            u.speed = 0.0

    def _go_home(self, u: Unit, now: float) -> None:
        inc_kind = (self.city.incident_kinds.get(u.incident) or "")
        patient = u.kind == "ambulance" and inc_kind in PATIENT_INCIDENTS and u.status == "on_scene"
        start = snap(u.x, u.z)
        u.incident, u.since, u.passed = "", now, set()
        if u.status in ("mobilizing",) and u.bay:
            u.status, u.response, u.speed = "garaged", False, 0.0
            return
        u.response = patient                                # a patient on board: lights and sirens to hospital
        u.cargo = "patient" if patient else ""
        u.status = "returning"
        if u.kerb:
            u.path = route(start, u.kerb)
        elif u.patrol:
            u.path = route(start, u.patrol[0])
        else:
            gate = min([(-EDGE, l) for l in LINES] + [(EDGE, l) for l in LINES], key=lambda g: abs(g[0] - u.x) + abs(g[1] - u.z))
            u.path = route(start, gate)

    def _drive(self, u: Unit, dt: float, speed: float) -> None:
        budget = speed * dt
        u.speed = speed
        while budget > 0 and u.path:
            tx, tz = u.path[0]
            dx, dz = tx - u.x, tz - u.z
            d = math.hypot(dx, dz)
            if d < 0.01:
                u.path.pop(0)
                continue
            u.dir = (dx / d, dz / d)
            j = self._junction_ahead(u, d)
            if j is not None:
                jd = abs(j[0] - u.x) + abs(j[1] - u.z)
                if jd <= budget + 1.0 and not self._may_cross(u, j, dt):
                    step = max(0.0, jd - 6.0)                # stop at the line, 6 m before the junction centre
                    u.x += u.dir[0] * min(step, d)
                    u.z += u.dir[1] * min(step, d)
                    u.speed = 0.0
                    return
            if budget >= d:
                u.x, u.z = tx, tz
                budget -= d
                u.path.pop(0)
            else:
                u.x += u.dir[0] * budget
                u.z += u.dir[1] * budget
                budget = 0
        if not u.path:
            u.speed = 0.0

    def _junction_ahead(self, u: Unit, seg: float) -> Optional[Tuple[float, float]]:
        """The next signalled junction on the current segment, not crossed yet."""
        dx, dz = u.dir
        best = None
        for r in range(2, 9):
            for c in range(2, 9):
                jx, jz = (c - 1) * BLOCK - EDGE, (r - 1) * BLOCK - EDGE
                if (r, c) in u.passed:
                    continue
                along = (jx - u.x) * dx + (jz - u.z) * dz
                lateral = abs((jx - u.x) * dz - (jz - u.z) * dx)
                if lateral < 1.0 and -0.5 < along <= seg + 0.5:
                    if best is None or along < best[0]:
                        best = (along, (jx, jz), (r, c))
        if best is None:
            return None
        if best[0] <= 0.5:
            u.passed.add(best[2])
            return None
        return best[1]

    def _may_cross(self, u: Unit, j: Tuple[float, float], dt: float) -> bool:
        r, c = int(round((j[1] + EDGE) / BLOCK)) + 1, int(round((j[0] + EDGE) / BLOCK)) + 1
        sig = self.city.devices.get(f"SIG-Junction_r{r}_c{c}")
        if sig is None:
            return True
        axis = "ew" if abs(u.dir[0]) >= abs(u.dir[1]) else "ns"
        green = self.city._signal(sig, axis) >= 2.0
        if green:
            u.wait = 0.0
            u.passed.add((r, c))
            return True
        if u.response:                                      # red-light clearance: slow, look, cross
            u.wait += dt
            if u.wait >= CLEARANCE_S:
                u.wait = 0.0
                u.passed.add((r, c))
                return True
            return False
        return False                                        # others wait for their green

    # ------------------------------------------------------------------ views
    def rows(self) -> List[dict]:
        return [u.row() for u in self.units.values()]

    def for_incident(self, iid: str) -> List[Unit]:
        return [u for u in self.units.values() if u.incident == iid]

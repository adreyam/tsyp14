"""Mock data of the city: parking lots (with spaces/sensors/barriers), drivers d1..dN, their frequent places and RFID tags.

    python -m citybrain.worldgen          # writes world/world.json (needs world/city.net.xml: python -m citybrain.netgen)

Every driver gets: a car with its own RFID tag (two drivers carry the SAME tag on purpose: d6 and d13), a workplace,
one or two favourite cafes, a children's school (if they have kids) and the spouse's workplace (if married).
Places are labelled with the driver's id ("d3-work", "d3-school", "d3-spouse", "d3-cafe", "d3-cafe2") and are placed
on different streets, away from each other and away from the parking lots.
"""
import json
import math
import os
import random

import sumolib

LAT0, LON0 = 36.8065, 10.1815          # invented centre (Tunis); only used to give the ranking real-looking coordinates
CX = CY = 700.0
SHARED_TAG = ("d6", "d13")             # these two cars carry the same RFID tag (duplicate-tag test)

LOT_SPECS = [  # id, name, capacity, reviews_sum, reviews_count
    ("P01", "Central Garage", 20, 82, 20),
    ("P02", "Station Parking", 16, 55, 14),
    ("P03", "Medina Parking", 12, 38, 9),
    ("P04", "Market Parking", 24, 80, 20),
    ("P05", "Lake Parking", 18, 4, 1),
    ("P06", "Hospital Parking", 14, 60, 15),
    ("P07", "University Parking", 22, 70, 18),
    ("P08", "Harbor Parking", 16, 30, 8),
]
SCHEDULES = {
    "work": [[{"days": "weekdays", "from": "07:00", "to": "16:00"}], [{"days": "weekdays", "from": "08:00", "to": "17:00"}],
             [{"days": "weekdays", "from": "09:00", "to": "18:00"}], [{"days": "weekdays", "from": "07:00", "to": "13:00"}]],
    "school": [[{"days": "weekdays", "from": "15:30", "to": "17:30"}], [{"days": "weekdays", "from": "12:00", "to": "13:30"}]],
    "spouse": [[{"days": "weekdays", "from": "17:30", "to": "19:30"}]],
    "cafe": [[{"days": "weekend", "from": "09:00", "to": "13:00"}, {"days": "weekdays", "from": "13:00", "to": "15:00"}],
             [{"days": "weekend", "from": "15:00", "to": "20:00"}, {"days": "weekdays", "from": "18:00", "to": "21:00"}]],
}
WEIGHT = {"work": 3, "school": 3, "spouse": 2, "cafe": 1}
DWELL_CLOCK_MIN = {"work": (180, 360), "school": (20, 40), "spouse": (20, 40), "cafe": (45, 90)}   # parked time in simulated clock minutes


def to_latlon(x, y):
    return LAT0 + (y - CY) / 111320.0, LON0 + (x - CX) / (111320.0 * math.cos(math.radians(LAT0)))


def from_latlon(lat, lon):
    return CX + (lon - LON0) * 111320.0 * math.cos(math.radians(LAT0)), CY + (lat - LAT0) * 111320.0


def _seg_point(net, edge, s):
    shape = edge.getShape()
    (x0, y0), (x1, y1) = shape[0], shape[-1]
    L = math.hypot(x1 - x0, y1 - y0)
    return x0 + (x1 - x0) * s / L, y0 + (y1 - y0) * s / L, (x1 - x0) / L, (y1 - y0) / L


def build_lots(net, rng):
    inner = [e for e in net.getEdges() if e.getFromNode().getType() == "traffic_light" and e.getToNode().getType() == "traffic_light"]
    chosen = []
    for _ in range(len(LOT_SPECS)):        # farthest-point sampling so the lots are spread over the whole map
        best, bd = None, -1
        for e in rng.sample(inner, len(inner)):
            mx, my = [(a + b) / 2 for a, b in zip(e.getFromNode().getCoord(), e.getToNode().getCoord())]
            d = min([math.hypot(mx - cx, my - cy) for cx, cy in chosen] or [1e9])
            if d > bd:
                best, bd, bm = e, d, (mx, my)
        chosen.append(bm)
        yield best


def lot_geometry(edge, cap):
    Le = edge.getLength()
    x0, y0, dx, dy = _seg_point(None, edge, 0.0)
    nx, ny = dy, -dx                              # right-hand normal (the lot is on the right side of the travelling direction)
    s0, s1 = 38.0, Le - 34.0
    L = s1 - s0
    u_in, u_out = 12.0, L - 12.0
    D = 42.0
    off = 9.0                                      # metres from the lane centre to the street-side edge of the lot

    def P(u, v):
        x, y, _, _ = _seg_point(None, edge, s0 + u)
        return round(x + nx * (off + v), 2), round(y + ny * (off + v), 2)

    spaces = []
    cols = [5.0 * k + 8.0 for k in range(int((L - 16) // 5))]
    for row, v in (("A", 10.0), ("B", 32.0)):
        for u in cols:
            if row == "A" and (abs(u - u_in) < 6.5 or abs(u - u_out) < 6.5):
                continue
            spaces.append((row, u, v))
    spaces.sort(key=lambda t: (abs(t[1] - u_in) + abs(t[2] - 21.0) * 0.6))   # nearest to the entrance first
    spaces = spaces[:cap]
    spaces.sort(key=lambda t: (t[0], t[1]))
    out = []
    counter = {"A": 0, "B": 0}
    for row, u, v in spaces:
        counter[row] += 1
        out.append({"code": f"{row}{counter[row]}", "u": u, "v": v, "x": P(u, v)[0], "y": P(u, v)[1], "row": row})
    out.sort(key=lambda s: abs(s["u"] - u_in) + abs(s["v"] - 21.0) * 0.6)
    for i, s in enumerate(out):
        s["position"] = i
    corners = [P(0, 0), P(L, 0), P(L, D), P(0, D)]
    return {"access_edge": edge.getID(), "s0": s0, "L": L, "D": D, "u_in": u_in, "u_out": u_out, "entry_pos": round(s0 + u_in, 2),
            "exit_pos": round(s0 + u_out, 2), "aisle_v": 21.0, "corners": corners,
            "gate_in": P(u_in, 0), "gate_out": P(u_out, 0), "aisle_in": P(u_in, 21.0), "aisle_out": P(u_out, 21.0),
            "centre": P(L / 2, D / 2), "spaces": out}


def place_candidates(net):
    cands = []
    seen = set()
    for e in net.getEdges():
        if e.getFromNode().getType() != "traffic_light" or e.getToNode().getType() != "traffic_light":
            continue                       # border roads are one-way entries/exits of the city: no place can be reached there
        a, b = e.getFromNode().getID(), e.getToNode().getID()
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        (x0, y0), (x1, y1) = e.getFromNode().getCoord(), e.getToNode().getCoord()
        L = math.hypot(x1 - x0, y1 - y0)
        dx, dy = (x1 - x0) / L, (y1 - y0) / L
        for frac in (0.3, 0.7):
            for side in (1, -1):
                px, py = x0 + dx * L * frac + (dy * 34 * side), y0 + dy * L * frac - (dx * 34 * side)
                cands.append({"x": round(px, 1), "y": round(py, 1), "edge": e.getID(), "pos": round(L * frac, 1)})
    return cands


def poly_dist(px, py, corners):
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    cx, cy = min(max(px, min(xs)), max(xs)), min(max(py, min(ys)), max(ys))
    return math.hypot(px - cx, py - cy)


def generate(net_file="world/city.net.xml", out="world/world.json", n_drivers=16, seed=7):
    rng = random.Random(seed)
    net = sumolib.net.readNet(net_file)
    lots = []
    for (lot_id, name, cap, rs, rc), edge in zip(LOT_SPECS, build_lots(net, rng)):
        g = lot_geometry(edge, cap)
        lat, lon = to_latlon(*g["centre"])
        lots.append({"id": lot_id, "name": name, "capacity": len(g["spaces"]), "review_sum": rs, "review_count": rc, "lat": lat, "lon": lon, **g})

    cands = place_candidates(net)
    cands = [c for c in cands if all(poly_dist(c["x"], c["y"], l["corners"]) > 75 for l in lots)]
    taken = []

    def pick(dmin=95.0):
        for c in rng.sample(cands, len(cands)):
            if all(math.hypot(c["x"] - t["x"], c["y"] - t["y"]) >= dmin for t in taken):
                taken.append(c)
                return c
        raise RuntimeError("not enough room for all the places; lower n_drivers")

    drivers, places = [], []
    first_names = ["Amine", "Sara", "Youssef", "Ines", "Karim", "Nour", "Omar", "Leila", "Hamza", "Rim", "Walid", "Mariem", "Slim", "Hela", "Fares", "Salma",
                   "Bilel", "Yasmine", "Anis", "Dorra"]
    for k in range(1, n_drivers + 1):
        did = f"d{k}"
        married = rng.random() < 0.6
        kids = rng.random() < (0.75 if married else 0.15)
        tag = f"RFID_D{k:02d}"
        shared = None
        if did == SHARED_TAG[1]:
            tag, shared = f"RFID_D{int(SHARED_TAG[0][1:]):02d}", SHARED_TAG[0]
        drivers.append({"id": did, "name": first_names[(k - 1) % len(first_names)], "rfid": tag, "plate": f"{100 + k * 7} TU {1000 + k * 113 % 9000}",
                        "married": married, "kids": kids, "shares_tag_with": shared,
                        "registered_tag": did != SHARED_TAG[1]})
        kinds = [("work", "work", None), ("cafe", "cafe", 0)]
        if rng.random() < 0.5:
            kinds.append(("cafe2", "cafe", 1))
        if kids:
            kinds.append(("school", "school", None))
        if married:
            kinds.append(("spouse", "spouse", None))
        for suffix, kind, idx in kinds:
            c = pick()
            sched = rng.choice(SCHEDULES[kind]) if idx is None else SCHEDULES["cafe"][idx]
            lat, lon = to_latlon(c["x"], c["y"])
            label = {"work": "workplace", "cafe": "favourite cafe", "school": "children's school", "spouse": "spouse's workplace"}[kind]
            if suffix == "cafe2":
                label = "favourite cafe (2nd)"
            places.append({"id": f"{did}-{suffix}", "driver": did, "kind": kind, "label": f"{did} {label}", "x": c["x"], "y": c["y"], "lat": lat, "lon": lon,
                           "edge": c["edge"], "pos": c["pos"], "weight": WEIGHT[kind], "schedule": sched, "dwell_clock_min": DWELL_CLOCK_MIN[kind]})
    border_in = [e.getID() for e in net.getEdges() if e.getFromNode().getType() == "dead_end"]
    border_out = [e.getID() for e in net.getEdges() if e.getToNode().getType() == "dead_end"]
    b = net.getBoundary()
    world = {"seed": seed, "origin": {"lat0": LAT0, "lon0": LON0, "cx": CX, "cy": CY}, "bbox": [b[0], b[1], b[2], b[3]],
             "net_file": net_file, "lots": lots, "drivers": drivers, "places": places, "border_in": border_in, "border_out": border_out,
             "emergency": {"ambulance": 3, "police": 3, "fire": 2}}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as fh:
        json.dump(world, fh, indent=1)
    return world


if __name__ == "__main__":
    w = generate()
    print(f"{len(w['lots'])} lots ({sum(l['capacity'] for l in w['lots'])} spaces), {len(w['drivers'])} drivers, {len(w['places'])} places")

"""Mock traffic data for training: many different days of traffic on the 2x2 grid, with emergency vehicles.

    python training/mock_data.py                       # 240 scenarios into data/mock_traffic/
    python training/mock_data.py --scenarios 500 --seed 7

Every scenario is a SUMO route file made of flows whose rate changes every 5 minutes, like a real day: morning rush
(heavy west->east and north->south), evening rush (the reverse), balanced, midday low, a sudden event spike, and
one-sided overload. The rates get random noise, 10 % of the vehicles are trucks, and 1-3 ambulances start at random times
from random entries. Output:
    data/mock_traffic/scenario_0001.rou.xml ...   the route files
    data/mock_traffic/manifest.csv                one row per scenario: profile, split (train / val / test), vehicles, ambulances
    data/mock_traffic/demand_windows.csv          the demand table the route files were made from (vehicles per hour)
"""

import argparse
import csv
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import sumolib  # noqa: E402

NET = "sumo_rl/nets/2x2grid/2x2.net.xml"
WINDOW = 300  # seconds per demand window
PROFILES = ["morning_rush", "evening_rush", "balanced", "midday_low", "event_spike", "one_sided", "random_walk"]

VTYPES = """    <vTypeDistribution id="mix">
        <vType id="car" probability="0.9" vClass="passenger" length="4.5" accel="2.6" decel="4.5" maxSpeed="13.9" sigma="0.5"/>
        <vType id="truck" probability="0.1" vClass="truck" length="9.0" accel="1.3" decel="3.5" maxSpeed="11" sigma="0.5"/>
    </vTypeDistribution>
    <vType id="ambulance" vClass="emergency" guiShape="emergency" color="1,0,0" length="6.5" width="2.4"
           accel="3.5" decel="5.0" maxSpeed="22" speedFactor="1.2" lcStrategic="1" lcSpeedGain="2">
        <param key="has.bluelight.device" value="true"/>
    </vType>"""


def od_pairs(net):
    """Valid entry->exit pairs with their direction group ('we', 'ns', 'ew', 'sn') and whether the trip turns."""
    entries = [e for e in net.getEdges() if e.getFromNode().getType() != "traffic_light"]
    exits = [e for e in net.getEdges() if e.getToNode().getType() != "traffic_light"]
    pairs = []
    for s in entries:
        for t in exits:
            if s == t:
                continue
            route, _ = net.getShortestPath(s, t)
            if not route:
                continue
            signals = sum(1 for e in route[:-1] if e.getToNode().getType() == "traffic_light")
            if signals < 2:
                continue
            (x0, y0), (x1, y1) = s.getShape()[0], t.getShape()[-1]
            dx, dy = x1 - x0, y1 - y0
            main = ("we" if dx > 0 else "ew") if abs(dx) >= abs(dy) else ("sn" if dy > 0 else "ns")
            straight = abs(dx) < 40 or abs(dy) < 40
            pairs.append({"from": s.getID(), "to": t.getID(), "dir": main, "signals": signals, "straight": straight})
    return pairs


def profile_multiplier(profile, window, n_windows, direction, rng, state):
    """Rate multiplier of one direction in one window (1.0 = the base rate)."""
    x = window / max(1, n_windows - 1)  # 0..1 across the scenario
    if profile == "morning_rush":
        base = 0.55 + 1.15 * math.sin(math.pi * x) if direction in ("we", "ns") else 0.45 + 0.3 * math.sin(math.pi * x)
    elif profile == "evening_rush":
        base = 0.55 + 1.15 * math.sin(math.pi * x) if direction in ("ew", "sn") else 0.45 + 0.3 * math.sin(math.pi * x)
    elif profile == "midday_low":
        base = 0.35 + 0.1 * math.sin(2 * math.pi * x)
    elif profile == "event_spike":
        base = 0.5 + (1.9 if state["spike"][0] <= window <= state["spike"][1] and direction == state["spike_dir"] else 0.2)
    elif profile == "one_sided":
        base = 1.6 if direction == state["heavy"] else 0.35
    elif profile == "random_walk":
        state["walk"][direction] = min(2.0, max(0.2, state["walk"].get(direction, 1.0) + rng.uniform(-0.45, 0.45)))
        base = state["walk"][direction]
    else:  # balanced
        base = 0.9 + 0.15 * math.sin(2 * math.pi * x)
    return max(0.05, base * rng.lognormvariate(0, 0.15))


def make_scenario(idx, pairs, rng, duration, base_rate):
    profile = PROFILES[idx % len(PROFILES)] if idx < 7 * 3 else rng.choice(PROFILES)
    n_windows = duration // WINDOW
    state = {"spike": (WINDOW * rng.randint(0, max(0, n_windows - 2)),), "spike_dir": rng.choice(["we", "ns", "ew", "sn"]),
             "heavy": rng.choice(["we", "ns", "ew", "sn"]), "walk": {}}
    state["spike"] = (state["spike"][0], state["spike"][0] + WINDOW)
    # each OD pair keeps a fixed share of its direction: straight trips carry more than turning ones
    shares = {(p["from"], p["to"]): (1.0 if p["straight"] else 0.35) * rng.uniform(0.7, 1.3) for p in pairs}
    per_dir = {}
    for p in pairs:
        per_dir.setdefault(p["dir"], []).append(p)
    flows, demand = [], []
    for w in range(n_windows):
        begin, end = w * WINDOW, (w + 1) * WINDOW
        for d, plist in per_dir.items():
            mult = profile_multiplier(profile, w, n_windows, d, rng, state)
            norm = sum(shares[(p["from"], p["to"])] for p in plist)
            for p in plist:
                vph = base_rate * mult * shares[(p["from"], p["to"])] / norm / len(per_dir)
                demand.append((w, p["from"], p["to"], round(vph, 1)))
                flows.append((begin, end, p["from"], p["to"], vph))
    return profile, flows, demand


def ambulances(pairs, rng, duration, n):
    out = []
    for k in range(n):
        p = rng.choice(pairs)
        out.append((rng.randint(60, max(61, duration - 240)), p["from"], p["to"]))
    return sorted(out)


def write_route_file(path, idx, flows, amb):
    items = []  # (begin, text): SUMO needs the file sorted by departure time
    for n, (b, e, f, t, vph) in enumerate(flows):
        if vph < 1:
            continue
        items.append((b, f'    <flow id="f{n}" type="mix" from="{f}" to="{t}" begin="{b}" end="{e}" probability="{min(0.9, vph / 3600):.5f}" '
                         'departSpeed="max" departPos="base" departLane="best"/>'))
    for k, (t0, f, t) in enumerate(amb):
        items.append((t0, f'    <flow id="amb_{idx}_{k}" type="ambulance" from="{f}" to="{t}" begin="{t0}" end="{t0 + 1}" number="1" '
                          'departSpeed="max" departLane="best"/>'))
    items.sort(key=lambda x: x[0])
    with open(path, "w") as fh:
        fh.write("\n".join(["<routes>", VTYPES] + [x[1] for x in items] + ["</routes>"]) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=240)
    ap.add_argument("--duration", type=int, default=1200, help="seconds of traffic per scenario (a multiple of 300)")
    ap.add_argument("--base-rate", type=float, default=1500.0, help="vehicles per hour entering the whole grid at multiplier 1")
    ap.add_argument("--out", default="data/mock_traffic")
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    assert a.duration % WINDOW == 0, "duration must be a multiple of 300 s"
    os.makedirs(a.out, exist_ok=True)
    net = sumolib.net.readNet(NET)
    pairs = od_pairs(net)
    rng = random.Random(a.seed)
    manifest, demand_rows = [], []
    for i in range(1, a.scenarios + 1):
        profile, flows, demand = make_scenario(i - 1, pairs, rng, a.duration, a.base_rate)
        amb = ambulances(pairs, rng, a.duration, rng.choice([1, 1, 2, 3]))
        name = f"scenario_{i:04d}"
        write_route_file(os.path.join(a.out, name + ".rou.xml"), i, flows, amb)
        expected = sum(vph * (WINDOW / 3600) for (_, _, _, _, vph) in flows)
        split = "train" if (i % 20) not in (0, 1, 2) else ("val" if (i % 20) in (1, 2) else "test")
        manifest.append({"scenario": name, "file": f"{a.out}/{name}.rou.xml".replace("\\", "/"), "split": split, "profile": profile,
                         "seconds": a.duration, "expected_vehicles": round(expected), "ambulances": len(amb)})
        demand_rows += [{"scenario": name, "window": w, "from": f, "to": t, "veh_per_hour": v} for (w, f, t, v) in demand]
    with open(os.path.join(a.out, "manifest.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(manifest[0])); wr.writeheader(); wr.writerows(manifest)
    with open(os.path.join(a.out, "demand_windows.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(demand_rows[0])); wr.writeheader(); wr.writerows(demand_rows)
    counts = {s: sum(m["split"] == s for m in manifest) for s in ("train", "val", "test")}
    veh = sum(m["expected_vehicles"] for m in manifest)
    print(f"{a.scenarios} scenarios in {a.out} ({counts}), about {veh:,} vehicles, {sum(m['ambulances'] for m in manifest)} ambulances")
    print("profiles:", {p: sum(m['profile'] == p for m in manifest) for p in PROFILES})


if __name__ == "__main__":
    main()

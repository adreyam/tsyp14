"""Headless benchmark: fixed-time signals vs the learned policy of runs/city, on identical traffic.

    python -m citybrain.benchmark --run-dir runs/city --scenarios 3 --seconds 900

Both controllers get exactly the same vehicles (same routes, same departure times, same ambulances / police / fire
trucks) and the same emergency-priority system; only the traffic-signal control differs. The result is appended to
<run-dir>/benchmarks.jsonl and shown in the dashboard. It runs in its own SUMO process, so the live city is not disturbed.
"""
import argparse
import json
import os
import pickle
import random
import sys
import time
import xml.etree.ElementTree as ET
from statistics import mean

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import sumolib  # noqa: E402

import sumo_rl  # noqa: E402
from sumo_rl.preemption import PreemptionConfig  # noqa: E402
from training.core import TabularQ, append_jsonl, make_encoder  # noqa: E402

EMERGENCY = {"ambulance": "amb", "police": "pol", "fire": "fire"}


def make_routes(world, net, path, seconds, seed, rate, jam=0):
    """Background trips (border to border) plus a few emergency runs, written as one routes file."""
    rng = random.Random(seed)
    types = open(os.path.join("world", "types.rou.xml")).read().replace("<routes>", "").replace("</routes>", "")
    cache = {}

    def route(o, d, vtype="passenger"):
        k = (o, d)
        if k not in cache:
            p, _ = net.getShortestPath(net.getEdge(o), net.getEdge(d))
            cache[k] = [e.getID() for e in p] if p else []
        return cache[k]

    trips = []
    t, n = 0.0, 0
    last_dep = 0.7 * seconds
    while t < last_dep:
        t += rng.expovariate(rate)
        for _ in range(8):
            o, d = rng.choice(world["border_in"]), rng.choice(world["border_out"])
            r = route(o, d)
            if len(r) >= 5:
                n += 1
                trips.append((t, f"b{n}", "mix", r))
                break
    for typ, prefix in EMERGENCY.items():
        for i in range(world["emergency"].get(typ, 2) - 1):
            for _ in range(10):
                o, d = rng.choice(world["border_in"]), rng.choice(world["border_out"])
                r = route(o, d)
                if len(r) >= 9:
                    trips.append((rng.uniform(30, 0.5 * seconds), f"{prefix}_{i + 1}", typ, r))
                    break
    if jam:       # traffic-jam test: `jam` extra cars within 60 s, from t=120 s, on the 5 longest of a sample of routes
        cand = {}
        for _ in range(60):
            o, d = rng.choice(world["border_in"]), rng.choice(world["border_out"])
            r = route(o, d)
            if len(r) >= 8:
                cand[(o, d)] = r
        long_routes = sorted(cand.values(), key=len, reverse=True)[:5]
        for i in range(jam):
            trips.append((120 + 60.0 * i / jam, f"j{i + 1}", "mix", long_routes[i % len(long_routes)]))
    trips.sort(key=lambda x: x[0])
    with open(path, "w") as fh:
        fh.write("<routes>\n" + types + "\n")
        for k, (dep, vid, typ, r) in enumerate(trips):
            fh.write(f'  <vehicle id="{vid}" type="{typ}" depart="{dep:.1f}" departLane="best" departSpeed="max"><route edges="{" ".join(r)}"/></vehicle>\n')
        fh.write("</routes>\n")
    return len(trips)


def tripinfo(path):
    other, emerg = [], []
    if os.path.exists(path):
        for t in ET.parse(path).getroot().iter("tripinfo"):
            rec = (float(t.get("waitingTime")), float(t.get("duration")), float(t.get("timeLoss")))
            (emerg if t.get("id").split("_")[0] in EMERGENCY.values() else other).append(rec)
    return other, emerg


def run(world, routes, seconds, seed, q=None):
    """One run. q=None -> fixed-time signals; otherwise greedy (no exploring, no learning) with the shared Q-table."""
    os.makedirs("data", exist_ok=True)
    out = f"data/bench_tripinfo_{os.getpid()}.xml"      # relative path without spaces (SUMO command line is split on spaces)
    pre = PreemptionConfig(emergency_vtypes=tuple(EMERGENCY), priority_by_vtype={"ambulance": 3, "fire": 2, "police": 1}, equipped=None)
    env = sumo_rl.SumoEnvironment(net_file=world["net_file"], route_file=routes, use_gui=False, num_seconds=seconds, delta_time=5,
                                  yellow_time=2, min_green=5, max_green=50, sumo_warnings=False, emergency_preemption=pre,
                                  fixed_ts=q is None, add_system_info=True, add_per_agent_info=False, sumo_seed=seed,
                                  time_to_teleport=60, additional_sumo_cmd=f"--no-step-log --tripinfo-output {out}")
    obs = env.reset()
    if isinstance(obs, tuple):
        obs = obs[0]
    encode = make_encoder(env)
    rng = np.random.default_rng(seed)
    stopped, speeds = [], []
    done = False
    while not done:
        acts = {}
        if q is not None:
            for ts in obs:
                s = encode(ts, obs[ts])
                n = env.traffic_signals[ts].num_green_phases
                v = q.values(s)[:n]
                acts[ts] = int(rng.choice(np.flatnonzero(v == v.max())))
        obs, _, dones, info = env.step(acts)
        done = dones["__all__"]
        stopped.append(info.get("system_total_stopped", 0))
        speeds.append(info.get("system_mean_speed", 0))
    env.close()
    other, emerg = tripinfo(out)
    if os.path.exists(out):
        os.remove(out)
    m = lambda rows, i: round(mean(r[i] for r in rows), 2) if rows else None  # noqa: E731
    return {"mean_wait": m(other, 0), "trip_time": m(other, 1), "time_loss": m(other, 2), "arrived": len(other),
            "emerg_wait": m(emerg, 0), "emerg_trip": m(emerg, 1), "emerg_arrived": len(emerg),
            "mean_stopped": round(mean(stopped), 2) if stopped else None, "peak_stopped": max(stopped) if stopped else None,
            "mean_speed": round(mean(x for x in speeds if x >= 0), 2) if speeds else None}


def avg(rows):
    out = {}
    for k in rows[0]:
        v = [r[k] for r in rows if r[k] is not None]
        out[k] = round(mean(v), 2) if v else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="runs/city")
    ap.add_argument("--scenarios", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=900)
    ap.add_argument("--jam", type=int, default=0, help="add a traffic jam of this many extra cars (e.g. 300)")
    ap.add_argument("--rate", type=float, default=0.45, help="vehicles entering the city per second")
    a = ap.parse_args()
    os.chdir(ROOT)
    world = json.load(open("world/world.json"))
    net = sumolib.net.readNet(world["net_file"])
    with open(os.path.join(a.run_dir, "checkpoint.pkl"), "rb") as fh:
        ck = pickle.load(fh)
    q = TabularQ(4, 0.0, 0.0)
    q.q = ck["q"]
    fixed, learned = [], []
    for i in range(a.scenarios):
        seed = 777_000 + i
        routes = os.path.join("data", f"bench_{os.getpid()}_{i}.rou.xml")
        n = make_routes(world, net, routes, a.seconds, seed, a.rate, a.jam)
        f = run(world, routes, a.seconds, seed)
        g = run(world, routes, a.seconds, seed, q)
        fixed.append(f)
        learned.append(g)
        print(f"scenario {i + 1}/{a.scenarios} ({n} vehicles): wait fixed {f['mean_wait']} s -> learned {g['mean_wait']} s", flush=True)
        os.remove(routes)
    row = {"episode": ck["episode"], "when": time.strftime("%Y-%m-%d %H:%M:%S"), "scenarios": a.scenarios, "seconds": a.seconds,
           "q_states": len(ck["q"]), "fixed": avg(fixed), "learned": avg(learned), "jam": a.jam}
    append_jsonl(os.path.join(a.run_dir, "benchmarks.jsonl"), row)
    print(json.dumps(row))


if __name__ == "__main__":
    main()

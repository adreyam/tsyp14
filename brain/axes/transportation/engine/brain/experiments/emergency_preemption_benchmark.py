"""Emergency-vehicle priority benchmark: the same traffic and the same ambulances, with and without preemption.

Usage (from the repo root):
    python experiments/emergency_preemption_benchmark.py                 # fixed-time signals, 5 seeds
    python experiments/emergency_preemption_benchmark.py --seeds 10 --loss 0.2
Prints, for the ambulances and for all the other vehicles, the mean trip duration, time lost, waiting time and number of
stops, plus the events of one preemption. Results are written to outputs/emergency_preemption/.
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from statistics import mean

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sumo_rl  # noqa: E402
from sumo_rl import PreemptionConfig  # noqa: E402

NET = "sumo_rl/nets/2x2grid/2x2.net.xml"
BASE_ROUTES = "sumo_rl/nets/2x2grid/2x2.rou.xml"
AMBULANCE_TRIPS = [("-h11", "-h13"), ("h13", "h11"), ("-v11", "-v13"), ("v13", "v11"), ("-h21", "-h23"),
                   ("-v21", "-v23"), ("-h11", "-v23"), ("h23", "h11"), ("-v21", "-h23"), ("v23", "v21")]
OUT = "outputs/emergency_preemption"


def write_ambulances(n, horizon):
    """n ambulances, evenly spread over the horizon, over different routes of the 2x2 grid."""
    os.makedirs(OUT, exist_ok=True)
    head = open("sumo_rl/nets/emergency/ambulances.rou.xml").read().split("    <flow")[0]
    gap = (horizon - 120) / max(n, 1)
    rows = []
    for i in range(n):
        f, t = AMBULANCE_TRIPS[i % len(AMBULANCE_TRIPS)]
        rows.append(f'    <flow id="amb_{i}" type="ambulance" from="{f}" to="{t}" begin="{int(60 + i * gap)}" number="1" departSpeed="max" departLane="best"/>')
    path = f"{OUT}/ambulances_{n}.rou.xml"
    open(path, "w").write(head + "\n".join(rows) + "\n</routes>\n")
    return path


def run(preempt, seed, loss, horizon, routes):
    trip = f"{OUT}/tripinfo_{'on' if preempt else 'off'}_{seed}.xml"
    cfg = PreemptionConfig(packet_loss=loss, seed=seed) if preempt else None
    env = sumo_rl.SumoEnvironment(
        net_file=NET, route_file=routes, fixed_ts=True, num_seconds=horizon, delta_time=5, sumo_seed=seed,
        emergency_preemption=cfg, sumo_warnings=False, additional_sumo_cmd=f"--tripinfo-output {trip} --no-step-log",
    )
    env.reset()
    done = False
    while not done:
        _, _, d, _ = env.step(None)
        done = d["__all__"]
    events = list(env.preemption.events) if env.preemption else []
    env.close()
    amb, other = [], []
    for t in ET.parse(trip).getroot().iter("tripinfo"):
        rec = {k: float(t.get(k)) for k in ("duration", "timeLoss", "waitingTime", "waitingCount")}
        (amb if t.get("id").startswith("amb") else other).append(rec)
    return amb, other, events


def summary(rows):
    return {k: mean(r[k] for r in rows) for k in ("duration", "timeLoss", "waitingTime", "waitingCount")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--loss", type=float, default=0.0, help="radio packet loss probability")
    ap.add_argument("--horizon", type=int, default=900)
    ap.add_argument("--ambulances", type=int, default=4, help="number of ambulances in the run")
    a = ap.parse_args()
    routes = BASE_ROUTES + "," + write_ambulances(a.ambulances, a.horizon)
    res = {False: ([], []), True: ([], [])}
    first_events = []
    for seed in range(1, a.seeds + 1):
        for p in (False, True):
            amb, other, ev = run(p, seed, a.loss, a.horizon, routes)
            res[p][0].extend(amb)
            res[p][1].extend(other)
            if p and not first_events:
                first_events = ev
    print(f"\n{a.seeds} seeds, {a.ambulances} ambulances, fixed-time signals, packet loss {a.loss:.0%}")
    for name, idx in (("AMBULANCES", 0), ("OTHER VEHICLES", 1)):
        off, on = summary(res[False][idx]), summary(res[True][idx])
        print(f"\n{name} (n = {len(res[False][idx])} without / {len(res[True][idx])} with)")
        print(f"{'':16}{'no preemption':>15}{'preemption':>13}{'change':>10}")
        for k, label in (("duration", "trip (s)"), ("timeLoss", "time lost (s)"), ("waitingTime", "waiting (s)"), ("waitingCount", "stops")):
            ch = on[k] - off[k]
            print(f"{label:16}{off[k]:15.2f}{on[k]:13.2f}{ch:+10.2f}")
    print("\nFirst preemption events (seed 1):")
    for e in first_events[:14]:
        print("  ", e)


if __name__ == "__main__":
    main()

"""Emergency-vehicle priority on one intersection: watch the signal, the radio chain and the ambulance.

    python experiments/emergency_preemption_demo.py            # text timeline, with and without preemption
    python experiments/emergency_preemption_demo.py --gui      # same, but opens sumo-gui twice, slowly: without, then with priority
    python experiments/emergency_preemption_demo.py --loss 0.5 # 50 % of the radio packets are lost

Two ambulances approach a red signal (one on each road). Without priority they stop; with it the signal turns
the approach green (through yellow) in time, holds it, and then rejoins its normal program.
"""

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sumo_rl  # noqa: E402
from sumo_rl import PreemptionConfig  # noqa: E402

NET = "sumo_rl/nets/single-intersection/single-intersection.net.xml"
ROUTES = "sumo_rl/nets/single-intersection/single-intersection.rou.xml,sumo_rl/nets/emergency/single_ambulances.rou.xml"
OUT = "outputs/emergency_preemption"


def run(preempt, gui, loss, seed=1, horizon=220, verbose=True):
    os.makedirs(OUT, exist_ok=True)
    trip = f"{OUT}/demo_tripinfo_{'on' if preempt else 'off'}.xml"
    cfg = PreemptionConfig(packet_loss=loss, seed=seed) if preempt else None
    # in the GUI the window must not close by itself: sumo-rl adds --quit-on-end, so the run is paced and held open here
    env = sumo_rl.SumoEnvironment(
        net_file=NET, route_file=ROUTES, fixed_ts=True, use_gui=gui, num_seconds=horizon, delta_time=5, sumo_seed=seed,
        emergency_preemption=cfg, sumo_warnings=False, additional_sumo_cmd=f"--tripinfo-output {trip} --no-step-log",
    )
    if gui:
        label = "WITH priority" if preempt else "WITHOUT priority"
        input(f"\nPress Enter to start the run {label}. sumo-gui will open; watch the red ambulance and its signal... ")
    env.reset()
    tl = env.sumo.trafficlight
    last_state, rows = None, []
    for _ in range(horizon):
        env._sumo_step()
        t = env.sim_step
        if gui:
            time.sleep(0.15)  # slow motion: about 7 simulated seconds per real second
            try:
                if "amb_we.0" in env.sumo.vehicle.getIDList():
                    env.sumo.gui.trackVehicle("View #0", "amb_we.0")  # the camera follows the ambulance
                    env.sumo.gui.setZoom("View #0", 1800)
            except Exception:
                pass
        st = tl.getRedYellowGreenState("t")
        if st != last_state:
            rows.append((t, st))
            last_state = st
    if gui:
        input("Run finished. Press Enter to close sumo-gui... ")
    events = list(env.preemption.events) if env.preemption else []
    stats = (env.preemption.messages_sent, env.preemption.messages_lost) if env.preemption else (0, 0)
    env.close()
    amb = {}
    for t in ET.parse(trip).getroot().iter("tripinfo"):
        if t.get("id").startswith("amb"):
            amb[t.get("id")] = {k: float(t.get(k)) for k in ("duration", "timeLoss", "waitingTime", "waitingCount")}
    return rows, events, amb, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--loss", type=float, default=0.0)
    a = ap.parse_args()
    off_rows, _, off_amb, _ = run(False, a.gui, 0.0)
    on_rows, events, on_amb, (sent, lost) = run(True, a.gui, a.loss)
    print("\nSignal 't' states (second, state)  -- links: 2 for each road, G green, y yellow, r red")
    print("  without preemption:", " ".join(f"{int(t)}:{s}" for t, s in off_rows))
    print("  with preemption   :", " ".join(f"{int(t)}:{s}" for t, s in on_rows))
    print(f"\nRadio: {sent} packets reached a receiver, {lost} lost\n\nWhat the system did:")
    for e in events:
        extra = {k: v for k, v in e.items() if k not in ("time", "signal", "event")}
        print(f"  t={e['time']:5.0f}s  {e['event']:16} {extra if extra else ''}")
    print("\nAmbulances              without    with")
    for vid in sorted(off_amb):
        for k, label in (("waitingTime", "waiting (s)"), ("waitingCount", "stops"), ("timeLoss", "time lost (s)"), ("duration", "trip (s)")):
            print(f"  {vid:6} {label:14}{off_amb[vid][k]:8.1f}{on_amb.get(vid, {}).get(k, float('nan')):8.1f}")


if __name__ == "__main__":
    main()

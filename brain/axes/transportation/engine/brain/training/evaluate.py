"""Test the saved agent on the held-out TEST scenarios and compare with fixed-time signals (both with the ambulances present).

    python training/evaluate.py                    # uses the latest checkpoint of runs/main
    python training/evaluate.py --best             # uses the best-on-validation model instead
"""

import argparse
import os
import pickle
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from training.core import Trainer  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="runs/main")
    ap.add_argument("--best", action="store_true")
    ap.add_argument("--scenarios", type=int, default=6)
    a = ap.parse_args()
    t = Trainer(a.run_dir)
    if a.best:
        with open(t.paths["best"], "rb") as fh:
            for ts, q in pickle.load(fh)["q"].items():
                t.agents[ts].q = q
    test = (t.splits["test"] or t.splits["val"])[: a.scenarios]
    print(f"Run '{t.name}' at episode {t.episode}. Testing on {len(test)} unseen scenarios.\n")
    fixed, agent = [], []
    for i, s in enumerate(test):
        f = t.run_fixed(s, 900_000 + i)
        g = t.run_episode(s, 900_000 + i, 0.0, learn=False, label="test")
        fixed.append(f)
        agent.append(g)
        print(f"{s['scenario']} {s['profile']:13s} wait fixed {f['mean_wait']:6} -> agent {g['mean_wait']:6} | ambulance wait fixed {f['amb_wait']} -> agent {g['amb_wait']}")
    sf, sa = t._summ(fixed), t._summ(agent)
    print(f"\nAverage waiting time per vehicle: fixed-time {sf['mean_wait']} s, agent {sa['mean_wait']} s ({100*(sf['mean_wait']-sa['mean_wait'])/sf['mean_wait']:+.1f} %)")
    print(f"Average ambulance waiting time:   fixed-time {sf['amb_wait']} s, agent {sa['amb_wait']} s")
    t.close()


if __name__ == "__main__":
    main()

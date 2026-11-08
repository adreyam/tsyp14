"""Statistical benchmark and ablation study.

  python -m bench.run --seeds 30                       # main comparison: Brain vs reactive vs do-nothing
  python -m bench.run --seeds 30 --set ablation         # remove one mechanism at a time
  python -m bench.run --policies full,reactive --families ramp,step --seeds 10

Every (policy, family, seed) is one fast-mode run of the real EnergyBrain against
the mock Twin and Guardian. Results go to bench/results/<set>.jsonl (one line per
run); `python -m bench.report` turns them into tables and charts.

Baselines and ablations are applied from outside (config overrides or a hook on
the built Brain); nothing here changes production behaviour.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from .scenarios import FAMILIES, generate

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "bench" / "results"


# ------------------------------------------------------------------ policy hooks
def _do_nothing(brain) -> None:
    brain.mode = "advisory"            # proposes only; nobody approves -> no action at all


def _no_trust_gate(brain) -> None:
    """Ablation: the gate still checks safety, but trust verdicts are ignored."""
    from energy_brain.gate import GateResult
    orig = brain._gate

    def gate(c, t, now):
        g = orig(c, t, now)
        if not g.passed and g.rule in ("trust", "trust_unavailable"):
            return GateResult(True, "trust(ablated)", "trust check removed for ablation", trust=g.trust)
        return g
    brain._gate = gate


# name -> (config overrides, brain hook, description)
POLICIES: dict[str, tuple[dict, Optional[Callable], str]] = {
    "full":             ({}, None, "Energy Brain v0.7 (all mechanisms)"),
    "reactive":         ({"forecast": {"mode": "reactive"}}, None, "acts only on measured overload"),
    "none":             ({}, _do_nothing, "never switches (lower bound)"),
    "v06":              ({"forecast": {"model": "trend"}, "consistency": {"enabled": False},
                          "action": {"planner": False, "alert_min_p": 0.0}}, None,
                         "v0.6 decision logic: fixed-window trend, single moves, no power-balance check"),
    # ---- ablations: one mechanism removed each
    "no_trust_gate":    ({}, _no_trust_gate, "trust verdicts ignored by the gate"),
    "no_suspect_hold":  ({"ingest": {"max_step_frac": 1e9}}, None, "implausible jumps accepted at once"),
    "no_persistence":   ({"forecast": {"risk_persistence": 1}}, None, "act on a single risky sample"),
    "no_hysteresis":    ({"forecast": {"clear_ratio": 1.0}}, None, "risk clears right at capacity"),
    "no_band":          ({"forecast": {"band_sigma": 0.0}}, None, "point forecast, no prediction interval"),
    "no_switch_cost":   ({"action": {"switching_cost": 0.0}}, None, "switching considered free"),
    "no_priority":      ({"action": {"priority_weight": 1.0, "priority_headroom": 1.0}}, None,
                         "hospital/priority zones treated like any other"),
    "no_restore":       ({"action": {"restore_enabled": False}}, None, "switches never put back"),
    "no_adaptive":      ({"forecast": {"model": "trend"}}, None, "fixed-window trend forecaster (v0.6)"),
    "no_planner":       ({"action": {"planner": False}}, None, "single moves only (no multi-zone plans)"),
    "no_physics":       ({"consistency": {"enabled": False}}, None, "no power-balance check"),
}
SETS = {
    "main": ["full", "v06", "reactive", "none"],
    "ablation": ["full", "no_adaptive", "no_planner", "no_physics", "no_trust_gate", "no_suspect_hold",
                 "no_persistence", "no_hysteresis", "no_band", "no_switch_cost", "no_priority", "no_restore"],
}


def _one(job: tuple[str, str, int, str]) -> dict:
    from scenarios.runner import run_scenario
    policy, family, seed, city = job
    overrides, hook, _ = POLICIES[policy]
    sc = generate(seed, family, city=city)
    sc.overrides = overrides
    tmp = Path(tempfile.mkdtemp(prefix="bench-"))
    try:
        t0 = time.perf_counter()
        res = run_scenario(sc, log_dir=tmp, seed=seed, brain_hook=hook)
        wall = time.perf_counter() - t0
    except Exception as e:  # a crash is a result too
        return {"policy": policy, "family": family, "seed": seed, "city": city, "error": repr(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    from .oracle import evaluate as oracle
    return {"policy": policy, "family": family, "seed": seed, "city": city, "wall_s": round(wall, 2),
            "steps": [s.__dict__ for s in sc.steps], **oracle(sc), **res["metrics"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="main", choices=list(SETS) + ["custom"])
    ap.add_argument("--policies", help="comma list (overrides --set)")
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--cities", default="city_a.yaml,city_c.yaml")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    ap.add_argument("--out", help="output name (default: the set name)")
    a = ap.parse_args()
    policies = a.policies.split(",") if a.policies else SETS[a.set]
    families = a.families.split(",")
    cities = a.cities.split(",")
    jobs = [(p, f, s, c) for s in range(a.seed0, a.seed0 + a.seeds) for c in cities for f in families for p in policies]
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{a.out or (a.set if not a.policies else 'custom')}.jsonl"
    print(f"{len(jobs)} runs ({len(policies)} policies x {len(families)} families x {len(cities)} cities x "
          f"{a.seeds} seeds) -> {out}")
    t0 = time.time()
    with out.open("w") as fh, mp.Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(_one, jobs), 1):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

"""Clairvoyant oracle: the best any switching policy could have done.

It knows the true (noiseless) demand at every second, ignores sensing, trust,
cooldowns and verification, and may re-configure the tie switches freely every
second (jammed switches excluded, no load shedding). For each second it picks the
tie configuration with the least weighted overload. This gives two references:

  preventable                  every second had a configuration with no overload
  oracle_energy_over_cap_kwh   the overload energy that remains even for the oracle

A policy is judged on preventable incidents, and on how close it gets to the
oracle's floor on the others.
"""
from __future__ import annotations

import itertools
import math
from pathlib import Path

import yaml

from scenarios.runner import Scenario

ROOT = Path(__file__).resolve().parents[1]


def _demand(z: dict, ramps: list[dict], t: float) -> float:
    d = z["base_kw"] + z.get("amplitude_kw", 0) * math.sin(2 * math.pi * t / z.get("period_s", 120))
    for r in ramps:
        if r["zone_id"] != z["zone_id"]:
            continue
        dt = t - r["at"]
        if 0 <= dt <= r["rise_s"] + r["hold_s"]:
            d += r["add_kw"] * (min(1.0, dt / r["rise_s"]) if r["rise_s"] > 0 else 1.0)
    return max(0.0, d)


def evaluate(sc: Scenario, dt: float = 1.0) -> dict:
    spec = yaml.safe_load((ROOT / "mocks/cities" / sc.city).read_text())
    zones = {z["zone_id"]: z for z in spec["zones"]}
    cap = {zid: z.get("capacity_kw") for zid, z in zones.items()}
    stuck = {s.args["switch_id"] for s in sc.steps
             if s.action == "stuck" and (s.args.get("stuck") or s.args.get("phantom"))}
    ties = [s for s in spec.get("switches", []) if s["kind"] == "tie" and s["switch_id"] not in stuck]
    ramps = [{**s.args, "at": s.at} for s in sc.steps if s.action == "ramp"]
    configs = [c for n in range(len(ties) + 1) for c in itertools.combinations(ties, n)]
    over_kwh, bad_s, t = 0.0, 0.0, 0.0
    while t < sc.duration_s:
        d = {zid: _demand(z, ramps, t) for zid, z in zones.items()}
        best = math.inf
        for cfg in configs:
            load = dict(d)
            for s in cfg:
                m = s["transfer_fraction"] * d[s["from_zone"]]
                load[s["from_zone"]] -= m
                load[s["to_zone"]] += m
            over = sum(max(0.0, load[z] - c) for z, c in cap.items() if c)
            best = min(best, over)
            if best == 0:
                break
        over_kwh += best * dt / 3600
        bad_s += dt * (best > 0)
        t += dt
    return {"preventable": bad_s == 0, "oracle_energy_over_cap_kwh": round(over_kwh, 3),
            "oracle_overload_s": bad_s}

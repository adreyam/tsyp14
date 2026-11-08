"""Randomized scenario generator for the statistical benchmark.

Every scenario is drawn from a seeded RNG, so a benchmark run is reproducible:
same seed list -> same scenarios -> same numbers. Families cover the situations
the Test Book checks one at a time, now with random zones, sizes, timings and
noise:

  calm       no incident (measures false alarms and needless switching)
  ramp       one zone ramps to 95-135% of capacity (rise 15-90 s)
  step       one zone jumps almost instantly (rise 1-4 s) to 105-130%
  double     two zones surge, staggered by 0-40 s (shared spare capacity)
  stuck      a ramp while a random tie switch is jammed
  spoof      a ramp in one zone while another zone's sensor is spoofed high;
             the Guardian flags the spoof 1-6 s later
  drift      stealthy manipulation the Guardian does NOT catch: a zone sensor drifts slowly,
             either masking a real surge (under-reads) or faking one (over-reads)
  phantom    a ramp while a random tie's status is spoofed (reports moving, never moves)
"""
from __future__ import annotations

import random
from pathlib import Path

import yaml

from scenarios.runner import Scenario, Step

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("calm", "ramp", "step", "double", "stuck", "spoof", "drift", "phantom")


def _city(city: str) -> dict:
    return yaml.safe_load((ROOT / "mocks/cities" / city).read_text())


def _surge(rng: random.Random, zone: dict, at: float, lo: float, hi: float, rise: tuple[float, float]) -> Step:
    target = rng.uniform(lo, hi) * zone["capacity_kw"]
    add = max(30.0, target - zone["base_kw"])
    return Step(round(at, 1), "ramp", {"zone_id": zone["zone_id"], "add_kw": round(add),
                                       "rise_s": round(rng.uniform(*rise), 1), "hold_s": round(rng.uniform(40, 160))},
                f"surge to ~{100 * target / zone['capacity_kw']:.0f}% of capacity")


def generate(seed: int, family: str, city: str = "city_a.yaml", duration_s: float = 220) -> Scenario:
    rng = random.Random(f"{family}-{seed}")
    spec = _city(city)
    zones = [z for z in spec["zones"] if z.get("capacity_kw")]
    ties = [s["switch_id"] for s in spec.get("switches", []) if s["kind"] == "tie"]
    steps: list[Step] = []
    t0 = rng.uniform(15, 50)
    if family == "calm":
        pass
    elif family == "ramp":
        steps.append(_surge(rng, rng.choice(zones), t0, 0.95, 1.35, (15, 90)))
    elif family == "step":
        steps.append(_surge(rng, rng.choice(zones), t0, 1.05, 1.30, (1, 4)))
    elif family == "double":
        a, b = rng.sample(zones, 2)
        steps += [_surge(rng, a, t0, 1.0, 1.25, (20, 80)), _surge(rng, b, t0 + rng.uniform(0, 40), 1.0, 1.25, (20, 80))]
    elif family == "stuck":
        steps.append(Step(3, "stuck", {"switch_id": rng.choice(ties), "stuck": True}, "jammed tie"))
        steps.append(_surge(rng, rng.choice(zones), t0, 1.0, 1.3, (20, 80)))
    elif family == "spoof":
        real, fake = rng.sample(zones, 2)
        steps.append(_surge(rng, real, t0, 1.0, 1.3, (20, 80)))
        ts = rng.uniform(10, 80)
        sensor = f"PL-{fake['zone_id']}"
        steps.append(Step(round(ts, 1), "spoof", {"sensor_id": sensor, "value_kw": round(1.3 * fake["capacity_kw"]),
                                                  "duration_s": 40}, "spoofed reading"))
        steps.append(Step(round(ts + rng.uniform(1, 6), 1), "guardian_override",
                          {"subject_id": sensor, "verdict": "untrusted", "reason": "spoofed"}, "Guardian flags it"))
        steps.append(Step(round(ts + 45, 1), "guardian_override", {"subject_id": sensor, "verdict": "trusted"}))
    elif family == "drift":
        z = rng.choice(zones)
        sensor = f"PL-{z['zone_id']}"
        if rng.random() < 0.5:     # mask a real surge
            surge = _surge(rng, z, t0, 1.05, 1.3, (30, 90))
            rate = -surge.args["add_kw"] / surge.args["rise_s"]
            steps += [surge, Step(surge.at, "drift", {"sensor_id": sensor, "rate_kw_s": round(rate, 2),
                                                      "duration_s": surge.args["rise_s"]}, "drift masks the surge")]
        else:                      # fake a surge
            target = rng.uniform(1.05, 1.3) * z["capacity_kw"] - z["base_kw"]
            dur = rng.uniform(40, 90)
            steps.append(Step(round(t0, 1), "drift", {"sensor_id": sensor, "rate_kw_s": round(target / dur, 2),
                                                      "duration_s": round(dur)}, "drift fakes a surge"))
    elif family == "phantom":
        steps.append(Step(3, "stuck", {"switch_id": rng.choice(ties), "stuck": False, "phantom": True},
                          "tie status spoofed"))
        steps.append(_surge(rng, rng.choice(zones), t0, 1.0, 1.3, (20, 80)))
    else:
        raise ValueError(family)
    return Scenario(f"B-{family}-{seed}", f"bench {family} #{seed}", "randomized benchmark scenario",
                    city=city, duration_s=duration_s, steps=steps)

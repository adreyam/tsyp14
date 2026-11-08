"""Turn benchmark results into tables (markdown + JSON summary).

  python -m bench.report                     # reads bench/results/main.jsonl and ablation.jsonl
Writes bench/results/RESULTS.md and bench/results/summary.json.
"""
from __future__ import annotations

import json
import random
import statistics as stats
from collections import defaultdict
from pathlib import Path

from .run import POLICIES, RESULTS

INCIDENT_FAMILIES = ("ramp", "step", "double", "stuck", "spoof", "drift", "phantom")
FAKE = "drift fakes a surge"


def load(name: str) -> list[dict]:
    p = RESULTS / f"{name}.jsonl"
    return [json.loads(line) for line in p.open()] if p.exists() else []


def ovl(r: dict) -> float:
    return sum(r["overload_seconds"].values())


def is_fake(r: dict) -> bool:
    """Runs with no real incident: calm days and drifts that only FAKE a surge."""
    return r["family"] == "calm" or any(s.get("note") == FAKE for s in r["steps"])


def key(r: dict) -> tuple:
    return (r["city"], r["family"], r["seed"])


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))] if xs else float("nan")


def boot_ci(diffs: list[float], n: int = 4000, seed: int = 1) -> tuple[float, float]:
    """95% percentile bootstrap CI of the mean of paired differences."""
    if not diffs:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    ms = sorted(mean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def metrics(rows: list[dict], none_by_key: dict) -> dict:
    inc = [r for r in rows if not is_fake(r)]
    prev = [r for r in inc if r["preventable"]]
    nonprev = [r for r in inc if not r["preventable"]]
    quiet = [r for r in rows if is_fake(r)]
    fake = [r for r in rows if r["family"] == "drift" and is_fake(r)]
    # an action is unnecessary if the do-nothing run of the same scenario had no true overload
    unnecessary = [len(r["commands"]) for r in rows if key(r) in none_by_key and ovl(none_by_key[key(r)]) == 0]
    return {
        "runs": len(rows),
        "incidents": len(inc),
        "preventable": len(prev),
        "prevented_pct": 100 * mean(ovl(r) == 0 for r in prev) if prev else float("nan"),
        "overload_s_mean": mean(ovl(r) for r in inc),
        "overload_s_p95": pct([ovl(r) for r in inc], 0.95),
        "energy_over_cap_kwh": mean(r["energy_over_capacity_kwh"] for r in inc),
        "gap_to_oracle_kwh": mean(r["energy_over_capacity_kwh"] - r["oracle_energy_over_cap_kwh"] for r in inc),
        "unpreventable_gap_kwh": mean(r["energy_over_capacity_kwh"] - r["oracle_energy_over_cap_kwh"] for r in nonprev),
        "ens_kwh": mean(r["energy_not_served_kwh"] for r in rows),
        "cmi": mean(r["customer_minutes_interrupted"] for r in rows),
        "runs_with_shedding_pct": 100 * mean(r["customer_minutes_interrupted"] > 0 for r in rows),
        "switch_ops": mean(r["switch_operations"] for r in rows),
        "unnecessary_ops": mean(unnecessary),
        "manipulated_pct": 100 * mean(bool(r["commands"]) for r in fake) if fake else float("nan"),
        "false_alarms_quiet": mean(r["risk_alarms"] for r in quiet if r["family"] == "calm"),
        "operator_alerts": mean(r["operator_alerts"] for r in rows),
        "mae_kw": mean(r["forecast_mae_kw"] for r in rows if r.get("forecast_mae_kw") is not None),
        "coverage_pct": 100 * mean(r["band_coverage"] for r in rows if r.get("band_coverage") is not None),
        "step_ms_p50": stats.median(r["step_ms_p50"] for r in rows),
        "step_ms_p95": pct([r["step_ms_p95"] for r in rows], 0.95),
    }


ROWS = [
    ("prevented_pct", "Preventable incidents fully prevented (%)", "{:.1f}", "up"),
    ("overload_s_mean", "True overload per incident, mean (s)", "{:.1f}", "down"),
    ("overload_s_p95", "True overload per incident, 95th pct (s)", "{:.0f}", "down"),
    ("energy_over_cap_kwh", "Energy above capacity per incident (kWh)", "{:.3f}", "down"),
    ("gap_to_oracle_kwh", "Gap to clairvoyant oracle (kWh)", "{:.3f}", "down"),
    ("ens_kwh", "Energy not served per run (kWh)", "{:.2f}", "down"),
    ("cmi", "Customer-minutes interrupted per run", "{:.0f}", "down"),
    ("runs_with_shedding_pct", "Runs where customers were cut off (%)", "{:.1f}", "down"),
    ("switch_ops", "Switch operations per run", "{:.2f}", "down"),
    ("unnecessary_ops", "Switch ops when doing nothing was safe", "{:.2f}", "down"),
    ("manipulated_pct", "Fake-surge attacks that triggered switching (%)", "{:.0f}", "down"),
    ("false_alarms_quiet", "Risk alarms on calm days", "{:.2f}", "down"),
    ("operator_alerts", "Operator alerts per run", "{:.2f}", "down"),
    ("mae_kw", "45 s forecast error, MAE (kW)", "{:.1f}", "down"),
    ("coverage_pct", "Upper band coverage (%, target 97.7)", "{:.1f}", "close"),
    ("step_ms_p50", "Decision cycle, median (ms)", "{:.1f}", "down"),
    ("step_ms_p95", "Decision cycle, 95th pct (ms)", "{:.1f}", "down"),
]


def table(cols: dict[str, dict], names: list[str]) -> str:
    head = "| Metric | " + " | ".join(names) + " |\n|---|" + "---:|" * len(names) + "\n"
    body = ""
    for k, label, fmt, _ in ROWS:
        body += f"| {label} | " + " | ".join(fmt.format(cols[n][k]) if cols[n][k] == cols[n][k] else "–"
                                           for n in names) + " |\n"
    return head + body


def by_family(rows: list[dict], policies: list[str]) -> str:
    fams = sorted({r["family"] for r in rows}, key=lambda f: (f != "calm", f))
    out = "| Family | runs | preventable | " + " | ".join(f"{p} overload s" for p in policies) + " | " + \
          " | ".join(f"{p} cust-min" for p in policies) + " |\n|---|---:|---:|" + "---:|" * (2 * len(policies)) + "\n"
    for f in fams:
        rs = {p: [r for r in rows if r["family"] == f and r["policy"] == p] for p in policies}
        ref = rs[policies[0]]
        n_prev = sum(r["preventable"] and not is_fake(r) for r in ref)
        out += f"| {f} | {len(ref)} | {n_prev} | " + " | ".join(f"{mean(ovl(r) for r in rs[p]):.1f}" for p in policies) \
               + " | " + " | ".join(f"{mean(r['customer_minutes_interrupted'] for r in rs[p]):.0f}" for p in policies) + " |\n"
    return out


def paired(rows, a: str, b: str, fn) -> tuple[float, float, float, int]:
    A = {key(r): r for r in rows if r["policy"] == a}
    B = {key(r): r for r in rows if r["policy"] == b}
    d = [fn(A[k]) - fn(B[k]) for k in A if k in B]
    lo, hi = boot_ci(d)
    return mean(d), lo, hi, len(d)


def main() -> None:
    out, summary = [], {}
    for name in ("main", "ablation"):
        rows = [r for r in load(name) if "error" not in r]
        errs = [r for r in load(name) if "error" in r]
        if not rows:
            continue
        pols = [p for p in POLICIES if any(r["policy"] == p for r in rows)]
        none = {key(r): r for r in load("main") if r.get("policy") == "none" and "error" not in r}
        cols = {p: metrics([r for r in rows if r["policy"] == p], none) for p in pols}
        summary[name] = cols
        seeds = len({r["seed"] for r in rows})
        cities = sorted({r["city"] for r in rows})
        out.append(f"## {'Main comparison' if name == 'main' else 'Ablation study'}\n")
        out.append(f"{len(rows)} runs ({len(pols)} policies x {len({r['family'] for r in rows})} scenario families x "
                   f"{len(cities)} cities ({', '.join(c.removesuffix('.yaml') for c in cities)}) x {seeds} seeds)"
                   + (f"; {len(errs)} runs crashed" if errs else "; no run crashed") + ".\n")
        out.append("Policies: " + "; ".join(f"**{p}** = {POLICIES[p][2]}" for p in pols) + ".\n")
        out.append(table(cols, pols))
        if name == "main":
            out.append("\n### By scenario family\n")
            out.append(by_family(rows, [p for p in ("full", "v06", "reactive", "none") if p in pols]))
            out.append("\n### Paired differences, v0.7 minus v0.6 (same scenarios, 95% bootstrap CI)\n")
            out.append("| Metric | mean difference | 95% CI | pairs |\n|---|---:|---|---:|\n")
            for label, fn in [("True overload per run (s)", ovl),
                              ("Energy above capacity (kWh)", lambda r: r["energy_over_capacity_kwh"]),
                              ("Customer-minutes interrupted", lambda r: r["customer_minutes_interrupted"]),
                              ("Switch operations", lambda r: r["switch_operations"]),
                              ("Operator alerts", lambda r: r["operator_alerts"]),
                              ("Forecast MAE (kW)", lambda r: r["forecast_mae_kw"] or 0.0)]:
                m, lo, hi, n = paired(rows, "full", "v06", fn)
                out.append(f"| {label} | {m:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {n} |\n")
                summary.setdefault("paired_full_minus_v06", {})[label] = [m, lo, hi, n]
            out.append("\n### Stealthy manipulation (drift family) and false flags\n")
            out.append("| Policy | drift attacks | detected | detection delay median / max (s) | masked real overloads "
                       "warned | fake surges that caused switching | customer-min cut by fake surges | power-balance "
                       "flags on non-attack runs |\n|---|---:|---:|---|---:|---:|---:|---:|\n")
            for p in ("full", "v06"):
                d = [r for r in rows if r["policy"] == p and r["family"] == "drift"]
                mask = [r for r in d if not is_fake(r)]
                fake = [r for r in d if is_fake(r)]
                det = sorted(r["consistency_flag_s"] - r["attack_start_s"] for r in d if r.get("consistency_flag_s") is not None)
                warned = [r for r in mask if ovl(r) > 0 and r.get("hidden_overload_alert_s") is not None]
                fp = [r for r in rows if r["policy"] == p and r["family"] != "drift" and r.get("consistency_flag_s") is not None]
                n_other = sum(r["policy"] == p and r["family"] != "drift" for r in rows)
                out.append(f"| {p} | {len(d)} | {len(det)} | " + (f"{stats.median(det):.1f} / {det[-1]:.1f}" if det else "–")
                           + f" | {len(warned)} of {sum(ovl(r) > 0 for r in mask)} | {sum(bool(r['commands']) for r in fake)} of "
                           f"{len(fake)} | {sum(r['customer_minutes_interrupted'] for r in fake):.0f} | {len(fp)} of {n_other} |\n")
                summary.setdefault("attacks", {})[p] = {"runs": len(d), "detected": len(det),
                    "delay_median": stats.median(det) if det else None, "delay_max": det[-1] if det else None,
                    "masked_overloads": sum(ovl(r) > 0 for r in mask), "masked_warned": len(warned),
                    "fake": len(fake), "fake_switched": sum(bool(r["commands"]) for r in fake),
                    "fake_cmi": sum(r["customer_minutes_interrupted"] for r in fake), "false_flags": len(fp),
                    "non_attack_runs": n_other}
        else:
            out.append("\n### Effect of removing each mechanism (difference vs full)\n")
            out.append("| Removed | overload s | cust-min | switch ops | needless ops | manipulated % | calm alarms |"
                       " coverage % |\n|---|---:|---:|---:|---:|---:|---:|---:|\n")
            f = cols["full"]
            for p in pols:
                if p == "full":
                    continue
                c = cols[p]
                d = lambda k: c[k] - f[k]  # noqa: E731
                out.append(f"| {p} ({POLICIES[p][2]}) | {d('overload_s_mean'):+.1f} | {d('cmi'):+.0f} | "
                           f"{d('switch_ops'):+.2f} | {d('unnecessary_ops'):+.2f} | {d('manipulated_pct'):+.0f} | "
                           f"{d('false_alarms_quiet'):+.2f} | {d('coverage_pct'):+.1f} |\n")
        out.append("\n")
    (RESULTS / "RESULTS.md").write_text("# Energy Brain benchmark results\n\n"
                                        "Generated by `python -m bench.report`. Method: see bench/README.md.\n\n"
                                        + "".join(out))
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print((RESULTS / "RESULTS.md").read_text())


if __name__ == "__main__":
    main()

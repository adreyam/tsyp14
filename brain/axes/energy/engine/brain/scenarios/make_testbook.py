"""Regenerate TESTBOOK.md from the latest run logs:  python -m scenarios.run all && python -m scenarios.make_testbook"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

from .testbook import TESTBOOK

ROOT = Path(__file__).resolve().parents[1]
TWIN = {"E1": "Normal daily load variation", "E2": "Z1 demand +280 kW over 60 s", "E2B": "Same as E2",
        "E3": "Streams fake 640 kW for PL-Z1 for 40 s, keeps true load internally", "E4": "Z4 demand +260 kW over 60 s",
        "E5": "S12 jammed (accepts commands, never moves); Z1 surge", "E6": "Z3 (hospital) +220 kW and Z1 +280 kW",
        "E7A": "Unknown city: advertises IDs only; north +450 kW", "E7B": "Operator toggles T-NS 4x, then north +450 kW",
        "E8": "Z1 surge", "E9": "S42 jammed; Z4 surge", "E10": "Z1 +280 kW for ~100 s, then back to normal",
        "E11": "S42 jammed; Z4 +300 kW (~130%) until t=140 s", "E12": "Operator toggles T-NS 6x; north +450 kW at t=100 s",
        "E13": "City C (meshed): D +200 kW at t=15 s, A +240 kW at t=80 s", "E13B": "Same as E13",
        "E14": "Z1 +280 kW; PL-Z1 under-reads by a growing offset that cancels it; SUB-N reports the real total",
        "E14B": "Same as E14", "E15": "No incident; PL-Z1 over-reads by +3 kW/s for 70 s; SUB-N reports the real total",
        "E15B": "Same as E15", "E16": "S12 status spoofed: reports 'closed' after the command, breaker never moves; Z1 surge",
        "E17": "Z1 demand +250 kW in 1 s at t=30 s; SUB-N measures it too", "E17B": "Same as E17",
        "E18": "Streams fake 650 kW for PL-Z4 for 40 s; SUB-S reports the real total", "E18B": "Same as E18"}
GUARD = {"E3": "Marks PL-Z1 untrusted 3 s after the spoof starts", "E5": "Receives the Brain's actuator_anomaly report",
         "E6": "Silent from t=10 s (HTTP 503)", "E8": "Marks S12 untrusted + quarantined",
         "E9": "All trusted (flood domain sends a priority hint for Z4)", "E11": "Receives the actuator_anomaly report for S42",
         "E12": "Distrusts PL-south from t=17 to 35 s",
         "E14": "Keeps trusting PL-Z1 (the drift is too slow for per-sensor checks); receives the Brain's consistency report",
         "E14B": "Keeps trusting PL-Z1", "E15": "Keeps trusting PL-Z1; receives the Brain's consistency report",
         "E15B": "Keeps trusting PL-Z1", "E16": "Receives the Brain's actuator_anomaly report for S12",
         "E18": "Marks PL-Z4 untrusted only 9 s after the spoof starts", "E18B": "Same as E18"}


def main() -> None:
    res = {}
    for f in glob.glob(str(ROOT.parent / "logs" / "*.log")):
        for line in open(f):
            d = json.loads(line)
            if d.get("type") == "scenario_result":
                res[d["scenario"]] = (d, os.path.basename(f))
    out = ["# Energy Test Book (City Brain, energy domain)\n",
           "Every scenario: one trigger, one log in `logs/`, automatic pass/fail. `python -m scenarios.run <ID>` (simulated "
           "clock), `--live` (real HTTP, real time) or `--twin mcp`. The whole book also runs in `pytest` on two noise seeds, "
           "over MCP, and in the simulator; the simulation page replays every run.\n",
           "Observed values are from the committed run logs (seed 7). *Forecast error* = mean absolute error of the 45 s "
           "forecast against the true load, leaving out windows that contain a switching action.\n",
           "| ID | Name | Result | Commands | Overload s | Peak load % | Forecast error |", "|---|---|---|---|---|---|---|"]
    for sid, sc in TESTBOOK.items():
        d, f = res[sid]
        m = d["metrics"]
        ov = {z: s for z, s in m["overload_seconds"].items() if s}
        out.append(f"| {sid} | {sc.name} | {'PASS' if d['passed'] else 'FAIL'} | {', '.join(m['commands']) or '—'} | "
                   f"{ov or 0} | {m['peak_load_pct']} | {m.get('forecast_mae_kw', '—')} kW |")
    e2 = [c for c in res["E2"][0]["checks"] if "acted while" in c["expect"]][0]["observed"]
    b = res["E2B"][0]["metrics"]
    out.append(f"\n**Prediction vs reaction (E2 vs E2B):** same surge. Predictive Brain: 0 s of overload ({e2}). "
               f"Reactive baseline: {b['overload_seconds']['Z1']} s of overload, peak {b['peak_load_pct']['Z1']}%. "
               f"Calm day (E1): {res['E1'][0]['metrics']['risk_alarms']} false alarms.\n")
    m13, m13b = res["E13"][0]["metrics"], res["E13B"][0]["metrics"]
    out.append(f"**Plans vs single moves (E13 vs E13B):** same two surges in the meshed city. With the planner: "
               f"{', '.join(m13['commands'])}; nobody cut off. Single moves only: {', '.join(m13b['commands'])}; "
               f"{m13b['customer_minutes_interrupted']:.0f} customer-minutes interrupted.\n")
    out.append("**Physics vs stealthy manipulation (E14/E15 vs E14B/E15B):** a slow drift that no per-sensor check "
               "catches. With the power-balance check the Brain flags it within seconds, warns of the hidden overload "
               "(E14) and refuses to be manipulated into switching (E15). Without it the Brain is blind (E14B) or "
               f"switches for nothing (E15B: {', '.join(res['E15B'][0]['metrics']['commands'])}).\n")
    for sid, sc in TESTBOOK.items():
        d, f = res[sid]
        layers = "Twin, City Brain" + (", Guardian" if sid in GUARD else "") + (", other Brain domain" if sid == "E9" else "")
        out += [f"## {sid} — {sc.name}\n", "| Field | Value |", "|---|---|", f"| ID | {sid} |", f"| Name | {sc.name} |",
                f"| Layers involved | {layers} |", f"| Description | {sc.description} |",
                f"| Trigger | `python -m scenarios.run {sid.lower()}` |", f"| Expected: Twin | {TWIN[sid]} |",
                f"| Expected: Guardian | {GUARD.get(sid, 'All trusted')} |",
                "| Expected: Brain | " + "<br>".join(c["expect"] for c in d["checks"]) + " |",
                "| Observed | " + "<br>".join(f"{'✅' if c['ok'] else '❌'} {c['observed']}".replace("|", "/")
                                          for c in d["checks"]) + " |",
                f"| How to verify | Simulation page `#{sid}`; log `logs/{f}` (last line = `scenario_result`) |\n"]
    (ROOT / "TESTBOOK.md").write_text("\n".join(out))
    print("\n".join(out[3:20]))


if __name__ == "__main__":
    main()

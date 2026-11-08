"""Replay one benchmark scenario and print its key events: python -m bench.debug double 2 [policy]"""
import sys, tempfile
from pathlib import Path
from scenarios.runner import run_scenario
from .scenarios import generate
from .run import POLICIES

KINDS = {"risk_detected", "risk_cleared", "candidates_evaluated", "decision", "gate_blocked", "verification",
         "operator_alert", "restoration", "reading_suspect", "plan"}

def main():
    fam, seed = sys.argv[1], int(sys.argv[2])
    pol = sys.argv[3] if len(sys.argv) > 3 else "full"
    sc = generate(seed, fam); ov, hook, _ = POLICIES[pol]; sc.overrides = ov
    for s in sc.steps: print("STEP", s.at, s.action, s.args, s.note)
    res = run_scenario(sc, log_dir=Path(tempfile.mkdtemp()), seed=seed, brain_hook=hook)
    import json
    for line in open(res["log"]):
        e = json.loads(line)
        if e.get("kind") in KINDS or e.get("type") == "scenario_result":
            print(e.get("t", ""), e.get("kind", e.get("type")), e.get("summary", ""))
            if e.get("kind") == "candidates_evaluated":
                for c in e["payload"].get("rejected", []): print("      rej", c["action"], c["why_not"])
                for c in e["payload"].get("ranked", []): print("      ok ", c["action"], c["score"])
    print(json.dumps(res["metrics"]))
main()

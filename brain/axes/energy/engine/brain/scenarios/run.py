"""Trigger: python -m scenarios.run <E1|E2|...|all> [--live] [--echo]"""
from __future__ import annotations

import argparse
import sys

from .runner import run_scenario
from .testbook import TESTBOOK


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", help=f"one of {', '.join(TESTBOOK)} or 'all'")
    ap.add_argument("--live", action="store_true", help="real HTTP servers and real time (demo mode)")
    ap.add_argument("--seed", type=int, default=7, help="mock noise seed")
    ap.add_argument("--twin", choices=["rest", "mcp"], default="rest", help="protocol the Brain uses to reach the Twin")
    ap.add_argument("--echo", action="store_true", help="print Brain events as they happen")
    a = ap.parse_args()
    ids = list(TESTBOOK) if a.scenario.lower() == "all" else [a.scenario.upper()]
    failed = 0
    for sid in ids:
        if sid not in TESTBOOK:
            sys.exit(f"unknown scenario '{sid}'. Available: {', '.join(TESTBOOK)}")
        res = run_scenario(TESTBOOK[sid], live=a.live, echo=a.echo, seed=a.seed, twin_protocol=a.twin)
        failed += not res["passed"]
        print(f"\n{sid}  {res['name']}  ->  {'PASS' if res['passed'] else 'FAIL'}")
        for c in res["checks"]:
            print(f"   [{'x' if c['ok'] else ' '}] {c['expect']}\n       {c['observed']}")
        m = res["metrics"]
        print(f"   commands={m['commands']} verifications={m['verifications']} level={m['knowledge_level']}")
        print(f"   overload_s={m['overload_seconds']} peak%={m['peak_load_pct']}")
        print(f"   log: {res['log']}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

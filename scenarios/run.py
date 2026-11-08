"""
Run a Test Book scenario with one command (spec R2). Each run writes logs/<ID>-<UTC>.jsonl.

    python scenarios/run.py EM02            the Brain alone with its mocks (Twin + Guardian stand-ins), in this process
    python scenarios/run.py --all           every scenario, one log each, a PASS/FAIL table at the end
    python scenarios/run.py --list          the scenarios
    python scenarios/run.py EM03 --server   play it on the running Brain (python brain/run_brain.py), e.g. so the
                                            control center shows it; with source: live, Twin commands go to the real city
Exit code 0 when every scenario run passes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "brain", "src"), os.path.join(ROOT, "brain")]

from citybrain.brain import Brain  # noqa: E402
from citybrain.config import ConfigError, load_config  # noqa: E402
from citybrain.scenario import ScenarioRunner, load_scenarios  # noqa: E402


def run_local(spec: dict, cfg_path) -> dict:
    cfg = load_config(cfg_path, {"source": "mock"})
    cfg.twin.poll_s, cfg.twin.incidents_poll_s = 0.25, 0.5
    if cfg.guardian.mode == "off":
        cfg.guardian.mode = "mock"
    brain = Brain(cfg)
    try:
        for _ in range(8):            # let the mock city settle (first readings, Guardian baselines)
            brain.step()
            time.sleep(0.05)
        return ScenarioRunner(brain, spec, drive=True).run(timeout_s=float(spec.get("duration_s", 30)) / float(spec.get("speed", 1)) + 30)
    finally:
        brain.stop()
        brain.audit.close()


def run_server(spec: dict, cfg_path) -> dict:
    cfg = load_config(cfg_path)
    base = f"http://{cfg.brain.host}:{cfg.brain.port}"
    token = cfg.brain.token() or ""
    req = urllib.request.Request(f"{base}/v1/scenarios/{spec['id']}/run", data=b"{}", method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            started = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"id": spec["id"], "verdict": "FAIL", "error": e.read().decode(), "checks": []}
    except OSError as e:
        return {"id": spec["id"], "verdict": "FAIL", "error": f"the Brain is not running at {base} ({e})", "checks": []}
    print(f"{spec['id']} started on the running Brain, log {started.get('log')}")
    deadline = time.time() + max(float(spec.get("duration_s", 30)) / float(spec.get("speed", 1)),
                                 float(spec.get("live_duration_s", spec.get("duration_s", 30)))) + 60
    while time.time() < deadline:
        time.sleep(1)
        with urllib.request.urlopen(f"{base}/v1/status", timeout=5) as r:
            st = json.loads(r.read()).get("scenario") or {}
        if st.get("id") == spec["id"] and st.get("state") not in ("starting", "running"):
            with urllib.request.urlopen(f"{base}/v1/timeline?limit=2000", timeout=5) as r:
                items = json.loads(r.read())["items"]
            done = [i for i in items if i["kind"] == "system" and i["summary"].startswith(f"Scenario {spec['id']} finished")]
            return done[-1]["payload"] if done else {"id": spec["id"], "verdict": st.get("state", "?").upper(), "checks": []}
    return {"id": spec["id"], "verdict": "FAIL", "error": "timed out", "checks": []}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--server", action="store_true", help="play on the running Brain (live or mock source)")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    sc = load_scenarios()
    if a.list or (not a.ids and not a.all):
        for s in sc.values():
            print(f"{s['id']:6} {s.get('interaction', ''):46} {s.get('name')}  [{', '.join(s.get('modes', ['mock']))}]")
        return 0
    ids = list(sc) if a.all else [i.upper() for i in a.ids]
    unknown = [i for i in ids if i not in sc]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)} (see --list)", file=sys.stderr)
        return 2
    results = []
    for sid in ids:
        try:
            res = run_server(sc[sid], a.config) if a.server else run_local(sc[sid], a.config)
        except ConfigError as e:
            print(e, file=sys.stderr)
            return 2
        results.append(res)
        print(f"\n{sid} {sc[sid]['name']}: {res['verdict']}")
        for c in res.get("checks", []):
            print(f"   [{'ok' if c['pass'] else 'FAIL'}] {json.dumps(c['check'], ensure_ascii=False)} -> {c['observed'][:150]}")
        if res.get("error"):
            print(f"   {res['error']}")
        if res.get("log"):
            print(f"   log: {os.path.relpath(res['log'], ROOT)}")
    if len(results) > 1:
        print("\n" + "  ".join(f"{r['id']}:{r['verdict']}" for r in results))
    return 0 if all(r["verdict"] in ("PASS", "SKIPPED") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

"""A scripted City Brain orchestrator supervising the Energy Brain agent over MCP.

It shows the calls a real orchestrator (LLM-based or not) makes:
status -> switch to advisory -> follow events -> what-if + approve proposals -> explain.

  python -m sim.server --speed 4 &             # live simulation with the agent at /agent/mcp
  python examples/orchestrator_demo.py --scenario E2

Against a real deployment: --url http://<host>:8300/mcp --key $ORCHESTRATOR_API_KEY --no-sim
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src")]
from energy_brain.adapters.mcp import McpSession  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8200/agent/mcp")
    ap.add_argument("--key", default=None)
    ap.add_argument("--scenario", default="E2")
    ap.add_argument("--seconds", type=float, default=60, help="how long to supervise (wall clock)")
    ap.add_argument("--no-sim", action="store_true", help="do not (re)start a simulation scenario")
    a = ap.parse_args()

    agent = McpSession("energy-agent", a.url, {"X-API-Key": a.key} if a.key else {}, 10.0)
    call = lambda tool, **kw: agent.call(tool, kw, tool)  # noqa: E731

    if not a.no_sim:
        base = a.url.split("/agent/")[0]
        httpx.post(f"{base}/api/scenario/{a.scenario}").raise_for_status()
        print(f"[orchestrator] started scenario {a.scenario} on the simulation")

    card = call("energy_describe_agent")
    print(f"[orchestrator] connected to '{card['name']}' v{card['version']}: {card['role']}")
    print(f"[orchestrator] status: {call('energy_get_status')['summary']}")
    print(f"[orchestrator] {call('energy_set_mode', mode='advisory', reason='demo: every switch needs my approval')['summary']}")

    cursor = call("energy_get_events", since=0, limit=1)["next_cursor"]
    end = time.monotonic() + a.seconds
    approved = 0
    while time.monotonic() < end:
        page = call("energy_get_events", since=cursor,
                    kinds=["risk_detected", "proposal", "decision", "gate_blocked", "verification", "operator_alert"])
        cursor = page["next_cursor"]
        for e in page["events"]:
            print(f"  [agent event] {e['kind']}: {e['summary'][:150]}")
        for p in call("energy_list_proposals")["proposals"]:
            sw = p["action"].split()[-1]
            check = call("energy_what_if", switch_id=sw)
            print(f"[orchestrator] proposal {p['proposal_id']}: {p['action']} for {p['for_zones']} ({p['reason']})")
            print(f"[orchestrator]   what-if says: {check['summary']}")
            if check.get("gate", {}) and check["gate"].get("would_pass"):
                r = call("energy_approve_proposal", proposal_id=p["proposal_id"])
                approved += r["executed"]
                print(f"[orchestrator]   APPROVE -> {r['summary']}")
        time.sleep(1.0)

    print(f"[orchestrator] explanation: {call('energy_explain')['summary']}")
    print(f"[orchestrator] final status: {call('energy_get_status')['summary']}")
    print(f"[orchestrator] {call('energy_set_mode', mode='autonomous', reason='demo over')['summary']}  "
          f"({approved} approval(s) given)")


if __name__ == "__main__":
    main()

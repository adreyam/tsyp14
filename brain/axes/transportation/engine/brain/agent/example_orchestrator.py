"""Example: what an orchestrator does with the agent. No LLM needed - it only shows the call pattern.

    python -m agent.example_orchestrator --url http://127.0.0.1:8000
Runs a stress test of the signals: status -> policy mode -> traffic jam -> wait -> report.
"""
import argparse
import time

from agent.client import BrainAgent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default="dev-agent-key")
    ap.add_argument("--cars", type=int, default=300)
    a = ap.parse_args()
    brain = BrainAgent(a.url, a.key, caller="example-orchestrator")
    say = lambda r: print(("  ok  " if r["ok"] else " FAIL ") + r["summary"])    # noqa: E731
    say(brain.call("get_city_status"))
    say(brain.call("set_signal_mode", mode="policy"))
    say(brain.call("set_simulation_controls", speed=60, clock_day=1, clock_time="12:00", traffic_level=1.0))
    r = brain.call("start_traffic_jam", cars=a.cars)
    say(r)
    if not r["ok"]:
        return
    while True:
        time.sleep(5)
        j = brain.call("get_traffic_jam_status")["data"]
        if j and j["state"] == "done":
            break
    say(brain.call("get_traffic_jam_status"))


if __name__ == "__main__":
    main()

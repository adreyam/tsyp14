"""
Minimal orchestrator calling the City Brain agent over HTTP (standard library only).

    set ORCHESTRATOR_TOKEN=<token printed by the Brain, or the one you set>
    python examples/orchestrator_http.py --url http://127.0.0.1:8100

It discovers the tools, reads the city, asks why for anything urgent, runs a
what-if, and shows how to hand the tool list to an LLM that does tool calling.
"""
import argparse
import json
import os
import urllib.request


class CityBrain:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def card(self) -> dict:
        with urllib.request.urlopen(self.url + "/v1/agent/card", timeout=10) as r:
            return json.loads(r.read())

    def call(self, tool: str, **arguments) -> dict:
        req = urllib.request.Request(
            self.url + "/v1/agent/call", method="POST",
            data=json.dumps({"tool": tool, "arguments": arguments}).encode(),
            headers={"Authorization": f"Bearer {self.token}", "X-Caller": "example-orchestrator",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["result"]


def as_llm_tools(card: dict) -> list:
    """The agent card as tool definitions for an LLM with tool calling
    (name / description / input_schema). Execute each tool_use with CityBrain.call."""
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["inputSchema"]} for t in card["tools"]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8100")
    a = p.parse_args()
    brain = CityBrain(a.url, os.environ["ORCHESTRATOR_TOKEN"])

    card = brain.card()
    print(f"{card['name']} {card['version']}: {len(card['tools'])} tools")

    status = brain.call("brain_status")
    print("bins needing collection:", status["bins"]["need_collection"])
    print("air zones by level:", status["air"]["zones_by_level"])

    urgent = status["bins"]["need_collection"] + status["air"]["zones_by_level"]["high"] \
        + status["air"]["zones_by_level"]["critical"]
    for target in urgent[:3]:
        why = brain.call("explain", id=target, limit=1)
        print(f"why {target}:", (why["current"] or {}).get("reason"))

    what_if = brain.call("assess_air", pm25=30, o3=20, no2=30, season="cold")
    print("what-if PM2.5 30, O3 20, NO2 30 (cold):", what_if["risk"], what_if["level"])

    print("tools for an LLM:", [t["name"] for t in as_llm_tools(card)])


if __name__ == "__main__":
    main()

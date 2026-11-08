#!/usr/bin/env python3
"""Verify the local NPC planner works safely with no cloud credential."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen


PROJECT = Path(__file__).resolve().parents[1]
PORT = 18816


def request(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    with urlopen(Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=headers, method="POST" if body else "GET"), timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    environment = os.environ.copy()
    environment.pop("OPENROUTER_API_KEY", None)
    process = subprocess.Popen([sys.executable, str(PROJECT / "scripts" / "npc_ai_bridge.py"), "--port", str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        for _ in range(20):
            try:
                health = request("/health")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("NPC planner did not start")
        plan = request("/plan", {"npc_id": "test_npc", "role": "utilities technician", "current_state": {"intent": "perform_job"}, "current_side": "north", "available_destinations": ["north", "east", "south", "west"], "legal_intents": ["perform_job", "run_errand"], "legal_actions": ["power_read", "hydrant_test"]})
        if health.get("remote_enabled") or plan.get("source") != "local_no_key" or plan.get("intent") != "perform_job" or plan.get("destination_side") not in {"north", "east", "south", "west"} or plan.get("next_action_id") not in {"power_read", "hydrant_test"}:
            raise RuntimeError(f"unsafe local NPC plan: health={health} plan={plan}")
        print("NPC_PLANNER_TEST_OK local_fallback=validated remote_key=not_required")
    finally:
        process.terminate()
        process.wait(timeout=3.0)


if __name__ == "__main__":
    main()

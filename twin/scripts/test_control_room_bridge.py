#!/usr/bin/env python3
"""Verify the Godot control-room channel against a real bundled SUMO bridge."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import time


PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / "runtime" / "sumo" / "usr" / "bin" / "python3.14"
BRIDGE = PROJECT / "scripts" / "sumo_bridge.py"
STATE_PORT = 8894
CONTROL_PORT = 8895


def receive_state(receiver: socket.socket, deadline: float) -> dict:
    while time.monotonic() < deadline:
        try:
            payload, _address = receiver.recvfrom(65535)
        except TimeoutError:
            continue
        return json.loads(payload)
    raise RuntimeError("bridge did not publish state")


def main() -> int:
    process = subprocess.Popen(
        [str(PYTHON), str(BRIDGE), "--warmup", "0", "--rate", "15", "--port", str(STATE_PORT), "--control-port", str(CONTROL_PORT)],
        cwd=PROJECT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", STATE_PORT))
    receiver.settimeout(0.5)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receive_state(receiver, time.monotonic() + 6.0)
        sender.sendto(json.dumps({"command": "all_red"}).encode("utf-8"), ("127.0.0.1", CONTROL_PORT))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = receive_state(receiver, deadline)
            operator = state.get("operator", {})
            all_red = all(set(signal.get("approaches", {}).values()) == {"r"} for signal in state.get("signals", []))
            if operator.get("last_action") == "all 49 signals: all red" and len(state.get("signals", [])) == 49 and all_red:
                break
        else:
            raise RuntimeError("control command was not reflected in the authoritative SUMO state")

        sender.sendto(json.dumps({"command": "set_speed", "multiplier": 4}).encode("utf-8"), ("127.0.0.1", CONTROL_PORT))
        sender.sendto(json.dumps({"command": "set_weather", "weather": "rain", "traffic_factor": 0.78}).encode("utf-8"), ("127.0.0.1", CONTROL_PORT))
        speed_state = {}
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            speed_state = receive_state(receiver, deadline)
            operator = speed_state.get("operator", {})
            if operator.get("speed") == 4 and operator.get("weather") == "rain":
                break
        else:
            raise RuntimeError("speed/weather controls were not reflected in SUMO")
        start_sim = float(speed_state["time"])
        start_wall = time.monotonic()
        latest = speed_state
        while time.monotonic() - start_wall < 0.65:
            latest = receive_state(receiver, time.monotonic() + 1.0)
        sim_rate = (float(latest["time"]) - start_sim) / max(time.monotonic() - start_wall, 0.001)
        if sim_rate < 3.2:
            raise RuntimeError(f"4x mode advanced SUMO at only {sim_rate:.2f}x wall time")
        print(f"CONTROL_ROOM_BRIDGE_TEST_OK controllers=49 speed=4x measured={sim_rate:.2f}x weather=rain")
        return 0
    finally:
        receiver.close()
        sender.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

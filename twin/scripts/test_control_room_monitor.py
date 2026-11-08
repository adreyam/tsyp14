#!/usr/bin/env python3
"""Verify that the external panel receives the exact live SUMO packet mirror."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time


PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / "runtime" / "sumo" / "usr" / "bin" / "python3.14"
BRIDGE = PROJECT / "scripts" / "sumo_bridge.py"
CITY_PORT = 8894
MONITOR_PORT = 8896
CONTROL_PORT = 8895


def receive(receiver: socket.socket, deadline: float) -> dict:
    while time.monotonic() < deadline:
        try:
            packet, _address = receiver.recvfrom(65535)
        except TimeoutError:
            continue
        return json.loads(packet)
    raise RuntimeError("timed out waiting for SUMO state")


def main() -> int:
    city_receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    monitor_receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    city_receiver.bind(("127.0.0.1", CITY_PORT))
    monitor_receiver.bind(("127.0.0.1", MONITOR_PORT))
    city_receiver.settimeout(0.5)
    monitor_receiver.settimeout(0.5)
    ready_file = Path(tempfile.gettempdir()) / f"city_monitor_test_ready_{CITY_PORT}"
    ready_file.unlink(missing_ok=True)
    process = subprocess.Popen(
        [
            str(PYTHON), str(BRIDGE), "--warmup", "90", "--rate", "15",
            "--port", str(CITY_PORT), "--monitor-port", str(MONITOR_PORT),
            "--control-port", str(CONTROL_PORT), "--ready-file", str(ready_file),
        ],
        cwd=PROJECT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # The warm-up (90 simulated seconds) must not eat the receive window.
        ready_deadline = time.monotonic() + 60.0
        while not ready_file.exists() and process.poll() is None and time.monotonic() < ready_deadline:
            time.sleep(0.1)
        deadline = time.monotonic() + 6.0
        city_state = receive(city_receiver, deadline)
        monitor_state = receive(monitor_receiver, deadline)
        fields = ("source", "time", "simulated_vehicle_count", "displayed_vehicle_count", "stopped_vehicle_count", "turning_vehicle_count")
        if any(city_state.get(field) != monitor_state.get(field) for field in fields):
            raise RuntimeError("control-room monitor packet diverged from CityLife's SUMO packet")
        if len(city_state.get("signals", [])) != 49 or len(monitor_state.get("vehicles", [])) != monitor_state.get("simulated_vehicle_count") or len(monitor_state.get("vehicles", [])) < 90:
            raise RuntimeError("monitor packet omitted live city traffic or signal data")
        print(f"CONTROL_ROOM_MONITOR_TEST_OK mirrored_state=authoritative vehicles={len(monitor_state['vehicles'])} signals=49")
        return 0
    finally:
        city_receiver.close()
        monitor_receiver.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

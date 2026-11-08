#!/usr/bin/env python3
"""Ensure stopped traffic is explainable rather than silently arbitrary."""

from collections import Counter
import sys

import sumo_bridge


def main() -> None:
    sumo_bridge.prepare_environment()
    import traci  # type: ignore

    command = [
        str(sumo_bridge.SUMO_BINARY), "-c", str(sumo_bridge.CONFIG), "--step-length", "0.1",
        "--device.rerouting.probability", "0.12", "--device.rerouting.period", "45", "--seed", "90210",
    ]
    traci.start(command, verbose=False)
    traffic = sumo_bridge.TrafficState(traci)
    reasons: Counter[str] = Counter()
    try:
        for _ in range(1200):
            traci.simulationStep()
            state = sumo_bridge.make_state(traffic, None)
            reasons.update(vehicle["stop_reason"] for vehicle in state["vehicles"] if vehicle["stopped"])
    finally:
        traci.close(False)
    explained = reasons["traffic_light"] + reasons["downstream_signal"] + reasons["queue"] + reasons["yielding_turn"] + reasons["entry_gate"]
    if explained == 0 or reasons["unexplained"] > max(3, explained // 100):
        raise SystemExit("SUMO_STOP_REASON_TEST_FAILED " + repr(dict(reasons)))
    print("SUMO_STOP_REASON_TEST_OK " + " ".join("%s=%d" % item for item in sorted(reasons.items())))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify that the exact 30 Hz vehicle stream contains no visual teleports."""

from __future__ import annotations

import sys

import sumo_bridge


def main() -> int:
    sumo_bridge.prepare_environment()
    import traci  # type: ignore

    step_length = 1.0 / 30.0
    traci.start(
        [
            str(sumo_bridge.SUMO_BINARY), "-c", str(sumo_bridge.CONFIG),
            "--step-length", str(step_length), "--no-step-log", "true",
            "--duration-log.disable", "true", "--no-warnings", "true",
            "--device.rerouting.probability", "0.12", "--device.rerouting.period", "45",
            "--seed", "90210",
        ],
        verbose=False,
    )
    traffic = sumo_bridge.TrafficState(traci)
    previous_positions: dict[str, tuple[float, float]] = {}
    max_step_distance = 0.0
    stream_replacements = 0
    try:
        for _ in range(2700):  # 90 seconds of reproducible pre-roll.
            traci.simulationStep()
            sumo_bridge.make_state(traffic, None, build_packet=False)
        continuation_events = 0
        for _ in range(1800):
            traci.simulationStep()
            state = sumo_bridge.make_state(traffic, None)
            continuation_events += int(state.get("continued_visible_trips", 0))
            vehicles = state["vehicles"]
            if len(vehicles) != state["simulated_vehicle_count"] or len(vehicles) != traffic.civilian_target:
                raise RuntimeError(f"expected all {traffic.civilian_target} simulated cars published, got {len(vehicles)}")
            current_positions: dict[str, tuple[float, float]] = {}
            for vehicle in vehicles:
                vehicle_id = str(vehicle["id"])
                position = (float(vehicle["x"]), float(vehicle["z"]))
                if vehicle_id in previous_positions:
                    old_x, old_z = previous_positions[vehicle_id]
                    max_step_distance = max(max_step_distance, ((position[0] - old_x) ** 2 + (position[1] - old_z) ** 2) ** 0.5)
                elif previous_positions:
                    stream_replacements += 1
                current_positions[vehicle_id] = position
            previous_positions = current_positions
    finally:
        traci.close(False)
    # 0.9 m is deliberately much higher than a 30 Hz city-speed movement;
    # any larger frame-to-frame jump would produce the visible glitch reported.
    if max_step_distance > 0.9:
        raise RuntimeError(f"visual stream jump too large: {max_step_distance:.3f} m")
    # This 60-second high-frequency stream is the jump/identity check. Require
    # proof that continuation occurs; the separate 120-second fleet regression
    # carries the stronger >=10 continuation-rate requirement.
    if stream_replacements != 0 or continuation_events < 1:
        raise RuntimeError(f"visible lifecycle was not stable: replacements={stream_replacements}, continuations={continuation_events}")
    print(
        "SUMO_VISUAL_CONTINUITY_TEST_OK "
        f"cars={traffic.civilian_target} max_frame_step={max_step_distance:.3f}m stream_replacements={stream_replacements} continuations={continuation_events}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"SUMO_VISUAL_CONTINUITY_TEST_FAILED: {error}", file=sys.stderr)
        raise

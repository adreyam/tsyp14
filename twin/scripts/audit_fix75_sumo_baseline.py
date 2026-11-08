#!/usr/bin/env python3
"""Measure boundary departures, mid-turn stops, and stop-line/queue behavior."""

from __future__ import annotations

from collections import defaultdict

import sumo_bridge


def main() -> int:
    sumo_bridge.prepare_environment()
    import traci  # type: ignore

    traci.start(
        [
            str(sumo_bridge.SUMO_BINARY), "-c", str(sumo_bridge.CONFIG),
            "--step-length", "0.1", "--seed", "90210", "--no-step-log", "true",
            "--duration-log.disable", "true", "--no-warnings", "true",
        ],
        verbose=False,
    )
    internal_stop_samples = 0
    internal_stop_episodes = 0
    internal_stop_seconds: dict[str, float] = defaultdict(float)
    maximum_internal_stop = 0.0
    front_red_distances: list[float] = []
    queued_red_distances: list[float] = []
    arrivals = 0
    traffic = sumo_bridge.TrafficState(traci)
    continued_total = 0
    replacements = 0
    previous_visible: set[str] = set()
    try:
        for _ in range(900):
            traci.simulationStep()
            sumo_bridge.make_state(traffic, None, build_packet=False)
        for _ in range(2400):
            traci.simulationStep()
            arrivals += traci.simulation.getArrivedNumber()
            state = sumo_bridge.make_state(traffic, None)
            continued_total += int(state.get("continued_visible_trips", 0))
            visible = {str(value["id"]) for value in state["vehicles"]}
            if previous_visible:
                replacements += len(visible - previous_visible)
            previous_visible = visible
            active_internal: set[str] = set()
            for vehicle_id in traci.vehicle.getIDList():
                speed = traci.vehicle.getSpeed(vehicle_id)
                road = traci.vehicle.getRoadID(vehicle_id)
                angle = traci.vehicle.getAngle(vehicle_id)
                is_turning = road.startswith(":") and abs(angle - round(angle / 90.0) * 90.0) > 4.0
                if speed < 0.15 and is_turning:
                    internal_stop_samples += 1
                    active_internal.add(vehicle_id)
                    if internal_stop_seconds[vehicle_id] == 0.0:
                        internal_stop_episodes += 1
                    internal_stop_seconds[vehicle_id] += 0.1
                    maximum_internal_stop = max(maximum_internal_stop, internal_stop_seconds[vehicle_id])
                elif vehicle_id in internal_stop_seconds:
                    internal_stop_seconds[vehicle_id] = 0.0
                if speed >= 0.15 or road.startswith(":"):
                    continue
                next_tls = traci.vehicle.getNextTLS(vehicle_id)
                if not next_tls:
                    continue
                _tls, _link, distance, signal = next_tls[0]
                if str(signal).lower() not in {"r", "y"}:
                    continue
                leader = traci.vehicle.getLeader(vehicle_id, 120.0)
                if leader is None:
                    front_red_distances.append(float(distance))
                else:
                    queued_red_distances.append(float(distance))
    finally:
        traci.close(False)

    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]

    print(
        "FIX75_SUMO_BASELINE|"
        f"arrivals={arrivals}|visible_replacements={replacements}|visible_trip_continuations={continued_total}|"
        f"midturn_stop_episodes={internal_stop_episodes}|midturn_stop_samples={internal_stop_samples}|"
        f"max_midturn_stop_s={maximum_internal_stop:.1f}|"
        f"front_red_distance_p50={percentile(front_red_distances, 0.50):.1f}|"
        f"front_red_distance_p95={percentile(front_red_distances, 0.95):.1f}|"
        f"queue_red_distance_p95={percentile(queued_red_distances, 0.95):.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

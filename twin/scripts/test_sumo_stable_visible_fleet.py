#!/usr/bin/env python3
"""Long TraCI regression: closed fully-rendered fleet, completed turns, stop lines."""

from __future__ import annotations

from collections import defaultdict
import sys

import sumo_bridge


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def main() -> int:
    sumo_bridge.prepare_environment()
    import traci  # type: ignore

    traci.start([
        str(sumo_bridge.SUMO_BINARY), "-c", str(sumo_bridge.CONFIG), "--step-length", "0.1",
        "--seed", "90210", "--no-step-log", "true", "--duration-log.disable", "true", "--no-warnings", "true",
    ], verbose=False)
    traffic = sumo_bridge.TrafficState(traci)
    initial_visible: set[str] = set()
    continuation_events = 0
    midturn_stop_seconds: dict[str, float] = defaultdict(float)
    maximum_midturn_stop = 0.0
    lead_red_distances: list[float] = []
    queued_red_distances: list[float] = []
    emergency_stops: set[str] = set()
    harsh_braking: set[str] = set()

    def sample_braking() -> None:
        emergency_stops.update(traci.simulation.getEmergencyStoppingVehiclesIDList())
        for braking_id in traci.vehicle.getIDList():
            if traci.vehicle.getAcceleration(braking_id) < -5.0:
                harsh_braking.add(braking_id)

    try:
        for _ in range(900):
            traci.simulationStep()
            sumo_bridge.make_state(traffic, None, build_packet=False)
            sample_braking()
        for sample in range(1200):
            traci.simulationStep()
            sample_braking()
            state = sumo_bridge.make_state(traffic, None)
            vehicles = state["vehicles"]
            ids = {str(vehicle["id"]) for vehicle in vehicles}
            if len(ids) != traffic.civilian_target or len(ids) != len(traci.vehicle.getIDList()):
                raise RuntimeError(f"sample {sample} published {len(ids)} of {len(traci.vehicle.getIDList())} simulated cars (target {traffic.civilian_target})")
            if not initial_visible:
                initial_visible = ids
            elif ids != initial_visible:
                raise RuntimeError(f"sample {sample} replaced {len(ids - initial_visible)} visible car identities")
            continuation_events += int(state.get("continued_visible_trips", 0))
            for vehicle_id in traci.vehicle.getIDList():
                speed = traci.vehicle.getSpeed(vehicle_id)
                road = traci.vehicle.getRoadID(vehicle_id)
                angle = traci.vehicle.getAngle(vehicle_id)
                turning = road.startswith(":") and abs(angle - round(angle / 90.0) * 90.0) > 4.0
                if turning and speed < 0.15:
                    midturn_stop_seconds[vehicle_id] += 0.1
                    maximum_midturn_stop = max(maximum_midturn_stop, midturn_stop_seconds[vehicle_id])
                else:
                    midturn_stop_seconds[vehicle_id] = 0.0
                if speed >= 0.15 or road.startswith(":"):
                    continue
                tls = traci.vehicle.getNextTLS(vehicle_id)
                if not tls or str(tls[0][3]).lower() not in {"r", "y"}:
                    continue
                distance = float(tls[0][2])
                if traci.vehicle.getLeader(vehicle_id, 120.0) is None:
                    lead_red_distances.append(distance)
                else:
                    queued_red_distances.append(distance)
    finally:
        traci.close(False)

    lead_p95 = percentile(lead_red_distances, 0.95)
    queue_p95 = percentile(queued_red_distances, 0.95)
    if continuation_events < 10:
        raise RuntimeError(f"only {continuation_events} visible route continuations occurred")
    if maximum_midturn_stop > 0.3:
        raise RuntimeError(f"a turning vehicle stopped inside a junction for {maximum_midturn_stop:.1f}s")
    if lead_p95 > 2.0:
        raise RuntimeError(f"lead red-light stop is {lead_p95:.1f}m before the stop line")
    if queue_p95 > 24.0:
        raise RuntimeError(f"red-light queue extends {queue_p95:.1f}m into the approach")
    if emergency_stops or harsh_braking:
        raise RuntimeError(f"harsh braking remained: emergency_stops={sorted(emergency_stops)}, harsh_braking={sorted(harsh_braking)}")
    print(
        "SUMO_STABLE_VISIBLE_FLEET_TEST_OK "
        f"cars={len(initial_visible)} replacements=0 continuations={continuation_events} max_midturn_stop={maximum_midturn_stop:.1f}s "
        f"lead_red_p95={lead_p95:.1f}m queue_red_p95={queue_p95:.1f}m emergency_stops=0 harsh_braking=0"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"SUMO_STABLE_VISIBLE_FLEET_TEST_FAILED: {error}", file=sys.stderr)
        raise

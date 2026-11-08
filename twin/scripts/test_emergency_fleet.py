#!/usr/bin/env python3
"""Headless lifecycle regression for the emergency fleet inside the bridge.

A garaged ambulance and a patrol car are dispatched to the same street, must
arrive at their stop in the lane (response mode, no collisions), then return:
the ambulance is handed back to its depot bay, the patrol car resumes patrol.
"""

from __future__ import annotations

import sys

import sumo_bridge


def main() -> int:
    sumo_bridge.prepare_environment()
    import traci  # type: ignore

    traci.start(sumo_bridge.sumo_command(0.1), verbose=False)
    traffic = sumo_bridge.TrafficState(traci)
    engine = sumo_bridge.create_engine(traci, True, 0.1)
    if engine is None:
        raise RuntimeError("incident engine failed to load")
    collisions: set[str] = set()
    statuses: dict[str, list[str]] = {}
    handoffs: list[dict] = []
    red_passes = 0
    try:
        for _ in range(900):
            traci.simulationStep()
            sumo_bridge.make_state(traffic, engine, build_packet=False)
        patrols = [unit for unit in engine.fleet.units.values() if unit.status == "patrol"]
        if len(patrols) < 2 or any(unit.vehicle_id not in traci.vehicle.getIDList() for unit in patrols):
            raise RuntimeError("patrol units are not on the network after warm-up")
        target = {"edge": "E_r6c6_r6c7", "pos": 40.0}
        for unit_id in ("AMB-1", "POL-1"):
            result = engine.handle_command({"command": "incident", "action": "send_unit", "unit": unit_id, "edge": target["edge"], "pos": target["pos"], "request": f"test-{unit_id}"})
            if "dispatched" not in result:
                raise RuntimeError(f"{unit_id} dispatch failed: {result}")
        released = set()
        for step in range(6000):
            traci.simulationStep()
            state = sumo_bridge.make_state(traffic, engine)
            collisions.update(traci.simulation.getCollidingVehiclesIDList())
            handoffs.extend(entry for entry in state.get("handoffs", []) if entry not in handoffs)
            for record in state["fleet"]:
                history = statuses.setdefault(record["unit"], [])
                if not history or history[-1] != record["status"]:
                    history.append(record["status"])
            for unit_id in ("AMB-1", "POL-1"):
                unit = engine.fleet.units[unit_id]
                if unit.status == "on_scene" and unit_id not in released and engine.now - unit.status_since > 15.0:
                    engine.handle_command({"command": "incident", "action": "recall_unit", "unit": unit_id})
                    released.add(unit_id)
            ambulance = engine.fleet.units["AMB-1"]
            if ambulance.status == "entering_depot":
                engine.handle_command({"command": "incident", "action": "unit_parked", "unit": "AMB-1"})
            if ambulance.status == "garaged" and "POL-1" in released and len(statuses["AMB-1"]) > 3:
                break
    finally:
        traci.close(False)
    ambulance_path = statuses.get("AMB-1", [])
    expected = ["mobilizing", "inserting", "en_route", "on_scene", "returning", "entering_depot", "garaged"]
    if [status for status in ambulance_path if status in expected][-len(expected):] != expected:
        raise RuntimeError(f"ambulance lifecycle incomplete: {ambulance_path}")
    if "on_scene" not in statuses.get("POL-1", []) or statuses["POL-1"][-1] != "patrol":
        raise RuntimeError(f"patrol lifecycle incomplete: {statuses.get('POL-1')}")
    if not any(entry.get("unit") == "AMB-1" and entry.get("reason") == "depot_entry" for entry in handoffs):
        raise RuntimeError("ambulance was removed from SUMO without a depot hand-off")
    unit_collisions = {vehicle for vehicle in collisions if "#" in vehicle}
    if unit_collisions:
        raise RuntimeError(f"emergency units collided: {sorted(collisions)}")
    print(f"EMERGENCY_FLEET_TEST_OK ambulance={'>'.join(ambulance_path)} patrol={'>'.join(statuses['POL-1'])} collisions={len(collisions)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"EMERGENCY_FLEET_TEST_FAILED: {error}", file=sys.stderr)
        raise

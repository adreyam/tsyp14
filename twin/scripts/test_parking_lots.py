#!/usr/bin/env python3
"""Smart car parks against a real SUMO run: cars park, lots count correctly, a full lot turns cars away and sends them to
another lot, parked cars leave and drive on, and the numbers always agree (run with the bundled Python, from scripts/)."""

from __future__ import annotations

import sys

import sumo_bridge


def main() -> int:
    sumo_bridge.prepare_environment()
    import traci  # type: ignore
    import parking_lots

    step_length = 1.0 / 15.0
    traci.start(sumo_bridge.sumo_command(step_length), verbose=False)
    traffic = sumo_bridge.TrafficState(traci)
    lots = parking_lots.ParkingLots(traci, sumo_bridge.OUTBOUND_GATES, sumo_bridge.visual_class_for_type)
    sumo_bridge.PARKING = lots
    assert len(lots.lots) >= 6, f"expected the sited lots, got {len(lots.lots)}"
    # Small, popular lots so that full lots (and the turn-away mechanism) show up within a few simulated minutes.
    for lot_id in ("LOT-01", "LOT-02"):
        lots.lots[lot_id].capacity = 3
        lots.lots[lot_id].popularity = 12.0
    snapshot_phase_counts = {phase: 0 for phase in ("entry_wait", "entering", "alighting", "parked", "returning", "boarding", "departure_wait", "leaving", "merge_wait", "merge_turn")}
    max_occupied = {lot_id: 0 for lot_id in lots.lots}
    redirected = denied = left = entered = 0
    warning_seen = False
    gate_seen = False
    waiting_gate_verified = False
    approach_stop_verified = False
    approach_lane_verified = False
    handoff_reasons: set[str] = set()
    for step in range(15 * 420):
        traci.simulationStep()
        state = sumo_bridge.make_state(traffic, None, build_packet=True)
        parking = state["parking"]
        public_lots = {str(item["id"]): item for item in parking["lots"]}
        for intent_id, intent in lots.intents.items():
            if intent.get("stage") == "accepting" and intent_id in traci.vehicle.getIDList():
                approach_lot = lots.lots[str(intent["lot"])]
                approach_position = float(traci.vehicle.getLanePosition(intent_id))
                if abs(approach_position - (approach_lot.pos - parking_lots.ENTRY_APPROACH_METRES)) < 2.0:
                    approach_stop_verified = True
                # A normal lane stop must retain its lane. SUMO's parking-stop
                # flag clears the lane id and shifts the body 3.2 m sideways
                # on the final tick, which becomes a visible slide at handoff.
                if str(traci.vehicle.getLaneID(intent_id)):
                    approach_lane_verified = True
        for live_lot in lots.lots.values():
            if any(str(car.get("phase", "")) in ("reserved", "entry_wait") for car in live_lot.cars.values()):
                assert bool(public_lots[live_lot.id]["gate_open"]), f"{live_lot.id} kept its arm shut for an admitted waiting car"
                waiting_gate_verified = True
        # The production presenter emits these after the routed citizen reaches
        # the car and after the door animation completes.  This headless bridge
        # test acknowledges the same handshake explicitly.
        for live_lot in lots.lots.values():
            for live_car in live_lot.cars.values():
                if live_car["phase"] == "returning":
                    lots.driver_event(str(live_car["id"]), "at_car")
                elif live_car["phase"] == "boarding":
                    lots.driver_event(str(live_car["id"]), "boarded")
        in_sumo = set(traci.vehicle.getIDList())
        for lot in parking["lots"]:
            cars = lot["cars"]
            assert lot["occupied"] <= lot["capacity"], f"{lot['id']} over capacity: {lot['occupied']}/{lot['capacity']}"
            assert len(cars) <= lot["occupied"], "published cars exceed the occupancy count"
            slots = [car["slot"] for car in cars]
            assert len(slots) == len(set(slots)), f"{lot['id']} has two cars in one bay"
            driveway_cars = [car for car in cars if car["phase"] in ("entering", "leaving", "merge_wait", "merge_turn")]
            assert len(driveway_cars) <= 1, f"{lot['id']} has overlapping driveway movements: {driveway_cars}"
            max_occupied[lot["id"]] = max(max_occupied[lot["id"]], lot["occupied"])
            for car in cars:
                snapshot_phase_counts[car["phase"]] += 1
                assert car["id"] not in in_sumo, f"{car['id']} is under parking-lot custody yet still driving in SUMO"
            warning_seen = warning_seen or lot["warning"]
            gate_seen = gate_seen or lot["gate_open"]
            denied = max(denied, sum(l["denied_total"] for l in parking["lots"]))
            redirected = max(redirected, sum(l["redirected_total"] for l in parking["lots"]))
            left = max(left, sum(l["left_total"] for l in parking["lots"]))
            entered = max(entered, sum(l["entered_total"] for l in parking["lots"]))
        for handoff in state["handoffs"]:
            handoff_reasons.add(str(handoff.get("reason", "")))
        assert traci.simulation.getCollidingVehiclesNumber() == 0, "a collision happened"
    traci.close()
    problems = []
    if entered < 6:
        problems.append(f"only {entered} cars ever parked")
    if left < 3:
        problems.append(f"only {left} cars ever left a lot")
    if denied < 1:
        problems.append("no car was ever turned away from a full lot")
    if redirected < 1:
        problems.append("no turned-away car was sent to another lot")
    if not warning_seen or not gate_seen:
        problems.append("the warning triangle or the open barrier never showed")
    if not waiting_gate_verified or not approach_stop_verified or not approach_lane_verified:
        problems.append("the admitted-car barrier, lane-centred stop, or pre-turn approach position was never verified")
    if not {"park", "park_exit"} <= handoff_reasons:
        problems.append(f"handoffs {handoff_reasons}")
    if problems:
        print("PARKING_TEST_FAILED " + "; ".join(problems))
        return 1
    print(f"PARKING_TEST_OK entered={entered} left={left} denied={denied} redirected={redirected} max_occupied={max_occupied}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

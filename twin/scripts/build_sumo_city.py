#!/usr/bin/env python3
"""Build the deterministic 9x9 SUMO network used by City Ground Zero."""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
SUMO_DIR = PROJECT / "sumo"
RUNTIME = PROJECT / "runtime" / "sumo"
NETCONVERT = RUNTIME / "usr" / "bin" / "netconvert"
GRID_SIZE = 9
BLOCK_METERS = 95
CIVILIAN_FLEET_SIZE = 100
CIVILIAN_DEPART_SPACING_SECONDS = 0.85
VEHICLE_CLASSES = json.loads((SUMO_DIR / "vehicle_classes.json").read_text(encoding="utf-8"))["classes"]


def _dimensions(visual_class: str) -> str:
    entry = VEHICLE_CLASSES[visual_class]
    return f'length="{entry["length"]}" width="{entry["width"]}"'


def vehicle_type_lines() -> list[str]:
    """Return every vType. Lengths/widths come from vehicle_classes.json so the
    simulated body and the rendered model are the same object."""
    # Civilians never overtake through the opposite lane on their own
    # (lcOpposite=0); the incident engine grants it per vehicle only while a
    # lane is physically blocked ahead of them.
    civilian = [
        f'<vType id="car" vClass="passenger" carFollowModel="Krauss" accel="2.6" decel="4.5" '
        f'emergencyDecel="8.5" sigma="0.16" tau="1.1" {_dimensions("car")} minGap="2.5" '
        f'maxSpeed="15.5" speedFactor="normc(1.0,0.10)" lcStrategic="1.0" lcOpposite="0.0"/>',
        f'<vType id="taxi" vClass="taxi" color="1,0.72,0.05" accel="2.4" decel="4.5" emergencyDecel="8.5" '
        f'sigma="0.12" tau="1.1" {_dimensions("taxi")} minGap="2.3" maxSpeed="14.5" lcOpposite="0.0"/>',
        f'<vType id="bus" vClass="bus" color="0.15,0.45,0.95" accel="1.4" decel="3.2" emergencyDecel="7.0" '
        f'sigma="0.08" tau="1.2" {_dimensions("bus")} minGap="3.0" maxSpeed="12.0" lcOpposite="0.0"/>',
    ]
    # Emergency units have a law-abiding base type and a response type that the
    # fleet swaps to live. The response type may clear red lights at a bounded
    # speed and overtake through the opposite lane; this junction-model approach
    # was validated collision-free, unlike SUMO's bluelight device alone.
    # Opposite-lane overtaking is never enabled per type: road_control grants
    # it per vehicle only when stuck behind a stopped/slow obstacle with room
    # to merge back before the next junction, and a head-on guard supervises.
    response = 'speedFactor="1.3" lcOpposite="0.0" jmDriveAfterRedTime="1000" jmDriveRedSpeed="5.5"'
    emergency = []
    for type_id, visual_class, accel, decel, max_speed in (
        ("police", "police", 3.0, 5.0, 19.0),
        ("ambulance", "ambulance", 2.4, 4.8, 17.0),
        ("fire", "fire", 1.9, 4.2, 16.0),
    ):
        common = (
            f'vClass="emergency" accel="{accel}" decel="{decel}" emergencyDecel="9.0" sigma="0.06" '
            f'tau="0.9" {_dimensions(visual_class)} minGap="2.0" maxSpeed="{max_speed}" lcStrategic="1.0"'
        )
        emergency.append(f'<vType id="{type_id}" {common} speedFactor="1.0" lcOpposite="0.0"/>')
        emergency.append(f'<vType id="{type_id}_response" {common} {response}/>')
    # Tow, utility and garbage trucks use amber beacons; they obey signals.
    for type_id, accel, max_speed in (("tow", 1.8, 14.0), ("utility", 1.8, 14.0), ("garbage", 1.4, 12.0)):
        emergency.append(
            f'<vType id="{type_id}" vClass="delivery" accel="{accel}" decel="4.2" emergencyDecel="8.0" '
            f'sigma="0.08" tau="1.0" {_dimensions(type_id)} minGap="2.2" maxSpeed="{max_speed}" '
            f'speedFactor="1.0" lcStrategic="1.0" lcOpposite="0.0"/>'
        )
    # A tow truck called to a scene drives as a responder: it keeps its own
    # size and amber beacons but accelerates and cruises like one and may
    # clear a red under the same supervised rules.
    emergency.append(
        f'<vType id="tow_response" vClass="delivery" accel="2.6" decel="4.8" emergencyDecel="9.0" '
        f'sigma="0.06" tau="0.9" {_dimensions("tow")} minGap="2.0" maxSpeed="17.0" '
        f'lcStrategic="1.0" {response}/>'
    )
    return civilian + emergency


def node_id(row: int, column: int) -> str:
    return f"Junction_r{row + 1}_c{column + 1}"


def edge_id(start: tuple[int, int], end: tuple[int, int]) -> str:
    return f"E_r{start[0] + 1}c{start[1] + 1}_r{end[0] + 1}c{end[1] + 1}"


def edge_path(nodes: list[tuple[int, int]]) -> str:
    return " ".join(edge_id(nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1))


def ring_nodes(margin: int) -> list[tuple[int, int]]:
    low = margin
    high = GRID_SIZE - margin - 1
    nodes: list[tuple[int, int]] = []
    nodes.extend((low, column) for column in range(low, high + 1))
    nodes.extend((row, high) for row in range(low + 1, high + 1))
    nodes.extend((high, column) for column in range(high - 1, low - 1, -1))
    nodes.extend((row, low) for row in range(high - 1, low - 1, -1))
    return nodes


def write_sources() -> None:
    SUMO_DIR.mkdir(parents=True, exist_ok=True)

    node_lines = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<nodes>"]
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE):
            # Every physical four-way junction inside the city perimeter is
            # signal-controlled.  Boundary nodes are entry/exit gates, not
            # city intersections, and retain priority handling.
            kind = "traffic_light" if 1 <= row <= 7 and 1 <= column <= 7 else "priority"
            node_lines.append(
                f'    <node id="{node_id(row, column)}" x="{column * BLOCK_METERS}" '
                f'y="{row * BLOCK_METERS}" type="{kind}"/>'
            )
    node_lines.append("</nodes>")
    (SUMO_DIR / "city.nod.xml").write_text("\n".join(node_lines) + "\n", encoding="utf-8")

    edge_lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<edges>",
        # One broad, explicit lane per direction aligns with the visible two-way
        # street: the carriageways are clear of the yellow centre marking.
        '    <type id="urban" priority="5" numLanes="1" speed="13.89" '
        'allow="passenger taxi bus delivery emergency"/>',
    ]
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE - 1):
            left = (row, column)
            right = (row, column + 1)
            edge_lines.append(
                f'    <edge id="{edge_id(left, right)}" from="{node_id(*left)}" '
                f'to="{node_id(*right)}" type="urban"/>'
            )
            edge_lines.append(
                f'    <edge id="{edge_id(right, left)}" from="{node_id(*right)}" '
                f'to="{node_id(*left)}" type="urban"/>'
            )
    for column in range(GRID_SIZE):
        for row in range(GRID_SIZE - 1):
            top = (row, column)
            bottom = (row + 1, column)
            edge_lines.append(
                f'    <edge id="{edge_id(top, bottom)}" from="{node_id(*top)}" '
                f'to="{node_id(*bottom)}" type="urban"/>'
            )
            edge_lines.append(
                f'    <edge id="{edge_id(bottom, top)}" from="{node_id(*bottom)}" '
                f'to="{node_id(*top)}" type="urban"/>'
            )
    edge_lines.append("</edges>")
    (SUMO_DIR / "city.edg.xml").write_text("\n".join(edge_lines) + "\n", encoding="utf-8")

    route_lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<routes>",
    ]
    route_lines.extend(f"    {line}" for line in vehicle_type_lines())
    # Independent origin/destination demand makes cars choose routes through the
    # grid, turn at junctions, queue, change lanes, and reroute around congestion.
    directed_edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE - 1):
            directed_edges.extend((((row, column), (row, column + 1)), ((row, column + 1), (row, column))))
    for column in range(GRID_SIZE):
        for row in range(GRID_SIZE - 1):
            directed_edges.extend((((row, column), (row + 1, column)), ((row + 1, column), (row, column))))

    # Do not create traffic on a central street. Real city traffic enters at a
    # perimeter gate and leaves through another perimeter gate, so the renderer
    # never has to pop a new car into view halfway down a road.
    def is_outer(node: tuple[int, int]) -> bool:
        return node[0] in (0, GRID_SIZE - 1) or node[1] in (0, GRID_SIZE - 1)

    departure_edges = [edge for edge in directed_edges if is_outer(edge[0]) and not is_outer(edge[1])]
    arrival_edges = [edge for edge in directed_edges if not is_outer(edge[0]) and is_outer(edge[1])]
    if not departure_edges or not arrival_edges:
        raise RuntimeError("Could not build city-edge departure and arrival roads")

    rng = random.Random(90210)
    # A closed civilian fleet: every vehicle SUMO simulates is rendered by
    # Godot. Each enters at a perimeter gate during the bridge warm-up and is
    # then kept inside the city by legal changeTarget continuations, so there is
    # no invisible background traffic for rendered cars to queue behind.
    for vehicle_index in range(CIVILIAN_FLEET_SIZE):
        start = rng.choice(departure_edges)
        destination = rng.choice(arrival_edges)
        while (
            abs(start[0][0] - destination[1][0]) + abs(start[0][1] - destination[1][1]) < 10
        ):
            destination = rng.choice(arrival_edges)
        vehicle_roll = rng.random()
        vehicle_type = "bus" if vehicle_roll < 0.08 else ("taxi" if vehicle_roll < 0.30 else "car")
        depart = vehicle_index * CIVILIAN_DEPART_SPACING_SECONDS
        route_lines.append(
            f'    <trip id="civ_{vehicle_index:03d}" type="{vehicle_type}" depart="{depart:.1f}" '
            f'from="{edge_id(*start)}" to="{edge_id(*destination)}" '
            f'departLane="best" departSpeed="max" arrivalLane="current"/>'
        )
    route_lines.append("</routes>")
    (SUMO_DIR / "city.rou.xml").write_text("\n".join(route_lines) + "\n", encoding="utf-8")

    config = """<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="city.net.xml"/>
        <route-files value="city.rou.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="86400"/>
        <step-length value="0.1"/>
    </time>
    <processing>
        <!-- Every vehicle is rendered: a teleport would be a visible jump. The
             bridge watchdog resolves genuine gridlock with legal reroutes. -->
        <time-to-teleport value="-1"/>
        <collision.action value="warn"/>
        <collision.check-junctions value="true"/>
    </processing>
    <routing>
        <device.rerouting.probability value="0.12"/>
        <device.rerouting.period value="45"/>
        <device.rerouting.adaptation-steps value="60"/>
    </routing>
    <report>
        <verbose value="false"/>
        <no-step-log value="true"/>
        <duration-log.disable value="true"/>
    </report>
</configuration>
"""
    (SUMO_DIR / "city.sumocfg").write_text(config, encoding="utf-8")


def build_network() -> None:
    if not NETCONVERT.is_file():
        raise SystemExit(f"Bundled netconvert is missing: {NETCONVERT}")
    library_root = RUNTIME / "usr" / "lib" / "x86_64-linux-gnu"
    environment = os.environ.copy()
    environment["SUMO_HOME"] = str(RUNTIME / "usr" / "share" / "sumo")
    environment["LD_LIBRARY_PATH"] = ":".join(
        (str(library_root), str(library_root / "blas"), str(library_root / "lapack"), str(RUNTIME / "usr" / "lib"))
    )
    command = [
        str(NETCONVERT),
        "--node-files", str(SUMO_DIR / "city.nod.xml"),
        "--edge-files", str(SUMO_DIR / "city.edg.xml"),
        "--output-file", str(SUMO_DIR / "city.net.xml"),
        "--junctions.corner-detail", "4",
        "--junctions.internal-link-detail", "8",
        "--tls.guess-signals", "true",
        "--tls.default-type", "static",
        # Normal routes and the visible-fleet continuation system never require
        # a U-turn. Removing those connections avoids an unserved fourth link
        # on every approach and prevents illogical edge-turn decisions.
        "--no-turnarounds",
        # Each one-lane carriageway knows its opposite lane. This is what lets
        # SUMO overtake a blocked lane (crash, stopped responder) through the
        # oncoming lane only when that lane is clear for the whole manoeuvre.
        "--opposites.guess", "true",
        # SUMO's type-file width is not applied by every netconvert version; use
        # the explicit builder default so lane centres are ±2.25 m from the road
        # centre, matching the visible 11.5 m carriageway.
        "--default.lanewidth", "4.5",
        # A readable urban sequence: sufficient movement time, then an
        # unmistakable yellow, then a real clearance period before cross
        # traffic receives green.
        "--tls.green.time", "9",
        "--tls.yellow.time", "3",
        "--tls.allred.time", "2",
        "--no-warnings", "true",
    ]
    subprocess.run(command, check=True, cwd=SUMO_DIR, env=environment)
    install_protected_turn_programs()


def install_protected_turn_programs() -> None:
    """Replace permissive turns with protected, all-red-separated phases."""
    network_path = SUMO_DIR / "city.net.xml"
    document = ET.parse(network_path)
    root = document.getroot()

    def phase_state(green: tuple[int, ...], yellow: tuple[int, ...] = ()) -> str:
        state = ["r"] * 12
        for index in green:
            state[index] = "G"
        for index in yellow:
            state[index] = "y"
        return "".join(state)

    # A compact 32 s cycle prevents a normal red-light queue from reaching the
    # middle of a 95 m approach. Turns remain fully protected: through traffic
    # receives no permissive turn, and every movement ends in yellow plus an
    # all-red clearance before the next conflict can enter the junction.
    phases = [
        (6, phase_state((0, 1, 6, 7))),
        (3, phase_state((), (0, 1, 6, 7))),
        (1, "r" * 12),
        (3, phase_state((2, 8))),
        (2, phase_state((), (2, 8))),
        (1, "r" * 12),
        (6, phase_state((3, 4, 9, 10))),
        (3, phase_state((), (3, 4, 9, 10))),
        (1, "r" * 12),
        (3, phase_state((5, 11))),
        (2, phase_state((), (5, 11))),
        (1, "r" * 12),
    ]
    controllers = root.findall("tlLogic")
    if len(controllers) != 49:
        raise RuntimeError(f"Expected 49 signal controllers, found {len(controllers)}")
    for controller in controllers:
        for old_phase in list(controller.findall("phase")):
            controller.remove(old_phase)
        for duration, state in phases:
            ET.SubElement(controller, "phase", {"duration": str(duration), "state": state})
    ET.indent(document, space="    ")
    document.write(network_path, encoding="UTF-8", xml_declaration=True)


def main() -> None:
    write_sources()
    build_network()
    print(f"Built SUMO city network: {SUMO_DIR / 'city.net.xml'}")


if __name__ == "__main__":
    main()

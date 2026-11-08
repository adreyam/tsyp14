#!/usr/bin/env python3
"""Verify that the closed civilian fleet only enters the city at perimeter gates."""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
ROUTES = PROJECT / "sumo" / "city.rou.xml"
GRID_HIGH = 9
EDGE = re.compile(r"E_r(\d+)c(\d+)_r(\d+)c(\d+)")


def is_outer(row: int, column: int) -> bool:
    return row in (1, GRID_HIGH) or column in (1, GRID_HIGH)


def main() -> None:
    root = ET.parse(ROUTES).getroot()
    if root.findall("flow"):
        raise SystemExit("background flows would create invisible traffic; the civilian fleet must be explicit trips")
    flows = root.findall("trip")
    if len(flows) < 90:
        raise SystemExit(f"expected the closed civilian fleet of at least 90 trips, found {len(flows)}")
    for flow in flows:
        start = EDGE.fullmatch(flow.attrib["from"])
        destination = EDGE.fullmatch(flow.attrib["to"])
        if start is None or destination is None:
            raise SystemExit(f"invalid route edge in {flow.attrib['id']}")
        start_values = tuple(map(int, start.groups()))
        destination_values = tuple(map(int, destination.groups()))
        if not is_outer(start_values[0], start_values[1]) or is_outer(start_values[2], start_values[3]):
            raise SystemExit(f"flow starts away from an incoming city edge: {flow.attrib['id']}")
        if is_outer(destination_values[0], destination_values[1]) or not is_outer(destination_values[2], destination_values[3]):
            raise SystemExit(f"flow leaves away from an outgoing city edge: {flow.attrib['id']}")
    print(f"SUMO_BOUNDARY_FLOW_TEST_OK trips={len(flows)} entries=perimeter first_targets=perimeter")


if __name__ == "__main__":
    main()

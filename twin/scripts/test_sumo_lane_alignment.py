#!/usr/bin/env python3
"""Assert that every straight SUMO carriageway is visibly off the road centre."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
NETWORK = PROJECT / "sumo" / "city.net.xml"
BLOCK_METERS = 95.0
EXPECTED_OFFSET = 2.25


def nearest_grid_coordinate(value: float) -> float:
    return round(value / BLOCK_METERS) * BLOCK_METERS


def main() -> None:
    tree = ET.parse(NETWORK)
    checked = 0
    offsets: set[float] = set()
    for edge in tree.findall("edge"):
        if edge.get("id", "").startswith(":"):
            continue
        for lane in edge.findall("lane"):
            points = [tuple(map(float, point.split(","))) for point in lane.get("shape", "").split()]
            if len(points) < 2:
                raise SystemExit(f"lane has no usable shape: {lane.get('id')}")
            start, end = points[0], points[-1]
            if abs(start[1] - end[1]) < 0.001:
                offset = abs(start[1] - nearest_grid_coordinate(start[1]))
            elif abs(start[0] - end[0]) < 0.001:
                offset = abs(start[0] - nearest_grid_coordinate(start[0]))
            else:
                raise SystemExit(f"unexpected non-cardinal road lane: {lane.get('id')}")
            if abs(offset - EXPECTED_OFFSET) > 0.02:
                raise SystemExit(f"lane is not in a carriageway: {lane.get('id')} offset={offset:.3f}")
            offsets.add(round(offset, 2))
            checked += 1
    if checked != 288:
        raise SystemExit(f"expected 288 directed road lanes, got {checked}")
    print(f"SUMO_LANE_ALIGNMENT_TEST_OK lanes={checked} offset={offsets.pop():.2f}m")


if __name__ == "__main__":
    main()

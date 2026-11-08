#!/usr/bin/env python3
"""Regression check for the readable city signal sequence."""

from pathlib import Path
import xml.etree.ElementTree as ET


NETWORK = Path(__file__).resolve().parents[1] / "sumo" / "city.net.xml"
EXPECTED = (
    (6, "GGrrrrGGrrrr"),
    (3, "yyrrrryyrrrr"),
    (1, "rrrrrrrrrrrr"),
    (3, "rrGrrrrrGrrr"),
    (2, "rryrrrrryrrr"),
    (1, "rrrrrrrrrrrr"),
    (6, "rrrGGrrrrGGr"),
    (3, "rrryyrrrryyr"),
    (1, "rrrrrrrrrrrr"),
    (3, "rrrrrGrrrrrG"),
    (2, "rrrrryrrrrry"),
    (1, "rrrrrrrrrrrr"),
)


def phase_kind(state: str) -> str:
    if "G" in state or "g" in state:
        return "green"
    if "y" in state or "Y" in state:
        return "yellow"
    return "red"


def main() -> None:
    root = ET.parse(NETWORK).getroot()
    controllers = root.findall("tlLogic")
    if len(controllers) != 49:
        raise SystemExit(f"expected 49 city-intersection controllers, found {len(controllers)}")
    for controller in controllers:
        actual = tuple((int(phase.attrib["duration"]), phase.attrib["state"]) for phase in controller.findall("phase"))
        if actual != EXPECTED:
            raise SystemExit(f"{controller.attrib['id']} has {actual}, expected {EXPECTED}")
        for _duration, state in actual:
            # Each approach owns right, straight, and left links. Opposite roads must receive
            # the same state, while the perpendicular axis is red unless this
            # is the all-red clearance phase.
            north, east, south, west = state[0:3], state[3:6], state[6:9], state[9:12]
            if north != south or east != west:
                raise SystemExit(f"{controller.attrib['id']} does not keep opposite approaches synchronized: {state}")
            north_south_active = any(value in "GgyY" for value in north)
            east_west_active = any(value in "GgyY" for value in east)
            if north_south_active and east_west_active:
                raise SystemExit(f"{controller.attrib['id']} allows conflicting perpendicular movements: {state}")
    print("SUMO_SIGNAL_TIMING_TEST_OK controllers=49 cycle=32s protected-through/protected-turn/yellow/allred")


if __name__ == "__main__":
    main()

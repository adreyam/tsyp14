#!/usr/bin/env python3
"""Batch-test real SUMO movement: turns, queues/stops, and traffic-light phases."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "runtime" / "sumo"
SUMO = RUNTIME / "usr" / "bin" / "sumo"
CONFIG = PROJECT / "sumo" / "city.sumocfg"


def runtime_environment() -> dict[str, str]:
    environment = os.environ.copy()
    library_root = RUNTIME / "usr" / "lib" / "x86_64-linux-gnu"
    library_paths = [library_root, library_root / "blas", library_root / "lapack", RUNTIME / "usr" / "lib"]
    environment["SUMO_HOME"] = str(RUNTIME / "usr" / "share" / "sumo")
    environment["LD_LIBRARY_PATH"] = ":".join(map(str, library_paths))
    return environment


def main() -> int:
    if not SUMO.is_file() or not CONFIG.is_file():
        raise SystemExit("Bundled SUMO executable or city configuration is missing")

    with tempfile.TemporaryDirectory(prefix="city-ground-zero-sumo-") as temp_directory:
        temp_path = Path(temp_directory)
        fcd = temp_path / "traffic.xml"
        lanechanges = temp_path / "lanechanges.xml"
        subprocess.run(
            [
                str(SUMO), "-c", str(CONFIG), "--end", "150", "--fcd-output", str(fcd),
                "--lanechange-output", str(lanechanges), "--no-warnings", "true",
            ],
            check=True,
            env=runtime_environment(),
            cwd=PROJECT / "sumo",
        )

        net = ET.parse(PROJECT / "sumo" / "city.net.xml").getroot()
        # SUMO's internal-lane `via` identifiers tie a rendered curved path to
        # the network connection that created it.  Count left and right paths
        # separately, so a regression to straight-only routes cannot pass.
        connection_direction = {
            connection.attrib["via"]: connection.attrib.get("dir", "")
            for connection in net.findall("connection")
            if connection.attrib.get("via", "").startswith(":")
        }
        turning_samples = 0
        left_turn_samples = 0
        right_turn_samples = 0
        stopped_samples = 0
        moving_samples = 0
        junction_lanes: set[str] = set()
        for _event, timestep in ET.iterparse(fcd, events=("end",)):
            if timestep.tag != "timestep" or float(timestep.attrib["time"]) < 90.0:
                continue
            for vehicle in timestep:
                speed = float(vehicle.attrib["speed"])
                angle = float(vehicle.attrib["angle"])
                lane = vehicle.attrib["lane"]
                moving_samples += int(speed > 1.0)
                stopped_samples += int(speed < 0.15)
                is_diagonal = abs(angle - round(angle / 90.0) * 90.0) > 4.0
                if lane.startswith(":") and is_diagonal:
                    turning_samples += 1
                    junction_lanes.add(lane)
                    var_direction = connection_direction.get(lane, "")
                    left_turn_samples += int(var_direction == "l")
                    right_turn_samples += int(var_direction == "r")
            timestep.clear()

        controllers = net.findall("tlLogic")
        phases = [phase.attrib["state"] for controller in controllers for phase in controller.findall("phase")]
        if (
            len(controllers) != 49
            or turning_samples < 500
            or left_turn_samples < 250
            or right_turn_samples < 250
            or stopped_samples < 1000
            or moving_samples < 10000
            or len(junction_lanes) < 40
            or not any("G" in phase for phase in phases)
            or not any("y" in phase for phase in phases)
            or not any("r" in phase for phase in phases)
        ):
            raise SystemExit(
                "SUMO behavior test failed: "
                f"turning={turning_samples}, left={left_turn_samples}, right={right_turn_samples}, "
                f"stopped={stopped_samples}, moving={moving_samples}, "
                f"junction_lanes={len(junction_lanes)}, controllers={len(controllers)}"
            )
        print(
            "SUMO_BEHAVIOR_TEST_OK "
            f"turning={turning_samples} left={left_turn_samples} right={right_turn_samples} "
            f"stopped={stopped_samples} moving={moving_samples} "
            f"junction_lanes={len(junction_lanes)} controllers={len(controllers)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

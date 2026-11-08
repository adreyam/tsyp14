"""
Mock test data and execution scenarios for Edge Computer Vision Fire & Smoke Detection
(based on YOLOv8 custom fire/smoke edge camera detection).

Includes:
- Confirmed Structural Active Fire scenario (Severity 5, Dual Fire Engines + Ambulance, Green wave)
- Dense Smoke Plume scenario (Severity 3, Reconnaissance Fire Engine)
- Nominal / Clear CV Camera scenario
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from modules.emergency_agent import EmergencyAgent


MOCK_FIRE_JSON_PATH = Path(__file__).resolve().parent / "mock_fire.json"

with open(MOCK_FIRE_JSON_PATH, "r", encoding="utf-8") as f:
    _MOCK_DATA = json.load(f)["scenarios"]

MOCK_ACTIVE_FIRE_EVENT: Dict[str, Any] = _MOCK_DATA["active_fire"]
MOCK_SMOKE_PLUME_EVENT: Dict[str, Any] = _MOCK_DATA["smoke_plume"]
MOCK_CLEAR_NOMINAL_EVENT: Dict[str, Any] = _MOCK_DATA["clear_nominal"]


def get_mock_fire_event(scenario: str = "active_fire") -> Dict[str, Any]:
    if scenario not in _MOCK_DATA:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose from: {list(_MOCK_DATA.keys())}")
    return json.loads(json.dumps(_MOCK_DATA[scenario]))


def run_verification():
    print("=" * 60)
    print("COMPUTER VISION FIRE & SMOKE DETECTION MOCK VERIFICATION")
    print("=" * 60)

    agent = EmergencyAgent()

    # 1. Active Fire
    active_resp = agent.process_event(MOCK_ACTIVE_FIRE_EVENT)
    print(f"\n1. Confirmed Active Fire (Severity {active_resp['severity']}):")
    print(f"   Actions: {[a['type'] for a in active_resp['actions']]}")
    print(f"   Units: {[a['units'] for a in active_resp['actions'] if a['units']]}")
    assert active_resp["severity"] == 5
    assert any(a["type"] == "dispatch_units" for a in active_resp["actions"])
    assert any(a["type"] == "corridor_clear" for a in active_resp["actions"])

    # 2. Smoke Plume
    smoke_resp = agent.process_event(MOCK_SMOKE_PLUME_EVENT)
    print(f"\n2. Dense Smoke Plume (Severity {smoke_resp['severity']}):")
    print(f"   Actions: {[a['type'] for a in smoke_resp['actions']]}")
    assert smoke_resp["severity"] == 3
    assert any(a["type"] == "dispatch_units" for a in smoke_resp["actions"])

    # 3. Nominal
    nominal_resp = agent.process_event(MOCK_CLEAR_NOMINAL_EVENT)
    print(f"\n3. Clear / Nominal Camera (Severity {nominal_resp['severity']}):")
    assert nominal_resp["severity"] == 1

    print("\nFire & Smoke mock verification finished successfully.")


if __name__ == "__main__":
    run_verification()

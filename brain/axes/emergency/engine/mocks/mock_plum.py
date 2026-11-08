"""
Mock data for the PLUM (Propagation of Local Undamped Motion) earthquake detector.

MOCK DATA ONLY: synthetic 3-axis accelerograms (m/s^2) from a small station network around
the city centre. They are inputs for the single PLUM algorithm (plum_engine.PlumStream /
PLUMEngine.run, two-station confirmation, Kilb et al. 2021); no detection result is stored here.

Formats
  * Station-network packet (one gateway message, several stations, same start time):
        data = {"stationNetwork": {"samplingRateHz", "startTime", "stations": [
                   {"sensor_id", "lat", "lon", "accel": {"vertical_z", "east_west_e", "north_south_n"}}]}}
  * Per-sensor packet (each sensor reports on its own; PLUM buffers and combines them):
        data = {"rawWaveformTelemetry": {"sensorId", "lat", "lon", "startTime", "samplingRateHz",
                                         "components": {...}, "units", "clippingDetected", "noiseFloorExceeded"}}
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
for _p in (ROOT_DIR, SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mocks.mock_city_scenarios import synth_station_network  # noqa: E402
from modules.plum_engine import PLUMEngine, PlumConfig, Station, StationData  # noqa: E402

# Six accelerometers about 1 km apart around the city centre (Tunis)
PLUM_MOCK_STATIONS: List[Dict[str, Any]] = [
    {"sensor_id": f"MOCK-SEIS-{k + 1:02d}", "lat": round(36.795 + 0.009 * (k // 3), 4), "lon": round(10.170 + 0.011 * (k % 3), 4)}
    for k in range(6)
]
CENTER = (36.7995, 10.181)
FS, DURATION_S = 40, 24


def _network(magnitude: float, epi=CENTER, depth_km: float = 8.0, spike_station: str | None = None,
             start: float | None = None) -> Dict[str, Any]:
    net = synth_station_network(PLUM_MOCK_STATIONS, epi[0], epi[1], magnitude=magnitude, depth_km=depth_km,
                                fs=FS, duration_s=DURATION_S, spike_station=spike_station)
    net["startTime"] = (start if start is not None else time.time() - DURATION_S)
    return net


def _network_event(eid: str, net: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": eid, "source": "mock_seismic_network", "kind": "seismic_sensor",
            "lat": CENTER[0], "lon": CENTER[1], "ts": net["startTime"] + DURATION_S,
            "data": {"stationNetwork": net, "mock": True}}


def sensor_packets(magnitude: float = 6.5, epi=CENTER, clipping: bool = False) -> List[Dict[str, Any]]:
    """One event per sensor (the streaming format), in arrival order."""
    net = _network(magnitude, epi)
    out = []
    for st in net["stations"]:
        out.append({
            "id": f"mock-{st['sensor_id'].lower()}", "source": "mock_seismic_sensor", "kind": "seismic_sensor",
            "lat": st["lat"], "lon": st["lon"], "ts": net["startTime"] + DURATION_S,
            "data": {"mock": True, "rawWaveformTelemetry": {
                "sensorId": st["sensor_id"], "lat": st["lat"], "lon": st["lon"], "startTime": net["startTime"],
                "samplingRateHz": FS, "components": st["accel"], "units": "m/s^2",
                "clippingDetected": clipping, "noiseFloorExceeded": False}},
        })
    return out


# Strong local quake (M7.0 under the centre): every station shakes, PLUM confirms on its own
MOCK_PLUM_STRONG_MOTION_EVENT: Dict[str, Any] = _network_event("evt-plum-strong-001", _network(7.0))

# Light shaking (M4.5): stations record motion but nobody reaches the PLUM trigger (MMI 4)
MOCK_PLUM_MINOR_MOTION_EVENT: Dict[str, Any] = _network_event("evt-plum-minor-002", _network(4.5))

# A single sensor, clipped, recording a 1-s spike (e.g. a truck hitting the housing): no neighbour
_spike = _network(6.0, spike_station="MOCK-SEIS-01")["stations"][0]
MOCK_PLUM_CLIPPING_ANOMALY_EVENT: Dict[str, Any] = {
    "id": "evt-plum-clipping-003", "source": "mock_seismic_sensor", "kind": "seismic_sensor",
    "lat": _spike["lat"], "lon": _spike["lon"],
    "data": {"mock": True, "rawWaveformTelemetry": {
        "sensorId": _spike["sensor_id"], "lat": _spike["lat"], "lon": _spike["lon"],
        "startTime": time.time() - DURATION_S, "samplingRateHz": FS, "components": _spike["accel"],
        "units": "m/s^2", "clippingDetected": True, "noiseFloorExceeded": False}},
}


def get_mock_plum_event(scenario: str = "strong_motion") -> Dict[str, Any]:
    """Retrieve a mock PLUM event dictionary by scenario key."""
    scenarios = {
        "strong_motion": MOCK_PLUM_STRONG_MOTION_EVENT,
        "critical": MOCK_PLUM_STRONG_MOTION_EVENT,
        "minor": MOCK_PLUM_MINOR_MOTION_EVENT,
        "clipping": MOCK_PLUM_CLIPPING_ANOMALY_EVENT,
    }
    if scenario not in scenarios:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose from {list(scenarios.keys())}")
    return scenarios[scenario].copy()


def get_mock_station_network_data() -> List[StationData]:
    """Batch input for PLUMEngine.run: a quake seen by every station plus an isolated spike on a
    station 60 km away (too far to be anyone's neighbour) that PLUM must suppress."""
    import numpy as np

    net = _network(6.5, start=0.0)
    data = [StationData(Station(s["sensor_id"], s["lat"], s["lon"]), FS,
                        accel={k: np.asarray(v) for k, v in s["accel"].items()}) for s in net["stations"]]
    far = synth_station_network([{"sensor_id": "MOCK-SEIS-FAR", "lat": 37.34, "lon": 10.18}], *CENTER,
                                fs=FS, duration_s=DURATION_S, spike_station="MOCK-SEIS-FAR")["stations"][0]
    data.append(StationData(Station("MOCK-SEIS-FAR", 37.34, 10.18), FS,
                            accel={k: np.asarray(v) for k, v in far["accel"].items()}))
    return data


if __name__ == "__main__":
    from modules.emergency_agent import EmergencyAgent

    print("=" * 60)
    print("PLUM MOCK DATA -> ONE PLUM ALGORITHM")
    print("=" * 60)

    print("\n1. Batch network run (PLUMEngine.run):")
    res = PLUMEngine.run(get_mock_station_network_data(), PlumConfig())
    for d in res.detections:
        print(f"   detection: {d.trigger_station} ({d.trigger_mmi}) confirmed by {d.confirming_station} ({d.confirming_mmi})")
    for s in res.suppressed:
        print(f"   suppressed: {s.station} at t={s.trigger_second}s ({s.reason})")
    assert res.detections and any(s.station == "MOCK-SEIS-FAR" for s in res.suppressed)

    print("\n2. Per-sensor packets streamed into the emergency agent:")
    agent = EmergencyAgent()
    for ev in sensor_packets(6.5):
        r = agent.process_event(ev)
        print(f"   + {ev['data']['rawWaveformTelemetry']['sensorId']}: level={r['risk_level']} "
              f"detections={len(r['plumDetections'])} pending={len(r['plumPending'])} suppressed={len(r['plumSuppressed'])}")

    for name in ("strong_motion", "minor", "clipping"):
        agent.reset_hazard_state()
        r = agent.process_event(get_mock_plum_event(name))
        print(f"\n3. {name}: level={r['risk_level']} peak MMI={r['peakMMI']} actions={[a['type'] for a in r['actions']]}")
    print("\nPLUM mock verification finished.")

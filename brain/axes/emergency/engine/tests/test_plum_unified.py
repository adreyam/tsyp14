"""
One PLUM algorithm (full two-station network detection) and one earthquake result (PLUM + catalog).
"""
import time

import src.modules.plum_engine as plum
import src.plum_dashboard as bench
from mocks.mock_plum import (MOCK_PLUM_CLIPPING_ANOMALY_EVENT, MOCK_PLUM_MINOR_MOTION_EVENT,
                             MOCK_PLUM_STRONG_MOTION_EVENT, sensor_packets)
from src.modules.emergency_agent import EmergencyAgent


def test_single_sensor_shortcut_is_gone():
    for name in ("process_telemetry", "calculate_pga", "pga_to_mmi_scalar"):
        assert not hasattr(plum.PLUMEngine, name)


def test_a_lone_sensor_never_raises_an_alert():
    r = EmergencyAgent().process_event(sensor_packets(7.0)[0])
    assert not r["plumAlertTriggered"]
    assert r["risk_level"] in {"low", "watch"}
    assert not any(a["type"] in {"alert", "evacuate", "dispatch_units"} for a in r["actions"])


def test_streamed_packets_are_combined_and_confirmed():
    agent, levels = EmergencyAgent(), []
    for ev in sensor_packets(6.5):
        levels.append(agent.process_event(ev))
    assert not levels[0]["plumDetections"]               # one sensor: not confirmed
    assert levels[1]["plumDetections"]                   # neighbour arrives: confirmed
    assert levels[-1]["risk_level"] == "warning"


def test_streaming_and_network_packet_reach_the_same_result():
    events, _ = bench.build_packets("local", 6.5, 0, 0, "id")
    streamed = bench.run_events(events, stream=True)["final"]
    batched = bench.run_events(events, stream=False)["final"]
    assert streamed["risk_level"] == batched["risk_level"]
    assert streamed["stationPeakMMI"] == batched["stationPeakMMI"]


def test_plum_alone_can_reach_critical():
    r = EmergencyAgent().process_event(MOCK_PLUM_STRONG_MOTION_EVENT)
    assert r["plumAlertTriggered"] and r["risk_level"] == "critical"
    assert r["hazard_assessment"]["independent_source_count"] >= 2   # confirming stations


def test_light_shaking_and_isolated_spikes_do_not_alarm():
    agent = EmergencyAgent()
    minor = agent.process_event(MOCK_PLUM_MINOR_MOTION_EVENT)
    assert minor["risk_level"] == "watch" and not minor["plumAlertTriggered"]
    agent.reset_hazard_state()
    clipped = agent.process_event(MOCK_PLUM_CLIPPING_ANOMALY_EVENT)
    assert clipped["risk_level"] == "low" and clipped["plumSuppressed"]
    assert [a["type"] for a in clipped["actions"]] == ["sensor_check"]
    assert any("clipping" in e for e in clipped["evidence"])


def _catalog(mag, lat, lon, ts):
    return {"source": "usgs_catalog", "kind": "seismic_catalog", "lat": lat, "lon": lon, "ts": ts,
            "data": {"magnitude": mag, "depth_km": 10.0, "epicenter_lat": lat, "epicenter_lon": lon,
                     "event_time": ts - 60, "catalog_source": "usgs"}}


def test_plum_and_catalog_merge_into_one_earthquake_result():
    agent = EmergencyAgent()
    now = time.time()
    waves = dict(MOCK_PLUM_MINOR_MOTION_EVENT, ts=now - 60)          # light shaking, no PLUM trigger
    agent.process_event(waves)
    r = agent.process_event(_catalog(6.5, 36.40, 10.14, now))        # distant M6.5 catalog solution
    assert r["hazard"] == "earthquake" and r["plumMode"] == "network"
    assert r["stationPeakMMI"]                                         # PLUM part is in the same result
    assert r["risk_level"] == "warning"                               # unconfirmed shaking is not a 2nd source
    agent.reset_hazard_state()
    agent.process_event(dict(MOCK_PLUM_STRONG_MOTION_EVENT, ts=now - 60))
    r = agent.process_event(_catalog(7.0, 36.80, 10.18, now))
    assert r["risk_level"] == "critical"
    assert r["hazard_assessment"]["independent_source_count"] >= 3      # catalog + confirming stations
    assert any("same earthquake" in e for e in r["evidence"])


def test_unrelated_quakes_are_not_merged():
    agent = EmergencyAgent()
    now = time.time()
    agent.process_event(dict(MOCK_PLUM_STRONG_MOTION_EVENT, ts=now - 3600))   # an hour earlier
    r = agent.process_event(_catalog(5.0, 36.40, 10.14, now))
    assert not r.get("stationPeakMMI")


def test_local_quake_without_land_mask_package_keeps_plum(monkeypatch):
    """Regression: without global-land-mask, a quake under the city was taken for an offshore
    quake; the tsunami path replaced the earthquake result and the PLUM detection disappeared."""
    import src.city_brain_dashboard as dashboard
    import src.city_model as city_model
    import src.modules.catalog_quake_engine as cq
    for mod in {cq, __import__("modules.catalog_quake_engine", fromlist=["x"])}:
        monkeypatch.setattr(mod, "_globe", None)          # built-in 5 km grid
    assert not cq.is_mediterranean_offshore(36.8045, 10.1855)  # city block E6 is land
    dashboard.reset_hazards()
    res = dashboard.run_steps(city_model.build_scenario_steps("earthquake", magnitude=6.5)[0])
    rep = res["decision"]["agent_reports"]["emergency"]
    assert rep["hazard"] == "earthquake"
    assert rep["plumDetections"], "trigger/confirm stations must reach the map"


def test_tsunami_response_keeps_the_plum_part():
    agent = EmergencyAgent()
    now = time.time()
    agent.process_event(dict(MOCK_PLUM_MINOR_MOTION_EVENT, ts=now - 60))
    r = agent.process_event(_catalog(7.2, 37.55, 11.25, now))      # offshore, Strait of Sicily
    assert r["hazard"] == "tsunami"
    assert r["stationPeakMMI"] and r["plumMode"] == "network"
    assert any(e.startswith("Same earthquake:") for e in r["evidence"])

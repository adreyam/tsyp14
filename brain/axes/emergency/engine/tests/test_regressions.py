"""
Regression tests for problems fixed after the catastrophic-hazard branch was merged over main.
"""
import time

import src.city_brain_dashboard as dashboard
import src.city_model as city_model
from src.modules.catalog_quake_engine import CatalogQuakeEngine, is_mediterranean_offshore
from src.modules.emergency_agent import EmergencyAgent
from src.modules.storm_engine import StormEngine
from src.orchestrator import AuditLog, INTEGRATED_AXES, LAYA_AVAILABLE, classify, CityEvent, decide


def _routes(event: dict) -> list:
    return classify({"event": CityEvent(**event).model_dump()})["routes"]


# --- dashboard server: crashed on import (AuditLog.DEFAULT_PATH, LAYA_AVAILABLE, INTEGRATED_AXES missing)
def test_dashboard_server_api_contract():
    assert AuditLog.DEFAULT_PATH
    assert isinstance(LAYA_AVAILABLE, bool)
    assert "emergency" in INTEGRATED_AXES
    h = dashboard.health()
    assert h["status"] == "online" and h["axes"]["emergency"] == "integrated"
    assert "total_events" in dashboard.stats()


# --- every dashboard scenario runs and routes the axes the scenario catalogue announces
def test_every_dashboard_scenario_runs_and_matches_its_rule_preview():
    catalogue = dashboard.scenario_catalogue()
    for name in city_model.SCENARIOS:
        steps, _target = city_model.build_scenario_steps(name, None, None, None)
        routes = set()
        for raw in steps:
            event = {k: v for k, v in raw.items() if not k.startswith("_")}
            routes |= set(_routes(event))
            decision = decide(event)
        assert routes == set(catalogue[name]["axes"]), name
        assert decision["activated_axes"], name


# --- main's sensor rules were lost: CV fire, flood sensor, PLUM, gunshot, parking, bins
def test_main_sensor_rules_restored():
    assert "emergency" in _routes({"source": "cam", "kind": "vision_fire_smoke",
                                   "data": {"fire_detected": True, "confidence": 0.9}})
    assert "emergency" in _routes({"source": "h", "kind": "flood_sensor",
                                   "data": {"water_level_cm": 80, "critical_threshold_cm": 50}})
    assert "emergency" in _routes({"source": "a", "kind": "acoustic_gunshot_triangulation",
                                   "data": {"decibelLevel": 120}})
    assert "mobility" in _routes({"source": "p", "kind": "smart_parking", "data": {"occupancy_pct": 98}})
    assert "environment" in _routes({"source": "b", "kind": "smart_waste_bin", "data": {"fill_pct": 95}})


# --- PLUM network false trigger is suppressed and must not cascade to other axes
def test_seismic_false_trigger_stays_on_emergency_and_is_suppressed():
    event, _ = city_model.build_scenario("seismic_spike", None, None, None)
    assert _routes(event) == ["emergency"]
    resp = EmergencyAgent().process_event(event)
    assert resp["plumMode"] == "network"
    assert resp["plumSuppressed"] and not resp["plumAlertTriggered"]
    assert resp["actions"][0]["type"] == "sensor_check"


# --- dispatch orders carry the unit list the dashboard simulation reads
def test_dispatch_actions_carry_units():
    agent = EmergencyAgent()
    resp = agent.process_event({"source": "s", "kind": "accident", "data": {"injuries_reported": 1}})
    assert resp["actions"][0]["units"] == ["police", "ambulance"]
    now = time.time()
    resp = agent.process_event({"source": "gauge", "kind": "hydro_observation", "ts": now,
                                "data": {"river_level_m": 5.5, "flood_stage_m": 4.5, "drainage_load": 0.95,
                                         "rain_24h_forecast_mm": 120, "soil_saturation": 0.95}})
    for a in resp["actions"]:
        if a["type"] == "dispatch_units":
            assert a["units"]


# --- calm readings must not cascade to mobility/energy/environment
def test_calm_hazard_readings_do_not_cascade():
    assert _routes({"source": "g", "kind": "hydro_observation",
                    "data": {"river_level_m": 1.0, "flood_stage_m": 4.5}}) == []
    assert _routes({"source": "c", "kind": "seismic_catalog", "data": {"magnitude": 4.6}}) == ["emergency"]


# --- inland quakes were classified offshore (tsunami candidates)
def test_offshore_detection_uses_land_mask():
    assert not is_mediterranean_offshore(36.8065, 10.1815)  # Tunis
    assert not is_mediterranean_offshore(34.74, 10.76)      # Sfax
    assert is_mediterranean_offshore(37.0, 10.4)            # Gulf of Tunis
    assert is_mediterranean_offshore(37.3, 11.8)            # Strait of Sicily


# --- stale data window from config was ignored by storm and catalog engines
def test_stale_events_are_ignored():
    now = time.time()
    old_gust = {"source": "st", "kind": "weather_observation", "ts": now - 3 * 3600,
                "data": {"gust_kmh": 140.0}}
    assert StormEngine().evaluate([old_gust], now_ts=now).risk_level == "low"
    old_quake = {"source": "usgs", "kind": "seismic_catalog", "ts": now - 5 * 3600,
                 "data": {"magnitude": 7.0, "depth_km": 10, "epicenter_lat": 37.0, "epicenter_lon": 10.4}}
    assert CatalogQuakeEngine().evaluate(old_quake, now_ts=now).risk_level == "low"

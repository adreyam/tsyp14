"""
Scenarios are mock sensor data only; every decision comes from the algorithms.
"""
import src.city_brain_dashboard as dashboard
import src.city_model as city_model
from mocks import mock_city_scenarios as MOCK
from src.modules.emergency_agent import EmergencyAgent


def _all_events():
    for name in city_model.SCENARIOS:
        steps, target = city_model.build_scenario_steps(name)
        for raw in steps:
            yield name, {k: v for k, v in raw.items() if not k.startswith("_")}, target


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not (k == "units" and isinstance(v, str)):   # "m/s^2" is a measurement unit, not a unit list
                yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_scenario_readings_carry_no_decision():
    for name, event, _ in _all_events():
        leaked = MOCK.DECISION_FIELDS & set(_keys(event["data"]))
        assert not leaked, f"{name}: decision field(s) in mock reading: {leaked}"


def test_every_scenario_reading_is_flagged_mock():
    for name, event, _ in _all_events():
        assert event["data"].get("mock") is True, name


def test_catalogue_has_no_hardcoded_axes():
    for name, spec in city_model.SCENARIOS.items():
        assert "axes" not in spec, f"{name}: axes must come from the orchestrator rules"
    for name, spec in dashboard.scenario_catalogue().items():
        assert spec["axes"] and spec["axes_source"].startswith("orchestrator rules")


def test_plum_reading_is_waveforms_only():
    steps, target = city_model.build_scenario_steps("earthquake", magnitude=6.5)
    waves = steps[0]
    assert waves["kind"] == "seismic_sensor"
    assert set(waves["data"]) == {"stationNetwork", "block_id", "mock"}   # no magnitude next to the waveforms
    assert steps[1]["kind"] == "seismic_catalog" and steps[1]["data"]["magnitude"] == 6.5


def test_accident_severity_is_computed_from_observations():
    agent = EmergencyAgent()
    minor = agent.process_event({"source": "cam", "kind": "accident", "data": {"injuries_reported": 0, "lanes_blocked": 1}})
    major = agent.process_event({"source": "cam", "kind": "accident", "data": {"injuries_reported": 4, "lanes_blocked": 2}})
    assert minor["severity"] < major["severity"] == 5
    # an injected 'severity' field is ignored for accidents
    forged = agent.process_event({"source": "cam", "kind": "accident", "data": {"severity": 5}})
    assert forged["severity"] == 2


def test_hazard_levels_come_from_engines_not_scenarios():
    # The flood level rises as the engine correlates more readings: the scenario only sends data.
    dashboard.reset_hazards()
    res = dashboard.run_steps(city_model.build_scenario_steps("river_flood")[0])
    levels = [st["hazard"]["risk_level"] for st in res["steps"]]
    sources = [st["hazard"]["sources"] for st in res["steps"]]
    assert levels[0] == "warning" and levels[-1] == "critical"
    assert sources == [1, 2, 3]
    # one catalog source cannot reach 'critical' however strong the quake (engine safety rule)
    dashboard.reset_hazards()
    q = dashboard.run_steps(city_model.build_scenario_steps("regional_quake", magnitude=7.0)[0])
    assert q["decision"]["agent_reports"]["emergency"]["risk_level"] == "warning"

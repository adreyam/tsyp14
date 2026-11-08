"""
Dashboard integration of the catastrophic-hazard engines and live feeds.
"""
import time

import src.city_brain_dashboard as dashboard
import src.city_model as city_model

EXPECTED = {
    "earthquake": ("earthquake", "critical"),     # PLUM confirmed locally + catalog solution
    "river_flood": ("flood", "critical"),
    "storm": ("storm", "critical"),
    "regional_quake": ("earthquake", "warning"),   # one catalog source: capped below critical
    "tsunami": ("tsunami", "critical"),           # offshore quake + tide gauge
}


def _run(name):
    events, target = city_model.build_scenario_steps(name)
    dashboard.reset_hazards()
    return dashboard.run_steps(events), target


def test_hazard_scenarios_reach_expected_levels():
    for name, (hazard, level) in EXPECTED.items():
        res, target = _run(name)
        rep = res["decision"]["agent_reports"]["emergency"]
        assert rep["hazard"] == hazard and rep["risk_level"] == level, name
        assert len(res["steps"]) == len(city_model.build_scenario_steps(name)[0])
        assert target["type"] == name


def test_hazard_drills_do_not_leak_into_each_other():
    _run("river_flood")
    res, _ = _run("storm")
    first = res["steps"][0]["hazard"]
    assert first["hazard"] == "storm" and first["sources"] == 1


def test_critical_hazards_need_approval_and_carry_no_cap():
    res, _ = _run("tsunami")
    rep = res["decision"]["agent_reports"]["emergency"]
    assert "cap_alert_xml" not in rep
    assert not any(a["type"] == "alert_cap" or a["resource"] == "channel:cap" for a in res["decision"]["actions"])
    assert rep["requires_human_approval"]
    assert any(a["type"] == "evacuate" and a["requires_human_approval"] for a in res["decision"]["actions"])


def test_hazard_monitor_reads_latest_assessments():
    for name in EXPECTED:
        _run(name)
    hz = dashboard.latest_hazards()
    latest = dict(EXPECTED.values())   # several scenarios may share a hazard: the last one run wins
    assert set(latest.items()) <= {(k, v["risk_level"]) for k, v in hz.items()}


def test_live_feeds_are_evaluated(monkeypatch):
    # patch the adapter classes the server holds (src/ is also importable as a top-level path)
    OpenMeteoAdapter, USGSAdapter = dashboard.FEED_ADAPTERS["open_meteo"], dashboard.FEED_ADAPTERS["usgs"]
    now_ms = time.time() * 1000
    monkeypatch.setattr(OpenMeteoAdapter, "fetch", lambda self, lat=None, lon=None: self.parse_payload(
        {"current": {"precipitation": 2.0, "wind_speed_10m": 20.0, "wind_gusts_10m": 85.0, "surface_pressure": 1003},
         "daily": {"precipitation_sum": [12.0]}}))
    monkeypatch.setattr(USGSAdapter, "fetch", lambda self: self.parse_payload({"features": [
        {"id": "a", "properties": {"mag": 5.9, "time": now_ms, "title": "M 5.9 near Bizerte"}, "geometry": {"coordinates": [10.05, 37.45, 9]}},
        {"id": "b", "properties": {"mag": 2.0, "time": now_ms, "title": "small"}, "geometry": {"coordinates": [-118, 34, 5]}}]}))
    res = dashboard.poll_feed("open_meteo")
    assert res["target"]["type"] == "weather_live" and res["decision"]["agent_reports"]["emergency"]["hazard"] == "storm"
    res = dashboard.poll_feed("usgs")
    assert res["fetched"] == 2 and res["evaluated"] == 1
    assert res["target"]["type"] == "regional_quake" and res["target"]["compass"] in {"N", "NNW", "NNE"}


def test_approval_is_recorded():
    out = dashboard.record_approval({"event_id": "evt1", "action_type": "evacuate"})
    assert out == {"status": "recorded", "event_id": "evt1", "action_type": "evacuate", "approved": True}

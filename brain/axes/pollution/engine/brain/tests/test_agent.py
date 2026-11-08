"""
The City Brain as an agent: tool registry (offline), the HTTP agent endpoint on a
live Brain (mock Twin and Guardian over HTTP), and the MCP server over stdio.
"""
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

from src.agent import AgentError, BrainAgent
from src.mcp_client import McpStdioClient
from tests.test_service import city  # noqa: F401  (live Brain + mocks fixture)

ORCH = "orchestrator-token-0123456789"
BRAIN_DIR = os.path.join(os.path.dirname(__file__), "..")


# ---- offline: tools that need no live Brain ---------------------------------------------
def test_every_tool_has_a_schema_and_a_kind():
    tools = BrainAgent(None).list_tools()
    names = {t["name"] for t in tools}
    assert {"brain_status", "get_bins", "get_air_zones", "explain", "assess_air",
            "assess_bin", "plan_route", "submit_readings", "run_cycle", "run_scenario"} <= names
    for t in tools:
        assert t["kind"] in ("read", "what_if", "act")
        assert t["inputSchema"]["type"] == "object"
        assert t["annotations"]["readOnlyHint"] == (t["kind"] != "act")


def test_assess_air_uses_the_team_formula():
    r = BrainAgent(None).call("assess_air", {"pm25": 30, "o3": 20, "no2": 30, "season": "cold"})["result"]
    assert r["r_pm"] == 120.0 and r["level"] == "high"
    assert r["risk"] == pytest.approx(120 + 0.1 * r["r_ox"], abs=0.1)
    assert r["actions_at_this_level"] == ["close_parking", "public_alert", "sensitive_alert"]


def test_assess_air_with_previous_hour():
    r = BrainAgent(None).call("assess_air", {"pm25": 50, "o3": 10, "no2": 10, "pm25_prev": 10,
                                             "season": "cold"})["result"]
    assert r["z_pm"] == pytest.approx(0.9 * 50 + 0.1 * 10)


def test_assess_bin_colours():
    a = BrainAgent(None)
    assert a.call("assess_bin", {"fill_pct": 95})["result"]["color"] == "red"
    r = a.call("assess_bin", {"weight_kg": 20, "capacity_kg": 50, "rate_pct_per_h": 15,
                              "zone_type": "crowded"})["result"]
    assert r["color"] == "orange" and r["collect_now"] and r["projected_pct"] == 100.0
    with pytest.raises(AgentError) as e:
        a.call("assess_bin", {"rate_pct_per_h": 3})
    assert e.value.code == "invalid_arguments"


def test_bad_calls_are_refused_with_a_code():
    a = BrainAgent(None)
    for name, args, code in [("nope", {}, "unknown_tool"),
                             ("assess_air", {"pm25": 1}, "invalid_arguments"),
                             ("assess_air", {"pm25": -1, "o3": 1, "no2": 1}, "invalid_arguments"),
                             ("assess_air", {"pm25": 1, "o3": 1, "no2": 1, "x": 1}, "invalid_arguments"),
                             ("brain_status", {}, "no_live_brain")]:
        with pytest.raises(AgentError) as e:
            a.call(name, args)
        assert e.value.code == code, name


def test_run_scenario_in_isolation():
    r = BrainAgent(None).call("run_scenario", {"id": "A3"})["result"]
    assert r["passed"] and r["isolated"] and len(r["checks"]) >= 3


# ---- live: the HTTP agent endpoint ---------------------------------------------------
@pytest.fixture
def live(city, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ORCHESTRATOR_TOKEN", ORCH)
    city.service._agent_token = ORCH        # the fixture's service was built before the env var
    return city


def call(c, tool, arguments=None, token=ORCH):
    req = urllib.request.Request(c.base + "/v1/agent/call", method="POST",
                                 data=json.dumps({"tool": tool, "arguments": arguments or {}}).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_agent_card_is_public_and_lists_tools(live):
    with urllib.request.urlopen(live.base + "/v1/agent/card", timeout=5) as r:
        card = json.loads(r.read())
    assert card["name"] == "city-brain" and card["live"]
    assert any(t["name"] == "run_cycle" for t in card["tools"])


def test_wrong_orchestrator_token_is_refused(live):
    code, _ = call(live, "brain_status", token="x" * 20)
    assert code == 401
    assert any(e.get("path") == "/v1/agent/call" for e in live.service.log.of("auth_refused"))


def test_orchestrator_drives_a_cycle_and_gets_the_decisions(live):
    live.twin.set_weight("b07", 46.0)                       # 92 %: red
    live.step_once()                                        # the Twin pushes its readings
    code, out = call(live, "run_cycle")
    assert code == 200
    stops = [d["stop"] for d in out["result"]["decisions"] if d["event"] == "dispatch"]
    assert "b07" in stops
    code, out = call(live, "get_bins", {"color": "red"})
    assert [b["id"] for b in out["result"]["bins"]] == ["b07"]
    code, out = call(live, "explain", {"id": "b07"})
    assert out["result"]["current"]["color"] == "red"
    assert any(d["event"] == "dispatch" for d in out["result"]["decisions"])
    calls = live.service.log.of("agent_call")
    assert [c["tool"] for c in calls][-3:] == ["run_cycle", "get_bins", "explain"]


def test_submitted_readings_still_go_through_the_guardian(live):
    live.cycle()
    live.guardian.distrust("b05", "operator test")
    msg = {"tool": "get_sensor_reading", "sensor_id": "b05", "type": "bin_weight",
           "weight_kg": 49, "capacity_kg": 50, "zone": "z", "zone_type": "crowded",
           "location": {"lat": 36.8, "lon": 10.18}, "timestamp": "2026-11-01T07:00:00Z"}
    code, out = call(live, "submit_readings", {"messages": [msg]})
    assert code == 200 and out["result"]["queued"] == 1
    code, out = call(live, "run_cycle")
    assert not [d for d in out["result"]["decisions"] if d.get("stop") == "b05"]
    assert any(d["event"] == "reading_blocked" and d["sensor_id"] == "b05"
               for d in out["result"]["decisions"])


def test_status_route_and_air_zones(live):
    live.cycle(2)
    code, out = call(live, "brain_status")
    assert code == 200 and out["result"]["bins"]["total"] == 10
    assert out["result"]["partners"]["guardian"] == "ok"
    code, out = call(live, "plan_route", {"from_node": "r0c0", "to_node": "r4c4"})
    assert out["result"]["nodes"][0] == "r0c0" and out["result"]["travel_time_s"] > 0
    code, out = call(live, "get_air_zones", {"zone": "B"})
    assert code == 200 and "B" in out["result"]["zones"]
    code, out = call(live, "get_air_zones", {"zone": "Z"})
    assert code == 404 and out["error"]["code"] == "not_found"


def test_act_tools_can_be_switched_off(live):
    live.service.settings.agent.allow_act = False
    code, _ = call(live, "run_cycle")
    assert code == 403
    code, _ = call(live, "brain_status")
    assert code == 200


# ---- MCP over stdio ------------------------------------------------------------------
def mcp(args, env=None):
    return McpStdioClient([sys.executable, "-m", "src.agent_mcp"] + args,
                          env=env or {}, cwd=BRAIN_DIR, timeout_s=60)


def test_mcp_offline_server_answers_what_if_tools():
    c = mcp(["--offline"])
    try:
        c.start()
        assert c.server_info["name"] == "city-brain"
        assert "assess_air" in c.tools
        r = c.call_tool("assess_air", {"pm25": 30, "o3": 20, "no2": 30, "season": "cold"})
        assert r["result"]["level"] == "high"
        with pytest.raises(Exception, match="no_live_brain"):
            c.call_tool("brain_status", {})
    finally:
        c.close()


def test_mcp_proxy_drives_the_live_brain(live):
    c = mcp(["--url", live.base], env={"ORCHESTRATOR_TOKEN": ORCH})
    try:
        live.twin.set_weight("b07", 46.0)
        live.step_once()
        out = c.call_tool("run_cycle", {})
        assert any(d.get("stop") == "b07" for d in out["result"]["decisions"])
        assert c.call_tool("brain_status", {})["result"]["bins"]["by_color"]["red"] == 1
    finally:
        c.close()


def test_mcp_proxy_falls_back_locally_when_the_brain_is_down():
    c = mcp(["--url", "http://127.0.0.1:9"])
    try:
        out = c.call_tool("assess_bin", {"fill_pct": 95})
        assert out["result"]["color"] == "red" and "local fallback" in out["served_by"]
        with pytest.raises(Exception, match="brain_unreachable"):
            c.call_tool("brain_status", {})
    finally:
        c.close()

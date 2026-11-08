"""MCP adapter against the official MCP SDK server (JSON and SSE responses)."""
import pytest
from fastapi.testclient import TestClient

import mocks.twin_mcp as tm
from energy_brain.adapters import PartnerError
from energy_brain.adapters.mcp import McpTwin
from energy_brain.config import PartnerConfig
from energy_brain.schemas import SwitchCommand
from mocks.twin import CitySim
from scenarios.runner import run_scenario
from scenarios.testbook import TESTBOOK


@pytest.fixture(params=[True, False], ids=["json", "sse"])
def mcp_client(request, city_a_spec):
    sim = CitySim(city_a_spec)
    with TestClient(tm.create_mcp_app(sim, api_key="k", json_response=request.param)) as c:
        yield c, sim


def twin(c, **kw):
    return McpTwin(PartnerConfig(adapter="mcp", base_url="http://testserver/mcp", api_key="k", **kw), c)


def test_reads_and_commands_over_mcp(mcp_client):
    c, sim = mcp_client
    t = twin(c)
    assert t.get_city().city_id == "mock-city-a"
    got = {r.sensor_id for r in t.get_readings().valid}
    assert {"PL-Z1", "PL-Z2", "PL-Z3", "PL-Z4"} <= got and {"SUB-N", "SUB-S"} <= got   # zones + substation meters
    ack = t.send_command(SwitchCommand(switch_id="S12", operation="close", decision_id="d", reason="r"))
    assert ack.status.value == "accepted" and sim.switches["S12"].state == "closed"


def test_wrong_key_and_unknown_tool_are_clear(mcp_client):
    c, _ = mcp_client
    with pytest.raises(PartnerError, match="authentication refused"):
        McpTwin(PartnerConfig(adapter="mcp", base_url="http://testserver/mcp", api_key="bad"), c).get_city()
    with pytest.raises(PartnerError, match="not offered by server"):
        twin(c, mcp_tools={"get_city": "get_grid"}).get_city()


def test_tool_error_surfaces(mcp_client):
    c, _ = mcp_client
    with pytest.raises(PartnerError, match="failed"):
        twin(c).s.call("energy_switch_command", {"command": {"switch_id": "S12"}}, "send_command")


@pytest.mark.parametrize("sid", ["E2", "E3", "E7B"])
def test_testbook_over_mcp(sid, tmp_path):
    res = run_scenario(TESTBOOK[sid], log_dir=tmp_path, twin_protocol="mcp")
    assert res["passed"], [c for c in res["checks"] if not c["ok"]]

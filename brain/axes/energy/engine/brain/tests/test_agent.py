"""The Energy Brain as an MCP agent supervised by an orchestrator."""
import pytest
from fastapi.testclient import TestClient

from energy_brain.adapters import PartnerError
from energy_brain.adapters.mcp import McpSession
from energy_brain.agent import TOOL_NAMES, AgentService, mcp_http_app
from scenarios.testbook import TESTBOOK
from sim.session import Session


class Orchestrator:
    """A minimal orchestrator: calls the agent's MCP tools (no LLM needed)."""

    def __init__(self, session: Session, key="orch-key"):
        self.session = session
        self.svc = AgentService(session.brain)
        self._cm = TestClient(mcp_http_app(self.svc, api_key=key))
        self.client = self._cm.__enter__()
        self.mcp = McpSession("agent", "http://testserver/mcp", {"X-API-Key": key}, 5.0, self.client)

    def call(self, tool, **args):
        return self.mcp.call(tool, args, tool)

    def run_until(self, t):
        while self.session.t < t:
            with self.svc.lock:
                self.session.step()

    def close(self):
        self._cm.__exit__(None, None, None)


@pytest.fixture
def orch():
    made = []

    def make(sid="E2", **kw):
        o = Orchestrator(Session(TESTBOOK[sid]), **kw)
        made.append(o)
        return o
    yield make
    for o in made:
        o.close()


def commands(o):
    return [f"{c.operation.value} {c.switch_id}" for c in o.session.brain.commands]


def test_agent_exposes_all_tools_and_a_card(orch):
    o = orch()
    o.mcp.connect()
    assert set(TOOL_NAMES) <= o.mcp.tools
    card = o.call("energy_describe_agent")
    assert card["name"] == "energy-brain" and card["autonomy"]["current_mode"] == "autonomous"
    st = o.call("energy_get_status")
    assert "summary" in st and len(st["zones"]) == 4 and st["mode"] == "autonomous"


def test_autonomous_agent_acts_and_explains(orch):
    o = orch()
    o.run_until(45)
    assert commands(o) == ["close S12"]
    ex = o.call("energy_explain")
    assert "close S12" in ex["summary"] and ex["steps"]
    z = o.call("energy_get_zone", zone_id="Z1")
    assert z["load_pct"] < 100 and z["sensor"]["trust"] == "trusted"


def test_advisory_mode_proposes_and_orchestrator_approves(orch):
    o = orch()
    assert o.call("energy_set_mode", mode="advisory", reason="night shift")["mode"] == "advisory"
    o.run_until(45)
    assert commands(o) == []                                   # nothing moves without approval
    props = o.call("energy_list_proposals")["proposals"]
    assert props and props[0]["action"] == "close S12"
    r = o.call("energy_approve_proposal", proposal_id=props[0]["proposal_id"])
    assert r["executed"], r
    o.run_until(52)
    assert commands(o) == ["close S12"]
    assert [v.outcome for v in o.session.brain.verifications] == ["SUCCESS"]
    assert not o.call("energy_approve_proposal", proposal_id="prop-nope")["executed"]


def test_veto_is_respected_and_audited(orch):
    o = orch()
    assert o.call("energy_veto_switch", switch_id="S12", duration_s=600, reason="maintenance")["vetoed_for_s"] == 600
    o.run_until(60)
    assert commands(o) == []
    ev = o.call("energy_get_events", kinds=["gate_blocked", "orchestrator"])["events"]
    assert any("vetoed by the orchestrator" in e["summary"] for e in ev)
    assert any("veto on S12" in e["summary"] for e in ev)
    o.call("energy_lift_veto", switch_id="S12")
    o.run_until(70)
    assert commands(o) == ["close S12"]


def test_what_if_options_and_bad_input(orch):
    o = orch()
    o.run_until(35)
    w = o.call("energy_what_if", switch_id="S43")
    assert "summary" in w and w["action"] == "close S43"
    opts = o.call("energy_evaluate_options")
    assert "recommendation" in opts and "summary" in opts
    assert "refused" in o.call("energy_get_zone", zone_id="NOPE")["summary"]
    assert "refused" in o.call("energy_set_mode", mode="chaos")["summary"]
    f = o.call("energy_forecast", zone_id="Z1", horizon_s=20)
    assert f["ready"] and any(p["in_s"] == 20 for p in f["points"])


def test_event_cursor_pages_forward(orch):
    o = orch()
    o.run_until(60)
    a = o.call("energy_get_events", since=0, limit=3)
    assert len(a["events"]) == 3 and a["more"]
    b = o.call("energy_get_events", since=a["next_cursor"], limit=200)
    assert b["events"] and all(e["seq"] >= a["next_cursor"] for e in b["events"])
    c = o.call("energy_get_events", since=b["next_cursor"])
    assert c["events"] == [] and not c["more"]          # caught up


def test_priority_from_orchestrator(orch):
    o = orch()
    r = o.call("energy_set_priority_zone", zone_id="Z4", reason="flood pump P-4 running", ttl_s=300)
    assert r["accepted"]
    assert o.call("energy_get_zone", zone_id="Z4")["priority"]


def test_wrong_orchestrator_key_refused():
    s = Session(TESTBOOK["E1"])
    with TestClient(mcp_http_app(AgentService(s.brain), api_key="right")) as c:
        bad = McpSession("agent", "http://testserver/mcp", {"X-API-Key": "wrong"}, 5.0, c)
        with pytest.raises(PartnerError, match="authentication refused"):
            bad.connect()


def test_agent_is_mounted_in_the_live_simulation():
    from sim.server import create_app
    with TestClient(create_app(speed=20)) as c:
        m = McpSession("agent", "http://testserver/agent/mcp", {}, 5.0, c)
        st = m.call("energy_get_status", {}, "status")
        assert st["city_id"] == "mock-city-a"
        assert m.call("energy_set_mode", {"mode": "advisory"}, "mode")["mode"] == "advisory"

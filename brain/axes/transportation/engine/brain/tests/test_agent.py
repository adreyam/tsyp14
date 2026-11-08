"""Tests of the agent layer that need no running Brain (HTTP is faked)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import tools  # noqa: E402


def test_every_tool_has_schema_and_description():
    m = tools.manifest()
    assert len(m) >= 15
    for t in m:
        assert t["description"] and t["input_schema"]["type"] == "object"


def test_unknown_tool_and_bad_arguments():
    assert tools.invoke("nope")["error"]["code"] == "unknown_tool"
    assert tools.invoke("set_signal_mode", {"mode": "x"})["error"]["code"] == "invalid_arguments"


def test_read_only_mode_blocks_writes(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_WRITE", "0")
    assert tools.invoke("set_signal_mode", {"mode": "policy"})["error"]["code"] == "write_disabled"


def test_brain_unreachable_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(tools, "BRAIN_URL", "http://127.0.0.1:1")
    r = tools.invoke("get_city_status")
    assert not r["ok"] and r["error"]["code"] == "brain_unreachable"


def test_write_tools_are_flagged():
    names = {t["name"] for t in tools.manifest() if not t["read_only"]}
    assert {"set_signal_mode", "start_traffic_jam", "dispatch_emergency", "reserve_parking"} <= names
    assert "get_city_status" not in names

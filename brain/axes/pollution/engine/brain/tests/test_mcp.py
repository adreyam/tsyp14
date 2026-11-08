"""
The Brain against the Twin team's REAL MCP server (vendored copy of
tools/mcp/city_mcp_server.py), backed by a fake Godot device API.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mocks.fake_city_api import FakeBin, FakeCityApi
from mocks.mock_guardian import ScriptedGuardian
from src.audit_log import AuditLog
from src.config import BrainSettings
from src.mcp_client import McpError, McpStdioClient
from src.mcp_twin import McpTwinClient
from src.models import TrustStatus
from src.service import BrainService

SERVER = [os.path.join(os.path.dirname(__file__), "..", "mocks", "vendor", "city_mcp_server.py")]


def bins():
    return [FakeBin("WGT-T001", "Street bin", 25.0, 0.0, 0.0, "A", weight_kg=5.0),
            FakeBin("WGT-T002", "Street bin", 25.0, 100.0, 50.0, "B", weight_kg=24.0),
            FakeBin("WGT-C001", "Container", 120.0, -80.0, 200.0, "F", weight_kg=114.0),
            FakeBin("WGT-T003", "Street bin", 25.0, 40.0, -60.0, "C", weight_kg=0.0)]


@pytest.fixture
def city():
    api = FakeCityApi(bins(), speed=0)
    port = api.serve(0)
    client = McpStdioClient([sys.executable] + SERVER, {"CITY_SIM_API_PORT": str(port)},
                            timeout_s=5)
    yield api, client, port
    client.close()
    api.stop()


# ---- client and conversion -------------------------------------------------------
def test_handshake_lists_the_twin_tools(city):
    _, client, _ = city
    client.start()
    assert client.server_info["name"] == "city-ground-zero"
    assert {"waste_bin_report", "empty_waste_bin", "read_all"} <= set(client.tools)


def test_bins_become_brain_readings(city):
    _, client, _ = city
    twin = McpTwinClient(client)
    msgs = {m["sensor_id"]: m for m in twin.fetch_readings()}
    assert set(msgs) == {"WGT-T001", "WGT-T002", "WGT-C001", "WGT-T003"}
    m = msgs["WGT-T002"]
    assert m["capacity_kg"] == pytest.approx(25.0, rel=0.01)       # learned from weight / fill
    assert m["zone"] == "B" and m["weight_kg"] == 24.0
    assert msgs["WGT-T003"]["capacity_kg"] == 25.0                  # empty bin: default per kind
    # 100 m east and 50 m south of the origin
    assert m["location"]["lon"] > twin.origin_lon and m["location"]["lat"] < twin.origin_lat


def test_empty_waste_bin_reaches_the_city(city):
    api, client, _ = city
    McpTwinClient(client).send_collection("WGT-T002")
    assert api.bins["WGT-T002"].weight_kg == pytest.approx(0.3)
    assert api.commands[-1] == {"id": "WGT-T002", "command": "empty"}


def test_tool_error_is_raised_with_its_message(city):
    _, client, _ = city
    with pytest.raises(McpError, match="unknown device"):
        client.call_tool("empty_waste_bin", {"id": "WGT-NOPE"})


def test_city_down_is_an_error_not_a_crash(city):
    api, client, _ = city
    client.start()
    api.stop()
    with pytest.raises(McpError, match="city_unreachable"):
        client.call_tool("waste_bin_report", {"once": True})


def test_server_that_cannot_start_is_reported():
    client = McpStdioClient(["this-command-does-not-exist-xyz"])
    with pytest.raises(McpError, match="cannot start"):
        client.call_tool("city_status")


def test_bad_position_is_rejected_by_the_brain_with_the_field_named():
    class FakeMcp:
        def call_tool(self, name, args):
            if name == "read_all":
                return {"devices": []}
            return {"timestamp": "2026-11-04T10:00:00Z",
                    "bins": [{"id": "WGT-T001", "weight_kg": 3, "fill_percent": 12, "position": "?"}]}
    msg = McpTwinClient(FakeMcp()).fetch_readings()[0]
    assert "location" not in msg and "conversion_error" in msg


# ---- the Brain service in crew mode, reading the Twin over MCP ----------------------
@pytest.fixture
def service(city, monkeypatch, tmp_path):
    api, _, port = city
    monkeypatch.setenv("BRAIN_TOKEN", "b" * 20)
    settings = BrainSettings(
        twin={"transport": "mcp_stdio",
              "mcp": {"command": [sys.executable] + SERVER,
                      "env": {"CITY_SIM_API_PORT": str(port)}, "timeout_s": 5}},
        guardian={"url": "http://127.0.0.1:9"},
        city={"decision": "crew", "reading_interval_s": 60, "crew_max_orders_per_cycle": 5},
        cycle_s=1000)
    guardian = ScriptedGuardian()
    svc = BrainService(settings, guardian=guardian, log=AuditLog())
    yield api, svc, guardian
    svc.stop()


def test_red_bins_are_emptied_through_mcp(service):
    api, svc, _ = service
    svc.run_cycle()
    orders = {e["stop"]: e for e in svc.log.of("dispatch")}
    assert set(orders) == {"WGT-T002", "WGT-C001"}                  # 96 % and 95 %: red
    assert all(e["truck_id"] == "crew" and e["color"] == "red" for e in orders.values())
    assert {c["id"] for c in api.commands} == {"WGT-T002", "WGT-C001"}
    assert api.bins["WGT-T001"].weight_kg == 5.0                     # green bin untouched


def test_untrusted_bin_is_never_emptied(service):
    api, svc, guardian = service
    guardian.distrust("WGT-T002", "fill jumped with no deposits")
    svc.run_cycle()
    assert "WGT-T002" not in {c["id"] for c in api.commands}
    assert [e for e in svc.log.of("reading_blocked") if e["sensor_id"] == "WGT-T002"]
    assert "WGT-C001" in {c["id"] for c in api.commands}            # the real one still is


def test_same_bin_is_not_ordered_twice_before_it_is_seen_empty(service):
    api, svc, _ = service
    svc.run_cycle()
    api.set_weight("WGT-T002", 24.5)        # order not carried out yet (still full)
    svc.run_cycle()
    assert [c["id"] for c in api.commands].count("WGT-T002") == 1


def test_twin_outage_is_logged_once_and_recovers(service):
    api, svc, _ = service
    svc.twin.mcp.close()
    svc.twin.mcp.command = ["this-command-does-not-exist-xyz"]
    svc.run_cycle()
    svc.run_cycle()
    assert len(svc.log.of("twin_unreachable")) == 1
    svc.twin.mcp.command = [sys.executable] + SERVER
    svc.run_cycle()
    assert len(svc.log.of("twin_recovered")) == 1
    assert svc.state["bins"]


def test_crew_mode_unit():
    """Crew decisions without any network: red first, cap per cycle, failures retried."""
    from src.brain import BrainConfig, CityBrain
    from src.graph import Graph

    class Twin:
        def __init__(self):
            self.sent, self.fail = [], False

        def send_collection(self, sid):
            if self.fail:
                raise ConnectionError("down")
            self.sent.append(sid)

    twin, log = Twin(), AuditLog()
    brain = CityBrain(Graph(), ScriptedGuardian(), twin, log,
                      BrainConfig(decision="crew", crew_max_orders_per_cycle=1))
    now = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)

    def reading(sid, kg, t):
        return {"sensor_id": sid, "weight_kg": kg, "capacity_kg": 25.0, "zone": "A",
                "location": {"lat": 36.8, "lon": 10.18}, "timestamp": t.isoformat()}

    twin.fail = True
    brain.step([reading("a", 24, now), reading("b", 23, now)], [], now)
    assert twin.sent == [] and log.of("twin_unavailable")
    twin.fail = False
    later = now + timedelta(minutes=1)
    brain.step([reading("a", 24, later), reading("b", 23, later)], [], later)
    assert twin.sent == ["a"]                        # one per cycle, fullest red first
    brain.step([reading("a", 24, later), reading("b", 23, later)], [], later)
    assert twin.sent == ["a", "b"]


# ---- air-quality axis over MCP ------------------------------------------------------
def test_air_monitors_become_brain_readings_with_units_converted(city):
    _, client, _ = city
    msgs = {m["sensor_id"]: m for m in McpTwinClient(client).fetch_air_readings()}
    assert set(msgs) == {"AIR-01", "AIR-02", "AIR-03"}
    m = msgs["AIR-02"]
    assert m["type"] == "air_quality" and m["zone"] == "F"
    assert m["pm25"] == 12.0 and m["o3"] == 12.0
    assert m["no2"] == pytest.approx(30.0 / (46.01 / 24.45), abs=0.01)   # ug/m3 -> ppb


def test_parking_lots_and_barrier_over_mcp(city):
    api, client, _ = city
    twin = McpTwinClient(client)
    assert twin.list_parking_lots() == {"LOT-01": "A", "LOT-02": "F"}
    twin.set_parking_barrier("LOT-02", "closed")
    assert api.lots["LOT-02"]["barrier"] == "closed"


def test_air_high_closes_the_car_park_through_mcp(service):
    api, svc, _ = service
    api.air["AIR-02"].pm25 = 40.0                  # zone F: critical in any season
    svc.run_cycle()
    assert svc.state["air"]["F"]["level"] == "critical"
    assert api.lots["LOT-02"]["barrier"] == "closed"
    assert api.lots["LOT-01"]["barrier"] == "auto"

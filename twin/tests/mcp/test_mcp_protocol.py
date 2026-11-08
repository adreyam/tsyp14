"""MCP protocol unit tests: a real MCP client (tools/mcp/city_cli.py) talks over stdio to the real MCP server process,
which forwards to a fake city. Nothing is mocked between the client and the server."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "tools" / "mcp"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import city_cli  # noqa: E402
import city_mcp_server  # noqa: E402
from fake_city import FakeCity  # noqa: E402

SERVER = str(PROJECT / "tools" / "mcp" / "city_mcp_server.py")


class McpProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.city = FakeCity()
        env = dict(os.environ, CITY_SIM_API_PORT=str(cls.city.port))
        cls.client = city_cli.MCPClient([sys.executable, SERVER], env=env)
        cls.init = cls.client.initialize()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.city.close()

    def call(self, name, **arguments):
        return self.client.call_tool(name, arguments)

    def data(self, name, **arguments):
        result = self.call(name, **arguments)
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return result["structuredContent"]

    # ---- handshake
    def test_initialize_handshake_and_capabilities(self):
        self.assertEqual(self.init["protocolVersion"], "2025-06-18")
        self.assertEqual(self.init["serverInfo"]["name"], "city-ground-zero")
        self.assertIn("tools", self.init["capabilities"])
        self.assertIn("resources", self.init["capabilities"])

    def test_older_protocol_version_is_negotiated(self):
        server = city_mcp_server.Server(city_mcp_server.SimClient(port=1))
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        reply = server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(reply["result"]["protocolVersion"], city_mcp_server.SUPPORTED_PROTOCOLS[0])

    def test_tools_are_described_with_schemas(self):
        names = {tool["name"] for tool in self.client.tools}
        for expected in ["city_status", "list_sensors", "read_sensor", "read_sensors", "watch_sensor", "sensor_summary", "waste_bin_report",
                         "list_actuators", "list_commands", "command_actuator", "set_weather", "list_zones", "read_zone", "trigger_earthquake", "clear_earthquakes", "list_parking_lots", "set_parking_barrier", "set_signal", "list_vehicles", "list_citizens", "list_incidents", "trigger_incident", "set_time_of_day", "set_signal_phase",
                         "set_power_switch", "set_valve", "set_street_light", "set_service_zone", "empty_waste_bin",
                         "set_clock_rate", "set_camera", "follow", "highlight_assets", "recharge_utilities", "set_auto_recharge", "set_all_street_lights",
                         "replay_incident", "focus_device", "set_viewport_layout", "control_incident", "control_replay", "read_event_log", "read_ui_state", "read_city"]:
            self.assertIn(expected, names)
        for tool in self.client.tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
            self.assertIn("readOnlyHint", tool["annotations"])
        read_sensor = next(t for t in self.client.tools if t["name"] == "read_sensor")
        self.assertEqual(read_sensor["inputSchema"]["required"], ["id"])

    def test_actuating_tools_are_not_marked_read_only(self):
        hints = {t["name"]: t["annotations"]["readOnlyHint"] for t in self.client.tools}
        self.assertTrue(hints["read_sensor"])
        self.assertFalse(hints["set_power_switch"])
        self.assertFalse(hints["empty_waste_bin"])

    # ---- sensors
    def test_read_sensor_goes_to_the_city(self):
        before = len(self.city.requests)
        data = self.data("read_sensor", id="AIR-01")
        self.assertEqual(data["readings"]["pm2_5"]["value"], 7.5)
        self.assertGreater(len(self.city.requests), before)
        self.assertEqual(self.city.requests[-1]["method"], "read")

    def test_read_sensor_is_live_not_cached(self):
        first = self.data("read_sensor", id="WGT-T002")["readings"]["bin_weight"]["value"]
        self.city.bins["WGT-T002"] = first + 5.0
        second = self.data("read_sensor", id="WGT-T002")["readings"]["bin_weight"]["value"]
        self.assertAlmostEqual(second - first, 5.0)

    def test_metric_filter(self):
        data = self.data("read_sensor", id="WGT-T001", metrics=["bin_weight"])
        self.assertEqual(list(data["readings"]), ["bin_weight"])

    def test_unknown_sensor_is_a_tool_error_not_a_crash(self):
        result = self.call("read_sensor", id="NOPE-1")
        self.assertTrue(result["isError"])
        self.assertIn("unknown_device", result["content"][0]["text"])
        self.assertEqual(self.data("city_status")["name"], "Fake City")

    def test_list_sensors_filters_by_family(self):
        data = self.data("list_sensors", family="waste")
        self.assertEqual({d["family"] for d in data["devices"]}, {"waste"})
        self.assertEqual(data["total"], 3)

    def test_read_sensors_reports_unknown_ids(self):
        data = self.data("read_sensors", ids=["AIR-01", "ZZ-9"])
        self.assertEqual([d["id"] for d in data["devices"]], ["AIR-01"])
        self.assertEqual(data["unknown_ids"], ["ZZ-9"])

    def test_waste_bin_report_sorts_fullest_first(self):
        self.city.bins.update({"WGT-T001": 3.0, "WGT-T002": 27.0, "WGT-C001": 12.0})
        data = self.data("waste_bin_report")
        fills = [b["fill_percent"] for b in data["bins"]]
        self.assertEqual(fills, sorted(fills, reverse=True))
        self.assertEqual(data["bins"][0]["id"], "WGT-T002")
        self.assertAlmostEqual(data["total_weight_kg"], 42.0)
        only_full = self.data("waste_bin_report", min_fill_percent=50)
        self.assertEqual([b["id"] for b in only_full["bins"]], ["WGT-T002"])

    def test_watch_sensor_streams_progress_and_samples_live_values(self):
        seen = []
        self.city.bins["WGT-T001"] = 1.0
        result = self.client.call_tool("watch_sensor", {"id": "WGT-T001", "metric": "bin_weight", "duration_s": 1.0, "interval_s": 0.25},
                                       on_progress=seen.append)
        samples = result["structuredContent"]["samples"]
        self.assertGreaterEqual(len(samples), 3)
        self.assertEqual(samples[0]["metric"], "bin_weight")
        self.assertGreaterEqual(len(seen), len(samples))
        self.assertTrue(all(m["method"] == "notifications/progress" for m in seen))
        self.assertIn("WGT-T001", seen[0]["params"]["message"])

    def test_a_data_function_streams_by_itself_until_cancelled(self):
        """The function itself streams (no wrapper, no loop on the client side): progress notifications until cancelled."""
        self.city.bins["WGT-T002"] = 5.0
        seen: list[dict] = []
        request_id = self.client.last_request_id + 1

        def on_progress(message):
            seen.append(json.loads(message["params"]["message"]))
            if len(seen) == 2:
                self.city.bins["WGT-T002"] = 9.0           # the world changes while the call is running
            if len(seen) == 4:
                self.client.cancel(request_id, "test")

        result = self.client.call_tool("read_sensor", {"id": "WGT-T002", "interval_s": 0.1}, on_progress=on_progress)
        info = result["structuredContent"]
        self.assertTrue(info["cancelled"])
        self.assertGreaterEqual(info["updates"], 4)
        self.assertEqual(len(info["samples"]), info["updates"])
        weights = [u["data"]["readings"]["bin_weight"]["value"] for u in seen]
        self.assertEqual(weights[0], 5.0)
        self.assertEqual(weights[-1], 9.0)
        self.assertTrue(all(u["tool"] == "read_sensor" for u in seen))
        self.assertEqual(self.data("city_status")["name"], "Fake City")      # the connection is healthy afterwards

    def test_every_data_function_streams_when_progress_is_requested(self):
        for name, arguments in [("city_status", {}), ("read_sensors", {"ids": ["AIR-01"]}), ("sensor_summary", {"family": "air"}), ("waste_bin_report", {})]:
            with self.subTest(name=name):
                seen = []
                result = self.client.call_tool(name, dict(arguments, duration_s=0.5, interval_s=0.1), on_progress=seen.append)
                self.assertGreaterEqual(result["structuredContent"]["updates"], 3)
                self.assertGreaterEqual(len(seen), 3)

    def test_the_default_tick_is_100_ms(self):
        spec = next(t for t in self.client.tools if t["name"] == "read_all")["inputSchema"]["properties"]["interval_s"]
        self.assertEqual(spec["default"], 0.1)
        seen = []
        started = time.monotonic()
        result = self.client.call_tool("city_status", {"duration_s": 1.0}, on_progress=seen.append)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(result["structuredContent"]["updates"], 6, "no interval_s given: it must tick about every 100 ms")
        self.assertLess(elapsed, 3.0)

    def test_panel_actions_are_tools(self):
        for name, arguments, target, command in [
                ("set_camera", {"view": "CAM-03"}, "CAMERA", "view"), ("follow", {"target": "car"}, "CAMERA", "follow"),
                ("highlight_assets", {"kind": "hospitals"}, "HIGHLIGHT", "show"), ("highlight_assets", {"kind": "clear"}, "HIGHLIGHT", "clear"),
                ("recharge_utilities", {}, "UTILITY", "recharge_all"), ("set_auto_recharge", {"enabled": True}, "UTILITY", "auto_recharge_on"),
                ("set_all_street_lights", {"mode": "dim", "level": 0.4}, "LIGHTS", "dim"), ("set_clock_rate", {"rate": 30}, "CLOCK", "set_rate"),
                ("replay_incident", {"id": "INC-1"}, "REPLAY", "play"), ("replay_incident", {"stop": True}, "REPLAY", "stop"),
                ("focus_device", {"id":"AIR-01"}, "FOCUS", "device"), ("set_viewport_layout", {"layout":"full"}, "VIEWPORT", "full"),
                ("control_incident", {"id":"INC-1", "action":"watch"}, "INCIDENTS", "watch"), ("control_replay", {"action":"camera", "value":"orbit"}, "REPLAY", "camera")]:
            with self.subTest(name=name, arguments=arguments):
                data = self.data(name, **arguments)
                self.assertEqual(data["id"], target)
        self.assertEqual(self.data("read_event_log", once=True)["count"], 2)
        self.assertIn("highlight_kinds", self.data("read_ui_state", once=True))
        city = self.data("read_city", once=True)
        self.assertTrue({"status", "devices", "vehicles", "citizens", "incidents", "units", "parking", "earthquakes", "events", "ui"} <= set(city))

    def test_streaming_stops_at_its_duration_without_a_cancel(self):
        result = self.client.call_tool("city_status", {"duration_s": 0.5, "interval_s": 0.1}, on_progress=lambda m: None)
        info = result["structuredContent"]
        self.assertFalse(info["cancelled"])
        self.assertGreaterEqual(info["updates"], 3)

    def test_once_returns_a_single_reading_even_when_progress_is_requested(self):
        seen = []
        result = self.client.call_tool("read_sensor", {"id": "AIR-01", "once": True}, on_progress=seen.append)
        self.assertNotIn("samples", result["structuredContent"])
        self.assertIn("readings", result["structuredContent"])
        self.assertEqual(seen, [])

    def test_stream_arguments_are_validated(self):
        result = self.call("read_sensor", id="AIR-01", interval_s=0)
        self.assertTrue(result["isError"])
        self.assertIn(">= 0.1", result["content"][0]["text"])
        result = self.call("read_sensor", id="AIR-01", once="yes")
        self.assertTrue(result["isError"])

    def test_every_tool_always_answers_with_one_structured_json_object(self):
        """Success or failure, the text content and the structured content are the same JSON object with the shared envelope."""
        samples = {"string": "A", "integer": 1, "number": 1.0, "boolean": False, "array": ["AIR-01"], "object": {}}
        for tool in self.client.tools:
            schema = tool["inputSchema"]
            arguments = {}
            for key in schema.get("required", []):
                spec = schema["properties"][key]
                kind = spec.get("type")
                kind = kind[0] if isinstance(kind, list) else kind
                arguments[key] = spec["enum"][0] if "enum" in spec else samples.get(kind, "A")
            if "duration_s" in schema["properties"] or "once" in schema["properties"]:
                arguments["once"] = True
            result = self.client.call_tool(tool["name"], arguments)
            with self.subTest(tool=tool["name"]):
                parsed = json.loads(result["content"][0]["text"])
                self.assertIsInstance(parsed, dict)
                self.assertEqual(parsed, result["structuredContent"])
                self.assertEqual(parsed["source"], "city_ground_zero")
                self.assertEqual(parsed["tool"], tool["name"])
                self.assertRegex(parsed["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
                if result["isError"]:
                    self.assertEqual(set(parsed["error"]), {"code", "message"})

    def test_errors_are_structured_json_too(self):
        for name, arguments in [("read_sensor", {"id": "NOPE"}), ("read_sensor", {}), ("set_weather", {"weather": "meteors"})]:
            result = self.call(name, **arguments)
            self.assertTrue(result["isError"])
            parsed = result["structuredContent"]
            self.assertEqual(parsed["tool"], name)
            self.assertIn("code", parsed["error"])
            self.assertEqual(json.loads(result["content"][0]["text"]), parsed)

    def test_sensor_summary(self):
        data = self.data("sensor_summary", family="air")
        self.assertEqual(data["families"]["air"]["pm2_5"]["devices"], 1)

    # ---- actuators
    def test_actuator_command_changes_the_city_and_reports_before_after(self):
        self.city.switch_closed = True
        data = self.data("set_power_switch", id="SWT-01", state="open")
        self.assertFalse(self.city.switch_closed)
        self.assertEqual(data["before"]["switch_closed"]["value"], 100.0)
        self.assertEqual(data["after"]["switch_closed"]["value"], 0.0)
        self.data("set_power_switch", id="SWT-01", state="close")
        self.assertTrue(self.city.switch_closed)

    def test_command_to_read_only_sensor_is_refused(self):
        result = self.call("command_actuator", id="AIR-01", command="open")
        self.assertTrue(result["isError"])
        self.assertIn("bad_command", result["content"][0]["text"])

    def test_empty_bin_and_weather(self):
        self.city.bins["WGT-C001"] = 100.0
        self.data("empty_waste_bin", id="WGT-C001")
        self.assertLess(self.city.bins["WGT-C001"], 1.0)
        self.data("set_weather", weather="rain")
        self.assertEqual(self.city.weather, "rain")

    def test_weather_enum_is_validated_before_reaching_the_city(self):
        before = len(self.city.requests)
        result = self.call("set_weather", weather="meteors")
        self.assertTrue(result["isError"])
        self.assertIn("must be one of", result["content"][0]["text"])
        self.assertEqual(len(self.city.requests), before)

    def test_argument_validation(self):
        for name, arguments, fragment in [
            ("read_sensor", {}, "missing required argument 'id'"),
            ("read_sensor", {"id": 5}, "must be string"),
            ("list_sensors", {"limit": 0}, ">= 1"),
            ("set_simulation_speed", {"speed": 99}, "<= 8"),
            ("set_power_switch", {"id": "SWT-01", "state": "toggle"}, "must be one of"),
            ("list_sensors", {"colour": "red"}, "unknown argument"),
        ]:
            with self.subTest(name=name, arguments=arguments):
                result = self.call(name, **arguments)
                self.assertTrue(result["isError"])
                self.assertIn(fragment, result["content"][0]["text"])

    def test_list_actuators_includes_global_controls(self):
        data = self.data("list_actuators")
        self.assertEqual([d["id"] for d in data["devices"]], ["SWT-01"])
        self.assertIn("WEATHER", data["global_controls"])

    # ---- protocol errors
    def test_unknown_method_and_tool(self):
        with self.assertRaises(city_cli.MCPError) as ctx:
            self.client.request("does/not/exist")
        self.assertIn("-32601", str(ctx.exception))
        with self.assertRaises(city_cli.MCPError) as ctx:
            self.client.request("tools/call", {"name": "nope", "arguments": {}})
        self.assertIn("-32602", str(ctx.exception))

    def test_ping_and_resources(self):
        self.assertEqual(self.client.request("ping"), {})
        uris = [r["uri"] for r in self.client.request("resources/list")["resources"]]
        self.assertIn("city://status", uris)
        status = json.loads(self.client.request("resources/read", {"uri": "city://status"})["contents"][0]["text"])
        self.assertEqual(status["name"], "Fake City")
        sensor = json.loads(self.client.request("resources/read", {"uri": "city://sensor/AIR-01"})["contents"][0]["text"])
        self.assertEqual(sensor["id"], "AIR-01")
        templates = self.client.request("resources/templates/list")["resourceTemplates"]
        self.assertEqual(templates[0]["uriTemplate"], "city://sensor/{id}")

    def test_raw_jsonrpc_roundtrip(self):
        reply = self.client.raw({"jsonrpc": "2.0", "id": 777, "method": "tools/call", "params": {"name": "city_status", "arguments": {}}})
        self.assertEqual(reply[-1]["id"], 777)
        self.assertFalse(reply[-1]["result"]["isError"])


class ServerWithoutCityTest(unittest.TestCase):
    def test_finds_the_city_from_the_port_file_without_any_configuration(self):
        import tempfile
        city = FakeCity()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "city_ground_zero_api_port").write_text(str(city.port))
            env = {k: v for k, v in os.environ.items() if k != "CITY_SIM_API_PORT"}
            env["TMPDIR"] = tmp
            client = city_cli.MCPClient([sys.executable, SERVER], env=env)
            try:
                client.initialize()
                result = client.call_tool("read_sensor", {"id": "AIR-01"})
                self.assertFalse(result["isError"], result["content"][0]["text"])
            finally:
                client.close()
                city.close()

    def test_tool_errors_when_the_city_is_not_running(self):
        client = city_cli.MCPClient([sys.executable, SERVER], env=dict(os.environ, CITY_SIM_API_PORT="1"))
        try:
            client.initialize()
            result = client.call_tool("read_sensor", {"id": "AIR-01"})
            self.assertTrue(result["isError"])
            self.assertIn("city_unreachable", result["content"][0]["text"])
        finally:
            client.close()

    def test_tools_list_works_without_a_city(self):
        client = city_cli.MCPClient([sys.executable, SERVER], env={k: v for k, v in os.environ.items() if k != "CITY_SIM_API_PORT"})
        try:
            client.initialize()
            self.assertGreater(len(client.tools), 10)
            result = client.call_tool("city_status", {})
            self.assertIn("city_unreachable", result["content"][0]["text"])
        finally:
            client.close()

    def test_reconnects_after_the_connection_drops(self):
        city = FakeCity()
        client = city_cli.MCPClient([sys.executable, SERVER], env=dict(os.environ, CITY_SIM_API_PORT=str(city.port)))
        try:
            client.initialize()
            self.assertFalse(client.call_tool("read_sensor", {"id": "AIR-01"})["isError"])
            city.drop_connections()
            self.assertFalse(client.call_tool("city_status", {})["isError"])
        finally:
            client.close()
            city.close()


class CliTest(unittest.TestCase):
    def test_argument_parsing(self):
        schema = {"required": ["id"]}
        self.assertEqual(city_cli.parse_arguments(["id=AIR-01", "limit=5", "flag=true", 'ids=["a","b"]'], None),
                         {"id": "AIR-01", "limit": 5, "flag": True, "ids": ["a", "b"]})
        self.assertEqual(city_cli.parse_arguments(["AIR-01"], schema), {"id": "AIR-01"})
        self.assertEqual(city_cli.parse_arguments(['{"id":"X","metrics":["a"]}'], schema), {"id": "X", "metrics": ["a"]})
        self.assertEqual(city_cli.parse_arguments(["time=22:30"], None), {"time": "22:30"})
        self.assertEqual(city_cli.parse_arguments(["ids=[AIR-01,NOISE-01]"], None), {"ids": ["AIR-01", "NOISE-01"]})

    def test_function_call_syntax(self):
        self.assertEqual(city_cli.parse_call('read_sensor(id="WGT-T001")'), ("read_sensor", [], {"id": "WGT-T001"}))
        self.assertEqual(city_cli.parse_call('read_sensors(ids=["A", "B"], once=True)'), ("read_sensors", [], {"ids": ["A", "B"], "once": True}))
        self.assertEqual(city_cli.parse_call('read_sensor("AIR-01")'), ("read_sensor", ["AIR-01"], {}))
        self.assertEqual(city_cli.parse_call("city_status()"), ("city_status", [], {}))
        self.assertIsNone(city_cli.parse_call("read_sensor id=AIR-01"))
        with self.assertRaises(city_cli.MCPError):
            city_cli.parse_call("read_sensor(id=)")

    def test_header_is_short_and_uses_function_syntax(self):
        city = FakeCity()
        client = city_cli.MCPClient([sys.executable, SERVER], env=dict(os.environ, CITY_SIM_API_PORT=str(city.port)))
        try:
            client.initialize()
            header = city_cli.Terminal(client).header()
            plain = re.sub(r"\x1b\[[0-9;]*m", "", header)
            self.assertLessEqual(len(plain.splitlines()), 34)
            self.assertIn('read_sensor(id)', plain)
            self.assertIn("set_power_switch(id, state)", plain)
            self.assertIn("stop with q or Ctrl+C", plain)
            self.assertIn("WGT-T001", plain.split("Ids to try")[1])
        finally:
            client.close()
            city.close()

    def test_sparkline_and_table(self):
        self.assertEqual(city_cli.sparkline([1, 1, 1]), "▁▁▁")
        line = city_cli.sparkline([0, 5, 10])
        self.assertEqual((line[0], line[-1]), ("▁", "█"))
        self.assertIn("a  b", city_cli.table([["1", "2"]], ["a", "b"]).splitlines()[0])

    def test_exec_mode_runs_commands_through_mcp(self):
        city = FakeCity()
        try:
            result = subprocess.run([sys.executable, str(PROJECT / "tools" / "mcp" / "city_cli.py"), "--port", str(city.port), "--show-protocol",
                                     "--exec", 'read_sensor(id="WGT-T001", once=True)', "--exec", 'read_sensor(id="AIR-01", duration_s=0.6, interval_s=0.2)', "--exec", "set_power_switch id=SWT-01 state=open"],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertIn('"method":"initialize"', out)
            self.assertIn('"method":"tools/call"', out)
            self.assertIn("bin_weight", out)
            self.assertIn("SWT-01 open", out)
            self.assertGreaterEqual(out.count("pm2_5"), 3)          # several streamed updates
            self.assertIn("live updates", out)
            self.assertFalse(city.switch_closed)
        finally:
            city.close()

    def test_every_example_references_a_real_tool(self):
        city = FakeCity()
        client = city_cli.MCPClient([sys.executable, SERVER], env=dict(os.environ, CITY_SIM_API_PORT=str(city.port)))
        try:
            client.initialize()
            names = {t["name"] for t in client.tools}
            for title, line in city_cli.EXAMPLES:
                if line.startswith("raw "):
                    continue
                call = city_cli.parse_call(line)
                self.assertIsNotNone(call, line)
                self.assertIn(call[0], names, title)
                schema = next(t["inputSchema"] for t in client.tools if t["name"] == call[0])
                for key in call[2]:
                    self.assertTrue(key in schema["properties"] or key in city_cli.TERMINAL_ARGS, f"{line}: unknown argument {key}")
        finally:
            client.close()
            city.close()


if __name__ == "__main__":
    unittest.main()

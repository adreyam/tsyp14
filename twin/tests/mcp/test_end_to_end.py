"""End to end: the real Godot city (headless control room) <- device API socket <- MCP server <- MCP client.
Every assertion below is answered by the running simulation; there is no stand-in anywhere in the chain."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "tools" / "mcp"))

import city_cli  # noqa: E402

GODOT = PROJECT / "runtime" / "linux-x86_64" / "godot"
SERVER = str(PROJECT / "tools" / "mcp" / "city_mcp_server.py")


@unittest.skipUnless(GODOT.exists(), "the bundled Godot runtime is not present")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ready = Path(tempfile.mkdtemp()) / "api_ready"
        env = dict(os.environ, CITY_SIM_API_PORT="0", CITY_SIM_API_READY_FILE=str(cls.ready))
        cls.log = tempfile.NamedTemporaryFile("w+", suffix=".log")
        cls.godot = subprocess.Popen([str(GODOT), "--headless", "--path", str(PROJECT), "--scene", "res://scenes/ControlRoom.tscn"],
                                     env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and not (cls.ready.exists() and cls.ready.read_text().strip()):
            if cls.godot.poll() is not None:
                raise RuntimeError("the city exited during startup")
            time.sleep(0.5)
        port = cls.ready.read_text().strip()
        probe = city_cli.MCPClient([sys.executable, SERVER], env=dict(os.environ, CITY_SIM_API_PORT=port))
        probe.initialize()
        until = time.monotonic() + 60
        while time.monotonic() < until:      # the street-bin sensors are fitted a moment after startup
            waste = probe.call_tool("city_status", {})["structuredContent"]["families"].get("waste", 0)
            if waste >= 100:
                break
            time.sleep(0.5)
        probe.close()
        cls.client = city_cli.MCPClient([sys.executable, SERVER], env=dict(os.environ, CITY_SIM_API_PORT=port))
        cls.client.initialize()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.godot.terminate()
        try:
            cls.godot.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cls.godot.kill()
        cls.log.close()

    def data(self, tool, **arguments):
        result = self.client.call_tool(tool, arguments)
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return result["structuredContent"]

    def test_status_counts_every_family(self):
        status = self.data("city_status")
        for family in ["waste", "signal", "power_switch", "water_valve", "lighting", "irrigation", "reservoir", "air", "traffic", "camera"]:
            self.assertGreater(status["families"].get(family, 0), 0, family)
        self.assertGreater(status["device_count"], 500)

    def test_bin_weight_sensor_follows_the_bin(self):
        first = self.data("list_sensors", family="waste", limit=200)["devices"]
        can = next(d for d in first if d["id"].startswith("WGT-T"))
        read = self.data("read_sensor", id=can["id"])
        self.assertEqual(set(read["readings"]), {"bin_weight", "fill_level", "deposits"})
        self.data("empty_waste_bin", id=can["id"])
        emptied = self.data("read_sensor", id=can["id"])
        self.assertLessEqual(emptied["readings"]["bin_weight"]["value"], 0.21)
        report = self.data("waste_bin_report", limit=5)
        self.assertEqual(len(report["bins"]), 5)
        self.assertGreaterEqual(report["bins"][0]["fill_percent"], report["bins"][-1]["fill_percent"])

    def test_actuators_through_mcp_change_the_simulation(self):
        before = self.data("read_sensor", id="SWT-01")["readings"]["switch_closed"]["value"]
        after = self.data("set_power_switch", id="SWT-01", state="open")["after"]["switch_closed"]["value"]
        self.assertEqual((before, after), (100.0, 0.0))
        self.assertEqual(self.data("read_sensor", id="SWT-01")["readings"]["powered"]["value"], 0.0)
        self.data("set_power_switch", id="SWT-01", state="close")
        self.assertEqual(self.data("read_sensor", id="SWT-01")["readings"]["switch_closed"]["value"], 100.0)
        self.assertEqual(self.data("set_street_light", id="LGT-002", mode="on")["after"]["light_level"]["value"], 100.0)
        self.data("set_street_light", id="LGT-002", mode="auto")

    def test_weather_changes_the_weather_stations(self):
        self.data("set_weather", weather="storm")
        self.assertEqual(self.data("city_status")["weather"], "storm")
        wind = self.data("read_sensor", id="WX-01")["readings"]["wind_speed"]["value"]
        self.assertGreater(wind, 8.0)
        self.data("set_weather", weather="clear")
        calm = self.data("read_sensor", id="WX-01")["readings"]["wind_speed"]["value"]
        self.assertLess(calm, wind)

    def test_watch_sensor_returns_a_live_series(self):
        series = self.data("watch_sensor", id="TRAF-01", metric="vehicle_count", duration_s=1.5, interval_s=0.5)
        self.assertGreaterEqual(len(series["samples"]), 3)
        frames = [s["frame"] for s in series["samples"]]
        self.assertEqual(frames, sorted(frames))
        self.assertGreater(frames[-1], frames[0])

    def test_subscription_streams_the_live_city_until_cancelled(self):
        can = next(d for d in self.data("list_sensors", family="waste", limit=200)["devices"] if d["id"].startswith("WGT-T"))["id"]
        seen = []
        request_id = self.client.last_request_id + 1

        def on_progress(message):
            seen.append(json.loads(message["params"]["message"]))
            if len(seen) == 3:
                self.client.cancel(request_id, "test")

        result = self.client.call_tool("read_sensor", {"id": can, "interval_s": 0.2}, on_progress=on_progress)
        self.assertTrue(result["structuredContent"]["cancelled"])
        frames = [u["data"]["sampled_at"]["frame"] for u in seen]
        self.assertEqual(frames, sorted(frames))
        self.assertGreater(frames[-1], frames[0])
        self.assertEqual(set(seen[0]["data"]["readings"]), {"bin_weight", "fill_level", "deposits"})

    def test_one_function_call_streams_live_city_data(self):
        """Plain function calling, as any MCP client would do it: no loop on our side."""
        result = self.client.call_tool("read_sensor", {"id": "TRAF-01", "duration_s": 1.5, "interval_s": 0.5})
        data = result["structuredContent"]
        self.assertGreaterEqual(len(data["samples"]), 3)
        frames = [s["data"]["sampled_at"]["frame"] for s in data["samples"]]
        self.assertGreater(frames[-1], frames[0])

    def test_a_subscribed_resource_is_pushed_when_the_city_changes(self):
        uri = "city://sensor/LGT-003"
        self.client.request("resources/subscribe", {"uri": uri})
        pushed = []
        collect = lambda m: pushed.append(m) if m.get("method") == "notifications/resources/updated" else None
        try:
            self.client.request("ping", on_notification=collect)
            time.sleep(1.3)
            self.data("set_street_light", id="LGT-003", mode="on")           # the city changes
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not any(m["params"]["uri"] == uri for m in pushed):
                self.client.request("ping", on_notification=collect)
                time.sleep(0.3)
            self.assertTrue(any(m["params"]["uri"] == uri for m in pushed))
            reading = json.loads(self.client.request("resources/read", {"uri": uri})["contents"][0]["text"])
            self.assertEqual(reading["readings"]["light_level"]["value"], 100.0)
        finally:
            self.client.request("resources/unsubscribe", {"uri": uri})
            self.data("set_street_light", id="LGT-003", mode="auto")

    def test_zones_seismic_sensors_and_earthquakes(self):
        zones = self.data("list_zones")
        self.assertEqual(zones["count"], 16)
        shown = self.data("show_zones", zones=["B", "D"])
        self.assertEqual(shown["shown_zones"], ["B", "D"])
        self.assertEqual(self.data("show_zones", zones=["ALL"], visible=False)["shown_zones"], [])
        zone = self.data("read_zone", zone="F", once=True)
        self.assertGreater(zone["buildings"], 5)
        self.assertIn("seismic", zone["devices_by_family"])
        quiet = self.data("read_sensor", id="SEIS-06", once=True)
        self.assertEqual(quiet["zone"], "F")
        self.assertEqual(len(quiet["waveform"]["components"]["vertical_z"]), 40)
        event = self.data("trigger_earthquake", zone="F", magnitude=6.6)
        self.assertEqual(event["earthquake"]["zone"], "F")
        time.sleep(3.0)
        shaking = self.data("read_sensor", id="SEIS-06", once=True)
        self.assertGreater(shaking["readings"]["ground_acceleration"]["value"], 10 * max(quiet["readings"]["ground_acceleration"]["value"], 0.001))
        self.assertGreater(self.data("list_earthquakes", once=True)["active"], 0)
        self.assertGreaterEqual(self.data("list_buildings", zone="F", limit=100)["total"], 5)
        cleared = self.data("clear_earthquakes")
        self.assertEqual(cleared["result"]["cleared_events"], 1)
        self.assertEqual(self.data("list_earthquakes", once=True)["earthquakes"], [])
        self.assertEqual(self.data("list_buildings", collapsed_only=True)["total"], 0)

    def test_parking_lots_through_mcp(self):
        lots = self.data("list_parking_lots")
        self.assertGreaterEqual(lots["count"], 6)
        lot_id = lots["lots"][0]["id"]
        self.assertEqual(self.data("read_parking_lot", id=lot_id, once=True)["capacity"], 12)
        applied = self.data("set_parking_barrier", id=lot_id, mode="closed")
        self.assertEqual(applied["after"]["capacity"]["value"], 12.0)
        self.assertEqual(self.data("read_parking_lot", id=lot_id, once=True)["barrier_mode"], "closed")
        self.data("set_parking_barrier", id=lot_id, mode="auto")
        self.assertEqual(self.data("read_sensor", id=lot_id, once=True)["family"], "parking")

    def test_vehicles_citizens_incidents_and_units_are_reachable(self):
        self.assertIn("vehicles", self.data("list_vehicles"))
        citizens = self.data("list_citizens", limit=3)
        self.assertGreater(len(citizens["citizens"]), 0)
        first = citizens["citizens"][0]["id"]
        self.assertEqual(self.data("read_citizen", id=first, once=True)["id"], first)
        self.assertIn("incidents", self.data("list_incidents"))
        self.assertIn("units", self.data("list_emergency_units"))
        self.assertTrue(self.client.call_tool("read_vehicle", {"id": "nothing", "once": True})["isError"])

    def test_the_web_dashboard_sees_the_live_city_through_mcp_only(self):
        from test_dashboard import BrowserLikeClient, INIT
        port = 19400 + os.getpid() % 400
        dashboard = subprocess.Popen([sys.executable, str(PROJECT / "tools" / "dashboard" / "dashboard_server.py"), "--port", str(port)],
                                     env=dict(os.environ, CITY_SIM_API_PORT=self.ready.read_text().strip()), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        browser = None
        try:
            for _ in range(60):
                try:
                    browser = BrowserLikeClient(port)
                    break
                except OSError:
                    time.sleep(0.2)
            self.assertIsNotNone(browser)
            browser.request("initialize", INIT)
            browser.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            # The live view: one MCP stream carrying every device.
            stream_id = browser.next_id + 1
            browser.post({"jsonrpc": "2.0", "id": stream_id, "method": "tools/call", "params": {"name": "read_all", "arguments": {"interval_s": 0.5}, "_meta": {"progressToken": "s"}}})
            browser.next_id = stream_id
            first = None
            deadline = time.monotonic() + 20
            while first is None and time.monotonic() < deadline:
                message = browser.next_message(20)
                if message.get("method") == "notifications/progress":
                    first = json.loads(message["params"]["message"])["data"]
            self.assertGreater(first["count"], 500)
            self.assertGreaterEqual({d["family"] for d in first["devices"]} & {"seismic", "parking", "waste", "signal", "reservoir"}, {"seismic", "parking", "waste", "signal", "reservoir"})
            # A command sent through the same MCP link shows up in the live stream.
            sent = browser.request("tools/call", {"name": "set_street_light", "arguments": {"id": "LGT-005", "mode": "on"}})
            self.assertFalse(sent["result"]["isError"])
            lit = False
            deadline = time.monotonic() + 15
            while not lit and time.monotonic() < deadline:
                message = browser.next_message(15)
                if message.get("method") == "notifications/progress":
                    devices = json.loads(message["params"]["message"])["data"]["devices"]
                    lit = any(d["id"] == "LGT-005" and d["readings"]["light_level"]["value"] == 100.0 for d in devices)
            self.assertTrue(lit, "the dashboard's stream did not show the light that was just switched on")
            browser.request("tools/call", {"name": "set_street_light", "arguments": {"id": "LGT-005", "mode": "auto"}})
        finally:
            if browser is not None:
                browser.close()
            dashboard.terminate()
            dashboard.wait(timeout=10)
            if dashboard.stdout:
                dashboard.stdout.close()

    def test_unknown_things_are_clean_errors(self):
        self.assertTrue(self.client.call_tool("read_sensor", {"id": "NOPE"})["isError"])
        self.assertTrue(self.client.call_tool("command_actuator", {"id": "AIR-01", "command": "open"})["isError"])
        self.assertTrue(self.client.call_tool("set_service_zone", {"id": "ZONE-missing", "service": "power", "state": "cut"})["isError"])


if __name__ == "__main__":
    unittest.main()

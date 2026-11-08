"""The web dashboard is an MCP client. These tests drive its HTTP+SSE transport exactly as the page's JavaScript does and check
that (a) the static page is served, (b) the transport relays MCP unchanged (initialize, tools/list, one-shot and streaming calls with
progress notifications, cancellation), and (c) it offers no other way to reach the simulation."""
from __future__ import annotations

import http.client
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_city import FakeCity  # noqa: E402

SERVER = PROJECT / "tools" / "dashboard" / "dashboard_server.py"
INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}


class BrowserLikeClient:
    """What app.js does: open /mcp/sse, learn the endpoint, POST JSON-RPC messages, read the `message` events."""

    def __init__(self, port: int):
        self.port = port
        self.events: queue.Queue = queue.Queue()
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.connection.request("GET", "/mcp/sse")
        self.response = self.connection.getresponse()
        assert self.response.status == 200 and self.response.getheader("Content-Type").startswith("text/event-stream")
        threading.Thread(target=self._read, daemon=True).start()
        kind, data = self.events.get(timeout=10)
        assert kind == "endpoint", kind
        self.endpoint = data
        self.next_id = 0

    def _read(self):
        event, data = None, []
        while True:
            try:
                line = self.response.fp.readline()
            except (OSError, ValueError):
                return
            if not line:
                return
            line = line.decode().rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and event:
                self.events.put((event, "\n".join(data)))
                event, data = None, []

    def post(self, message: dict) -> int:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", self.endpoint, json.dumps(message), {"Content-Type": "application/json"})
        status = connection.getresponse().status
        connection.close()
        return status

    def next_message(self, timeout: float = 10.0) -> dict:
        kind, data = self.events.get(timeout=timeout)
        return json.loads(data)

    def request(self, method: str, params: dict | None = None, side: list | None = None) -> dict:
        self.next_id += 1
        request_id = self.next_id
        assert self.post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}) == 202
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            message = self.next_message(20)
            if message.get("id") == request_id:
                return message
            if side is not None:
                side.append(message)
        raise TimeoutError(method)

    def close(self):
        try:
            self.response.fp.raw._sock.shutdown(2)   # what a closed browser tab does: the server sees the connection end
        except (OSError, AttributeError):
            pass
        self.response.close()
        self.connection.close()


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.city = FakeCity()
        cls.port = 18870 + os.getpid() % 500
        cls.process = subprocess.Popen([sys.executable, str(SERVER), "--port", str(cls.port)], env=dict(os.environ, CITY_SIM_API_PORT=str(cls.city.port)),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(60):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=2)
                connection.request("GET", "/health")
                if connection.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        if cls.process.stdout:
            cls.process.stdout.close()
        cls.city.close()

    def get(self, path: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), body

    def test_the_page_and_its_assets_are_served(self):
        status, kind, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", kind)
        self.assertIn(b"City Ground Zero", body)
        self.assertEqual(self.get("/app.js")[0], 200)
        for asset in ["/charts.js", "/jsonview.js", "/views.js", "/views2.js"]:
            self.assertEqual(self.get(asset)[0], 200, asset)
        self.assertEqual(self.get("/style.css")[0], 200)
        status, kind, body = self.get("/logo.png")
        self.assertEqual((status, kind, body[:4]), (200, "image/png", b"\x89PNG"))

    def test_the_server_serves_nothing_outside_the_web_folder(self):
        for path in ["/../dashboard_server.py", "/..%2f..%2fmcp/city_mcp_server.py", "/%2e%2e/%2e%2e/project.godot", "/nothing.js"]:
            self.assertEqual(self.get(path)[0], 404, path)

    def test_the_page_reaches_the_city_only_through_mcp(self):
        html = self.get("/")[2].decode()
        scripts = re.findall(r'<script src="([^"]+\.js)"', html)
        self.assertEqual(scripts, ["/charts.js", "/jsonview.js", "/app.js", "/views.js", "/views2.js"])
        text = "\n".join(self.get(path)[2].decode() for path in scripts)
        self.assertIn("/mcp/sse", text)
        self.assertNotIn("WebSocket", text)
        self.assertNotIn("8820", text)                      # never the device API itself
        for part in text.split("fetch(")[1:]:
            self.assertTrue(part.lstrip().startswith("this.endpoint"), "the page fetches something that is not the MCP endpoint")
        for tool_call in ["read_city", "set_weather", "set_time_of_day", "set_simulation_speed", "set_camera", "follow", "highlight_assets", "trigger_incident", "trigger_earthquake", "set_power_switch", "set_valve", "set_parking_barrier", "empty_waste_bin"]:
            self.assertIn(f'"{tool_call}"', text)

    def test_mcp_handshake_through_the_transport(self):
        client = BrowserLikeClient(self.port)
        try:
            self.assertTrue(client.endpoint.startswith("/mcp/message?session="))
            init = client.request("initialize", INIT)
            self.assertEqual(init["result"]["serverInfo"]["name"], "city-ground-zero")
            self.assertEqual(client.post({"jsonrpc": "2.0", "method": "notifications/initialized"}), 202)
            tools = {t["name"] for t in client.request("tools/list")["result"]["tools"]}
            self.assertTrue({"read_all", "city_status", "list_vehicles", "list_parking_lots", "list_zones", "waste_bin_report"} <= tools)
        finally:
            client.close()

    def test_live_data_streams_through_progress_notifications_and_can_be_cancelled(self):
        client = BrowserLikeClient(self.port)
        try:
            client.request("initialize", INIT)
            self.city.bins["WGT-T001"] = 3.0
            stream_id = client.next_id + 1
            # Two things at once on one connection: a long stream and a one-shot call that must not wait for it.
            client.post({"jsonrpc": "2.0", "id": stream_id, "method": "tools/call",
                         "params": {"name": "read_all", "arguments": {"interval_s": 0.2}, "_meta": {"progressToken": "t1"}}})
            client.next_id = stream_id
            side: list = []
            one_shot = client.request("tools/call", {"name": "city_status", "arguments": {"once": True}}, side=side)
            self.assertFalse(one_shot["result"]["isError"])
            progress: list = [m for m in side if m.get("method") == "notifications/progress"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(progress) < 3:
                message = client.next_message(10)
                if message.get("method") == "notifications/progress":
                    progress.append(message)
            self.assertGreaterEqual(len(progress), 3)
            first = json.loads(progress[0]["params"]["message"])
            self.assertEqual(first["tool"], "read_all")
            self.assertGreater(first["data"]["count"], 0)
            self.city.bins["WGT-T001"] = 9.5
            changed = False
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not changed:
                message = client.next_message(10)
                if message.get("method") == "notifications/progress":
                    devices = json.loads(message["params"]["message"])["data"]["devices"]
                    changed = any(d["id"] == "WGT-T001" and d["readings"]["bin_weight"]["value"] == 9.5 for d in devices)
            self.assertTrue(changed, "the stream did not follow the city")
            client.post({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": stream_id, "reason": "test"}})
            final = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and final is None:
                message = client.next_message(10)
                if message.get("id") == stream_id:
                    final = message
            self.assertIsNotNone(final, "the cancelled stream never answered")
            self.assertTrue(final["result"]["structuredContent"]["cancelled"])
        finally:
            client.close()

    def test_transport_rejects_bad_requests(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("POST", "/mcp/message?session=nope", "{}", {"Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().status, 404)
        client = BrowserLikeClient(self.port)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            connection.request("POST", client.endpoint, "not json", {"Content-Type": "application/json"})
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            client.close()

    def test_sessions_close_with_their_browser_connections(self):
        client = BrowserLikeClient(self.port)
        client.request("initialize", INIT)
        client.close()
        for _ in range(40):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
            connection.request("GET", "/health")
            if json.loads(connection.getresponse().read())["sessions"] == 0:
                return
            time.sleep(0.3)
        self.fail("dashboard sessions did not close")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_page_script_parses(self):
        result = subprocess.run(["node", "--check", str(PROJECT / "tools" / "dashboard" / "web" / "app.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_start_script_accepts_the_dashboard_flag(self):
        script = (PROJECT / "run_control_room.sh").read_text()
        for needle in ["--dashboard)", "--dashboard=*)", "--no-browser)", "dashboard_server.py", "CITY_DASHBOARD_URL"]:
            self.assertIn(needle, script)
        self.assertEqual(subprocess.run(["bash", "-n", str(PROJECT / "run_control_room.sh")]).returncode, 0)


if __name__ == "__main__":
    unittest.main()

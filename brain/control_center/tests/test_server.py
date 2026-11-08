"""Control center tests: feed, event mapping, overview with every backend down, HTTP routes,
and the live check against running backends (skipped when they are not up).

Run from control_center/:   python -m unittest discover -s tests -v
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

DEAD = {"listen": {"host": "127.0.0.1", "port": 0}, "backends": {
    "waste_air": {"url": "http://127.0.0.1:9", "console": "/timeline", "controls_url": "http://127.0.0.1:9",
                  "token_env": "CC_TEST_NO_TOKEN"},
    "energy": {"url": "http://127.0.0.1:9", "console": "/"},
    "transport": {"url": "http://127.0.0.1:9", "console": "/"}}}


class FeedTest(unittest.TestCase):
    def test_repeats_collapse_and_since(self):
        f = server.Feed(size=10)
        f.add("waste", "info", "Bin b01 emptied")
        f.add("waste", "info", "Bin b01 emptied")
        f.add("air", "watch", "Zone A air quality warning (risk 71)")
        self.assertEqual(len(f.items), 2)
        self.assertEqual(f.items[0]["count"], 2)
        self.assertEqual([i["axis"] for i in f.since(0, 50, {"air"})], ["air"])
        self.assertEqual(f.since(3, 50, None), [])

    def test_interleaved_repeat_moves_to_end(self):
        f = server.Feed(size=10)
        f.add("air", "alert", "Reading from AIR-05 ignored")
        f.add("energy", "info", "Z1 back within limits")
        f.add("air", "alert", "Reading from AIR-05 ignored")
        self.assertEqual([i["text"] for i in f.items], ["Z1 back within limits", "Reading from AIR-05 ignored"])
        self.assertEqual(f.items[-1]["count"], 2)
        self.assertEqual(f.items[-1]["replaces"], 1)

    def test_ring_buffer(self):
        f = server.Feed(size=3)
        for i in range(5):
            f.add("energy", "info", f"event {i}")
        self.assertEqual([i["text"] for i in f.items], ["event 2", "event 3", "event 4"])


class WasteAirMappingTest(unittest.TestCase):
    def test_events(self):
        m = server.waste_air_item
        self.assertEqual(m({"event": "dispatch", "truck_id": "t1", "stop": "b04", "color": "orange"})[:3],
                         ("waste", "watch", "Truck t1 sent to b04, orange"))
        self.assertEqual(m({"event": "air_level", "zone": "B", "level": "high", "risk": 105})[:2], ("air", "alert"))
        self.assertEqual(m({"event": "reading_blocked", "axis": "air", "sensor_id": "AIR-05", "reason": "spike"})[0], "air")
        self.assertEqual(m({"event": "air_action", "zone": "B", "action": "close_parking", "target": "LOT-02"})[2],
                         "Zone B: car park closed LOT-02")
        self.assertIsNone(m({"event": "bin_status", "sensor_id": "b01", "color": "green"}))
        self.assertIsNone(m({"event": "something_new"}))


class OfflineAppTest(unittest.TestCase):
    """Every backend down: the control center still answers and says so."""

    @classmethod
    def setUpClass(cls):
        cls.app = server.App(json.loads(json.dumps(DEAD)))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_page_and_static(self):
        code, ctype, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"City Brain", body)
        for f in ("/static/app.js", "/static/app.css"):
            self.assertEqual(self.get(f)[0], 200)

    def test_no_path_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/static/../server.py")
        self.assertEqual(c.exception.code, 404)

    def test_overview_all_offline(self):
        _, _, body = self.get("/api/overview")
        ov = json.loads(body)
        self.assertEqual({v["status"] for v in ov["axes"].values()}, {"offline"})
        self.assertIn("No axis is reachable", ov["sentence"])

    def test_feed_and_config(self):
        feed = json.loads(self.get("/api/feed?after=0&limit=10")[2])
        self.assertIn("items", feed)
        cfg = json.loads(self.get("/api/config")[2])
        self.assertTrue(cfg["consoles"]["waste_air"].endswith("/timeline"))

    def test_unknown_control_refused(self):
        code, _ = self.post("/api/control/shell.exec", {"cmd": "rm"})
        self.assertEqual(code, 404)

    def test_bad_control_id_refused(self):
        code, _ = self.post("/api/control/energy.scenario/..%2F..%2Fadmin", {})
        self.assertIn(code, (400, 404))

    def test_agent_call_when_backend_down(self):
        code, res = self.post("/api/agents/transport/call", {"tool": "get_overview", "arguments": {}})
        self.assertIn(code, (200, 502, 503))
        self.assertFalse(res.get("ok", False))

    def test_testbook_routes(self):
        code, _ = self.post("/api/transport/testbook/..%2Fadmin/run", {})
        self.assertEqual(code, 404)
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/api/transport/testbook")
        self.assertEqual(c.exception.code, 502)          # transport Brain down

    def test_unknown_agent(self):
        code, _ = self.post("/api/agents/nobody/call", {"tool": "x", "arguments": {}})
        self.assertEqual(code, 404)


def _up(url):
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except Exception:
        return False


@unittest.skipUnless(_up("http://127.0.0.1:8765/api/health"), "control center not running on 8765")
class LiveTest(unittest.TestCase):
    """With start_all running: every axis online and every agent answers a read tool."""

    def test_all_online(self):
        with urllib.request.urlopen("http://127.0.0.1:8765/api/overview", timeout=10) as r:
            ov = json.loads(r.read())
        self.assertEqual({k: v["online"] for k, v in ov["links"].items()},
                         {"waste_air": True, "energy": True, "transport": True})

    def test_agents_answer(self):
        for agent, tool in (("waste_air", "brain_status"), ("energy", "energy_get_status"), ("transport", "get_city_status")):
            req = urllib.request.Request(f"http://127.0.0.1:8765/api/agents/{agent}/call",
                                         json.dumps({"tool": tool, "arguments": {}}).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                self.assertTrue(json.loads(r.read())["ok"], agent)


if __name__ == "__main__":
    unittest.main()


class ScenarioTest(unittest.TestCase):
    def test_catalog_shape(self):
        import re
        import scenarios
        by_axis = {}
        for sc in scenarios.CATALOG:
            by_axis.setdefault(sc["axis"], []).append(sc["id"])
            self.assertRegex(sc["id"], r"^[A-Z][0-9]$")
            for st in sc["steps"] + sc["end"]:
                a = st["action"]
                if a["type"] == "control":
                    self.assertIn(a["name"], server.CONTROL_ROUTES, sc["id"])
                    if "{id}" in server.CONTROL_ROUTES[a["name"]][2]:
                        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", a["id"] or ""))
                else:
                    self.assertIn(a["agent"], ("waste_air", "energy", "transport"))
        self.assertEqual({k: len(v) for k, v in by_axis.items()},
                         {"waste": 3, "air": 3, "energy": 3, "transport": 3, "city": 2})
        city = [sc for sc in scenarios.CATALOG if sc["axis"] == "city"]
        for sc in city:
            self.assertEqual({st["axis"] for st in sc["steps"]}, {"waste", "air", "energy", "transport"})

    def test_runner_plays_steps_then_undoes(self):
        import time
        import scenarios
        calls = []
        sc = {"id": "Z9", "axis": "waste", "title": "Test", "watch": "", "duration": 0.6,
              "steps": [scenarios.step(0, "waste", "{bin} first", scenarios.ctl("waste_air.control", action="spoof", sensor_id=scenarios.EMPTIEST)),
                        scenarios.step(0.2, "transport", "second", scenarios.agent("transport", "dispatch_emergency", kind="fire", lat=0, lon=0))],
              "end": [scenarios.step(0, "waste", "undo", scenarios.ctl("waste_air.control", action="unspoof", sensor_id=scenarios.EMPTIEST))]}
        feed = server.Feed()
        state = {"bins": {"b01": {"fill_pct": 60}, "b02": {"fill_pct": 5}, "b03": {"fill_pct": 1}},
                 "blocked_bins": {"b03": "silent"}}
        r = scenarios.Runner(lambda n, i, b: calls.append(("control", b["action"], b.get("sensor_id"))),
                             lambda a, t, args: (calls.append(("agent", t)), {"ok": True})[1], feed, lambda: state)
        r.by_id["Z9"] = sc
        self.assertEqual(r.start("Z9")["state"], "running")
        time.sleep(1.2)
        # the emptiest trusted bin is b02 (b03 is emptier but already distrusted)
        self.assertEqual(calls, [("control", "spoof", "b02"), ("agent", "dispatch_emergency"), ("control", "unspoof", "b02")])
        v = r.view("Z9")
        self.assertEqual(v["done"][0]["label"], "b02 first")
        self.assertEqual(v["state"], "done")
        self.assertTrue(all(d["ok"] for d in v["done"]))

    def test_refused_step_is_reported(self):
        import time
        import scenarios
        sc = {"id": "Z8", "axis": "air", "title": "T", "watch": "", "duration": 0.3,
              "steps": [scenarios.step(0, "air", "boom", scenarios.ctl("waste_air.control", action="air_stop"))], "end": []}
        def control(*_):
            raise RuntimeError("Twin unreachable")
        r = scenarios.Runner(control, lambda *a: {"ok": True}, server.Feed())
        r.by_id["Z8"] = sc
        r.start("Z8")
        time.sleep(0.8)
        d = r.view("Z8")["done"][0]
        self.assertFalse(d["ok"])
        self.assertIn("Twin unreachable", d["error"])

"""
City Brain control center: one dashboard for every axis (waste, air quality,
energy, transport), standard library only.

    python control_center/server.py                 then open http://127.0.0.1:8765
    python control_center/server.py --config control_center/config.json --port 8765

What it does
  - watches the three axis backends in the background:
        waste and air quality   GET /v1/state, /v1/events          (brain/axes/pollution/engine/brain)
        energy                  Server-Sent Events /api/stream      (brain/axes/energy/engine/brain, sim.server)
        transport               GET /api/state, /api/v1/timeline    (brain/axes/transportation/engine/brain, citybrain.server)
        City Brain orchestrator GET /v1/status, /v1/top, /v1/map, /v1/timeline   (brain/run_brain.py, the Twin-driven
                                orchestrator with its four agents; live or mock source)
  - turns them into one situation (what needs attention, and why) and one decision feed;
  - lists the tools of the three agents and relays tool calls to them
        waste/air over HTTP (Bearer token), transport over HTTP (X-Agent-Key), energy over MCP;
  - relays a short whitelist of demo controls (scenarios, injections, simulation speed);
  - serves the dashboard (control_center/static).

It holds no city data of its own: every number comes from an axis backend. If a
backend is down, its part of the dashboard says so and the rest keeps working.
"""
import argparse
import scenarios as scenario_lib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
SEVERITY = {"info": 0, "watch": 1, "alert": 2, "critical": 3}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- configuration
DEFAULT_CONFIG = {
    "listen": {"host": "127.0.0.1", "port": 8765},
    "backends": {
        "waste_air": {"url": "http://127.0.0.1:8100", "console": "/timeline",
                      "controls_url": "http://127.0.0.1:8200",
                      "token_env": "ORCHESTRATOR_TOKEN"},
        "energy": {"url": "http://127.0.0.1:8400", "console": "/", "api_key_env": "ENERGY_AGENT_KEY"},
        "transport": {"url": "http://127.0.0.1:8000", "console": "/", "twin_url": "http://127.0.0.1:9001",
                      "agent_key_env": "AGENT_KEY", "agent_key_default": "dev-agent-key",
                      "admin_key_env": "PARKING_ADMIN_KEY", "admin_key_default": "dev-admin-key"},
        "brain": {"url": "http://127.0.0.1:8500", "token_env": "BRAIN_TOKEN", "token_default": "tsyp14-brain-dev-token"},
    },
}


def load_config(path: Optional[str]) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                for k2, v2 in v.items():
                    if isinstance(v2, dict) and isinstance(cfg[k].get(k2), dict):
                        cfg[k][k2].update(v2)
                    else:
                        cfg[k][k2] = v2
            elif not k.startswith("_"):
                cfg[k] = v
    return cfg


# ---------------------------------------------------------------- small HTTP helpers
class Refused(Exception):
    """A request the control center itself refuses (unknown control, bad id): 4xx, not 502."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Upstream(Exception):
    pass


def http_json(method: str, url: str, body=None, headers: Optional[dict] = None,
              timeout: float = 4.0, raw: bool = False):
    data = None if body is None else json.dumps(body).encode()
    h = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return (r, payload) if raw else (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        text = e.read()[:600].decode("utf-8", "replace")
        try:
            detail = json.loads(text)
        except ValueError:
            detail = text
        raise Upstream(json.dumps({"status": e.code, "detail": detail}))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Upstream(f"unreachable: {getattr(e, 'reason', e)}")


class Link:
    """Health of one backend link: online/offline with the last error and timing."""

    def __init__(self, name: str):
        self.name = name
        self.online = False
        self.since = now_iso()
        self.last_ok: Optional[str] = None
        self.error: Optional[str] = None
        self.latency_ms: Optional[float] = None

    def ok(self, ms: float) -> None:
        if not self.online:
            self.since = now_iso()
        self.online, self.error, self.last_ok, self.latency_ms = True, None, now_iso(), round(ms, 1)

    def fail(self, err: str) -> None:
        if self.online:
            self.since = now_iso()
        self.online, self.error = False, err[:200]

    def view(self) -> dict:
        return {"online": self.online, "since": self.since, "last_ok": self.last_ok,
                "error": self.error, "latency_ms": self.latency_ms}


# ---------------------------------------------------------------- the feed
class Feed:
    """One stream of decisions and alerts across the axes, newest last."""

    def __init__(self, size: int = 1500):
        self.items: deque = deque(maxlen=size)
        self.seq = 0
        self.lock = threading.Lock()

    def add(self, axis: str, severity: str, text: str, wall: Optional[str] = None,
            ref: Optional[str] = None, kind: Optional[str] = None) -> None:
        with self.lock:
            # Collapse repeats: the same message from the same axis among the last 12 items is moved
            # to the end with its count raised (the page drops the copy it had, via "replaces").
            count, replaces = 1, None
            for old in list(self.items)[-12:]:
                if old["axis"] == axis and old["text"] == text:
                    count, replaces = old["count"] + 1, old["seq"]
                    self.items.remove(old)
                    break
            self.seq += 1
            self.items.append({"seq": self.seq, "wall": wall or now_iso(), "axis": axis,
                               "severity": severity, "text": text, "ref": ref, "kind": kind,
                               "count": count, "replaces": replaces})

    def since(self, after: int, limit: int, axes: Optional[set]) -> List[dict]:
        with self.lock:
            out = [i for i in self.items if i["seq"] > after and (not axes or i["axis"] in axes)]
        return out[-limit:]


# ---------------------------------------------------------------- waste and air quality
def waste_air_item(e: dict):
    """(axis, severity, text, ref) for one event of the waste/air Brain, or None."""
    ev = e.get("event")
    sid = e.get("sensor_id")
    if ev == "dispatch":
        who = "A crew" if e.get("truck_id") == "crew" else f"Truck {e.get('truck_id')}"
        color = f", {e['color']}" if e.get("color") else ""
        return "waste", "watch", f"{who} sent to {e.get('stop')}{color}", e.get("stop")
    if ev == "bin_status":
        if e.get("emptied"):
            return "waste", "info", f"Bin {sid} emptied", sid
        if e.get("color") == "red":
            return "waste", "alert", f"Bin {sid} is full: {e.get('reason')}", sid
        if e.get("color") == "orange":
            return "waste", "watch", f"Bin {sid} will be full before the next pass: {e.get('reason')}", sid
        return None
    if ev == "reading_blocked":
        axis = "air" if e.get("axis") == "air" else "waste"
        return axis, "alert", f"Reading from {sid} ignored: {e.get('reason')}", sid
    if ev == "air_level":
        sev = {"critical": "critical", "high": "alert", "warning": "watch"}.get(e.get("level"), "info")
        return "air", sev, f"Zone {e.get('zone')} air quality {e.get('level')} (risk {e.get('risk')})", e.get("zone")
    if ev == "air_action":
        words = {"sensitive_alert": "alert sent to schools and clinics",
                 "public_alert": "public alert issued", "close_parking": "car park closed",
                 "reopen_parking": "car park reopened",
                 "lift_sensitive_alert": "alert to schools and clinics lifted",
                 "lift_public_alert": "public alert lifted"}
        what = words.get(e.get("action"), e.get("action"))
        target = f" {e['target']}" if e.get("target") else ""
        sev = "alert" if e.get("action") in ("close_parking", "public_alert") else "info"
        return "air", sev, f"Zone {e.get('zone')}: {what}{target}", e.get("zone")
    if ev == "air_zone_unknown":
        return "air", "alert", f"Zone {e.get('zone')} has no trusted sensor: level and actions kept", e.get("zone")
    if ev == "air_zone_known":
        return "air", "info", f"Zone {e.get('zone')} has trusted data again", e.get("zone")
    if ev == "guardian_unavailable":
        return "waste", "alert", "Guardian unreachable: waste and air readings are ignored", None
    if ev == "guardian_recovered":
        return "waste", "info", "Guardian reachable again", None
    if ev in ("twin_unavailable", "twin_unreachable"):
        return "waste", "watch", f"Twin refused or did not answer: {str(e.get('error'))[:120]}", None
    if ev == "rejected_message":
        return "waste", "watch", f"Malformed message rejected: {str(e.get('error'))[:140]}", e.get("id")
    if ev == "auth_refused":
        return "waste", "alert", f"Request with a wrong token refused ({e.get('path')})", None
    if ev == "agent_call" and e.get("kind") == "act":
        return "waste", "info", f"Orchestrator called {e.get('tool')}", None
    return None


class WasteAirWatcher(threading.Thread):
    def __init__(self, cfg: dict, feed: Feed):
        super().__init__(daemon=True, name="waste-air")
        self.url = cfg["url"].rstrip("/")
        self.feed = feed
        self.link = Link("waste_air")
        self.state: dict = {}
        self.after = 0
        self.risk: Dict[str, deque] = {}             # zone -> deque[(wall, risk, level)]
        self.sensors: Dict[str, dict] = {}           # latest air reading per sensor
        self.first = True

    def run(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                self.state = http_json("GET", self.url + "/v1/state")
                page = http_json("GET", f"{self.url}/v1/events?after={self.after}")
                evts = page.get("events", [])
                if evts and evts[0].get("seq", 0) < self.after:   # backend restarted
                    evts = []
                for e in evts:
                    self.after = max(self.after, e.get("seq", 0) + 1)
                    self._event(e)
                if not evts and self.state.get("city_time") is None and self.after > 0:
                    pass
                self.link.ok((time.monotonic() - t0) * 1000)
                self.first = False
            except Upstream as err:
                if self.link.online:
                    self.feed.add("waste", "alert", "Waste and air Brain unreachable", kind="link")
                self.link.fail(str(err))
                self.after = 0 if "unreachable" in str(err) else self.after
            time.sleep(1.5)

    def _event(self, e: dict) -> None:
        wall = e.get("wall")
        if e.get("event") == "air_zone_risk":
            dq = self.risk.setdefault(e["zone"], deque(maxlen=720))
            dq.append((wall or now_iso(), e.get("risk"), e.get("level")))
            return
        if e.get("event") == "air_reading":
            self.sensors[e["sensor_id"]] = {**{k: e.get(k) for k in ("zone", "pm25", "o3", "no2")},
                                            "wall": wall, "trusted": True}
            return
        if e.get("event") == "reading_blocked" and e.get("sensor_id") in self.sensors:
            self.sensors[e["sensor_id"]]["trusted"] = False
            self.sensors[e["sensor_id"]]["reason"] = e.get("reason")
        item = waste_air_item(e)
        if item:
            axis, sev, text, ref = item
            self.feed.add(axis, sev, text, wall=wall, ref=ref, kind=e.get("event"))


# ---------------------------------------------------------------- energy
ENERGY_SEVERITY = {
    "operator_alert": "critical", "risk_detected": "alert", "gate_blocked": "alert",
    "security": "alert", "degraded_mode": "alert", "consistency": "alert",
    "actuator_anomaly": "alert", "verdict_flapping": "alert",
    "decision": "watch", "command_sent": "watch", "proposal": "watch", "plan_deviation": "watch",
    "reading_suspect": "watch", "reading_rejected": "watch", "verdict_change": "watch",
    "sequence_reset": "watch", "partner_error": "watch",
    "verification": "info", "restoration": "info", "risk_cleared": "info",
    "learned_effect": "info", "orchestrator": "info", "priority_hint": "info",
    "scenario_marker": "info", "command_ack": "info",
}


class EnergyWatcher(threading.Thread):
    """Keeps one Server-Sent Events subscription to the energy simulation."""

    def __init__(self, cfg: dict, feed: Feed):
        super().__init__(daemon=True, name="energy")
        self.url = cfg["url"].rstrip("/")
        self.feed = feed
        self.link = Link("energy")
        self.city: dict = {}
        self.frame: dict = {}
        self.history: deque = deque(maxlen=600)      # compact frames for the charts
        self.health: dict = {}
        self.lock = threading.Lock()

    def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                self.health = http_json("GET", self.url + "/api/health")
                req = urllib.request.Request(self.url + "/api/stream",
                                             headers={"Accept": "text/event-stream"})
                t0 = time.monotonic()
                with urllib.request.urlopen(req, timeout=15) as r:
                    self.link.ok((time.monotonic() - t0) * 1000)
                    backoff = 1.0
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("data:"):
                            self._message(json.loads(line[5:]))
                            self.link.ok(self.link.latency_ms or 0)
            except Upstream as err:
                self._down(str(err))
            except (urllib.error.URLError, OSError, ValueError) as err:
                self._down(f"unreachable: {getattr(err, 'reason', err)}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)

    def _down(self, err: str) -> None:
        if self.link.online:
            self.feed.add("energy", "alert", "Energy Brain unreachable", kind="link")
        self.link.fail(err)

    def _message(self, m: dict) -> None:
        with self.lock:
            if m.get("type") == "reset":
                self.city = m.get("city", {})
                self.history.clear()
                self.scenario_done = False
                sc = self.city.get("scenario", {})
                self.feed.add("energy", "info", f"Energy scenario started: {sc.get('name', sc.get('id'))}",
                              kind="scenario")
                return
            if m.get("type") != "frame":
                return
            self.frame = m
            self.history.append({"t": m.get("t"), "wall": now_iso(),
                                 "zones": {z: {k: v.get(k) for k in ("read", "lvl", "fc", "up", "true", "risk")}
                                           for z, v in m.get("zones", {}).items()}})
        for e in m.get("events", []):
            sev = ENERGY_SEVERITY.get(e.get("k"))
            if sev:
                self.feed.add("energy", sev, e.get("s", ""), kind=e.get("k"))
        if m.get("result"):
            self.scenario_done = True
            ok = m["result"].get("passed")
            self.feed.add("energy", "info" if ok else "alert",
                          f"Energy scenario finished: {'all checks passed' if ok else 'a check failed'}",
                          kind="scenario_result")

    def snapshot(self) -> dict:
        with self.lock:
            return {"city": self.city, "frame": self.frame, "health": self.health,
                    "history": list(self.history)}


# ---------------------------------------------------------------- transport
def transport_severity(item: dict) -> str:
    k = (item.get("kind") or "").lower()
    text = (item.get("text") or "").lower()
    if k == "check_fail" or (k == "scenario_end" and " fail " in f" {text} "):
        return "alert"                                  # a Test Book check or scenario failed
    if k in ("check_pass", "scenario_start") or k == "scenario_end":
        return "info"
    if any(w in k for w in ("quarantin", "untrust", "blocked", "duplicate", "security", "refused")):
        return "alert"
    if any(w in k for w in ("failed", "error", "alert", "flood")):
        return "watch"
    if "emergency" in k or "preempt" in k or "priority" in text:
        return "watch"
    return "info"


class TransportWatcher(threading.Thread):
    def __init__(self, cfg: dict, feed: Feed):
        super().__init__(daemon=True, name="transport")
        self.url = cfg["url"].rstrip("/")
        self.feed = feed
        self.link = Link("transport")
        self.state: dict = {}
        self.training: dict = {}
        self.world: Optional[dict] = None
        self.after = 0
        self._last_training = 0.0
        self.counts: deque = deque(maxlen=720)       # (wall, vehicles, stopped, mean_wait, mean_speed)

    def run(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                st = http_json("GET", self.url + "/api/state", timeout=6)
                self.state = st
                c = st.get("counts") or {}
                if st.get("ready"):
                    self.counts.append((now_iso(), len(st.get("vehicles", [])), c.get("stopped"),
                                        c.get("mean_wait"), c.get("mean_speed")))
                if self.world is None and st.get("ready"):
                    self.world = http_json("GET", self.url + "/api/world", timeout=10)
                tl = http_json("GET", f"{self.url}/api/v1/timeline?after={self.after}&limit=200")
                last = tl.get("last_id", self.after)
                if last < self.after:                      # backend restarted
                    self.after, self.world = 0, None
                for it in tl.get("items", []):
                    self.after = max(self.after, it.get("id", 0))
                    sev = transport_severity(it)
                    if sev == "info" and it.get("kind") in ("reading", "verdict"):
                        continue
                    self.feed.add("transport", sev, it.get("text", ""), wall=it.get("ts"),
                                  kind=it.get("kind"))
                if time.monotonic() - self._last_training > 15:
                    self.training = http_json("GET", self.url + "/api/training", timeout=8)
                    self._last_training = time.monotonic()
                self.link.ok((time.monotonic() - t0) * 1000)
            except Upstream as err:
                if self.link.online:
                    self.feed.add("transport", "alert", "Transport Brain unreachable", kind="link")
                self.link.fail(str(err))
            time.sleep(2.0)


# ---------------------------------------------------------------- the situation

# ---------------------------------------------------------------- City Brain orchestrator (Twin-driven, four agents)
BRAIN_AXIS_TO_FEED = {"emergency": "emergency", "transportation": "transport", "pollution": "air", "energy": "energy"}
BRAIN_FEED_KINDS = {"decision", "action", "ignored", "conflict", "system", "error", "a2a"}


class BrainWatcher(threading.Thread):
    """Follows the City Brain orchestrator: status (live/mock source), top situations, map items and timeline."""

    def __init__(self, cfg: dict, feed: Feed):
        super().__init__(daemon=True, name="brain-watcher")
        self.url = cfg["url"].rstrip("/")
        self.token = os.environ.get(cfg.get("token_env", "BRAIN_TOKEN")) or cfg.get("token_default") or ""
        self.feed = feed
        self.link = Link("brain")
        self.status: dict = {}
        self.top: dict = {}
        self.map: dict = {}
        self.scenarios: dict = {}
        self.timeline: deque = deque(maxlen=3000)
        self.after = 0
        self.tick = 0

    def run(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                st = http_json("GET", self.url + "/v1/status", timeout=3)
                self.link.ok((time.monotonic() - t0) * 1000)
                if self.status.get("started") and st.get("started") != self.status.get("started"):
                    self.after, self.timeline = 0, deque(maxlen=3000)      # the Brain restarted
                self.status = st
                self.top = http_json("GET", self.url + "/v1/top", timeout=3)
                self.map = http_json("GET", self.url + "/v1/map", timeout=4)
                tl = http_json("GET", f"{self.url}/v1/timeline?after={self.after}&limit=1000", timeout=4)
                for r in tl.get("items", []):
                    self.timeline.append(r)
                    if r["kind"] in BRAIN_FEED_KINDS and not (r["kind"] == "decision" and r["severity"] == "info") \
                            and not (r["kind"] == "a2a" and " working: " in r["summary"]) \
                            and not (r["kind"] in ("action", "decision") and "green wave" in r["summary"].lower().replace("_", " ")):
                        axis = BRAIN_AXIS_TO_FEED.get(r.get("axis") or "", "emergency" if r["kind"] != "system" else "city")
                        src = "MOCK " if r.get("source") == "mock" else ""
                        self.feed.add(axis, r["severity"] if r["severity"] in SEVERITY else "info",
                                      f"{src}Brain {r['kind']}: {r['summary']}", wall=r["ts"], ref=r.get("trace_id"), kind="brain_" + r["kind"])
                self.after = tl.get("last", self.after)
                if self.tick % 5 == 0:
                    self.scenarios = http_json("GET", self.url + "/v1/scenarios", timeout=4)
                self.tick += 1
            except (Upstream, OSError, ValueError, KeyError) as e:
                if self.link.online:
                    self.feed.add("city", "alert", f"City Brain orchestrator unreachable at {self.url}: start brain/run_brain.py")
                self.link.fail(str(e))
                self.status = {}
            time.sleep(1.0)

    def view(self) -> dict:
        return {"link": self.link.view(), "status": self.status, "top": self.top.get("axes", {}),
                "rule": self.top.get("rule"), "running": (self.scenarios or {}).get("running")}

    def run_scenario(self, sid: str) -> dict:
        return http_json("POST", f"{self.url}/v1/scenarios/{quote(sid)}/run", body={},
                         headers={"Authorization": f"Bearer {self.token}"}, timeout=6)

    def stop_scenario(self) -> dict:
        res = http_json("POST", f"{self.url}/v1/scenarios/stop", body={},
                        headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        self.scenarios = http_json("GET", self.url + "/v1/scenarios", timeout=4)
        return res

def _pct(a, b):
    return None if not b else round(100.0 * a / b)


class Situation:
    """Turns the three backends into one picture: per axis a status, a headline and
    figures, and a city-wide sentence of what needs attention."""

    def __init__(self, wa: WasteAirWatcher, en: EnergyWatcher, tr: TransportWatcher):
        self.wa, self.en, self.tr = wa, en, tr

    def waste(self) -> dict:
        if not self.wa.link.online:
            return {"status": "offline", "headline": "Waste Brain offline", "attention": []}
        s = self.wa.state
        bins = s.get("bins", {})
        by = {c: sorted(b for b, v in bins.items() if v["color"] == c)
              for c in ("red", "orange", "yellow", "green")}
        blocked = sorted(b for b in s.get("blocked_bins", s.get("blocked", {})) if b in bins)
        trucks = s.get("trucks", {})
        busy = [t for t, v in trucks.items() if v.get("status") not in ("idle", None)]
        att = []
        if by["red"]:
            att.append(("alert", f"{len(by['red'])} bin{'s' if len(by['red']) > 1 else ''} full"))
        if by["orange"]:
            att.append(("watch", f"{len(by['orange'])} to collect soon"))
        if blocked:
            att.append(("alert", f"{len(blocked)} bin sensor{'s' if len(blocked) > 1 else ''} distrusted"))
        status = max([a[0] for a in att], key=SEVERITY.get, default="ok")
        if not bins:
            headline = "Waiting for bin readings"
        elif by["red"] or by["orange"]:
            headline = f"{len(by['red']) + len(by['orange'])} of {len(bins)} bins need collection"
        else:
            headline = f"All {len(bins)} bins within limits"
        return {"status": status, "headline": headline, "attention": att,
                "figures": {"bins": len(bins), "red": len(by["red"]), "orange": len(by["orange"]),
                            "blocked": len(blocked), "trucks": len(trucks), "trucks_busy": len(busy)},
                "city_time": s.get("city_time")}

    def air(self) -> dict:
        if not self.wa.link.online:
            return {"status": "offline", "headline": "Air-quality Brain offline", "attention": []}
        zones = self.wa.state.get("air", {})
        if not zones:
            return {"status": "ok", "headline": "Waiting for air readings", "attention": [],
                    "figures": {"zones": 0}}
        rank = {"normal": 0, "warning": 1, "high": 2, "critical": 3}
        att = []
        for z, v in sorted(zones.items(), key=lambda kv: -rank.get(kv[1].get("kept_level") or "normal", 0)):
            lvl = v.get("kept_level") or v.get("level")
            if v.get("unknown") and v.get("kept_level"):
                att.append(("alert", f"zone {z} air has no trusted sensor"))
            if lvl == "critical":
                att.append(("critical", f"zone {z} air quality is critical"))
            elif lvl == "high":
                att.append(("alert", f"zone {z} air quality is high"))
            elif lvl == "warning":
                att.append(("watch", f"zone {z} air quality warning"))
        status = max([a[0] for a in att], key=SEVERITY.get, default="ok")
        worst = max(zones.items(), key=lambda kv: kv[1].get("risk") or 0)
        headline = (f"Worst zone {worst[0]}: risk {worst[1].get('risk')}, {worst[1].get('kept_level') or worst[1].get('level')}"
                    if att else f"All {len(zones)} zones normal")
        return {"status": status, "headline": headline, "attention": att,
                "figures": {"zones": len(zones),
                            "restricted": sum(1 for v in zones.values() if "close_parking" in v.get("actions", [])),
                            "worst_zone": worst[0], "worst_risk": worst[1].get("risk")}}

    def energy(self) -> dict:
        if not self.en.link.online:
            return {"status": "offline", "headline": "Energy Brain offline", "attention": []}
        snap = self.en.snapshot()
        f, city = snap["frame"], snap["city"]
        if not f:
            return {"status": "ok", "headline": "Waiting for the first grid cycle", "attention": []}
        caps = {z["id"]: z for z in city.get("zones", [])}
        att, loads = [], []
        for zid, z in f.get("zones", {}).items():
            cap = caps.get(zid, {}).get("cap")
            load = z.get("acc") if z.get("acc") is not None else z.get("read")
            pct = _pct(load or 0, cap)
            loads.append((pct or 0, zid))
            tags = caps.get(zid, {}).get("tags", [])
            if z.get("risk"):
                who = f"{zid} ({', '.join(tags)})" if tags else zid
                sev = "critical" if z.get("prio") else "alert"
                att.append((sev, f"{who} may overload within {int(city.get('horizon', 45))} s"))
            if z.get("trust") not in ("trusted", None, "missing"):
                att.append(("watch", f"{zid} sensor {z.get('trust')}"))
        if not f.get("guardian_ok", True):
            att.append(("alert", "energy Guardian unreachable: degraded mode"))
        status = max([a[0] for a in att], key=SEVERITY.get, default="ok")
        top = max(loads) if loads else (0, "-")
        headline = (f"Highest load {top[1]} at {top[0]}% of capacity" if not att
                    else att[0][1][0].upper() + att[0][1][1:])
        return {"status": status, "headline": headline, "attention": att,
                "figures": {"zones": len(loads), "max_load_pct": top[0], "max_load_zone": top[1],
                            "mode": f.get("mode"), "level": f.get("level"),
                            "commands": f.get("commands"), "guardian_ok": f.get("guardian_ok"),
                            "scenario": city.get("scenario", {}).get("name")}}

    def transport(self) -> dict:
        if not self.tr.link.online:
            return {"status": "offline", "headline": "Transport Brain offline", "attention": []}
        s = self.tr.state
        if not s.get("ready"):
            return {"status": "ok", "headline": "City simulation starting", "attention": []}
        c = s.get("counts", {})
        lots = s.get("lots", {})
        free = sum(l.get("free", 0) for l in lots.values())
        total = sum(l.get("total", 0) for l in lots.values())
        moving = len(s.get("vehicles", []))
        stopped = c.get("stopped") or 0
        att = []
        if s.get("jam"):
            att.append(("watch", "traffic-jam test running"))
        if moving and stopped / moving > 0.5:
            att.append(("alert", f"{_pct(stopped, moving)}% of vehicles standing still"))
        full = [k for k, l in lots.items() if l.get("free") == 0]
        if full:
            att.append(("watch", f"car park{'s' if len(full) > 1 else ''} {', '.join(full)} full"))
        emerg = sum(c.get(k, 0) for k in ("ambulance", "police", "fire"))
        status = max([a[0] for a in att], key=SEVERITY.get, default="ok")
        headline = (f"{moving} vehicles, mean wait {c.get('mean_wait')} s, {free} of {total} parking spaces free"
                    if not att else att[0][1][0].upper() + att[0][1][1:])
        learning = s.get("learning", {})
        return {"status": status, "headline": headline, "attention": att,
                "figures": {"vehicles": moving, "stopped": stopped, "mean_wait": c.get("mean_wait"),
                            "mean_speed": c.get("mean_speed"), "emergency": emerg,
                            "parking_free": free, "parking_total": total, "clock": s.get("clock"),
                            "signal_mode": learning.get("mode"), "episode": learning.get("episode"),
                            "preemptions": learning.get("preemptions")}}

    def overview(self) -> dict:
        axes = {"waste": self.waste(), "air": self.air(), "energy": self.energy(),
                "transport": self.transport()}
        names = {"waste": "Waste", "air": "Air", "energy": "Energy", "transport": "Transport"}
        att = [(SEVERITY[s], names[a], t) for a, v in axes.items() for s, t in v.get("attention", [])]
        att.sort(key=lambda x: -x[0])
        offline = [names[a] for a, v in axes.items() if v["status"] == "offline"]
        worst = max([v["status"] for v in axes.values() if v["status"] != "offline"],
                    key=lambda s: SEVERITY.get(s, -1), default="ok")
        if att:
            parts = [t for _, _, t in att[:3]]
            sentence = "Needs attention: " + "; ".join(parts) + "."
            more = len(att) - 3
            if more > 0:
                sentence = sentence[:-1] + f"; and {more} more."
        elif offline and len(offline) == 4:
            sentence = "No axis is reachable. Start the backends with start_all."
        else:
            sentence = "The city runs normally: no axis needs attention."
        if offline and len(offline) < 4:
            sentence += f" Offline: {', '.join(offline)}."
        return {"at": now_iso(), "status": worst, "sentence": sentence, "axes": axes,
                "attention": [{"severity": [k for k, v in SEVERITY.items() if v == s][0],
                               "axis": a, "text": t} for s, a, t in att]}


# ---------------------------------------------------------------- agents
class EnergyMcp:
    """Minimal MCP client over Streamable HTTP (JSON responses) for the energy agent."""

    def __init__(self, url: str, api_key: Optional[str]):
        self.url = url.rstrip("/") + "/agent/mcp"
        self.key = api_key
        self.session: Optional[str] = None
        self.ids = 0
        self.lock = threading.Lock()

    def _post(self, body: dict, timeout: float = 30.0):
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self.key:
            headers["X-API-Key"] = self.key
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                sid = r.headers.get("mcp-session-id")
                if sid:
                    self.session = sid
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404) and self.session:
                self.session = None
            raise Upstream(json.dumps({"status": e.code, "detail": e.read()[:300].decode("utf-8", "replace")}))
        except (urllib.error.URLError, OSError) as e:
            raise Upstream(f"unreachable: {getattr(e, 'reason', e)}")
        if not text.strip():
            return None
        if text.lstrip().startswith("{"):
            return json.loads(text)
        for line in text.splitlines():                  # SSE answer
            if line.startswith("data:"):
                return json.loads(line[5:])
        return None

    def _rpc(self, method: str, params: dict) -> dict:
        self.ids += 1
        out = self._post({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params})
        if out is None:
            raise Upstream("empty MCP answer")
        if "error" in out:
            raise Upstream(json.dumps({"status": "mcp", "detail": out["error"]}))
        return out.get("result", {})

    def _ensure(self) -> None:
        if self.session:
            return
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "city-brain-control-center", "version": "1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call(self, method: str, params: dict) -> dict:
        with self.lock:
            for attempt in (0, 1):
                try:
                    self._ensure()
                    return self._rpc(method, params)
                except Upstream:
                    if attempt:
                        raise
                    self.session = None


class Agents:
    """The three agents behind one registry. Tool names are namespaced by agent."""

    INFO = {
        "waste_air": {"name": "Waste and air quality agent", "axes": ["waste", "air"],
                      "protocol": "HTTP · POST /v1/agent/call"},
        "energy": {"name": "Energy agent", "axes": ["energy"], "protocol": "MCP · /agent/mcp"},
        "transport": {"name": "Transport agent", "axes": ["transport"],
                      "protocol": "HTTP · POST /agent/invoke"},
    }

    def __init__(self, cfg: dict):
        b = cfg["backends"]
        self.wa = b["waste_air"]
        self.tr = b["transport"]
        self.wa_token = os.environ.get(self.wa.get("token_env", "ORCHESTRATOR_TOKEN"))
        self.tr_key = os.environ.get(self.tr.get("agent_key_env", "AGENT_KEY")) or self.tr.get("agent_key_default")
        self.energy = EnergyMcp(b["energy"]["url"], os.environ.get(b["energy"].get("api_key_env", "ENERGY_AGENT_KEY")))
        self.cache: Dict[str, dict] = {}

    def _tools(self, agent: str) -> List[dict]:
        if agent == "waste_air":
            card = http_json("GET", self.wa["url"].rstrip("/") + "/v1/agent/card")
            return [{"name": t["name"], "description": t["description"],
                     "kind": t.get("kind", "read"), "schema": t.get("inputSchema", {})}
                    for t in card.get("tools", [])]
        if agent == "transport":
            m = http_json("GET", self.tr["url"].rstrip("/") + "/agent/manifest",
                          headers={"X-Agent-Key": self.tr_key or ""})
            return [{"name": t["name"], "title": t.get("title"), "description": t["description"],
                     "kind": "read" if t.get("read_only") else "act",
                     "schema": t.get("input_schema", {})} for t in m.get("tools", [])]
        res = self.energy.call("tools/list", {})
        out = []
        for t in res.get("tools", []):
            ann = t.get("annotations") or {}
            if "readOnlyHint" in ann:
                kind = "read" if ann["readOnlyHint"] else "act"
            else:      # the energy agent does not annotate: its verbs say it
                n = t["name"]
                kind = ("what_if" if any(w in n for w in ("what_if", "evaluate", "forecast"))
                        else "read" if any(w in n for w in ("get_", "explain", "list_", "describe"))
                        else "act")
            out.append({"name": t["name"], "description": t.get("description", ""), "kind": kind,
                        "schema": t.get("inputSchema", {})})
        return out

    def registry(self) -> dict:
        out = {}
        for agent, info in self.INFO.items():
            try:
                tools = self._tools(agent)
                self.cache[agent] = {"tools": tools, "at": now_iso()}
                out[agent] = {**info, "online": True, "tools": tools}
            except Upstream as e:
                cached = self.cache.get(agent, {})
                out[agent] = {**info, "online": False, "error": str(e)[:200],
                              "tools": cached.get("tools", [])}
        return out

    def call(self, agent: str, tool: str, args: dict) -> dict:
        t0 = time.monotonic()
        try:
            if agent == "waste_air":
                if not self.wa_token:
                    return {"ok": False, "error": {"code": "no_token",
                            "message": "set ORCHESTRATOR_TOKEN for the control center (the same as the waste/air Brain)"}}
                res = http_json("POST", self.wa["url"].rstrip("/") + "/v1/agent/call",
                                {"tool": tool, "arguments": args}, timeout=120,
                                headers={"Authorization": f"Bearer {self.wa_token}",
                                         "X-Caller": "control-center"})
                out = {"ok": True, "result": res.get("result"), "summary": None}
            elif agent == "transport":
                res = http_json("POST", self.tr["url"].rstrip("/") + "/agent/invoke",
                                {"tool": tool, "arguments": args, "caller": "control-center"},
                                timeout=120, headers={"X-Agent-Key": self.tr_key or ""})
                out = {"ok": bool(res.get("ok")), "result": res.get("data"),
                       "summary": res.get("summary"), "error": res.get("error")}
            elif agent == "energy":
                res = self.energy.call("tools/call", {"name": tool, "arguments": args})
                data = res.get("structuredContent")
                if data is None:
                    texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
                    try:
                        data = json.loads(texts[0]) if texts else None
                    except ValueError:
                        data = {"text": "\n".join(texts)}
                out = {"ok": not res.get("isError"), "result": data, "summary": None,
                       "error": None if not res.get("isError") else {"code": "tool_error", "message": str(data)[:300]}}
            else:
                return {"ok": False, "error": {"code": "unknown_agent", "message": agent}}
        except Upstream as e:
            detail = str(e)
            try:
                parsed = json.loads(detail)
                err = parsed.get("detail", {}).get("error") if isinstance(parsed.get("detail"), dict) else None
                if isinstance(err, dict):
                    return {"ok": False, "error": err, "ms": round((time.monotonic() - t0) * 1000)}
            except ValueError:
                pass
            return {"ok": False, "error": {"code": "upstream", "message": detail[:400]},
                    "ms": round((time.monotonic() - t0) * 1000)}
        out["ms"] = round((time.monotonic() - t0) * 1000)
        return out


# ---------------------------------------------------------------- demo controls (whitelist)
CONTROL_ROUTES = {
    # name: (backend key, method, path template, allowed body keys or None)
    "energy.scenario": ("energy", "POST", "/api/scenario/{id}", None),
    "energy.control": ("energy", "POST", "/api/control", {"paused", "speed"}),
    "energy.inject": ("energy", "POST", "/api/inject", {"action", "args"}),
    "waste_air.control": ("waste_air_controls", "POST", "/v1/control", {"action", "sensor_id", "value"}),
    "transport.controls": ("transport", "POST", "/api/sim/controls", None),
    "transport.scenario": ("transport", "POST", "/api/sim/scenario/{id}", None),
    "transport.jam": ("transport", "POST", "/api/sim/jam", {"size", "seconds"}),
    # attacks played by the transport mock Twin; the transport Guardian has to catch them
    "transport.fake_sensor": ("transport_twin", "POST", "/sim/fake_sensor", {"lot_id", "space", "occupied", "seconds"}),
    "transport.flood": ("transport_twin", "POST", "/sim/flood", {"lot_id", "space", "per_second", "seconds"}),
}


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.feed = Feed()
        b = cfg["backends"]
        self.wa = WasteAirWatcher(b["waste_air"], self.feed)
        self.en = EnergyWatcher(b["energy"], self.feed)
        self.tr = TransportWatcher(b["transport"], self.feed)
        self.brain = BrainWatcher(b.get("brain", DEFAULT_CONFIG["backends"]["brain"]), self.feed)
        self.situation = Situation(self.wa, self.en, self.tr)
        self.agents = Agents(cfg)
        self.scenarios = scenario_lib.Runner(self.control, self.agents.call, self.feed, lambda: self.wa.state)
        self.started = now_iso()

    def start(self) -> None:
        for t in (self.wa, self.en, self.tr, self.brain):
            t.start()

    def links(self) -> dict:
        return {"waste_air": self.wa.link.view(), "energy": self.en.link.view(),
                "transport": self.tr.link.view(), "brain": self.brain.link.view()}

    def consoles(self) -> dict:
        b = self.cfg["backends"]
        return {"waste_air": b["waste_air"]["url"].rstrip("/") + b["waste_air"].get("console", "/"),
                "waste_air_controls": b["waste_air"].get("controls_url", "").rstrip("/") + "/control",
                "energy": b["energy"]["url"].rstrip("/") + b["energy"].get("console", "/"),
                "transport": b["transport"]["url"].rstrip("/") + b["transport"].get("console", "/")}

    # -- transport Test Book (catalog and run script live in brain/axes/transportation/engine/scenarios)
    def _transport_admin(self) -> dict:
        t = self.cfg["backends"]["transport"]
        return {"X-Admin-Key": os.environ.get(t.get("admin_key_env", "PARKING_ADMIN_KEY"))
                or t.get("admin_key_default", "dev-admin-key")}

    def testbook(self) -> dict:
        tb = http_json("GET", self.cfg["backends"]["transport"]["url"].rstrip("/") + "/api/v1/scenarios", timeout=10) or {}
        pend = getattr(self, "_tb_pending", None)
        if pend and not tb.get("running") and time.monotonic() - pend[1] < 45:
            # requested, script still preparing the city
            last = next((x.get("last") for x in tb.get("scenarios", []) if x.get("id") == pend[0]), None)
            fresh = last and last.get("finished") and last["finished"] > getattr(self, "_tb_pending_iso", "")
            if not fresh:
                tb["starting"] = pend[0]
        return tb

    def testbook_run(self, sid: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,16}", sid):
            raise Refused(400, "bad scenario id")
        # The Brain marks a scenario as running only once its script has prepared the city (a few
        # seconds). Refuse a second request in that gap, or the second would cut the first short.
        pend = getattr(self, "_tb_pending", None)
        if pend and time.monotonic() - pend[1] < 45:
            cur = (self.testbook() or {}).get("running")
            if not cur:
                raise Refused(409, f"Test Book {pend[0]} is starting: wait until it has finished")
        cur = (self.testbook() or {}).get("running")
        if cur:
            raise Refused(409, f"Test Book {cur} is still running")
        self._tb_pending = (sid, time.monotonic())
        self._tb_pending_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        res = http_json("POST", self.cfg["backends"]["transport"]["url"].rstrip("/") + f"/api/v1/scenarios/{quote(sid)}/run",
                        {}, headers=self._transport_admin(), timeout=15)
        self.feed.add("transport", "info", f"Test Book {sid} requested from the control center", kind="scenario")
        return res or {"started": sid}

    # -- one Stop button for every kind of scenario -------------------------
    def running(self) -> List[dict]:
        """Every scenario running right now, whichever part of the city plays it."""
        out = []
        if self.brain.link.online:
            try:                                  # fresh: the watcher only refreshes the Test Book every 5 s
                self.brain.scenarios = http_json("GET", self.brain.url + "/v1/scenarios", timeout=3)
            except (Upstream, OSError, ValueError):
                pass
        br = (self.brain.scenarios or {}).get("running") or {}
        if br.get("state") in ("starting", "running"):
            out.append({"kind": "brain", "id": br.get("id"), "name": br.get("name"), "t": br.get("t"), "duration_s": br.get("duration_s")})
        for sid, r in list(self.scenarios.runs.items()):
            if r.get("state") == "running":
                out.append({"kind": "control_center", "id": sid, "name": self.scenarios.by_id[sid].get("title")})
        if self.tr.link.online:
            try:
                tb = self.testbook()
                if tb.get("running") or tb.get("starting"):
                    out.append({"kind": "transport", "id": tb.get("running") or tb.get("starting"), "name": "Transport Test Book"})
            except (Upstream, OSError, ValueError):
                pass
        sc = (self.en.city or {}).get("scenario") or {}
        if self.en.link.online and sc.get("id") not in (None, "", "FREE") and not getattr(self.en, "scenario_done", False):
            out.append({"kind": "energy", "id": sc.get("id"), "name": sc.get("name")})
        return out

    def stop_all(self) -> dict:
        """Stop whatever is running. Each part stops its own way: the Brain undoes its Twin changes, the
        control-center scenarios reset every axis, the transport Test Book closes its run, energy goes to free play."""
        stopped, errors = [], []
        for r in self.running():
            try:
                if r["kind"] == "brain":
                    res = self.brain.stop_scenario() or {}
                    undone = res.get("undone") or []
                    stopped.append(f"Brain {r['id']}" + (f" (undone: {', '.join(undone)})" if undone else ""))
                elif r["kind"] == "control_center":
                    res = self.scenarios.reset()
                    errors += res.get("errors", [])
                    stopped.append(f"scenario {r['id']}")
                elif r["kind"] == "transport":
                    http_json("POST", self.cfg["backends"]["transport"]["url"].rstrip("/") + f"/api/v1/scenarios/{quote(str(r['id']))}/stop",
                              {}, headers=self._transport_admin(), timeout=10)
                    self._tb_pending = None
                    stopped.append(f"transport Test Book {r['id']}")
                elif r["kind"] == "energy":
                    self.control("energy.scenario", "FREE", {})
                    stopped.append(f"energy {r['id']}")
            except (Upstream, Refused, OSError, ValueError) as e:
                errors.append(f"{r['kind']} {r['id']}: {e}")
        if stopped:
            self.feed.add("city", "watch" if errors else "info", "Stopped from the control center: " + "; ".join(stopped), kind="scenario")
        return {"ok": not errors, "stopped": stopped, "errors": errors}

    def control(self, name: str, ident: Optional[str], body: dict) -> dict:
        route = CONTROL_ROUTES.get(name)
        if not route:
            raise Refused(404, f"unknown control {name}")
        key, method, path, allowed = route
        b = self.cfg["backends"]
        if key == "waste_air_controls":
            base = b["waste_air"].get("controls_url", "")
        elif key == "transport_twin":
            base = b["transport"].get("twin_url", "http://127.0.0.1:9001")
        else:
            base = b[key]["url"]
        base = base.rstrip("/")
        if "{id}" in path:
            if not ident or not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", ident):
                raise Refused(400, "bad id")
            path = path.replace("{id}", quote(ident))
        if allowed is not None:
            body = {k: v for k, v in body.items() if k in allowed}
        return http_json(method, base + path, body, timeout=10) or {"ok": True}


# ---------------------------------------------------------------- HTTP
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".json": "application/json", ".ico": "image/x-icon", ".png": "image/png"}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CityBrainControlCenter/1.0"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200_000:
                raise ValueError("body too large")
            raw = self.rfile.read(n) if n else b"{}"
            v = json.loads(raw or b"{}")
            if not isinstance(v, dict):
                raise ValueError("body must be a JSON object")
            return v

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path
            try:
                if p in ("/", "/index.html"):
                    return self._static("index.html")
                if p.startswith("/static/"):
                    return self._static(p[len("/static/"):])
                if p == "/api/health":
                    return self._send(200, {"ok": True, "started": app.started, "links": app.links()})
                if p == "/api/scenarios":
                    return self._send(200, app.scenarios.catalog())
                if p == "/api/scenarios/running":
                    return self._send(200, {"running": app.running()})
                if p == "/api/config":
                    return self._send(200, {"consoles": app.consoles(), "links": app.links()})
                if p == "/api/overview":
                    return self._send(200, {**app.situation.overview(), "links": app.links()})
                if p == "/api/feed":
                    after = int(q.get("after", ["0"])[0])
                    limit = min(int(q.get("limit", ["200"])[0]), 1000)
                    axes = set(q["axes"][0].split(",")) if q.get("axes") and q["axes"][0] else None
                    return self._send(200, {"items": app.feed.since(after, limit, axes),
                                            "last": app.feed.seq})
                if p == "/api/waste_air":
                    return self._send(200, {"link": app.wa.link.view(), "state": app.wa.state,
                                            "risk": {z: list(d) for z, d in app.wa.risk.items()},
                                            "sensors": app.wa.sensors})
                if p == "/api/energy":
                    return self._send(200, {"link": app.en.link.view(), **app.en.snapshot()})
                if p == "/api/energy/scenarios":
                    return self._send(200, http_json("GET", app.cfg["backends"]["energy"]["url"].rstrip("/") + "/api/scenarios"))
                if p == "/api/transport":
                    s = dict(app.tr.state)
                    return self._send(200, {"link": app.tr.link.view(), "state": s,
                                            "counts": list(app.tr.counts),
                                            "training": {k: v for k, v in app.tr.training.items()
                                                         if k not in ("origin_evals",)}})
                if p == "/api/transport/testbook":
                    return self._send(200, app.testbook())
                if p == "/api/transport/world":
                    return self._send(200, app.tr.world or {})
                if p == "/api/agents":
                    return self._send(200, app.agents.registry())
                if p == "/api/brain":
                    return self._send(200, app.brain.view())
                if p == "/api/brain/map":
                    return self._send(200, app.brain.map or {})
                if p == "/api/brain/a2a":
                    return self._send(200, http_json("GET", app.brain.url + "/v1/a2a", timeout=4))
                if p == "/api/brain/timeline":
                    after = int(q.get("after", ["0"])[0])
                    return self._send(200, {"items": [r for r in list(app.brain.timeline) if r["seq"] > after][-1500:],
                                            "last": app.brain.after})
                if p == "/api/brain/scenarios":
                    return self._send(200, app.brain.scenarios or {"scenarios": []})
                return self._send(404, {"error": "not found"})
            except Upstream as e:
                return self._send(502, {"error": str(e)})
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})

        def do_POST(self):
            p = urlparse(self.path).path
            try:
                body = self._body()
                m = re.fullmatch(r"/api/agents/(waste_air|energy|transport)/call", p)
                if m:
                    tool = body.get("tool")
                    if not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", tool):
                        return self._send(400, {"error": "tool must be a tool name"})
                    args = body.get("arguments") or {}
                    if not isinstance(args, dict):
                        return self._send(400, {"error": "arguments must be an object"})
                    res = app.agents.call(m.group(1), tool, args)
                    app.feed.add({"waste_air": "waste", "energy": "energy", "transport": "transport"}[m.group(1)],
                                 "info" if res.get("ok") else "watch",
                                 f"Control center called {tool}" + ("" if res.get("ok") else " (refused)"),
                                 kind="agent_call")
                    return self._send(200, res)
                m = re.fullmatch(r"/api/brain/scenarios/([A-Z]{2}[0-9]{2})/run", p)
                if m:
                    return self._send(200, app.brain.run_scenario(m.group(1)))
                m = re.fullmatch(r"/api/transport/testbook/([A-Za-z0-9_\-]{1,16})/run", p)
                if m:
                    return self._send(200, app.testbook_run(m.group(1)))
                if p == "/api/scenarios/reset":
                    return self._send(200, app.scenarios.reset())
                if p == "/api/scenarios/stop":
                    return self._send(200, app.stop_all())
                m = re.fullmatch(r"/api/scenarios/([A-Z][0-9])/run", p)
                if m:
                    if m.group(1) not in app.scenarios.by_id:
                        return self._send(404, {"error": f"unknown scenario {m.group(1)}"})
                    return self._send(200, app.scenarios.start(m.group(1)))
                m = re.fullmatch(r"/api/control/([a-z_]+\.[a-z_]+)(?:/([A-Za-z0-9_\-]+))?", p)
                if m:
                    return self._send(200, app.control(m.group(1), m.group(2), body))
                return self._send(404, {"error": "not found"})
            except Refused as e:
                return self._send(e.code, {"error": str(e)})
            except Upstream as e:
                return self._send(502, {"error": str(e)})
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})

        def _static(self, name: str):
            path = os.path.normpath(os.path.join(STATIC, name))
            if not path.startswith(STATIC) or not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            with open(path, "rb") as f:
                data = f.read()
            return self._send(200, data, TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="City Brain control center")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    host = a.host or cfg["listen"]["host"]
    port = a.port or cfg["listen"]["port"]
    app = App(cfg)
    app.start()
    try:
        httpd = ThreadingHTTPServer((host, port), make_handler(app))
    except OSError as e:
        print(f"Port {port} is already used by another program ({e}).\n"
              f"Start the control center on a free port, for example:  python server.py --port {port + 1}\n"
              f"and open http://localhost:{port + 1}")
        return 1
    httpd.daemon_threads = True
    print(f"City Brain control center on http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    for k, v in cfg["backends"].items():
        print(f"  {k:<10} {v['url']}")
    try:
        httpd.serve_forever(0.25)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

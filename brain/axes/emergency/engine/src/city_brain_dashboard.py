"""
City Brain Control Room server.

Serves the dashboard and runs every scenario through the real orchestrator
(rules + Laya System 1 + Gemini System 2 fallback), logging each decision to SQLite.

API
- GET  /                 -> dashboard/city_brain_dashboard.html
- GET  /api/health       -> engine status (Laya, Gemini) and which axes are integrated
- GET  /api/city         -> city model: blocks, facilities, tunnels, bins, truck routes
- GET  /api/blocks       -> blocks only (kept for older clients)
- GET  /api/scenarios    -> scenario catalogue (mock readings; axes previewed by the orchestrator rules)
- GET  /api/stats        -> aggregate statistics from city_brain_audit.db
- GET  /api/history      -> last 30 decisions from city_brain_audit.db
- GET  /api/hazards      -> latest assessment per hazard engine (flood, earthquake, tsunami, storm)
- GET  /api/feeds        -> live feed catalogue (Open-Meteo, USGS, EMSC)
- POST /api/scenario     -> {"scenario": "...", "block": "D5", "bin_id": "..."} -> builds the
                            sensor event(s), runs decide() on each, returns event + decision + map target
                            (+ "steps" for multi-reading hazard scenarios)
- POST /api/feed         -> {"feed": "open_meteo" | "usgs" | "emsc"} -> polls the live feed adapter and
                            runs the relevant events through decide()
- POST /api/decide       -> raw CityEvent JSON -> decide()
- POST /api/approve      -> {"event_id", "action_type", "approved": true} -> operator approval, logged
- POST /api/reset        -> clears the emergency agent's hazard correlation buffer

Usage:
    python src/city_brain_dashboard.py
"""
from __future__ import annotations

import http.server
import json
import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DASHBOARD_FILE = PROJECT_ROOT / "dashboard" / "city_brain_dashboard.html"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import orchestrator  # noqa: E402
from orchestrator import AuditLog, decide, PRIORITY, SAFETY_FLOOR  # noqa: E402
import city_model  # noqa: E402
from modules.feeds import EMSCAdapter, OpenMeteoAdapter, USGSAdapter  # noqa: E402
from modules.catalog_quake_engine import land_mask_source  # noqa: E402

FEED_ADAPTERS = {"open_meteo": OpenMeteoAdapter, "usgs": USGSAdapter, "emsc": EMSCAdapter}
FEED_MIN_MAG = {"usgs": 4.5, "emsc": 4.0}
FEED_MAX_EVENTS = 5  # evaluate at most this many quakes per poll (strongest first)

PORT = 8085
DB_PATH = AuditLog.DEFAULT_PATH
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("city_brain_server")
_DECIDE_LOCK = threading.Lock()  # one decision at a time (shared SQLite + model state)


def _db() -> sqlite3.Connection | None:
    return sqlite3.connect(DB_PATH) if Path(DB_PATH).exists() else None


def health() -> dict:
    return {
        "status": "online",
        "laya_loaded": orchestrator.LAYA_AVAILABLE,
        "gemini_configured": orchestrator.LLM_ROUTER is not None,
        "gemini_model": orchestrator.LLM_MODEL,
        "axes": {a: ("integrated" if a in orchestrator.INTEGRATED_AXES else "not_integrated") for a in PRIORITY},
        "blocks": len(city_model.BLOCKS),
        "database": Path(DB_PATH).name,
        "land_mask": land_mask_source(),
    }


def stats() -> dict:
    out = {"total_events": 0, "safety_floor_triggers": 0, "conflicts_resolved": 0,
           "awaiting_axes": 0, "primary_axis_counts": {a: 0 for a in PRIORITY}}
    conn = _db()
    if not conn:
        return out
    try:
        rows = conn.execute("SELECT decision FROM decisions").fetchall()
        out["total_events"] = len(rows)
        for (raw,) in rows:
            try:
                dec = json.loads(raw)
            except Exception:
                continue
            if dec.get("primary_axis") in out["primary_axis_counts"]:
                out["primary_axis_counts"][dec["primary_axis"]] += 1
            out["conflicts_resolved"] += len(dec.get("conflicts") or [])
            if dec.get("unavailable_axes"):
                out["awaiting_axes"] += 1
            # a deterministic rule scored at or above the safety floor
            if any(w.get("score", 0) >= SAFETY_FLOOR and "rules" in w.get("source", "") for w in dec.get("why", [])):
                out["safety_floor_triggers"] += 1
    finally:
        conn.close()
    return out


def slim_event(event: dict) -> dict:
    """Drop raw waveforms (hundreds of KB) before sending an event to the browser.
    The audit DB keeps the full event."""
    net = (event.get("data") or {}).get("stationNetwork")
    if not net:
        return event
    data = dict(event["data"])
    data["stationNetwork"] = {k: v for k, v in net.items() if k != "stations"}
    data["stationNetwork"]["stations"] = [{k: v for k, v in st.items() if k != "accel"} for st in net.get("stations", [])]
    data["stationNetwork"]["waveforms_omitted"] = True
    return {**event, "data": data}


def history(limit: int = 30) -> list[dict]:
    conn = _db()
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT event_id, ts, event, decision FROM decisions ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"event_id": r[0], "ts": r[1], "event": slim_event(json.loads(r[2])), "decision": json.loads(r[3])} for r in rows]
    except Exception as exc:
        log.warning("Could not read audit db: %s", exc)
        return []
    finally:
        conn.close()


def run_decide(event: dict) -> tuple[dict, dict, float]:
    with _DECIDE_LOCK:
        ev = orchestrator.CityEvent(**event)
        t0 = time.time()
        decision = decide(ev)
        return ev.model_dump(), decision, round((time.time() - t0) * 1000, 2)


_LAST_RUN = {"mode": None}  # "drill" (simulated scenario) or "live" (external feed)


def preview_axes(name: str) -> list[str]:
    """Axes the orchestrator's deterministic rules route this scenario's mock readings to.
    Pure: no agent runs, nothing is logged. At run time Laya/Gemini may add to it."""
    events, _ = city_model.build_scenario_steps(name)
    axes: set[str] = set()
    for raw in events:
        ev = orchestrator.CityEvent(**{k: v for k, v in raw.items() if not k.startswith("_")})
        axes |= {a for a, b in orchestrator._rule_scores(ev).items() if b["score"] >= orchestrator.ROUTE_THRESHOLD}
    return [a for a in PRIORITY if a in axes]


def scenario_catalogue() -> dict:
    return {name: {**spec, "axes": preview_axes(name), "axes_source": "orchestrator rules (preview)", "data": "mock"}
            for name, spec in city_model.SCENARIOS.items()}


def reset_hazards() -> None:
    orchestrator._EMERGENCY_AGENT_INSTANCE.reset_hazard_state()


def enter_mode(mode: str) -> None:
    """Simulated drill readings must never be correlated with real feed data (and vice versa)."""
    if _LAST_RUN["mode"] != mode:
        reset_hazards()
    _LAST_RUN["mode"] = mode


def _hazard_summary(decision: dict) -> dict | None:
    rep = (decision.get("agent_reports") or {}).get("emergency") or {}
    if not rep.get("hazard"):
        return None
    ha = rep.get("hazard_assessment") or {}
    return {"hazard": rep["hazard"], "risk_level": rep.get("risk_level"), "risk_score": rep.get("risk_score"),
            "sources": ha.get("independent_source_count"), "eta_minutes": ha.get("eta_minutes"),
            "requires_human_approval": rep.get("requires_human_approval", False)}


def run_steps(events: list[dict]) -> dict:
    """Runs a sequence of readings through decide(); returns the last one plus a per-step summary."""
    steps, total_ms, last = [], 0.0, None
    for raw in events:
        label = raw.get("_step")
        ev, decision, ms = run_decide({k: v for k, v in raw.items() if not k.startswith("_")})
        total_ms += ms
        steps.append({"label": label or ev["kind"], "kind": ev["kind"], "source": ev["source"], "event_id": ev["id"],
                      "primary_axis": decision.get("primary_axis"), "latency_ms": ms, "hazard": _hazard_summary(decision)})
        last = (ev, decision)
    return {"event": last[0], "decision": last[1], "latency_ms": round(total_ms, 2), "steps": steps}


def latest_hazards() -> dict:
    """Latest assessment of each hazard engine, read back from the audit log."""
    out: dict = {}
    conn = _db()
    if not conn:
        return out
    try:
        rows = conn.execute("SELECT ts, event, decision FROM decisions ORDER BY ts DESC LIMIT 400").fetchall()
        for ts, raw_ev, raw_dec in rows:
            try:
                dec = json.loads(raw_dec)
            except Exception:
                continue
            h = _hazard_summary(dec)
            if h and h["hazard"] not in out:
                ev = json.loads(raw_ev)
                out[h["hazard"]] = {**h, "ts": ts, "kind": ev.get("kind"), "source": ev.get("source")}
    finally:
        conn.close()
    return out


def poll_feed(name: str) -> dict:
    """Polls one live feed adapter and runs its relevant events through the orchestrator."""
    if name not in FEED_ADAPTERS:
        raise ValueError(f"unknown feed '{name}'")
    events = FEED_ADAPTERS[name]().fetch()
    enter_mode("live")
    fetched = len(events)
    if name in FEED_MIN_MAG:
        events = [e for e in events if float(e["data"].get("magnitude") or 0) >= FEED_MIN_MAG[name]]
        events = sorted(events, key=lambda e: -float(e["data"]["magnitude"]))[:FEED_MAX_EVENTS]
    out = {"feed": name, "title": city_model.FEEDS[name]["title"], "fetched": fetched, "evaluated": len(events)}
    if not events:
        out["message"] = (f"{fetched} event(s) in the feed, none at or above M{FEED_MIN_MAG.get(name, 0):.1f}: nothing to evaluate."
                          if name in FEED_MIN_MAG else "The feed returned no data.")
        return out
    for e in events:
        e.setdefault("data", {})["block_id"] = "city"
        e["_step"] = e["data"].get("title") or e["data"].get("flynn_region") or e["kind"]
    res = run_steps(events)
    # Show the most severe result on the map, not just the last one evaluated
    order = {"critical": 4, "warning": 3, "watch": 2, "low": 1, None: 0}
    best = max(range(len(res["steps"])), key=lambda i: order.get((res["steps"][i]["hazard"] or {}).get("risk_level"), 0))
    ev = {k: v for k, v in events[best].items() if not k.startswith("_")}
    out.update(res)
    if best != len(events) - 1:   # re-read the most severe decision from its own step
        out["event"] = {**ev, "id": res["steps"][best]["event_id"]}
        out["decision"] = _decision_by_event_id(res["steps"][best]["event_id"]) or res["decision"]
    if ev["kind"] == "seismic_catalog":
        d = ev["data"]
        out["target"] = {"type": "regional_quake", "block": "E5", "magnitude": d.get("magnitude"), "depth_km": d.get("depth_km"),
                         "live": True, "title": d.get("title") or d.get("flynn_region"),
                         **city_model.regional_target(float(d["epicenter_lat"]), float(d["epicenter_lon"]))}
    else:
        obs = next((e for e in events if e["kind"] == "weather_observation"), events[0])
        out["target"] = {"type": "weather_live", "block": city_model.WEATHER_STATION["block"], "live": True,
                         "readings": obs.get("data", {}),
                         "forecast": next((e["data"] for e in events if e["kind"] == "weather_forecast"), {})}
    return out


def _decision_by_event_id(event_id: str) -> dict | None:
    conn = _db()
    if not conn:
        return None
    try:
        row = conn.execute("SELECT decision FROM decisions WHERE event_id = ? ORDER BY ts DESC LIMIT 1", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def record_approval(body: dict) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS approvals (event_id TEXT, ts REAL, action_type TEXT, approved INTEGER, operator TEXT)")
        conn.execute("INSERT INTO approvals VALUES (?,?,?,?,?)",
                     (str(body.get("event_id", "")), time.time(), str(body.get("action_type", "")),
                      1 if body.get("approved", True) else 0, str(body.get("operator", "control_room"))))
        conn.commit()
    finally:
        conn.close()
    return {"status": "recorded", "event_id": body.get("event_id"), "action_type": body.get("action_type"),
            "approved": bool(body.get("approved", True))}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        log.debug(fmt, *args)

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/city_brain_dashboard.html"):
            if not DASHBOARD_FILE.exists():
                return self._json({"error": "dashboard file not found"}, 404)
            body = DASHBOARD_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/api/health":
            self._json(health())
        elif path == "/api/city":
            self._json(city_model.city_payload())
        elif path == "/api/blocks":
            self._json(city_model.BLOCKS)
        elif path == "/api/scenarios":
            self._json(scenario_catalogue())
        elif path == "/api/stats":
            self._json(stats())
        elif path == "/api/history":
            self._json(history())
        elif path == "/api/hazards":
            self._json(latest_hazards())
        elif path == "/api/feeds":
            self._json(city_model.FEEDS)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/api/scenario":
                name = body.get("scenario")
                events, target = city_model.build_scenario_steps(name, body.get("block"), body.get("bin_id"),
                                                                 body.get("magnitude"))
                enter_mode("drill")
                if name in city_model.HAZARD_BUILDERS:
                    reset_hazards()   # every hazard drill starts from a clean correlation buffer
                res = run_steps(events)
                out = {"scenario": name, "event": slim_event(res["event"]), "target": target,
                       "decision": res["decision"], "latency_ms": res["latency_ms"]}
                if len(events) > 1 or name in city_model.HAZARD_BUILDERS:
                    out["steps"] = res["steps"]
                return self._json(out)
            if self.path == "/api/feed":
                if body.get("feed") not in FEED_ADAPTERS:
                    raise ValueError(f"unknown feed '{body.get('feed')}'")
                try:
                    res = poll_feed(body.get("feed"))
                except Exception as exc:
                    log.warning("Live feed %s failed: %s", body.get("feed"), exc)
                    return self._json({"error": f"Live feed unreachable ({type(exc).__name__}: {exc})"}, 502)
                if "event" in res:
                    res["event"] = slim_event(res["event"])
                return self._json(res)
            if self.path == "/api/approve":
                return self._json(record_approval(body))
            if self.path == "/api/reset":
                reset_hazards()
                return self._json({"status": "reset"})
            if self.path == "/api/decide":
                event, decision, ms = run_decide(body)
                return self._json({"event": slim_event(event), "decision": decision, "latency_ms": ms})
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            log.exception("Request failed")
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)


def start_server() -> None:
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("", PORT), Handler) as httpd:
        h = health()
        print("=" * 64)
        print(" CITY BRAIN CONTROL ROOM")
        print(f" Open        : http://localhost:{PORT}")
        print(f" Laya (S1)   : {'loaded' if h['laya_loaded'] else 'not loaded (rules only)'}")
        print(f" Gemini (S2) : {'configured' if h['gemini_configured'] else 'off (no GEMINI_API_KEY)'}")
        print(f" Axes        : {', '.join(f'{a}={s}' for a, s in h['axes'].items())}")
        print(f" Audit DB    : {DB_PATH}")
        print(f" Land/sea map: {h['land_mask']}")
        print("=" * 64)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


if __name__ == "__main__":
    start_server()

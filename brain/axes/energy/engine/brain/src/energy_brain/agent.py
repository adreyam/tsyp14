"""Energy Brain as an AI agent for the City Brain orchestrator (MCP server).

The agent runs its own closed loop (observe -> predict -> decide -> gate -> act
-> verify) and the orchestrator supervises it through MCP tools, any time:

  ask        energy_get_status, energy_get_zone, energy_forecast, energy_evaluate_options,
             energy_what_if, energy_explain, energy_get_events, energy_list_proposals,
             energy_describe_agent  (+ resource energy://agent-card)
  steer      energy_set_mode (autonomous | advisory), energy_veto_switch, energy_lift_veto,
             energy_approve_proposal, energy_set_priority_zone

Authority: the agent acts on its own in autonomous mode; in advisory mode it only
proposes and the orchestrator approves. Nothing the orchestrator does bypasses the
Safety & Trust Gate: approvals are re-checked against the current situation and the
gate, vetoes can only restrict. Every steering call is written to the audit log.

Run:
  python -m energy_brain.agent --config config/config.yaml                      # MCP over HTTP on :8300/mcp
  python -m energy_brain.agent --config config/config.yaml --transport stdio    # for a local orchestrator
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .schemas import PriorityHint

AGENT_NAME = "energy-brain"
AGENT_VERSION = "0.6"


# ============================================================================ service

class AgentService:
    """Thread-safe facade over a running EnergyBrain. The loop owner shares `lock`."""

    def __init__(self, brain, lock: Optional[threading.RLock] = None):
        self.brain = brain
        self.lock = lock or threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _t(self) -> float:
        b = self.brain
        return round(b.clock() - (b.t_start or b.clock()), 1)

    def _zone(self, zid: str) -> dict:
        from .decision import outlook, p_overload
        b, st = self.brain, self.brain.state
        if zid not in st.zones:
            raise ValueError(f"unknown zone_id '{zid}'. Zones: {sorted(st.zones)}")
        z, cap = st.zones[zid], st.capacity(zid)
        fc = z.model.snapshot()
        o = outlook(st).get(zid)
        h = b.cfg.forecast.horizon_s
        status = ("no data" if z.last_kw is None else "overload" if cap and z.last_kw > cap
                  else "overload predicted" if z.at_risk else "normal")
        return {
            "zone_id": zid, "tags": [t.value for t in st.zone(zid).tags], "priority": st.is_priority(zid),
            "status": status, "load_kw": _r(z.last_kw), "capacity_kw": cap,
            "load_pct": round(100 * z.last_kw / cap) if cap and z.last_kw is not None else None,
            "forecast": None if not fc.ready else {
                "horizon_s": h, "expected_peak_kw": _r(o.mu) if o else None, "upper_band_kw": _r(o.upper) if o else None,
                "trend_kw_per_s": round(fc.trend, 2),
                "p_overload": round(p_overload(o.mu, o.s, cap), 3) if o and cap else None},
            "j1_plan_kw": _r(b.plan(zid)),
            "sensor": {"id": z.sensor_id, "trust": st.verdict_status(z.sensor_id) if z.sensor_id else "missing",
                       "suspect_jump": z.suspect_kw is not None, "flapping": bool(z.sensor_id and st.is_flapping(z.sensor_id))},
        }

    # ------------------------------------------------------------------ read tools
    def status(self) -> dict:
        with self.lock:
            b, st = self.brain, self.brain.state
            zones = [self._zone(z) for z in st.zones]
            risky = [z["zone_id"] for z in zones if z["status"] in ("overload", "overload predicted")]
            sw = []
            for s in st.city.switches:
                cur = st.switches.get(s.switch_id)
                v = b.vetoes.get(s.switch_id)
                sw.append({"switch_id": s.switch_id, "kind": s.kind.value if s.kind else None,
                           "state": cur.state.value if cur else None, "effect_known": s.switch_id in b.effects,
                           "failed": s.switch_id in b.failed, "verifying": s.switch_id in b.pending,
                           "changed_by_brain": s.switch_id in b.changed,
                           "vetoed_for_s": round(v[0] - b.clock()) if v and b.clock() < v[0] else 0})
            alerts = [e for e in b.log.events[-200:] if e.kind == "operator_alert"]
            summary = (f"{st.city.city_id}: knowledge {b.knowledge.level}, mode {b.mode}, Guardian "
                       f"{'online' if b.guardian_ok is not False else 'UNREACHABLE (degraded mode)'}; "
                       + (f"risk in {', '.join(risky)}" if risky else "all zones normal")
                       + (f"; {len(b.proposals)} proposal(s) waiting for approval" if b.proposals else "")
                       + (f"; {len(b.pending)} action(s) being verified" if b.pending else ""))
            return {"summary": summary, "t_s": self._t(), "city_id": st.city.city_id, "mode": b.mode,
                    "knowledge_level": b.knowledge.level, "guardian_online": b.guardian_ok is not False,
                    "twin_online": b.twin_ok, "zones": zones, "switches": sw,
                    "pending_proposals": [p.as_dict(st, b.clock()) for p in b.proposals.values()],
                    "last_alert": alerts[-1].summary if alerts else None,
                    "events_cursor": len(b.log.events)}

    def zone(self, zone_id: str) -> dict:
        with self.lock:
            z = self._zone(zone_id)
            f = z["forecast"]
            z["summary"] = (f"{zone_id} at {z['load_pct'] if z['load_pct'] is not None else z['load_kw']}"
                            f"{'%' if z['load_pct'] is not None else ' kW'} ({z['status']})"
                            + (f", expected peak {f['expected_peak_kw']} kW within {f['horizon_s']:g} s, "
                               f"P(overload) {round(100 * (f['p_overload'] or 0))}%" if f else ", forecast warming up")
                            + f", sensor {z['sensor']['trust']}")
            return z

    def forecast(self, zone_id: str, horizon_s: Optional[float] = None) -> dict:
        with self.lock:
            b, st = self.brain, self.brain.state
            if zone_id not in st.zones:
                raise ValueError(f"unknown zone_id '{zone_id}'")
            fc = st.zones[zone_id].model.snapshot()
            if not fc.ready:
                return {"zone_id": zone_id, "ready": False, "summary": f"{zone_id}: forecast warming up "
                        f"({fc.n}/{b.cfg.forecast.warmup_samples} samples)"}
            hs = sorted({15.0, 30.0, b.cfg.forecast.horizon_s, *( [float(horizon_s)] if horizon_s else [])})
            k = b.cfg.forecast.band_sigma
            pts = [{"in_s": h, "expected_kw": _r(fc.at(h)), "upper_kw": _r(fc.upper(h, k)),
                    "j1_plan_kw": _r(b.day_ahead.expected(zone_id, b.tod() + h) if b.day_ahead else None)} for h in hs]
            return {"zone_id": zone_id, "ready": True, "trend_kw_per_s": round(fc.trend, 2), "points": pts,
                    "method": "rolling least-squares trend + regression prediction interval; J-1 = smoothed yesterday",
                    "summary": f"{zone_id}: {pts[-1]['expected_kw']} kW expected in {pts[-1]['in_s']:g} s "
                               f"(upper {pts[-1]['upper_kw']} kW, trend {fc.trend:+.2f} kW/s)"}

    def evaluate_options(self) -> dict:
        with self.lock:
            o = self.brain.options()
            o["summary"] = (f"zones at risk: {', '.join(o['zones_at_risk']) or 'none'}; "
                            f"{len(o['ranked'])} option(s) better than doing nothing; recommendation: {o['recommendation']}")
            return o

    def what_if(self, switch_id: str) -> dict:
        with self.lock:
            r = self.brain.what_if(switch_id)
            if r.get("known") is False or "after" not in r:
                r["summary"] = f"{switch_id}: {r.get('why')}"
            else:
                g = r.get("gate")
                r["summary"] = (f"{r['action']}: " + ("better than doing nothing" if r["better_than_doing_nothing"]
                                                     else f"not chosen ({r['why_not']})")
                                + (f"; gate would {'PASS' if g['would_pass'] else 'BLOCK'} ({g['detail']})" if g else ""))
            return r

    def explain(self) -> dict:
        """Plain-language account: the last action and its result, then what is going on now."""
        with self.lock:
            evs = self.brain.log.events
            keep = {"risk_detected", "candidates_evaluated", "decision", "gate_passed", "gate_blocked", "proposal",
                    "command_sent", "verification", "actuator_anomaly", "operator_alert", "restoration", "risk_cleared",
                    "degraded_mode", "orchestrator", "learned_effect", "verdict_change", "verdict_flapping",
                    "reading_suspect", "plan_deviation", "plan", "consistency"}
            rel = [e for e in evs if e.kind in keep]
            dec = next((e for e in reversed(rel) if e.kind == "decision"), None)
            ver = next((e for e in reversed(rel) if e.kind == "verification" and dec
                        and e.correlation_id == dec.correlation_id), None)
            if dec is not None:
                i = rel.index(dec)
                start = max((j for j in range(i + 1) if rel[j].kind == "risk_detected"), default=max(0, i - 3))
                steps_src = rel[max(0, start - 2):][-16:]
            else:
                steps_src = rel[-10:]
            steps = [{"at": e.ts.isoformat(), "kind": e.kind, "text": e.summary} for e in steps_src]
            now = self.status()["summary"]
            if dec is None:
                head = ("No action taken so far" + (f"; latest: {rel[-1].summary}" if rel else "; all zones normal")
                        + ".")
            else:
                result = ver.payload.get("outcome") if ver else "not verified yet"
                head = f"Last action: {dec.summary}. Result: {result}."
            at_risk = [z for z, s in self.brain.state.zones.items() if s.at_risk]
            blocked = [e.summary for e in rel[-30:] if e.kind in ("gate_blocked", "operator_alert")
                       and any(e.summary.startswith(z) or f" {z} " in e.summary for z in at_risk)]
            return {"summary": f"{head} Now: {now}." + (f" Open issue: {blocked[-1]}" if blocked else ""),
                    "last_decision": dec.summary if dec else None,
                    "result": (ver.payload.get("outcome") if ver else None) if dec else None,
                    "steps": steps}

    def events(self, since: int = 0, kinds: Optional[list[str]] = None, limit: int = 50) -> dict:
        with self.lock:
            evs = self.brain.log.events
            since = max(0, min(int(since), len(evs)))
            out = []
            for i in range(since, len(evs)):
                e = evs[i]
                if e.kind == "forecast" and not (kinds and "forecast" in kinds):
                    continue
                if kinds and e.kind not in kinds:
                    continue
                out.append({"seq": i, "at": e.ts.isoformat(), "kind": e.kind, "summary": e.summary,
                            "correlation_id": e.correlation_id})
                if len(out) >= limit:
                    return {"events": out, "next_cursor": i + 1, "more": True}
            return {"events": out, "next_cursor": len(evs), "more": False}

    def proposals(self) -> dict:
        with self.lock:
            b = self.brain
            ps = [p.as_dict(b.state, b.clock()) for p in b.proposals.values() if b.clock() < p.expires_t]
            return {"mode": b.mode, "proposals": ps,
                    "summary": f"{len(ps)} proposal(s) waiting" + (f": {', '.join(p['action'] for p in ps)}" if ps else "")
                               + ("" if b.mode == "advisory" else " (autonomous mode: the agent acts itself)")}

    def card(self) -> dict:
        b = self.brain
        return {
            "name": AGENT_NAME, "version": AGENT_VERSION, "layer": "City Brain", "domain": "energy",
            "role": "Keeps every zone of the power grid within capacity by forecasting overloads and operating grid "
                    "switches through the City Twin, only on evidence the Guardian trusts.",
            "autonomy": {"current_mode": b.mode, "modes": {
                "autonomous": "the agent acts on its own; the orchestrator supervises (query, veto, switch mode)",
                "advisory": "the agent only proposes; an action happens when the orchestrator approves it"}},
            "inputs": ["Twin: power-load readings, switch status (REST or MCP)", "Guardian: trust verdicts",
                       "orchestrator / other domains: priority zones, vetoes, approvals"],
            "outputs": ["switch commands to the Twin (open/close)", "audit events", "alerts", "explanations"],
            "guarantees": [
                "never acts on readings the Guardian marks untrusted, or on flapping/expired verdicts (except the "
                "documented degraded mode: reversible relief of a priority zone)",
                "never de-energizes a priority zone; never pushes a zone over capacity",
                "load shedding only as a last resort, only on MEASURED overload",
                "every command verified on the sensors; failed actuators reported to the Guardian",
                "the orchestrator cannot bypass the safety & trust gate"],
            "limits": ["load-transfer model, not power flow", "linear-trend forecast (45 s horizon)",
                       "one change per zone at a time", "acts only on switches whose effect it knows"],
            "tools": [t for t in TOOL_NAMES],
        }

    # ------------------------------------------------------------------ steer tools
    def set_mode(self, mode: str, reason: str = "") -> dict:
        with self.lock:
            r = self.brain.set_mode(mode, reason)
            r["summary"] = f"mode is now {mode} (was {r['previous']})"
            return r

    def veto(self, switch_id: str, duration_s: float = 300, reason: str = "") -> dict:
        with self.lock:
            r = self.brain.veto(switch_id, duration_s, reason)
            r["summary"] = f"{switch_id} vetoed for {r['vetoed_for_s']:.0f} s: the agent will not operate it"
            return r

    def lift_veto(self, switch_id: str) -> dict:
        with self.lock:
            r = self.brain.lift_veto(switch_id)
            r["summary"] = f"veto on {switch_id} " + ("lifted" if r["was_vetoed"] else "was not active")
            return r

    def approve(self, proposal_id: str) -> dict:
        with self.lock:
            r = self.brain.approve(proposal_id)
            r["summary"] = (f"executed {r['action']}; verification follows" if r["executed"]
                            else f"not executed: {r['why']}")
            return r

    def set_priority(self, zone_id: str, reason: str, ttl_s: float = 600, source: str = "orchestrator") -> dict:
        with self.lock:
            ok = self.brain.add_priority_hint(PriorityHint(
                zone_id=zone_id, source=source, reason=reason,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=max(1.0, float(ttl_s)))))
            return {"accepted": ok, "summary": (f"{zone_id} is a priority zone ({reason})" if ok
                                                else f"refused (unknown zone '{zone_id}' or bad expiry)")}


def _r(v):
    return None if v is None else round(v, 1)


# ============================================================================ MCP server

TOOL_NAMES = ["energy_get_status", "energy_get_zone", "energy_forecast", "energy_evaluate_options", "energy_what_if",
              "energy_explain", "energy_get_events", "energy_list_proposals", "energy_describe_agent",
              "energy_set_mode", "energy_veto_switch", "energy_lift_veto", "energy_approve_proposal",
              "energy_set_priority_zone"]


def _server_class():
    try:
        from mcp.server.mcpserver import MCPServer
        return MCPServer, True
    except ImportError:
        from mcp.server.fastmcp import FastMCP
        return FastMCP, False


def build_mcp(svc: AgentService, json_response: bool = True):
    from mcp.server.transport_security import TransportSecuritySettings
    Server, sdk2 = _server_class()
    sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    instructions = ("Energy Brain agent of the City Brain (power grid). Start with energy_get_status. Use "
                    "energy_explain to tell a human what happened, energy_what_if before steering, energy_get_events "
                    "with the returned cursor to follow activity. The agent acts on its own in autonomous mode; "
                    "switch to advisory to approve each action yourself. You cannot bypass its safety & trust gate.")
    server = Server(name=AGENT_NAME, instructions=instructions) if sdk2 else \
        Server(name=AGENT_NAME, instructions=instructions, json_response=json_response, transport_security=sec)

    def safe(fn, *a, **k) -> dict:
        try:
            return fn(*a, **k)
        except ValueError as e:
            return {"error": str(e), "summary": f"refused: {e}"}

    @server.tool(description="Overall situation of the city's power grid: every zone (load, capacity, forecast, "
                             "overload probability, sensor trust), every switch, the agent's mode, pending proposals "
                             "and the latest alert. Call this first.")
    def energy_get_status() -> dict[str, Any]:
        return svc.status()

    @server.tool(description="Details of one zone: load, capacity, 45 s forecast with overload probability, "
                             "J-1 plan, sensor trust, priority.")
    def energy_get_zone(zone_id: str) -> dict[str, Any]:
        return safe(svc.zone, zone_id)

    @server.tool(description="Load forecast of a zone at 15, 30 and 45 s (and an optional extra horizon), with "
                             "upper band and the J-1 plan for the same moments.")
    def energy_forecast(zone_id: str, horizon_s: Optional[float] = None) -> dict[str, Any]:
        return safe(svc.forecast, zone_id, horizon_s)

    @server.tool(description="Compare every possible switching action with doing nothing, right now: predicted "
                             "load and overload probability per zone, why each rejected option was rejected, and "
                             "the recommendation. Does not act.")
    def energy_evaluate_options() -> dict[str, Any]:
        return svc.evaluate_options()

    @server.tool(description="Dry run: what toggling one switch would do now (predicted loads, overload "
                             "probabilities) and whether the safety & trust gate would allow it. Does not act.")
    def energy_what_if(switch_id: str) -> dict[str, Any]:
        return safe(svc.what_if, switch_id)

    @server.tool(description="Plain-language explanation of the latest episode: what the agent saw, the options "
                             "it compared, what the gate said, what it did and whether it worked.")
    def energy_explain() -> dict[str, Any]:
        return svc.explain()

    @server.tool(description="Audit events after a cursor (use next_cursor from the previous call). Optional kinds "
                             "filter, e.g. ['decision','operator_alert','gate_blocked','proposal'].")
    def energy_get_events(since: int = 0, kinds: Optional[list[str]] = None, limit: int = 50) -> dict[str, Any]:
        return svc.events(since, kinds, min(max(1, limit), 200))

    @server.tool(description="Actions the agent proposes and is waiting for you to approve (advisory mode).")
    def energy_list_proposals() -> dict[str, Any]:
        return svc.proposals()

    @server.tool(description="What this agent is, what it can and cannot do, its guarantees and its tools.")
    def energy_describe_agent() -> dict[str, Any]:
        return svc.card()

    @server.tool(description="Set who acts: 'autonomous' (the agent operates switches itself) or 'advisory' "
                             "(it only proposes; use energy_approve_proposal). Give a reason for the audit log.")
    def energy_set_mode(mode: str, reason: str = "") -> dict[str, Any]:
        return safe(svc.set_mode, mode, reason)

    @server.tool(description="Forbid the agent from operating one switch for duration_s seconds (max 3600), e.g. "
                             "during maintenance. Restrictive only: it can never make the agent act.")
    def energy_veto_switch(switch_id: str, duration_s: float = 300, reason: str = "") -> dict[str, Any]:
        return safe(svc.veto, switch_id, duration_s, reason)

    @server.tool(description="Remove a veto set with energy_veto_switch.")
    def energy_lift_veto(switch_id: str) -> dict[str, Any]:
        return safe(svc.lift_veto, switch_id)

    @server.tool(description="Approve a proposal (advisory mode). It is re-checked against the current situation "
                             "and the safety & trust gate before anything moves; the answer says if it executed.")
    def energy_approve_proposal(proposal_id: str) -> dict[str, Any]:
        return safe(svc.approve, proposal_id)

    @server.tool(description="Mark a zone as priority for ttl_s seconds (max 900), e.g. a flood pump is running "
                             "or a hospital is on backup. Priority zones are never switched off and keep 15% "
                             "headroom.")
    def energy_set_priority_zone(zone_id: str, reason: str, ttl_s: float = 600) -> dict[str, Any]:
        return safe(svc.set_priority, zone_id, reason, ttl_s)

    @server.resource("energy://agent-card", name="agent-card", description="Energy Brain agent card",
                     mime_type="application/json")
    def agent_card() -> str:
        return json.dumps(svc.card())

    return server, sdk2, sec


def mcp_http_app(svc: AgentService, api_key: Optional[str] = None, json_response: bool = True):
    """Starlette app serving MCP at /mcp, protected by X-API-Key when api_key is set."""
    server, sdk2, sec = build_mcp(svc, json_response)
    app = server.streamable_http_app(json_response=json_response, transport_security=sec) if sdk2 \
        else server.streamable_http_app()
    if api_key:
        import secrets
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        class Auth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                key = request.headers.get("x-api-key")
                if not (key and secrets.compare_digest(key, api_key)):
                    with svc.lock:
                        svc.brain.log.emit("security", "agent MCP call refused: bad or missing orchestrator key")
                    return JSONResponse({"detail": "missing or invalid X-API-Key"}, status_code=401)
                return await call_next(request)
        app.add_middleware(Auth)
    return app


# ============================================================================ notifications

class Notifier:
    """Pushes important events to the orchestrator's webhook from a background thread (never blocks the loop)."""

    def __init__(self, url: str, kinds: list[str], api_key: Optional[str] = None):
        import httpx
        self.url, self.kinds = url, set(kinds)
        self.q: queue.Queue = queue.Queue(maxsize=1000)
        self.client = httpx.Client(timeout=2.0, headers={"X-API-Key": api_key} if api_key else {})
        threading.Thread(target=self._run, daemon=True).start()

    def __call__(self, evt) -> None:
        if evt.kind in self.kinds:
            try:
                self.q.put_nowait(evt.model_dump(mode="json"))
            except queue.Full:
                pass

    def _run(self) -> None:
        while True:
            body = self.q.get()
            try:
                self.client.post(self.url, json={"agent": AGENT_NAME, "event": body})
            except Exception:
                pass


# ============================================================================ CLI

def main() -> None:
    from .adapters import PartnerError, make_guardian, make_twin
    from .brain import EnergyBrain
    from .config import load_city, load_config
    from .eventlog import EventLog

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--city-file", default=None)
    ap.add_argument("--transport", choices=["http", "stdio"], default="http")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8300)
    a = ap.parse_args()

    cfg = load_config(a.config)
    oc = cfg.orchestrator
    notifier = Notifier(oc.notify_url, oc.notify_kinds, oc.api_key) if oc.notify_url else None
    log = EventLog(cfg.log_dir, "energy-agent", sink=notifier, echo=a.transport == "http")
    city_file = a.city_file or cfg.city_file
    brain = EnergyBrain(cfg, make_twin(cfg.twin), make_guardian(cfg.guardian), log,
                        local_city=load_city(city_file) if city_file else None)
    while True:
        try:
            brain.start()
            break
        except PartnerError as e:
            log.emit("partner_error", f"{e} -- retrying in 3 s")
            time.sleep(3)
    brain.mode = oc.start_mode
    svc = AgentService(brain)

    def loop():
        while True:
            t0 = time.monotonic()
            with svc.lock:
                brain.step()
            time.sleep(max(0.0, cfg.poll_interval_s - (time.monotonic() - t0)))
    threading.Thread(target=loop, daemon=True).start()

    if a.transport == "stdio":
        server, _, _ = build_mcp(svc)
        server.run("stdio")
    else:
        import uvicorn
        print(f"Energy Brain agent: MCP at http://{a.host}:{a.port}/mcp (mode {brain.mode})")
        uvicorn.run(mcp_http_app(svc, oc.api_key), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

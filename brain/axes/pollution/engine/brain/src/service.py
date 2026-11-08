"""
City Brain as a network service (HTTP + JSON).

    python -m src.service --config config/brain.json

What it exposes
  POST /v1/readings   Twin -> Brain. One message, or a JSON array of messages
                      (get_sensor_reading, get_truck_state). Bearer token required.
  GET  /v1/health     liveness, no token.
  GET  /v1/events     timeline events (?after=N), read-only.
  GET  /v1/state      current bin colors, blocked sensors, trucks, read-only.
  GET  /timeline      the live timeline screen (R3).

What it calls (addresses from the config file, token in the Authorization header)
  Guardian  POST <guardian.url><verdict_path>   one verdict per reading
  Twin      POST <twin.url><dispatch_path>      one dispatch per new stop
  Twin      GET  <twin.url><map_path>           the city map, at startup

Received messages are queued; every `cycle_s` seconds the Brain runs one
CityBrain.step() on what arrived. The decision logic is the same code that
the standalone scenarios run with the mocks.
"""
import argparse
import hmac
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from .air import AirBrain, AirConfig
from .audit_log import AuditLog
from .bin_status import BinConfig
from .brain import BrainConfig, CityBrain
from .config import BrainSettings, ConfigError, load
from .graph import Graph
from .http_clients import HttpGuardianClient, HttpTwinClient, PartnerError
from .planner import PlannerConfig

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


def _parse_ts(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_twin(settings: BrainSettings, brain_token: str):
    t = settings.twin
    if t.transport == "mcp_stdio":
        from .mcp_client import McpStdioClient
        from .mcp_twin import McpTwinClient
        m = t.mcp
        client = McpStdioClient(m.command, m.env, m.cwd, m.timeout_s)
        return McpTwinClient(client, m.origin_lat, m.origin_lon, m.default_capacity_kg,
                             m.zone_types, focus_on_order=m.focus_on_order)
    return HttpTwinClient(t.url, brain_token, t.dispatch_path, t.map_path, t.timeout_s)


def load_map(settings: BrainSettings, twin: HttpTwinClient, log: AuditLog) -> Graph:
    """The map from a file, or from the Twin (retried: the Twin may start later)."""
    now = datetime.now(timezone.utc)
    if settings.map.source == "file":
        with open(settings.map.path, encoding="utf-8") as f:
            graph = Graph.from_dict(json.load(f))
        log.log(now, "city_brain", "map_loaded", source=settings.map.path,
                nodes=len(graph.nodes), warnings=graph.warnings)
        return graph
    last_error = None
    for attempt in range(1, settings.map.retries + 1):
        try:
            graph = Graph.from_dict(twin.get_map())
            log.log(datetime.now(timezone.utc), "city_brain", "map_loaded",
                    source=twin.map_url, nodes=len(graph.nodes), warnings=graph.warnings)
            return graph
        except (PartnerError, KeyError, ValueError, TypeError) as e:
            last_error = e
            print(f"[brain] map not available yet ({e}); attempt {attempt}/"
                  f"{settings.map.retries}", file=sys.stderr)
            time.sleep(settings.map.retry_every_s)
    raise ConfigError(f"could not get the city map from the Twin: {last_error}")


class BrainService:
    def __init__(self, settings: BrainSettings, guardian=None, twin=None,
                 graph: Optional[Graph] = None, log: Optional[AuditLog] = None):
        self.settings = settings
        self.pull = settings.twin.transport == "mcp_stdio"
        # HTTP Twin pushes readings with its token; an MCP Twin is read by the Brain (pull).
        self._accepted_token = None if self.pull else settings.twin_token
        brain_token = settings.brain_token
        self.log = log or AuditLog(settings.log_path, wall_clock=True)
        self.twin = twin or make_twin(settings, brain_token)
        self.guardian = guardian or HttpGuardianClient(
            settings.guardian.url, brain_token, settings.guardian.verdict_path,
            settings.guardian.timeout_s, settings.guardian.retry_after_s)
        city = settings.city
        if graph is None:
            if city.decision == "crew":
                graph = Graph()                     # no routing: the Twin's crew empties the bin
            elif self.pull and settings.map.source == "twin":
                raise ConfigError("an MCP Twin has no map tool: use map.source = file, "
                                  "or city.decision = crew")
            else:
                graph = load_map(settings, self.twin, self.log)
        config = BrainConfig(
            bin=BinConfig(red_pct=city.red_pct, green_pct=city.green_pct,
                          pass_interval_minutes=dict(city.pass_interval_minutes),
                          fallback_pass_interval_minutes=city.fallback_pass_interval_minutes,
                          history_size=city.history_size,
                          emptied_drop_pct=city.emptied_drop_pct),
            silence_factor=city.silence_factor,
            planner=PlannerConfig(truck_capacity_kg=city.truck_capacity_kg,
                                  horizon_stops=city.horizon_stops,
                                  depot_node=city.depot_node),
            reading_interval_s=city.reading_interval_s,
            decision=city.decision,
            crew_max_orders_per_cycle=city.crew_max_orders_per_cycle,
            crew_reorder_after_s=city.crew_reorder_after_s)
        self._twin_down = False
        from .agent import BrainAgent
        self.agent = BrainAgent(self) if settings.agent.enabled else None
        tok = os.environ.get(settings.agent.token_env, "")
        self._agent_token = tok if len(tok) >= 16 else None
        self.brain = CityBrain(graph, self.guardian, self.twin, self.log, config)
        a = settings.air
        self.air = AirBrain(self.guardian, self.twin, self.log, AirConfig(
            lag_s=a.lag_s, silence_s=a.silence_s, min_level_s=a.min_level_s,
            exit_margin=a.exit_margin, exit_readings=a.exit_readings,
            warm_months=tuple(a.warm_months)), outage=self.brain) if a.enabled else None
        self._inbox: List = []
        self._inbox_lock = threading.Lock()
        self._step_lock = threading.Lock()
        self._city_now: Optional[datetime] = None
        self._stop = threading.Event()
        self.state: dict = {"city_time": None, "bins": {}, "blocked": {}, "trucks": {}}
        self.started_at = datetime.now(timezone.utc)
        self.httpd: Optional[ThreadingHTTPServer] = None

    # ---- inputs ------------------------------------------------------------
    def check_token(self, header: Optional[str]) -> bool:
        if self._accepted_token is None:            # pull mode: nobody pushes readings
            return False
        if not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].encode(), self._accepted_token.encode())

    def receive(self, payload) -> int:
        messages = payload if isinstance(payload, list) else [payload]
        with self._inbox_lock:
            self._inbox.extend(messages)
        return len(messages)

    # ---- one decision cycle --------------------------------------------------
    def run_cycle(self) -> int:
        """Process everything received since the last cycle. Returns how many
        messages were processed (0 = nothing to do)."""
        if self.pull:
            self._pull_from_twin()
        with self._inbox_lock:
            batch, self._inbox = self._inbox, []
        if not batch:
            return 0
        def kind(m):
            if not isinstance(m, dict):
                return "bin"                     # rejected by the bin axis, with a reason
            if m.get("tool") == "get_truck_state":
                return "truck"
            return "air" if m.get("type") == "air_quality" else "bin"
        trucks = [m for m in batch if kind(m) == "truck"]
        air = [m for m in batch if kind(m) == "air"]
        bins = [m for m in batch if kind(m) == "bin"]
        now = self._clock(batch)
        with self._step_lock:
            self.brain.step(bins, trucks, now)
            if self.air is not None:
                self.air.step(air, now)          # also with no reading: checks silent sensors
            self.state = self._snapshot(now)
        return len(batch)

    def _pull_from_twin(self) -> None:
        """MCP Twin: read every bin once per cycle. An outage is logged once."""
        now = datetime.now(timezone.utc)
        try:
            messages = list(self.twin.fetch_readings())
            if self.air is not None and hasattr(self.twin, "fetch_air_readings"):
                messages += self.twin.fetch_air_readings()
        except Exception as e:
            if not self._twin_down:
                self._twin_down = True
                self.log.log(now, "city_brain", "twin_unreachable", error=str(e))
            return
        if self._twin_down:
            self._twin_down = False
            self.log.log(now, "city_brain", "twin_recovered")
        for w in getattr(self.twin, "warnings", [])[:]:
            self.log.log(now, "city_brain", "twin_warning", error=w)
            self.twin.warnings.remove(w)
        self.receive(messages)

    def _clock(self, batch) -> datetime:
        if self.settings.clock == "wall":
            return datetime.now(timezone.utc)
        stamps = [t for t in (_parse_ts(m.get("timestamp")) for m in batch
                              if isinstance(m, dict)) if t]
        latest = max(stamps) if stamps else datetime.now(timezone.utc)
        if self._city_now and latest < self._city_now:
            latest = self._city_now            # city time never goes backwards
        self._city_now = latest
        return latest

    def _snapshot(self, now: datetime) -> dict:
        b = self.brain
        plans = b.planner.plans
        return {
            "city_time": now.isoformat().replace("+00:00", "Z"),
            "bins": {sid: {"color": s.color.value, "fill_pct": round(s.fill_pct, 1),
                           "projected_pct": None if s.projected_pct is None
                           else round(s.projected_pct, 1),
                           "reason": s.reason}
                     for sid, s in sorted(b.status.items())},
            "blocked_bins": dict(sorted(b.untrusted.items())),
            "blocked": dict(sorted({**b.untrusted,
                                    **(self.air.untrusted if self.air else {})}.items())),
            "air": self.air.snapshot() if self.air else {},
            "trucks": {tid: {"status": t.status.value, "load_kg": t.current_load_kg,
                             "next_stop": plans[tid].committed.bin_id
                             if tid in plans and plans[tid].committed else None}
                       for tid, t in sorted(b.trucks.items())},
        }

    def loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.run_cycle()
            except Exception as e:                  # never let one cycle kill the layer
                self.log.log(datetime.now(timezone.utc), "city_brain", "cycle_error",
                             error=f"{type(e).__name__}: {e}")
            self._stop.wait(max(0.0, self.settings.cycle_s - (time.monotonic() - started)))

    # ---- server --------------------------------------------------------------
    def serve(self, host: Optional[str] = None, port: Optional[int] = None,
              block: bool = True) -> ThreadingHTTPServer:
        host = host or self.settings.listen.host
        port = self.settings.listen.port if port is None else port
        self.httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.loop, daemon=True, name="brain-cycle").start()
        if block:
            try:
                self.httpd.serve_forever()
            finally:
                self.stop()
        else:
            threading.Thread(target=self.httpd.serve_forever, args=(0.1,), daemon=True,
                             name="brain-http").start()
        return self.httpd

    def stop(self) -> None:
        self._stop.set()
        mcp = getattr(self.twin, "mcp", None)
        if mcp is not None:
            mcp.close()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def _make_handler(service: BrainService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CityBrain/1.0"

        def log_message(self, *args):          # quiet: the audit log is the record
            pass

        def _send(self, code: int, body, content_type: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/v1/health":
                self._send(200, {"status": "ok", "layer": "city_brain",
                                 "started_at": service.started_at.isoformat()})
            elif url.path == "/v1/events":
                try:
                    after = int(parse_qs(url.query).get("after", ["0"])[0])
                except ValueError:
                    return self._send(400, {"error": "after must be an integer"})
                self._send(200, {"events": service.log.since(after)[:2000]})
            elif url.path == "/v1/state":
                self._send(200, service.state)
            elif url.path == "/v1/agent/card":
                if service.agent is None:
                    return self._send(404, {"error": "agent interface disabled in the config"})
                self._send(200, service.agent.card())
            elif url.path in ("/", "/timeline"):
                with open(os.path.join(STATIC_DIR, "timeline.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, {"error": f"unknown path {url.path}"})

        def _agent_call(self):
            agent = service.agent
            if agent is None:
                return self._send(404, {"error": "agent interface disabled in the config"})
            if service._agent_token is None:
                return self._send(503, {"error": f"agent interface needs "
                                                 f"{service.settings.agent.token_env} (16+ characters)"})
            header = self.headers.get("Authorization") or ""
            if not (header.startswith("Bearer ") and hmac.compare_digest(
                    header[7:].encode(), service._agent_token.encode())):
                service.log.log(datetime.now(timezone.utc), "city_brain", "auth_refused",
                                client=self.client_address[0], path="/v1/agent/call")
                return self._send(401, {"error": "missing or wrong Bearer token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > service.settings.max_body_bytes:
                    return self._send(413, {"error": "body too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
                name, args = body["tool"], body.get("arguments") or {}
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {"error": f"body must be {{\"tool\": ..., \"arguments\": {{...}}}}: {e}"})
            tool = agent.tools.get(name)
            if tool is not None and tool.kind == "act" and not service.settings.agent.allow_act:
                return self._send(403, {"error": f"'{name}' is an act tool; agent.allow_act is false"})
            caller = self.headers.get("X-Caller") or self.client_address[0]
            from .agent import AgentError
            try:
                return self._send(200, agent.call(name, args, caller=str(caller)[:64]))
            except AgentError as e:
                code = {"unknown_tool": 404, "not_found": 404, "invalid_arguments": 400,
                        "no_live_brain": 503}.get(e.code, 409)
                return self._send(code, {"error": {"code": e.code, "message": e.message}})

        def do_POST(self):
            url = urlparse(self.path)
            if url.path == "/v1/agent/call":
                return self._agent_call()
            if url.path != "/v1/readings":
                return self._send(404, {"error": f"unknown path {url.path}"})
            if not service.check_token(self.headers.get("Authorization")):
                service.log.log(datetime.now(timezone.utc), "city_brain", "auth_refused",
                                client=self.client_address[0], path=url.path)
                return self._send(401, {"error": "missing or wrong Bearer token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, {"error": "bad Content-Length"})
            if length > service.settings.max_body_bytes:
                return self._send(413, {"error": f"body larger than "
                                                 f"{service.settings.max_body_bytes} bytes"})
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                service.log.log(datetime.now(timezone.utc), "city_brain", "rejected_message",
                                tool=None, id=None, error=f"body is not JSON: {e}")
                return self._send(400, {"error": f"body is not JSON: {e}"})
            n = service.receive(payload)
            # Content is validated in the next cycle; bad messages are logged there.
            self._send(202, {"accepted": n})

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the City Brain service.")
    parser.add_argument("--config", help="path to the config file (default config/brain.json)")
    args = parser.parse_args(argv)
    try:
        settings = load(args.config)
        service = BrainService(settings)
    except ConfigError as e:
        print(f"[brain] configuration problem: {e}", file=sys.stderr)
        return 2
    s = settings.listen
    print(f"[brain] listening on http://{s.host}:{s.port}  "
          f"(timeline: http://{'127.0.0.1' if s.host == '0.0.0.0' else s.host}:{s.port}/timeline)")
    twin = settings.twin.url or " ".join(settings.twin.mcp.command)
    print(f"[brain] twin ({settings.twin.transport}) = {twin}   guardian = {settings.guardian.url}")
    try:
        service.serve()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

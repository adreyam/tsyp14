"""
City Brain HTTP API (standard library only), for the control center and for partners.

Read-only, no token (local timeline screen):
    GET  /v1/health                    {"status": "ok"}
    GET  /v1/status                    source (live|mock), Twin connection, Guardian, counters, running scenario
    GET  /v1/timeline?after=N&limit=M  audit records (event, verdict, decision, conflict, action, feedback, ...)
    GET  /v1/map                       devices, recent events and actions with their places, incidents, units, lots
    GET  /v1/top                       the 3 most important situations of each axis (12)
    GET  /v1/scenarios                 the Test Book scenarios and their last result
    GET  /v1/schemas                   JSON Schema of Event, Verdict, Action and A2A task
    GET  /v1/a2a                       A2A: agent cards (skills of each axis), counters, the latest tasks
    GET  /v1/a2a/agents                the agent cards only
    GET  /v1/a2a/tasks?limit=N         the latest A2A tasks, newest first;  GET /v1/a2a/tasks/<task_id>  one task

Write, `Authorization: Bearer <BRAIN_TOKEN>`:
    POST /v1/events                    one Event or a list (a partner pushing data); 202, or 400 with the reason
    POST /v1/verdicts                  one Verdict or a list (a Guardian pushing verdicts)
    POST /v1/scenarios/<ID>/run        play a Test Book scenario (mock source; Twin-command scenarios also live)
    POST /v1/scenarios/stop            stop the running scenario; its Twin changes are undone, the result is STOPPED
    POST /v1/a2a/tasks                 send an A2A task to an axis agent {from_agent, to_agent, skill, input, reason}
                                       (an axis engine or an operator tool); 202 with the task, follow it with GET
    POST /v1/a2a/tasks/<id>/cancel     cancel an open task you sent
"""
from __future__ import annotations

import hmac
import json
import re
import time

from pydantic import ValidationError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .brain import Brain
from .contracts import A2ATask, explain_validation, json_schemas
from .scenario import load_index, load_scenarios, run_in_background


def make_handler(brain: Brain):
    token = brain.cfg.brain.token()
    max_body = brain.cfg.brain.max_body_bytes
    results: dict = {}

    class H(BaseHTTPRequestHandler):
        server_version = "CityBrain/1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, obj: Any) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass                      # the browser closed the tab or the poll was cancelled: nothing to answer

        def _err(self, code: int, err: str, msg: str) -> None:
            self._send(code, {"error": {"code": err, "message": msg}})

        def _authorised(self) -> bool:
            got = self.headers.get("Authorization", "")
            if not token:
                self._err(503, "no_token", "the Brain has no token configured (set BRAIN_TOKEN)")
                return False
            if not got.startswith("Bearer ") or not hmac.compare_digest(got[7:].strip(), token):
                brain.audit.write("error", None, brain.source.name, f"refused {self.command} {self.path}: missing or wrong token",
                                  severity="watch", payload={"client": self.client_address[0]})
                self._err(401, "auth_refused", "missing or wrong Authorization: Bearer token")
                return False
            return True

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > max_body:
                self._err(413, "too_large", f"body over {max_body} bytes")
                return None
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = u.path.rstrip("/") or "/"
            try:
                if p == "/v1/health":
                    return self._send(200, {"status": "ok", "source": brain.source.name})
                if p == "/v1/status":
                    return self._send(200, brain.status())
                if p == "/v1/timeline":
                    after, limit = int(q.get("after", 0)), min(int(q.get("limit", 300)), 2000)
                    items = brain.audit.since(after, limit)
                    if q.get("trace"):
                        items = [r for r in items if r.get("trace_id") == q["trace"]]
                    return self._send(200, {"last": brain.audit.last_seq, "items": items})
                if p == "/v1/map":
                    with brain.lock:
                        devices = brain.source.devices() or [
                            {"id": d, "family": r["family"], "kind": r["kind"], "zone": r["zone"], "location": {"x": r["x"], "z": r["z"]}}
                            for d, r in brain.orch.city.registry.items()]
                        return self._send(200, {"source": brain.source.name, "devices": devices,
                                                "events": brain.orch.events[-200:], "actions": brain.orch.actions[-200:],
                                                "city": brain.source.snapshot(),
                                                "quarantined": brain.guardian.status().get("quarantined", {}),
                                                "situations": [s.view() for s in brain.orch.city.active()],
                                                "spotlight": brain.scenario.spot.view() if brain.scenario and brain.scenario.spot
                                                and brain.scenario.state in ("starting", "running") else None,
                                                "green_waves": [{"task": tid, "unit": w["unit"], "incident": w.get("incident"),
                                                                 "junctions": {sig: c for sig, (c, _) in w["held"].items()}}
                                                                for tid, w in getattr(brain.orch.agents.get("transportation"), "waves", {}).items()]})
                if p == "/v1/top":
                    return self._send(200, {"rule": "per axis: unresolved first (active, contained, blocked), then severity "
                                                    "(critical > alert > watch > info), then most recent update; resolved "
                                                    "situations stay 10 minutes", "axes": brain.orch.top(3)})
                if p == "/v1/scenarios":
                    sc = load_scenarios()
                    return self._send(200, {"running": brain.scenario.view() if brain.scenario else None,
                                            "groups": [{"axis": g.get("axis"), "title": g.get("title"), "scenarios": g["scenarios"]}
                                                       for g in load_index()],
                                            "scenarios": [{k: s.get(k) for k in ("id", "name", "axis", "interaction", "layers", "description",
                                                                                 "modes", "duration_s", "focus")} | {"last": results.get(s["id"])}
                                                          for s in sc.values()]})
                if p == "/v1/schemas":
                    return self._send(200, json_schemas())
                if p == "/v1/a2a":
                    with brain.lock:
                        return self._send(200, brain.orch.a2a.view())
                if p == "/v1/a2a/agents":
                    return self._send(200, {"agents": brain.orch.a2a.cards()})
                if p == "/v1/a2a/tasks":
                    with brain.lock:
                        return self._send(200, {"tasks": brain.orch.a2a.recent(min(int(q.get("limit", 100)), 500))})
                m = re.fullmatch(r"/v1/a2a/tasks/([A-Za-z0-9_-]{1,48})", p)
                if m:
                    t = brain.orch.a2a.get(m.group(1))
                    return self._send(200, t.dump()) if t else self._err(404, "unknown_task", f"no A2A task {m.group(1)}")
                self._err(404, "not_found", f"no route GET {p}")
            except ValueError as e:
                self._err(400, "bad_query", str(e))

        def do_POST(self):
            p = urlparse(self.path).path.rstrip("/")
            if not self._authorised():
                return
            raw = self._body()
            if raw is None:
                return
            if p == "/v1/scenarios/stop":
                r = brain.scenario
                if not (r and r.stop()):
                    return self._err(409, "idle", "no scenario is running")
                for _ in range(60):                 # the runner ends at its next tick, then undoes its Twin changes
                    if r.state == "stopped":
                        break
                    time.sleep(0.1)
                results[r.spec["id"]] = r.result or {"verdict": "STOPPED"}
                return self._send(200, r.view() | {"undone": (r.result or {}).get("undone", [])})
            m = re.fullmatch(r"/v1/scenarios/([A-Za-z0-9_-]{1,16})/run", p)
            if m:
                sc = load_scenarios().get(m.group(1))
                if sc is None:
                    return self._err(404, "unknown_scenario", f"no scenario {m.group(1)}")
                if brain.scenario and brain.scenario.state in ("starting", "running"):
                    return self._err(409, "busy", f"scenario {brain.scenario.spec['id']} is still running")
                r = run_in_background(brain, sc)
                results[sc["id"]] = {"started": True, "log": r.log_path.name}
                return self._send(202, r.view())
            if p == "/v1/a2a/tasks":
                try:
                    data = json.loads(raw or b"null")
                    if not isinstance(data, dict):
                        raise ValueError("an A2A task must be a JSON object")
                    if data.get("from_agent") in brain.orch.agents:
                        raise ValueError(f"from_agent '{data['from_agent']}' is an axis agent of the Brain: name your own service "
                                         f"(e.g. energy-engine, operator)")
                    allowed = {"from_agent", "to_agent", "skill", "input", "reason", "context_id", "schema"}
                    extra = set(data) - allowed
                    if extra:
                        raise ValueError(f"unknown field(s) {', '.join(sorted(extra))}; send {', '.join(sorted(allowed - {'schema'}))}")
                    task = A2ATask(**data)
                except ValidationError as e:
                    return self._err(400, "invalid", explain_validation(e, "a2a_task"))
                except ValueError as e:
                    return self._err(400, "invalid", str(e))
                with brain.lock:
                    brain.orch.a2a.submit(task)
                return self._send(202, task.dump())
            m = re.fullmatch(r"/v1/a2a/tasks/([A-Za-z0-9_-]{1,48})/cancel", p)
            if m:
                with brain.lock:
                    t = brain.orch.a2a.get(m.group(1))
                    if t is None:
                        return self._err(404, "unknown_task", f"no A2A task {m.group(1)}")
                    if not t.open:
                        return self._err(409, "closed", f"task {t.task_id} is already {t.state}")
                    brain.orch.a2a.cancel(t, "operator", "canceled over HTTP")
                    return self._send(200, t.dump())
            if p in ("/v1/events", "/v1/verdicts"):
                try:
                    data = json.loads(raw or b"null")
                except ValueError as e:
                    brain.orch.ingest_raw(raw)
                    return self._err(400, "not_json", f"body is not JSON: {e}")
                items = data if isinstance(data, list) else [data]
                if len(items) > 500:
                    return self._err(413, "too_many", "at most 500 messages per request")
                accepted, problems = 0, []
                for i, item in enumerate(items):
                    if p == "/v1/verdicts" and isinstance(item, dict):
                        item.setdefault("schema", "citybrain.verdict/1")
                    with brain.lock:
                        ok, msg = brain.orch.ingest_raw(item, pushed=True)
                    if ok:
                        accepted += 1
                    else:
                        problems.append({"index": i, "problem": msg})
                if problems and not accepted:
                    return self._send(400, {"error": {"code": "invalid", "message": problems[0]["problem"]}, "problems": problems})
                return self._send(202, {"accepted": accepted, "problems": problems})
            self._err(404, "not_found", f"no route POST {p}")

    return H, results


def serve(brain: Brain) -> None:
    handler, _ = make_handler(brain)
    srv = ThreadingHTTPServer((brain.cfg.brain.host, brain.cfg.brain.port), handler)
    brain.start()
    print(f"City Brain on http://{brain.cfg.brain.host}:{brain.cfg.brain.port}  source={brain.source.name}  "
          f"guardian={brain.guardian.mode}  log={brain.audit.path}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        brain.stop()
        srv.server_close()

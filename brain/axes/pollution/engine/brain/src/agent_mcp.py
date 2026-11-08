"""
MCP server for the City Brain agent (stdio, newline-delimited JSON-RPC 2.0,
standard library only). An orchestrator or an MCP host starts it as a child
process and gets the Brain's tools (src/agent.py).

Three modes:
  proxy (default)   talks to a running Brain service over HTTP:
                      python -m src.agent_mcp --url http://127.0.0.1:8100
                    token: environment variable ORCHESTRATOR_TOKEN.
                    If the Brain cannot be reached, the what-if tools and
                    run_scenario still answer, computed locally.
  embedded          starts the Brain service itself, from a config file:
                      python -m src.agent_mcp --embedded --config config/brain.json
  offline           no live Brain at all: what-if tools and scenarios only:
                      python -m src.agent_mcp --offline

Example MCP host entry (from the brain/ folder):
  {"mcpServers": {"city-brain": {"command": "python",
     "args": ["-m", "src.agent_mcp", "--url", "http://127.0.0.1:8100"],
     "cwd": "<path to brain/>", "env": {"ORCHESTRATOR_TOKEN": "<token>"}}}}
"""
import argparse
import json
import os
import queue
import sys
import threading
import urllib.error
import urllib.request
from typing import Any, Optional

from .agent import AGENT_NAME, AGENT_VERSION, AgentError, BrainAgent

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]


class HttpBackend:
    """Calls the agent of a running Brain service over HTTP."""

    def __init__(self, url: str, token: Optional[str], timeout_s: float = 30.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.local = BrainAgent(None)            # what-if fallback when the Brain is down

    def _req(self, method: str, path: str, body=None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "X-Caller": "mcp"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error")
            except ValueError:
                err = None
            if isinstance(err, dict):
                raise AgentError(err.get("code", f"http_{e.code}"), err.get("message", ""))
            raise AgentError(f"http_{e.code}", str(err or e.reason))
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(f"City Brain unreachable at {self.url}: "
                                  f"{getattr(e, 'reason', e)}")

    def list_tools(self):
        try:
            return self._req("GET", "/v1/agent/card")["tools"]
        except (ConnectionError, AgentError):
            return self.local.list_tools()

    def call(self, name: str, args: dict) -> dict:
        try:
            return self._req("POST", "/v1/agent/call", {"tool": name, "arguments": args})
        except ConnectionError as e:
            tool = self.local.tools.get(name)
            if tool is not None and not tool.needs_brain:
                out = self.local.call(name, args, caller="mcp-local")
                out["served_by"] = "local fallback (City Brain unreachable)"
                return out
            raise AgentError("brain_unreachable", str(e))


class LocalBackend:
    def __init__(self, agent: BrainAgent):
        self.agent = agent

    def list_tools(self):
        return self.agent.list_tools()

    def call(self, name: str, args: dict) -> dict:
        tool = self.agent.tools.get(name)
        svc = self.agent.service
        if tool is not None and tool.kind == "act" and svc is not None \
                and not svc.settings.agent.allow_act:
            raise AgentError("not_allowed", f"'{name}' is an act tool; agent.allow_act is false")
        return self.agent.call(name, args, caller="mcp")


def _content(data: dict, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}],
            "structuredContent": data, "isError": is_error}


def handle(backend, msg: dict) -> Optional[dict]:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None or mid is None:
        return None                                       # notifications and stray answers
    try:
        if method == "initialize":
            asked = str(params.get("protocolVersion", PROTOCOLS[0]))
            result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": AGENT_NAME, "version": AGENT_VERSION},
                      "instructions": ("City Brain agent. Start with brain_status. Read tools "
                                       "explain the city; what-if tools compute without acting; "
                                       "act tools never bypass the Guardian.")}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{k: t[k] for k in ("name", "description", "inputSchema",
                                                     "annotations") if k in t}
                                for t in backend.list_tools()]}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                result = _content(backend.call(str(name), args))
            except AgentError as e:
                result = _content({"error": {"code": e.code, "message": e.message}}, True)
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except Exception as e:                                # never let one call kill the server
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(backend, stdin=sys.stdin, stdout=sys.stdout) -> int:
    lock = threading.Lock()

    def send(message: Any) -> None:
        with lock:
            stdout.write(json.dumps(message, separators=(",", ":"), default=str) + "\n")
            stdout.flush()

    def respond(message: dict) -> None:
        out = handle(backend, message)
        if out is not None:
            send(out)

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(message, list):
            outs = [o for o in (handle(backend, m) for m in message if isinstance(m, dict)) if o]
            if outs:
                send(outs)
        elif isinstance(message, dict) and message.get("method") == "tools/call":
            threading.Thread(target=respond, args=(message,), daemon=True).start()
        elif isinstance(message, dict):
            respond(message)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="MCP server (stdio) for the City Brain agent.")
    p.add_argument("--url", default=os.environ.get("BRAIN_URL", "http://127.0.0.1:8100"),
                   help="running Brain service (proxy mode)")
    p.add_argument("--embedded", action="store_true", help="start the Brain service here")
    p.add_argument("--config", help="config file for --embedded")
    p.add_argument("--offline", action="store_true", help="what-if tools and scenarios only")
    a = p.parse_args(argv)
    if a.offline:
        backend = LocalBackend(BrainAgent(None))
    elif a.embedded:
        from .config import ConfigError, load
        from .service import BrainService
        try:
            service = BrainService(load(a.config))
        except ConfigError as e:
            print(f"[city-brain mcp] {e}", file=sys.stderr)
            return 2
        service.serve(block=False)
        backend = LocalBackend(service.agent)
    else:
        backend = HttpBackend(a.url, os.environ.get("ORCHESTRATOR_TOKEN"))
    return serve(backend)


if __name__ == "__main__":
    sys.exit(main())

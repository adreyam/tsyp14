"""MCP adapter for the Twin: the brief has the Twin expose sensors and actuators
as MCP tools. This is a minimal, dependency-free MCP client speaking the
protocol directly (JSON-RPC 2.0 over Streamable HTTP), so it works with any
server SDK version (Python 1.x FastMCP, 2.x MCPServer, TypeScript, ...).

Tool names are configurable (twin.mcp_tools), so pointing the Brain at a Twin
whose tools are called differently is a config change. Results are read from
`structuredContent` when present, otherwise from the first JSON text block.
"""
from __future__ import annotations

import itertools
import json
from typing import Any, Optional

import httpx
from pydantic import TypeAdapter, ValidationError

from ..config import PartnerConfig
from ..schemas import CityDescriptor, CommandAck, PowerReading, SwitchCommand, SwitchStatus, utcnow
from .base import PartnerError, ReadingsBatch
from .mapping import remap, remap_city
from .rest import _fmt
from .signing import sign

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26"]
DEFAULT_TOOLS = {
    "get_city": "energy_get_city",
    "get_readings": "energy_get_readings",
    "get_switches": "energy_get_switches",
    "send_command": "energy_switch_command",
}


class McpSession:
    """Synchronous MCP Streamable-HTTP session (initialize -> tools/list -> tools/call)."""

    def __init__(self, partner: str, url: str, headers: dict[str, str], timeout: float,
                 client: Optional[httpx.Client] = None, secret: Optional[str] = None):
        self.secret = secret
        self.partner, self.url = partner, url
        self._http = client or httpx.Client(timeout=timeout)
        self._headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                         **headers}
        self._ids = itertools.count(1)
        self.session_id: Optional[str] = None
        self.protocol: Optional[str] = None
        self.tools: set[str] = set()

    # --------------------------------------------------------------- transport
    def _post(self, payload: dict, op: str) -> Optional[dict]:
        h = dict(self._headers)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        body = json.dumps(payload, separators=(",", ":")).encode()
        if self.secret:
            h.update(sign(self.secret, body))
        try:
            r = self._http.post(self.url, content=body, headers=h)
        except httpx.TimeoutException:
            raise PartnerError(self.partner, op, "timed out")
        except httpx.HTTPError as e:
            raise PartnerError(self.partner, op, f"unreachable ({e.__class__.__name__}: {e})")
        if r.status_code in (401, 403):
            raise PartnerError(self.partner, op, f"authentication refused (HTTP {r.status_code}) -- check api_key", False)
        if r.status_code == 404 and self.session_id:
            self.session_id = None          # server forgot our session: re-initialize next call
            raise PartnerError(self.partner, op, "MCP session expired; will re-initialize")
        if r.status_code >= 400:
            raise PartnerError(self.partner, op, f"HTTP {r.status_code}: {r.text[:200]}", r.status_code >= 500)
        if sid := r.headers.get("mcp-session-id"):
            self.session_id = sid
        if "id" not in payload:
            return None                     # notification: 202, no body
        msg = self._parse(r, payload["id"], op)
        if "error" in msg:
            err = msg["error"]
            raise PartnerError(self.partner, op, f"MCP error {err.get('code')}: {err.get('message')}", False)
        return msg.get("result", {})

    def _parse(self, r: httpx.Response, req_id: int, op: str) -> dict:
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for block in r.text.split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in block.splitlines() if l.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if msg.get("id") == req_id:
                    return msg
            raise PartnerError(self.partner, op, "no JSON-RPC response for our request in the event stream", False)
        try:
            return r.json()
        except ValueError:
            raise PartnerError(self.partner, op, "response is not valid JSON", False)

    def _rpc(self, method: str, params: dict, op: str) -> dict:
        return self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}, op)

    # --------------------------------------------------------------- protocol
    def connect(self) -> None:
        res = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSIONS[0], "capabilities": {},
                                       "clientInfo": {"name": "energy-brain", "version": "0.3"}}, "initialize")
        self.protocol = res.get("protocolVersion", PROTOCOL_VERSIONS[0])
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, "initialized")
        tools = self._rpc("tools/list", {}, "tools/list")
        self.tools = {t["name"] for t in tools.get("tools", [])}

    def call(self, name: str, arguments: dict, op: str) -> Any:
        if self.session_id is None and self.protocol is None:
            self.connect()
        if self.tools and name not in self.tools:
            raise PartnerError(self.partner, op, f"tool '{name}' not offered by server (has: {sorted(self.tools)}); "
                               f"map it in config twin.mcp_tools", False)
        try:
            res = self._rpc("tools/call", {"name": name, "arguments": arguments}, op)
        except PartnerError as e:
            if "re-initialize" in e.detail:
                self.protocol = None
                self.connect()
                res = self._rpc("tools/call", {"name": name, "arguments": arguments}, op)
            else:
                raise
        text = next((c.get("text") for c in res.get("content", []) if c.get("type") == "text"), None)
        if res.get("isError"):
            raise PartnerError(self.partner, op, f"tool '{name}' failed: {(text or '')[:200]}", False)
        sc = res.get("structuredContent")
        if sc is not None:
            # SDKs wrap non-object return values as {"result": value}
            return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
        if text is None:
            raise PartnerError(self.partner, op, f"tool '{name}' returned no content", False)
        try:
            return json.loads(text)
        except ValueError:
            raise PartnerError(self.partner, op, f"tool '{name}' returned non-JSON text: {text[:120]}", False)


class McpTwin:
    partner = "twin"
    _status_list = TypeAdapter(list[SwitchStatus])

    def __init__(self, cfg: PartnerConfig, client: Optional[httpx.Client] = None):
        headers = {"User-Agent": "energy-brain/0.3"}
        if cfg.api_key:
            headers[cfg.auth_header] = cfg.api_key
        self.tools = {**DEFAULT_TOOLS, **(cfg.mcp_tools or {})}
        self.s = McpSession("twin", cfg.base_url, headers, cfg.timeout_s, client, cfg.signing_secret)
        self.fmap = cfg.field_map or {}

    def get_city(self) -> CityDescriptor:
        data = remap_city(self.s.call(self.tools["get_city"], {}, "get_city"), self.fmap.get("city"))
        try:
            return CityDescriptor.model_validate(data)
        except ValidationError as e:
            raise PartnerError("twin", "get_city", f"invalid city descriptor: {_fmt(e)}", False)

    def get_readings(self) -> ReadingsBatch:
        data = remap(self.s.call(self.tools["get_readings"], {}, "get_readings"), self.fmap.get("readings"),
                     PowerReading, many=True)
        if not isinstance(data, list):
            raise PartnerError("twin", "get_readings", "expected a list of power_reading", False)
        batch = ReadingsBatch()
        for item in data:
            try:
                batch.valid.append(PowerReading.model_validate(item))
            except ValidationError as e:
                batch.rejected.append((item, _fmt(e)))
        return batch

    def get_switches(self) -> list[SwitchStatus]:
        data = remap(self.s.call(self.tools["get_switches"], {}, "get_switches"), self.fmap.get("switches"),
                     SwitchStatus, many=True)
        try:
            return self._status_list.validate_python(data)
        except ValidationError as e:
            raise PartnerError("twin", "get_switches", f"invalid switch status: {_fmt(e)}", False)

    def send_command(self, cmd: SwitchCommand) -> CommandAck:
        data = self.s.call(self.tools["send_command"], {"command": cmd.model_dump(mode="json")}, "send_command")
        try:
            ack = CommandAck.model_validate(data)
        except ValidationError:
            # Tolerate Twins that do not return our ack shape: a non-error tool result means "received".
            # The Brain never treats an ack as success anyway -- verification decides from the sensors.
            return CommandAck(command_id=cmd.command_id, status="accepted", ts=utcnow(),
                              detail=f"non-standard ack: {json.dumps(data)[:120]}")
        if ack.command_id != cmd.command_id:
            raise PartnerError("twin", "send_command", f"ack is for '{ack.command_id}', expected '{cmd.command_id}'", False)
        return ack

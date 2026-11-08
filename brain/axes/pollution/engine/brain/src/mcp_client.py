"""
Minimal MCP client over stdio (newline-delimited JSON-RPC 2.0), standard library only.

It starts the Twin's MCP server as a child process, does the initialize /
notifications/initialized handshake, then sends tools/call requests and waits
for the matching answer. Progress notifications (live streams) are ignored: the
Brain always asks for one reading (once=true).

If the server dies, the next call starts it again. Every problem raises
McpError, which the Brain treats like any partner failure.
"""
import itertools
import json
import os
import subprocess
import threading
from typing import Dict, List, Optional

PROTOCOL_VERSION = "2025-06-18"


class McpError(Exception):
    """The MCP server could not be reached, or the tool answered with an error."""


class McpStdioClient:
    def __init__(self, command: List[str], env: Optional[Dict[str, str]] = None,
                 cwd: Optional[str] = None, timeout_s: float = 10.0,
                 client_name: str = "city-brain"):
        self.command = command
        self.env = env or {}
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.client_name = client_name
        self.server_info: dict = {}
        self.tools: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._ids = itertools.count(1)
        self._waiting: Dict[int, dict] = {}
        self._events: Dict[int, threading.Event] = {}
        self._lock = threading.Lock()          # one writer at a time
        self._start_lock = threading.Lock()
        self.stderr_tail: List[str] = []

    # ---- process -------------------------------------------------------------
    def start(self) -> None:
        with self._start_lock:
            if self._proc and self._proc.poll() is None:
                return
            env = {**os.environ, **{k: str(v) for k, v in self.env.items()}}
            try:
                self._proc = subprocess.Popen(
                    self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, cwd=self.cwd, env=env,
                    text=True, encoding="utf-8", bufsize=1)
            except OSError as e:
                raise McpError(f"cannot start the MCP server {self.command}: {e}")
            threading.Thread(target=self._read_stdout, args=(self._proc,), daemon=True).start()
            threading.Thread(target=self._read_stderr, args=(self._proc,), daemon=True).start()
            init = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "1.0"}})
            self.server_info = init.get("serverInfo", {})
            self._notify("notifications/initialized", {})
            self.tools = [t["name"] for t in self._request("tools/list", {}).get("tools", [])]

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    @property
    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in (msg if isinstance(msg, list) else [msg]):
                if isinstance(m, dict) and "id" in m and m["id"] in self._events:
                    self._waiting[m["id"]] = m
                    self._events[m["id"]].set()
        for ev in list(self._events.values()):     # the server is gone: wake every waiter
            ev.set()

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-20:]

    # ---- JSON-RPC --------------------------------------------------------------
    def _send(self, message: dict) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            raise McpError("MCP server is not running")
        try:
            with self._lock:
                proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpError(f"MCP server closed its input: {e}")

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict:
        rid = next(self._ids)
        ev = self._events[rid] = threading.Event()
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            if not ev.wait(self.timeout_s):
                self._notify("notifications/cancelled", {"requestId": rid, "reason": "timeout"})
                raise McpError(f"{method}: no answer within {self.timeout_s:.0f} s")
            msg = self._waiting.pop(rid, None)
            if msg is None:
                tail = " | ".join(self.stderr_tail[-3:])
                raise McpError(f"{method}: MCP server stopped{': ' + tail if tail else ''}")
            if "error" in msg:
                err = msg["error"]
                raise McpError(f"{method}: JSON-RPC error {err.get('code')}: {err.get('message')}")
            return msg.get("result") or {}
        finally:
            self._events.pop(rid, None)

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        """Call one tool; return its structured result (the JSON object)."""
        if not self.alive:
            self.start()
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        data = result.get("structuredContent")
        if data is None:                                    # fall back to the text content
            try:
                data = json.loads(result["content"][0]["text"])
            except (KeyError, IndexError, json.JSONDecodeError, TypeError):
                raise McpError(f"{name}: answer has no JSON content")
        if result.get("isError") or (isinstance(data, dict) and "error" in data):
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise McpError(f"{name}: {err.get('code', 'error')}: {err.get('message', data)}")
        return data

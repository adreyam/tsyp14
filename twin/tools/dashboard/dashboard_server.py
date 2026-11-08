#!/usr/bin/env python3
"""Web dashboard for City Ground Zero: a static page plus a transparent MCP transport.

The page is an MCP client. Everything it shows comes from MCP tool calls (live data through the tools' progress streams); this
server holds no simulation data and never talks to the simulation. For each browser connection it starts one MCP server process
(tools/mcp/city_mcp_server.py) and relays the protocol unchanged, using MCP's HTTP + Server-Sent-Events transport:

    GET  /mcp/sse                      opens a session; events: `endpoint` (where to POST), then every server message
    POST /mcp/message?session=<id>     one JSON-RPC message from the client

Standard library only. Binds to 127.0.0.1 unless told otherwise.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import socket
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
SERVER_SCRIPT = HERE.parent / "mcp" / "city_mcp_server.py"
LOGO = HERE.parent.parent / "assets" / "branding" / "lg-out.png"
MAX_SESSIONS = 12
MAX_BODY = 1 << 20


class Session:
    """One browser connection = one MCP server process, relayed line by line."""

    def __init__(self, command: list[str], env: dict) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.outbox: queue.Queue = queue.Queue()
        self.alive = True
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.rstrip("\n")
            if line:
                self.outbox.put(line)
        self.alive = False
        self.outbox.put(None)

    def send(self, line: str) -> None:
        assert self.process.stdin is not None
        with self._lock:
            self.process.stdin.write(line.strip() + "\n")
            self.process.stdin.flush()

    def close(self) -> None:
        self.alive = False
        try:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait(timeout=2)
        except Exception:
            self.process.kill()
        for stream in (self.process.stdout,):
            if stream:
                stream.close()


class Dashboard(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, command: list[str], env: dict) -> None:
        super().__init__(address, handler)
        self.command = command
        self.env = env
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def open_session(self) -> Session | None:
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                return None
            session = Session(self.command, self.env)
            self.sessions[session.id] = session
            return session

    def drop_session(self, session: Session) -> None:
        with self.lock:
            self.sessions.pop(session.id, None)
        session.close()


class Handler(BaseHTTPRequestHandler):
    server: Dashboard
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:      # quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/mcp/sse":
            return self._sse()
        if path == "/health":
            return self._send(200, json.dumps({"ok": True, "sessions": len(self.server.sessions)}).encode(), "application/json")
        if path == "/logo.png" and LOGO.is_file():
            return self._send(200, LOGO.read_bytes(), "image/png")
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / name).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind + ("; charset=utf-8" if kind.startswith("text/") or kind.endswith("javascript") else ""))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/mcp/message":
            return self._send(404, b"not found", "text/plain")
        session = self.server.sessions.get((parse_qs(parsed.query).get("session") or [""])[0])
        length = int(self.headers.get("Content-Length", "0") or 0)
        if session is None or not session.alive:
            return self._send(404, b'{"error":"unknown or closed session"}', "application/json")
        if length <= 0 or length > MAX_BODY:
            return self._send(400, b'{"error":"bad body"}', "application/json")
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            json.loads(body)
        except json.JSONDecodeError:
            return self._send(400, b'{"error":"not JSON"}', "application/json")
        session.send(body)
        self._send(202, b"Accepted", "text/plain")

    def _browser_gone(self) -> bool:
        return _peer_closed(self.connection)

    def _sse(self) -> None:
        session = self.server.open_session()
        if session is None:
            return self._send(503, b"too many dashboard sessions", "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(f"event: endpoint\ndata: /mcp/message?session={session.id}\n\n".encode())
            self.wfile.flush()
            idle = 0
            while True:
                try:
                    line = session.outbox.get(timeout=1.0)
                except queue.Empty:
                    if self._browser_gone():
                        break
                    idle += 1
                    if idle >= 15:
                        idle = 0
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                    continue
                if line is None:
                    break
                self.wfile.write(f"event: message\ndata: {line}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.drop_session(session)


def _peer_closed(connection: socket.socket) -> bool:
    try:
        return connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Web dashboard for City Ground Zero (an MCP client)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CITY_DASHBOARD_PORT", "8870")))
    parser.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    server = Dashboard((args.host, args.port), Handler, [sys.executable, str(SERVER_SCRIPT)], env)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"City dashboard: {url}   (an MCP client: all data comes through the MCP tools)", flush=True)
    if args.open:
        try:
            import webbrowser
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for session in list(server.sessions.values()):
            session.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

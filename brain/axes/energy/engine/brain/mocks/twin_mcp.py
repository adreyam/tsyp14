"""Mock Twin exposed as MCP tools (official MCP SDK server, Streamable HTTP).

Shares the same CitySim as the REST mock, so scenario control still goes
through the REST /_scenario endpoints while the Brain talks MCP.

Run:  python -m mocks.twin_mcp --city mocks/cities/city_a.yaml --port 8111
      (MCP endpoint: http://127.0.0.1:8111/mcp)
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

try:                                   # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    _SDK2 = True
except ImportError:                    # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    _SDK2 = False
from mcp.server.transport_security import TransportSecuritySettings

from .twin import CitySim


def create_mcp_app(sim: CitySim, api_key: Optional[str] = None, json_response: bool = True):
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if _SDK2:
        server = _Server(name="mock-twin-energy", version="1.0")
    else:
        server = _Server(name="mock-twin-energy", json_response=json_response, transport_security=security)

    @server.tool(description="Describe the energy grid: zones (capacity, tags) and switches (effects)")
    def energy_get_city() -> dict[str, Any]:
        return sim.descriptor()

    @server.tool(description="Latest power-load reading of every zone")
    def energy_get_readings() -> list[dict[str, Any]]:
        return sim.readings()

    @server.tool(description="Current state of every grid switch")
    def energy_get_switches() -> list[dict[str, Any]]:
        return sim.switch_status()

    @server.tool(description="Operate a grid switch. Argument: a switch_command object")
    def energy_switch_command(command: dict[str, Any]) -> dict[str, Any]:
        for k in ("command_id", "switch_id", "operation"):
            if k not in command:
                raise ValueError(f"command.{k} is required")
        if command["operation"] not in ("open", "close"):
            raise ValueError("command.operation must be 'open' or 'close'")
        return sim.command(command)

    app = server.streamable_http_app(json_response=json_response, transport_security=security) if _SDK2 \
        else server.streamable_http_app()

    if api_key:
        class Auth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if request.headers.get("x-api-key") != api_key:
                    return JSONResponse({"detail": "missing or invalid X-API-Key"}, status_code=401)
                return await call_next(request)
        app.add_middleware(Auth)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default=str(Path(__file__).parent / "cities" / "city_a.yaml"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8111)
    args = ap.parse_args()
    import uvicorn
    sim = CitySim(yaml.safe_load(Path(args.city).read_text()))
    uvicorn.run(create_mcp_app(sim, api_key=os.environ.get("TWIN_API_KEY", "dev-twin-key")),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

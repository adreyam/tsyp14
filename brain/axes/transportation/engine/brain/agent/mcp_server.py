"""MCP server for the City Brain agent. Any MCP-capable orchestrator can list and call the tools.

    python -m agent.mcp_server                      stdio (the orchestrator starts this process itself)
    python -m agent.mcp_server --http --port 8765   streamable HTTP at http://127.0.0.1:8765/mcp

The Brain must be running (BRAIN_URL, default http://127.0.0.1:8000). Works with the MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import tools  # noqa: E402

try:
    from mcp.server.mcpserver import MCPServer as _Server      # mcp 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Server           # mcp 1.x


def build():
    srv = _Server("city-brain-transportation", instructions=(
        "Transportation axis of a simulated smart city: traffic signals learned with reinforcement learning, emergency-vehicle priority, "
        "smart parking and traffic-jam stress tests. Start with get_city_status. Tools marked read-only never change the city."))

    def make(name):
        t = tools.REGISTRY[name]

        def run(**kwargs):
            return json.dumps(tools.invoke(name, kwargs), ensure_ascii=False)
        # expose the real signature so the SDK builds the same JSON schema
        run.__signature__ = __import__("inspect").signature(t["fn"])
        run.__annotations__ = dict(getattr(t["fn"], "__annotations__", {}))
        run.__name__, run.__doc__ = name, t["description"]
        return run

    for name, t in tools.REGISTRY.items():
        try:
            from mcp.types import ToolAnnotations
            ann = ToolAnnotations(readOnlyHint=t["read_only"], destructiveHint=False if t["read_only"] else None, openWorldHint=False)
            srv.tool(name=name, title=t["title"], description=t["description"], annotations=ann)(make(name))
        except (ImportError, TypeError):
            srv.tool(name=name, description=t["description"])(make(name))
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = build()
    if a.http:
        try:
            srv.settings.host, srv.settings.port = a.host, a.port
        except AttributeError:
            pass
        srv.run(transport="streamable-http", **({} if hasattr(srv, "settings") else {"host": a.host, "port": a.port}))
    else:
        srv.run(transport="stdio")


if __name__ == "__main__":
    main()

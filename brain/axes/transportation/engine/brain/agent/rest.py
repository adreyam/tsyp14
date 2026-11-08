"""HTTP side of the agent, mounted inside the Brain (citybrain/server.py).

    GET  /.well-known/agent.json   agent card: who this agent is and what it can do (no secrets, no key needed)
    GET  /agent/manifest           tools with JSON schemas                      (header X-Agent-Key)
    POST /agent/invoke             {"tool": "...", "arguments": {...}}          (header X-Agent-Key)
    POST /api/agent/dispatch-emergency   internal helper used by the dispatch_emergency tool (X-Admin-Key)

Every call is written to the Timeline (layer "brain", kind "agent_call") so the three-layer log shows what the orchestrator did.
"""
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional

from . import tools

router = APIRouter(tags=["agent"])
AGENT_KEY = os.environ.get("AGENT_KEY", "dev-agent-key")      # change it in production
bridge_ref = {"router": None}                                 # set by server.py: the bridge router module (gives access to the bridge)

CARD = {
    "name": "city-brain-transportation",
    "description": "Transportation axis of the City Brain: reinforcement-learned traffic signals with emergency-vehicle priority, smart parking "
                   "(ranking, reservations, device-trust aware) and traffic-jam stress tests, for a simulated city.",
    "version": "1.0",
    "protocols": {"rest": {"manifest": "/agent/manifest", "invoke": "/agent/invoke", "auth": "header X-Agent-Key"},
                  "mcp": "python -m agent.mcp_server (stdio) or --http"},
    "capabilities": {"read": True, "write": True},
}


def _auth(key):
    if key != AGENT_KEY:
        raise HTTPException(401, "Send the agent key in the header X-Agent-Key.")


@router.get("/.well-known/agent.json")
def agent_card():
    return dict(CARD, skills=[{"id": t["name"], "name": t["title"], "description": t["description"], "read_only": t["read_only"]} for t in tools.REGISTRY.values()])


@router.get("/agent/manifest")
def get_manifest(x_agent_key: str = Header(default="")):
    _auth(x_agent_key)
    return {"agent": CARD["name"], "write_enabled": tools.allow_write(), "tools": tools.manifest()}


class Invoke(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    request_id: Optional[str] = None
    caller: Optional[str] = None            # name of the orchestrator / workflow, for the audit trail


@router.post("/agent/invoke")
def invoke(body: Invoke, x_agent_key: str = Header(default="")):
    _auth(x_agent_key)
    res = tools.invoke(body.tool, body.arguments)
    res["request_id"] = body.request_id
    br = bridge_ref["router"].bridge if bridge_ref["router"] else None
    if br is not None and body.tool in tools.REGISTRY and not tools.REGISTRY[body.tool]["read_only"]:
        br.tl.add("brain", "agent_call", f"{body.caller or 'orchestrator'} called {body.tool}({body.arguments}) -> {'ok' if res['ok'] else 'FAILED: ' + res['error']['message']}",
                  ref=body.request_id)
    return res


class Dispatch(BaseModel):
    kind: Literal["ambulance", "fire", "police"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    call_id: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9\-]+$")


@router.post("/api/agent/dispatch-emergency")
def dispatch(b: Dispatch, x_admin_key: str = Header(default="")):
    from app.config import settings
    from citybrain.bridge.service import BridgeError, EmergencyData
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")
    br = bridge_ref["router"].bridge
    try:
        d = EmergencyData(call_id=b.call_id, kind=b.kind, location={"lat": b.lat, "lon": b.lon})
        res = br._emergency(None, d)         # same path as an emergency call from the Twin (dispatch, timeline, command to the Twin)
    except BridgeError as e:
        raise HTTPException(e.status, e.message if hasattr(e, "message") else str(e))
    return res

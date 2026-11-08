"""Mock Guardian -- binary trust verdicts, controllable by scenario scripts (R1).

Modes:
  trusted    every subject is trusted unless overridden (default)
  untrusted  every subject is untrusted unless overridden
  silent     the Guardian is "down": verdict requests return HTTP 503

Run:  python -m mocks.guardian --port 8102
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModeIn(_In):
    mode: str = Field(pattern="^(trusted|untrusted|silent)$")


class OverrideIn(_In):
    subject_id: str
    verdict: str = Field(pattern="^(trusted|untrusted)$")
    quarantined: bool = False
    reason: Optional[str] = None


class FlapIn(_In):
    subject_id: str
    on: bool = True


class StaleIn(_In):
    age_s: float = Field(ge=0)


def create_app(api_key: Optional[str] = None, signing_secret: Optional[str] = None) -> FastAPI:
    from ._signing import add_signing
    app = FastAPI(title="Mock Guardian", version="1.0")
    add_signing(app, signing_secret)
    st: dict[str, Any] = {"mode": "trusted", "overrides": {}, "received": [], "age_s": 0.0, "n": 0}
    app.state.st = st

    def auth(x_api_key: Optional[str] = Header(default=None)):
        if api_key and x_api_key != api_key:
            raise HTTPException(401, "missing or invalid X-API-Key")

    @app.get("/health")
    def health():
        return {"status": "ok", "layer": "guardian-mock", "mode": st["mode"]}

    @app.get("/verdicts", dependencies=[Depends(auth)])
    def verdicts(subject: list[str] = Query(default=[], description="sensor_id or switch_id, repeatable")):
        if st["mode"] == "silent":
            raise HTTPException(503, "guardian unavailable (mock silent mode)")
        if not subject:
            raise HTTPException(422, "at least one ?subject=<id> is required")
        ts = (datetime.now(timezone.utc) - timedelta(seconds=st["age_s"])).isoformat()
        out = []
        st["calls"] = st.get("calls", 0) + 1
        for sid in subject:
            o = st["overrides"].get(sid)
            if sid in st.get("flap", set()):
                o = {"verdict": "trusted" if st["calls"] % 2 else "untrusted", "quarantined": False,
                     "reason": "mock flapping"}
            st["n"] += 1
            out.append({
                "type": "trust_verdict", "verdict_id": f"gv-{st['n']}", "subject_id": sid, "ts": ts,
                "verdict": o["verdict"] if o else st["mode"],
                "quarantined": bool(o and o["quarantined"]),
                "reason": (o or {}).get("reason") or f"mock default ({st['mode']})",
            })
        return out

    @app.post("/events", dependencies=[Depends(auth)])
    def events(evt: dict):
        st["received"].append(evt)
        return {"ok": True}

    # control API for scenario scripts
    @app.post("/_control/mode", dependencies=[Depends(auth)])
    def mode(m: ModeIn):
        st["mode"] = m.mode
        return {"ok": True}

    @app.post("/_control/override", dependencies=[Depends(auth)])
    def override(o: OverrideIn):
        st["overrides"][o.subject_id] = o.model_dump()
        return {"ok": True}

    @app.post("/_control/flap", dependencies=[Depends(auth)])
    def flap(f: FlapIn):
        st.setdefault("flap", set())
        (st["flap"].add if f.on else st["flap"].discard)(f.subject_id)
        return {"ok": True}

    @app.post("/_control/stale", dependencies=[Depends(auth)])
    def stale(s: StaleIn):
        st["age_s"] = s.age_s
        return {"ok": True}

    @app.post("/_control/reset", dependencies=[Depends(auth)])
    def reset():
        st.update(mode="trusted", overrides={}, received=[], age_s=0.0)
        return {"ok": True}

    @app.get("/_control/received", dependencies=[Depends(auth)])
    def received():
        return st["received"]

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8102)
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(create_app(api_key=os.environ.get("GUARDIAN_API_KEY", "dev-guardian-key"),
                           signing_secret=os.environ.get("GUARDIAN_SIGNING_SECRET") or None),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

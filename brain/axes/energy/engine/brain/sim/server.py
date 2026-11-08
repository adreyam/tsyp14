"""Live Energy Brain simulation: the real Brain + mock Twin/Guardian, streamed
to a browser dashboard. Launch scenarios or inject events and watch it decide.

  python -m sim.server              # then open http://127.0.0.1:8200
  python -m sim.server --port 8200 --speed 4 --twin mcp

Endpoints
  GET  /                      dashboard (live mode; recorded replays included)
  GET  /api/stream            Server-Sent Events: {"type":"reset", city} then {"type":"frame", ...} per cycle
  GET  /api/scenarios         list of Test Book scenarios
  POST /api/scenario/{id}     restart with a scenario (id 'FREE' = free play on city A)
  POST /api/inject            {"action": "ramp|spoof|stuck|switch|guardian_mode|guardian_override|hint", "args": {...}}
  POST /api/control           {"paused": bool?, "speed": float?}
  /agent/mcp                  the Energy Brain AGENT (MCP): what an orchestrator calls (see AGENT.md)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from scenarios.runner import Step
from scenarios.testbook import TESTBOOK

from .session import FREE_PLAY, Session

HERE = Path(__file__).resolve().parent


class Inject(BaseModel):
    action: str
    args: dict[str, Any] = {}


class Control(BaseModel):
    paused: Optional[bool] = None
    speed: Optional[float] = None


class Engine:
    """Owns the session and the stepping thread; fans frames out to SSE clients."""

    def __init__(self, speed: float, twin_protocol: str):
        self.speed, self.paused, self.twin_protocol = speed, False, twin_protocol
        self.lock = threading.RLock()
        self.subscribers: list[asyncio.Queue] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.history: list[dict] = []
        self.session: Optional[Session] = None
        self.reset(FREE_PLAY)
        threading.Thread(target=self._run, daemon=True).start()

    def reset(self, scenario) -> None:
        with self.lock:
            if self.session:
                self.session.close()
            self.session = Session(scenario, twin_protocol=self.twin_protocol)
            self.history = []
            self.paused = False
            self._lvl = self.session.brain.knowledge.level
            self._publish({"type": "reset", "city": self.session.city()})

    def inject(self, action: str, args: dict) -> None:
        with self.lock:
            self.session.modified = True
            self.session.apply(Step(self.session.t, action, args, "(manual)"))

    def _run(self) -> None:
        while True:
            t0 = time.monotonic()
            if not self.paused:
                with self.lock:
                    s = self.session
                    if s.finished:
                        self.paused = True
                    else:
                        frame = s.step()
                        frame["type"] = "frame"
                        if s.brain.knowledge.level != self._lvl:   # knowledge can change (learning)
                            self._lvl = s.brain.knowledge.level
                            frame["city"] = s.city()
                        self.history.append(frame)
                        self.history = self.history[-600:]
                        self._publish(frame)
            time.sleep(max(0.02, s.cfg.poll_interval_s / max(self.speed, 0.1) - (time.monotonic() - t0)))

    def _publish(self, msg: dict) -> None:
        if self.loop is None:
            return
        data = json.dumps(msg)
        for q in list(self.subscribers):
            self.loop.call_soon_threadsafe(q.put_nowait, data)


def create_app(speed: float = 2.0, twin_protocol: str = "rest", agent_key: Optional[str] = None) -> FastAPI:
    from contextlib import asynccontextmanager
    from energy_brain.agent import AgentService, mcp_http_app
    eng = Engine(speed, twin_protocol)

    class SimAgent(AgentService):
        """The agent always speaks for the Brain of the scenario currently running."""
        @property
        def brain(self):
            return eng.session.brain

        @brain.setter
        def brain(self, _):
            pass

    agent = SimAgent(None, eng.lock)
    agent_app = mcp_http_app(agent, api_key=agent_key)

    @asynccontextmanager
    async def lifespan(_app):
        eng.loop = asyncio.get_running_loop()
        async with agent_app.router.lifespan_context(agent_app):
            yield

    app = FastAPI(title="Energy Brain simulation", lifespan=lifespan)
    app.state.engine = eng
    app.state.agent = agent
    app.mount("/agent", agent_app)        # MCP endpoint: /agent/mcp

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (HERE / "dashboard.html").read_text(encoding="utf-8")
        rec = HERE / "recordings.json"
        replay = rec.read_text(encoding="utf-8") if rec.exists() else "null"
        return html.replace("/*__REPLAY_DATA__*/null", replay).replace("/*__LIVE__*/false", "true")

    @app.get("/api/health")
    def health():
        s = eng.session
        return {"ok": True, "scenario": s.scenario.id, "t": s.t, "paused": eng.paused, "speed": eng.speed}

    @app.get("/api/scenarios")
    def scenarios():
        return [{"id": s.id, "name": s.name, "description": s.description} for s in TESTBOOK.values()]

    @app.post("/api/scenario/{sid}")
    def scenario(sid: str):
        sid = sid.upper()
        if sid == "FREE":
            eng.reset(FREE_PLAY)
        elif sid in TESTBOOK:
            eng.reset(TESTBOOK[sid])
        else:
            raise HTTPException(404, f"unknown scenario '{sid}'")
        return {"ok": True}

    @app.post("/api/inject")
    def inject(i: Inject):
        try:
            eng.inject(i.action, i.args)
        except Exception as e:  # bad zone id, bad action: tell the UI
            raise HTTPException(422, str(e))
        return {"ok": True}

    @app.post("/api/control")
    def control(c: Control):
        if c.paused is not None:
            eng.paused = c.paused
        if c.speed is not None:
            eng.speed = max(0.25, min(c.speed, 20))
        return {"paused": eng.paused, "speed": eng.speed}

    @app.get("/api/stream")
    async def stream():
        q: asyncio.Queue = asyncio.Queue()
        with eng.lock:
            hello = [json.dumps({"type": "reset", "city": eng.session.city()})] + \
                    [json.dumps(f) for f in eng.history]
        eng.subscribers.append(q)

        async def gen():
            try:
                for h in hello:
                    yield f"data: {h}\n\n"
                while True:
                    data = await q.get()
                    yield f"data: {data}\n\n"
            finally:
                eng.subscribers.remove(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8200)
    ap.add_argument("--speed", type=float, default=2.0, help="simulated seconds per real second")
    ap.add_argument("--twin", choices=["rest", "mcp"], default="rest")
    ap.add_argument("--agent-key", default=None, help="require this X-API-Key on the agent MCP endpoint")
    a = ap.parse_args()
    import uvicorn
    print(f"Energy Brain simulation on http://{a.host}:{a.port}  (speed x{a.speed}, Twin over {a.twin.upper()})")
    print(f"Energy Brain agent (MCP) for orchestrators: http://{a.host}:{a.port}/agent/mcp")
    uvicorn.run(create_app(a.speed, a.twin, a.agent_key), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

"""Run the Energy Brain against the configured Twin and Guardian.

  python -m energy_brain.run --config config/config.yaml [--city-file config/knowledge/x.yaml] [--minutes 10]

Also exposes a tiny inbound API (default port 8103) so other Brain domains
can send cross-domain priority hints, e.g. the flood logic protecting a pump:

  POST /priority-hints   {"zone_id":"Z4","source":"flood","reason":"pump P-4 running","expires_at":"...Z"}
  GET  /health
"""
from __future__ import annotations

import argparse
import threading
import time
from typing import Optional

import httpx

from .adapters import PartnerError, make_guardian, make_twin
from .brain import EnergyBrain
from .config import load_city, load_config
from .eventlog import EventLog
from .schemas import BrainEvent, PriorityHint


def timeline_sink(url: Optional[str]):
    if not url:
        return None
    client = httpx.Client(timeout=0.5)

    def post(evt: BrainEvent) -> None:
        client.post(url, json=evt.model_dump(mode="json"))
    return post


def inbound_app(brain: EnergyBrain, cfg):
    """Inbound API for other Brain domains. Every call except /health needs the inbound key (checked before
    the body is even parsed); calls are rate-limited per client and hint expiry is capped by the Brain, so a
    leaked or buggy client cannot pin zones."""
    import secrets
    from collections import deque
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Energy Brain inbound")
    hits: dict[str, deque] = {}

    @app.middleware("http")
    async def guard(request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        host = request.client.host if request.client else "?"
        key = request.headers.get("x-api-key")
        if cfg.inbound.api_key and not (key and secrets.compare_digest(key, cfg.inbound.api_key)):
            brain.log.emit("security", f"inbound call refused: bad or missing key from {host}")
            return JSONResponse({"detail": "missing or invalid X-API-Key"}, status_code=401)
        now = time.monotonic()
        q = hits.setdefault(host, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= cfg.inbound.rate_per_min:
            brain.log.emit("security", f"inbound rate limit hit by {host}")
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        q.append(now)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok", "layer": "brain", "domain": "energy", "cycle": brain.cycle,
                "knowledge_level": brain.knowledge.level, "guardian_ok": brain.guardian_ok}

    @app.post("/priority-hints")
    def hint(h: PriorityHint):
        if not brain.add_priority_hint(h):
            raise HTTPException(422, "hint rejected (unknown zone or already expired)")
        return {"ok": True}

    return app


def inbound_api(brain: EnergyBrain, cfg, port: int):
    import uvicorn
    if not cfg.inbound.api_key:
        brain.log.emit("security", "inbound API started WITHOUT a key (set INBOUND_API_KEY)")
    srv = uvicorn.Server(uvicorn.Config(inbound_app(brain, cfg), host="0.0.0.0", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    return srv


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--city-file", default=None)
    ap.add_argument("--minutes", type=float, default=0, help="0 = run until Ctrl+C")
    ap.add_argument("--inbound-port", type=int, default=8103)
    a = ap.parse_args()

    cfg = load_config(a.config)
    city_file = a.city_file or cfg.city_file
    log = EventLog(cfg.log_dir, "energy-brain", sink=timeline_sink(cfg.timeline_url), echo=True)
    brain = EnergyBrain(cfg, make_twin(cfg.twin), make_guardian(cfg.guardian), log,
                        local_city=load_city(city_file) if city_file else None)
    while True:
        try:
            brain.start()
            break
        except PartnerError as e:
            log.emit("partner_error", f"{e} -- retrying in 3 s")
            time.sleep(3)
    inbound_api(brain, cfg, a.inbound_port)
    end = time.monotonic() + a.minutes * 60 if a.minutes else float("inf")
    try:
        while time.monotonic() < end:
            t0 = time.monotonic()
            brain.step()
            time.sleep(max(0.0, cfg.poll_interval_s - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
        print(f"log: {log.path}")


if __name__ == "__main__":
    main()

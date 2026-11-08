"""HTTP interface of the Brain towards the Twin and the Guardian (documented in INTERFACE.md)."""
import json
import os
import subprocess
import sys

from fastapi import APIRouter, Header, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.config import settings

from .service import BridgeError

router = APIRouter(prefix="/api/v1", tags=["interface"])
bridge = None            # set by server.py
cfg = None


def _need(request_key, who):
    expected = cfg["auth"]["inbound"][f"{who}_key"]
    if not request_key:
        raise BridgeError(401, "missing_api_key", f"Send your key in the header X-API-Key (the Brain expects the {who} key).")
    if request_key != expected:
        raise BridgeError(401, "invalid_api_key", f"The X-API-Key is not the {who} key configured in config/brain.yaml (auth.inbound.{who}_key).")


async def _json_body(request: Request):
    raw = await request.body()
    if len(raw) > cfg["limits"]["max_body_bytes"]:
        raise BridgeError(413, "body_too_large", f"The body is larger than {cfg['limits']['max_body_bytes']} bytes.")
    try:
        return json.loads(raw or b"null")
    except ValueError:
        raise BridgeError(400, "invalid_json", "The body is not valid JSON.")


def _guard(fn):
    try:
        return fn()
    except BridgeError as e:
        return JSONResponse(e.body(), status_code=e.status)


@router.get("/health")
async def health():
    return {"status": "ok", "layer": "city-brain", "interface_version": "1.0", "twin_link": cfg["twin"]["enabled"], "guardian_link": cfg["guardian"]["enabled"]}


@router.post("/readings")
async def readings(request: Request, x_api_key: str = Header(default="")):
    try:
        _need(x_api_key, "twin")
        body = await _json_body(request)
        if isinstance(body, dict) and "readings" in body:
            body = body["readings"]
        res = await run_in_threadpool(bridge.ingest, body)     # never block the event loop: the Brain must answer health checks during a flood
        return JSONResponse(res, status_code=200 if all(r.get("accepted") for r in res["results"]) else 207)
    except BridgeError as e:
        return JSONResponse(e.body(), status_code=e.status)


@router.post("/verdicts")
async def verdicts(request: Request, x_api_key: str = Header(default="")):
    try:
        _need(x_api_key, "guardian")
        body = await _json_body(request)
        items = body if isinstance(body, list) else [body]
        return {"results": [await run_in_threadpool(bridge.verdict, i) for i in items]}
    except BridgeError as e:
        return JSONResponse(e.body(), status_code=e.status)


@router.get("/timeline")
def timeline(after: int = 0, limit: int = Query(200, le=1000), layer: str = None):
    return {"items": bridge.tl.since(after, limit, layer), "last_id": bridge.tl.n}


@router.get("/state")
def state():
    return bridge.status()


# ------------------------------------------------------------------ scenarios (test book)
def _admin(x_admin_key):
    if x_admin_key != settings.ADMIN_KEY:
        raise BridgeError(401, "invalid_admin_key", "Send the administrator key in X-Admin-Key.")


def _catalog():
    path = os.path.join(cfg["brain"]["scenarios_dir"], "catalog.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@router.get("/scenarios")
def scenarios():
    res = {r["id"]: r for r in bridge.tl.results()}
    return {"scenarios": [dict(c, last=res.get(c["id"])) for c in _catalog()], "running": bridge.tl.scenario["id"] if bridge.tl.scenario else None}


@router.post("/scenarios/{sid}/start")
def sc_start(sid: str, title: str = "", x_admin_key: str = Header(default="")):
    return _guard(lambda: (_admin(x_admin_key), {"log": bridge.tl.start_scenario(sid, title)})[1])


@router.post("/scenarios/{sid}/check")
def sc_check(sid: str, text: str, ok: bool, x_admin_key: str = Header(default="")):
    return _guard(lambda: (_admin(x_admin_key), bridge.tl.note_check(text, ok), {"ok": True})[2])


@router.post("/scenarios/{sid}/stop")
def sc_stop(sid: str, x_admin_key: str = Header(default="")):
    return _guard(lambda: (_admin(x_admin_key), bridge.tl.stop_scenario() or {"ok": True})[1])


@router.post("/scenarios/{sid}/run")
def sc_run(sid: str, x_admin_key: str = Header(default="")):
    """One button: runs the scenario script (it drives the mocks and checks the result), logs go to the logs folder."""
    def go():
        _admin(x_admin_key)
        script = os.path.join(cfg["brain"]["scenarios_dir"], "run.py")
        if sid not in {c["id"] for c in _catalog()} or not os.path.exists(script):
            raise BridgeError(404, "unknown_scenario", f"No scenario {sid}. See TESTBOOK.md.")
        if bridge.tl.scenario:
            raise BridgeError(409, "scenario_running", f"Scenario {bridge.tl.scenario['id']} is still running.")
        port = os.environ.get("PORT", str(cfg["brain"]["port"]))
        out = open(os.path.join(cfg["brain"]["logs_dir"], "runner.out"), "a")
        bridge.tl.add("brain", "scenario", f"{sid} requested: preparing the city (reset, cars parked) - the scenario itself starts in a few seconds")
        subprocess.Popen([sys.executable, script, sid, "--brain", f"http://127.0.0.1:{port}"], stdout=out, stderr=subprocess.STDOUT)
        return {"started": sid}
    return _guard(go)


@router.post("/trust/reset")
def trust_reset(x_admin_key: str = Header(default="")):
    return _guard(lambda: (_admin(x_admin_key), bridge.reset_runtime(), {"ok": True})[2])

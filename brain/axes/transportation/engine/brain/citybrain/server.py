"""One backend for the whole city: SUMO + signal training + emergency priority + parking system + dashboard.

    python -m citybrain.server            # then open http://localhost:8000
"""
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.makedirs("data", exist_ok=True)
os.environ.setdefault("PARKING_DB_URL", "sqlite:///./data/citybrain.db")
os.environ.setdefault("PARKING_RANK_DEMO_EXTRA_LOTS", "0")
os.environ.setdefault("PARKING_LEFT_SPACE_GRACE_S", "3600")
os.environ.setdefault("PARKING_ASSIGNMENT_TIMEOUT_S", "3600")
os.environ.setdefault("PARKING_PRESENCE_TIMEOUT_S", "0")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402

from app.routers import admin, city, gateway, recommend, terminal  # noqa: E402
from app.services import watchdog  # noqa: E402
from . import api, seedworld  # noqa: E402
from .engine import Engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
engine: Engine = None


def build_world_if_missing():
    if not (ROOT / "world" / "city.net.xml").exists():
        from . import netgen
        netgen.build("world")
    if not (ROOT / "world" / "world.json").exists():
        from . import worldgen
        worldgen.generate()


@asynccontextmanager
async def lifespan(_):
    global engine
    build_world_if_missing()
    import json
    world = json.load(open("world/world.json"))
    if os.environ.get("CITY_KEEP_DB") != "1":
        seedworld.reset_and_seed(world)          # parking sessions belong to the running simulation: start clean
    else:
        seedworld.init_db(world)
    # the old "demo reset" button would re-create the two demo lots: make it reload THIS city instead
    import app.seed as app_seed
    app_seed.reset_and_seed = lambda: seedworld.reset_and_seed(world)
    app_seed.init_db = lambda: seedworld.init_db(world)
    stop = threading.Event()
    watchdog.start(stop)
    engine = Engine(fresh=os.environ.get("CITY_FRESH_TRAINING") == "1")
    api.engine = engine
    engine.start()
    from .bridge import config as bridge_config, router as bridge_router
    from .bridge.service import Bridge
    cfg = bridge_config.load()
    bridge_router.cfg = cfg
    bridge_router.bridge = Bridge(cfg, engine)
    bridge_router.bridge.tl.add("brain", "startup", f"City Brain started (twin link {'on' if cfg['twin']['enabled'] else 'off'} -> {cfg['twin']['url']}, "
                                f"guardian link {'on' if cfg['guardian']['enabled'] else 'off'} -> {cfg['guardian']['url']})")
    yield
    stop.set()
    engine.stop()


app = FastAPI(title="City Brain - live city", version="1.0", lifespan=lifespan)
app.include_router(gateway.router)
app.include_router(terminal.router)
app.include_router(admin.router)
app.include_router(city.router)
app.include_router(recommend.router)
app.include_router(api.router)
from .bridge import router as _bridge_router  # noqa: E402
app.include_router(_bridge_router.router)
from agent import rest as _agent_rest  # noqa: E402
_agent_rest.bridge_ref["router"] = _bridge_router
app.include_router(_agent_rest.router)


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(STATIC / "dashboard.html")


@app.get("/terminal/{lane_id}", include_in_schema=False)
def terminal_page(lane_id: str):
    return FileResponse(ROOT / "app" / "static" / "terminal.html")


@app.get("/admin", include_in_schema=False)
def old_admin():
    return RedirectResponse("/#admin")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("citybrain.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="warning")

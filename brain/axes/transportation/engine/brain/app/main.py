"""Smart parking backend (City Brain - transportation axis).

Run (Windows CMD, from the backend folder):
    venv\\Scripts\\activate
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from .routers import admin, city, gateway, recommend, terminal
from .seed import init_db
from .services import watchdog
from .services import recommend as recommend_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stop = threading.Event()
    watchdog.start(stop)
    _start_snapshots(stop)
    yield
    stop.set()


def _start_snapshots(stop: threading.Event) -> None:
    """Sample every lot's occupancy periodically: this history feeds the +30 min forecast."""
    from .config import settings
    from .database import SessionLocal, state_lock

    def loop():
        while not stop.wait(settings.RANK_SNAPSHOT_INTERVAL_S):
            try:
                with state_lock, SessionLocal() as db:
                    recommend_service.snapshot_all(db)
                    db.commit()
            except Exception:
                logging.getLogger("parking.snapshots").exception("snapshot failed")

    threading.Thread(target=loop, name="parking-snapshots", daemon=True).start()


app = FastAPI(title="Smart Parking - City Brain", version="1.0", lifespan=lifespan)
app.include_router(gateway.router)
app.include_router(terminal.router)
app.include_router(admin.router)
app.include_router(city.router)
app.include_router(recommend.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/sim", include_in_schema=False)
def simulation_page():
    """Live animated simulation: the page plays the ESP32 gateway of lot P01."""
    return FileResponse(STATIC / "sim.html")


@app.get("/car", include_in_schema=False)
def car_page():
    """Simulated car screen: ranks parking offers locally, drives to the lot, parks and leaves."""
    return FileResponse(STATIC / "car.html")


@app.get("/duplicate", include_in_schema=False)
def duplicate_page():
    """Illustrated demo: a tag that is already parked in lot P02 shows up at the gate of lot P01."""
    return FileResponse(STATIC / "duplicate.html")


@app.get("/car/ranking.js", include_in_schema=False)
def car_ranking_js():
    return FileResponse(STATIC / "car_ranking.js", media_type="application/javascript")


@app.get("/terminal/{lane_id}", include_in_schema=False)
def terminal_page(lane_id: str):
    return FileResponse(STATIC / "terminal.html")

"""HTTP API of the city: world data, live snapshot, controls, driver cars, training, administration extras."""
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal, state_lock
from app.models import Alert, ParkingSession
from app.services.common import FlowError, now

from . import models as cm

router = APIRouter(prefix="/api", tags=["city"])
engine = None          # set by server.py
_world_cache = {}


def _eng():
    if engine is None or not engine.ready.is_set():
        raise HTTPException(503, "The simulation is starting, try again in a few seconds.")
    if engine.error:
        raise HTTPException(500, f"The simulation stopped: {engine.error}")
    return engine


def _do(fn):
    e = _eng()
    try:
        return e.call(fn)
    except FlowError as ex:
        raise HTTPException(409, str(ex))
    except KeyError as ex:
        raise HTTPException(404, f"Unknown {ex}")
    except TimeoutError as ex:
        raise HTTPException(504, str(ex))


def require_admin(x_admin_key: str = Header(default="")):
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")


# ------------------------------------------------------------------ static world (map)
@router.get("/world")
def world():
    e = _eng()
    if "w" not in _world_cache:
        w = e.world
        edges = []
        for ed in e.net.getEdges():
            sh = ed.getShape()
            edges.append({"id": ed.getID(), "shape": [[round(x, 1), round(y, 1)] for x, y in (sh[0], sh[-1])], "border": ed.getFromNode().getType() == "dead_end"
                          or ed.getToNode().getType() == "dead_end"})
        tls = [{"id": n.getID(), "x": n.getCoord()[0], "y": n.getCoord()[1]} for n in e.net.getNodes() if n.getType() == "traffic_light"]
        lots = [{k: l[k] for k in ("id", "name", "capacity", "corners", "gate_in", "gate_out", "access_edge", "lat", "lon", "L", "D")}
                | {"spaces": [{"code": s["code"], "x": s["x"], "y": s["y"], "row": s["row"]} for s in l["spaces"]]} for l in w["lots"]]
        places = [{k: p[k] for k in ("id", "driver", "kind", "label", "x", "y", "lat", "lon", "weight", "schedule")} for p in w["places"]]
        drivers = [{k: d[k] for k in ("id", "name", "rfid", "plate", "married", "kids", "shares_tag_with")} for d in w["drivers"]]
        _world_cache["w"] = {"bbox": w["bbox"], "edges": edges, "signals": tls, "lots": lots, "places": places, "drivers": drivers}
    return _world_cache["w"]


@router.get("/state")
def state():
    return _eng().pub


# ------------------------------------------------------------------ simulation controls
class Controls(BaseModel):
    speed: Optional[float] = None
    paused: Optional[bool] = None
    auto_trips: Optional[bool] = None
    misspark_prob: Optional[float] = None
    traffic_level: Optional[float] = None
    time_scale: Optional[float] = None
    day: Optional[int] = None              # 0 = Monday
    time: Optional[str] = None             # "HH:MM"
    ambulance: Optional[int] = None
    police: Optional[int] = None
    fire: Optional[int] = None


class JamBody(BaseModel):
    size: int = 300
    seconds: int = 60


@router.post("/sim/jam")
def sim_jam(b: JamBody):
    """Traffic-jam test: flood the city with extra cars and measure how the signals cope."""
    e = _eng()
    try:
        return {"ok": True, "jam": _do(lambda: e.start_jam(b.size, b.seconds))}
    except ValueError as ex:
        raise HTTPException(409, str(ex))


@router.post("/sim/controls")
def controls(c: Controls):
    e = _eng()

    def apply():
        if c.speed is not None:
            e.speed = max(0.0, min(200.0, c.speed))
        if c.paused is not None:
            e.paused = c.paused
        if c.auto_trips is not None:
            e.auto_trips = c.auto_trips
        if c.misspark_prob is not None:
            e.misspark_prob = max(0.0, min(1.0, c.misspark_prob))
        if c.traffic_level is not None:
            e.traffic_level = max(0.0, min(2.0, c.traffic_level))
        if c.time_scale is not None:
            old = e.clock()
            e.time_scale = max(1.0, min(600.0, c.time_scale))
            e.set_clock(old[0], f"{old[1] // 60:02d}:{old[1] % 60:02d}")
        if c.day is not None or c.time is not None:
            dow, minutes, _ = e.clock()
            e.set_clock(c.day if c.day is not None else dow, c.time or f"{minutes // 60:02d}:{minutes % 60:02d}")
        for k in ("ambulance", "police", "fire"):
            v = getattr(c, k)
            if v is not None:
                e.emergency_target[k] = max(0, min(10, v))
        return True
    # pause/speed must work even while the sim thread is waiting in pause: handled there too
    if c.paused is not None or c.speed is not None:
        if c.speed is not None:
            e.speed = max(0.0, min(200.0, c.speed))
        if c.paused is not None:
            e.paused = c.paused
        c = c.model_copy(update={"speed": None, "paused": None})
    _do(apply)
    return {"ok": True}


@router.post("/sim/scenario/{name}")
def scenario(name: str):
    e = _eng()
    if name == "duplicate":
        return _do(lambda: e.parking.scenario_duplicate()) or {"ok": True}
    if name == "wrongspace":
        return _do(lambda: e.parking.scenario_wrongspace()) or {"ok": True}
    raise HTTPException(404, "Unknown scenario")


# ------------------------------------------------------------------ driver cars
class StartTrip(BaseModel):
    place_id: Optional[str] = None
    autopilot: bool = False
    no_tag: bool = False


class Choose(BaseModel):
    lot_id: str


class ParkChoice(BaseModel):
    space: Optional[str] = None      # None / "assigned" -> the assigned space; otherwise a space code; "other" -> any free one


class Dest(BaseModel):
    place_id: str


def _car(e, cid):
    car = e.parking.cars.get(cid)
    if car is None:
        raise KeyError(cid)
    return car


@router.get("/cars/{cid}")
def car_state(cid: str):
    e = _eng()
    return _do(lambda: e.parking.car_view(_car(e, cid), full=True))


@router.post("/cars/{cid}/autopilot")
def car_autopilot(cid: str, on: bool = True):
    e = _eng()
    car = _car(e, cid)
    car.autopilot = on
    return {"ok": True, "autopilot": on}


@router.post("/cars/{cid}/start-trip")
def car_start(cid: str, body: StartTrip):
    e = _eng()
    _do(lambda: e.parking.start_trip(_car(e, cid), body.place_id, autopilot=body.autopilot, no_tag=body.no_tag))
    return {"ok": True}


@router.post("/cars/{cid}/destination")
def car_destination(cid: str, body: Dest):
    e = _eng()

    def f():
        car = _car(e, cid)
        p = e.parking.places.get(body.place_id)
        if p is None or p["driver"] != cid:
            raise FlowError("Not one of this driver's places.")
        car.dest = p
        car.offers = None
    _do(f)
    return {"ok": True}


@router.post("/cars/{cid}/offers")
def car_offers(cid: str):
    """Ask the City Brain for the 3 best parking lots for this car (7 criteria, ranked here in the backend)."""
    e = _eng()
    return _do(lambda: e.parking.offers(_car(e, cid)))


@router.post("/cars/{cid}/choose")
def car_choose(cid: str, body: Choose):
    e = _eng()
    _do(lambda: e.parking.choose(_car(e, cid), body.lot_id))
    return {"ok": True}


@router.post("/cars/{cid}/request-ticket")
def car_ticket(cid: str):
    e = _eng()
    _do(lambda: e.parking.request_ticket(_car(e, cid)))
    return {"ok": True}


@router.post("/cars/{cid}/park")
def car_park(cid: str, body: ParkChoice):
    e = _eng()
    _do(lambda: e.parking.set_park_choice(_car(e, cid), body.space or "assigned"))
    return {"ok": True}


@router.post("/cars/{cid}/repark")
def car_repark(cid: str):
    e = _eng()
    _do(lambda: e.parking.request_repark(_car(e, cid)))
    return {"ok": True}


@router.post("/cars/{cid}/leave")
def car_leave(cid: str):
    e = _eng()
    _do(lambda: e.parking.request_leave(_car(e, cid)))
    return {"ok": True}


@router.post("/cars/{cid}/hold")
def car_hold(cid: str, on: bool = True):
    e = _eng()
    car = _car(e, cid)
    car.hold = on
    return {"ok": True, "hold": on}


@router.post("/cars/{cid}/redirect")
def car_redirect(cid: str):
    e = _eng()
    _do(lambda: e.parking.redirect(_car(e, cid)))
    return {"ok": True}


@router.post("/cars/{cid}/give-up")
def car_giveup(cid: str):
    e = _eng()
    _do(lambda: e.parking.leave_without_parking(_car(e, cid)))
    return {"ok": True}


@router.post("/cars/{cid}/messages/{mid}/ack")
def car_ack(cid: str, mid: int):
    with state_lock, SessionLocal() as db:
        m = db.get(cm.CarMessage, mid)
        if m is None or m.driver_id != cid:
            raise HTTPException(404, "Unknown message")
        m.acknowledged = True
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ administration extras
class NoteBody(BaseModel):
    note: str = ""


@router.post("/admin/sessions/{session_id}/notify-repark", dependencies=[Depends(require_admin)])
def notify_repark(session_id: int, body: NoteBody):
    """The administration tells the car (on its dash screen) to re-park in its assigned space."""
    e = _eng()
    with state_lock, SessionLocal() as db:
        s = db.get(ParkingSession, session_id)
        if s is None:
            raise HTTPException(404, "Unknown session")
        car = next((c for c in e.parking.cars.values() if c.session_id == s.id), None)
        if car is None:
            raise HTTPException(409, "No car of this city is linked to that session")
        from app.models import Space
        sp = db.get(Space, s.space_id) if s.space_id else None
        code = sp.code if sp else "?"
        text = f"Administration: please move to your assigned space {code}." + (f" ({body.note})" if body.note.strip() else "")
        db.add(cm.CarMessage(driver_id=car.id, kind="REPARK", text=text, session_id=s.id, space_code=code, created_at=now()))
        db.commit()
    return {"ok": True, "driver": car.id, "space": code}


@router.post("/admin/cars/{cid}/message", dependencies=[Depends(require_admin)])
def admin_message(cid: str, body: NoteBody):
    if len(body.note.strip()) < 2:
        raise HTTPException(422, "Write a message")
    with state_lock, SessionLocal() as db:
        if db.get(cm.Driver, cid) is None:
            raise HTTPException(404, "Unknown driver")
        db.add(cm.CarMessage(driver_id=cid, kind="ADMIN", text=f"Administration: {body.note.strip()}", created_at=now()))
        db.commit()
    return {"ok": True}


@router.get("/admin/notices", dependencies=[Depends(require_admin)])
def notices(limit: int = 40):
    with state_lock, SessionLocal() as db:
        rows = db.scalars(select(cm.AdminNotice).order_by(cm.AdminNotice.id.desc()).limit(limit)).all()
        return [{"id": n.id, "lot_id": n.lot_id, "driver_id": n.driver_id, "kind": n.kind, "text": n.text, "seen": n.seen,
                 "at": n.created_at.isoformat(timespec="seconds") + "Z"} for n in rows]


@router.post("/admin/notices/{nid}/seen", dependencies=[Depends(require_admin)])
def notice_seen(nid: int):
    with state_lock, SessionLocal() as db:
        n = db.get(cm.AdminNotice, nid)
        if n:
            n.seen = True
            db.commit()
    return {"ok": True}


@router.get("/admin/lots", dependencies=[Depends(require_admin)])
def admin_lots():
    from app.routers.city import _lots
    with state_lock, SessionLocal() as db:
        out = _lots(db)
        open_alerts = {}
        for a in db.scalars(select(Alert).where(Alert.resolved.is_(False))):
            open_alerts[a.lot_id] = open_alerts.get(a.lot_id, 0) + 1
        for l in out["lots"]:
            l["open_alerts"] = open_alerts.get(l["lot_id"], 0)
        return out


# ------------------------------------------------------------------ training
class Mode(BaseModel):
    mode: str


@router.get("/training")
def training():
    e = _eng()
    v = e.learning_view()
    L = e.learner
    bench = []
    if os.path.exists(L.p_bench):
        bench = [json.loads(l) for l in open(L.p_bench) if l.strip()]
    v["benchmarks"] = bench[-10:]
    v["benchmark_running"] = bool(_bench_proc and _bench_proc.poll() is None)
    # the saved 2x2 training this city started from
    from training.core import read_jsonl
    ev = read_jsonl("runs/main/evals.jsonl")
    v["origin_evals"] = ev
    v["run_dir"] = L.run_dir
    return v


@router.post("/training/mode")
def training_mode(body: Mode):
    e = _eng()
    if body.mode not in ("train", "policy"):
        raise HTTPException(422, "mode must be train or policy")

    def f():
        e.learner.mode = body.mode
        e.event(f"Signals: {'learning (training on)' if body.mode == 'train' else 'using the learned policy (training off)'}")
    _do(f)
    return {"ok": True}


@router.post("/training/save")
def training_save():
    e = _eng()
    _do(lambda: e.learner.save())
    return {"ok": True, "episode": e.learner.episode}


_bench_proc = None


@router.post("/training/benchmark")
def training_benchmark(scenarios: int = 3, seconds: int = 900, jam: int = 0):
    """Compare the fixed-time signals with the current learned policy on identical traffic (separate process, headless)."""
    global _bench_proc
    e = _eng()
    if _bench_proc and _bench_proc.poll() is None:
        raise HTTPException(409, "A benchmark is already running")
    _do(lambda: e.learner.save())
    log = open("data/benchmark.log", "w")
    _bench_proc = subprocess.Popen([sys.executable, "-m", "citybrain.benchmark", "--run-dir", e.learner.run_dir, "--scenarios", str(scenarios),
                                    "--seconds", str(seconds)] + (["--jam", str(jam)] if jam else []), stdout=log, stderr=subprocess.STDOUT)
    return {"ok": True}


# ------------------------------------------------------------------ developer debugging (CITY_DEBUG=1)
class Evalbody(BaseModel):
    code: str


@router.post("/debug/eval")
def debug_eval(body: Evalbody):
    if os.environ.get("CITY_DEBUG") != "1":
        raise HTTPException(404, "disabled")
    e = _eng()
    return {"result": repr(_do(lambda: eval(body.code, {"e": e, "traci": __import__("traci")})))}

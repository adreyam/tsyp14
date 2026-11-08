"""Top-3 parking recommendation for drivers (City Brain transportation axis)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..database import get_db, state_lock
from ..models import LotProfile, ParkingLot, Reservation
from ..services import recommend as svc
from ..services.common import iso, now

router = APIRouter(prefix="/api/city", tags=["city-brain-ranking"])


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RecommendBody(BaseModel):
    origin: Point                       # where the driver is
    destination: Point                  # where the driver wants to end up
    top_n: int = Field(default=3, ge=1, le=10)
    exclude: List[str] = []             # lots to skip (use for a redirect after a lot turned out full)
    require_online: bool = False        # drop lots whose gateway is not reporting
    weights: Optional[dict] = None      # optional fixed weights instead of CRITIC
    max_walk_m: float = Field(default=2000.0, gt=0, le=20000)   # hard limit: lots farther than this from the goal are dropped


class CandidatesBody(BaseModel):
    origin: Point
    destination: Point
    exclude: List[str] = []
    require_online: bool = False
    max_walk_m: float = Field(default=2000.0, gt=0, le=20000)


class ReserveBody(BaseModel):
    lot_id: str
    eta_min: float = Field(ge=0, le=240)


class FeedbackBody(BaseModel):
    rating: int = Field(ge=1, le=5)


@router.post("/recommend")
def recommend(body: RecommendBody, db: DB = Depends(get_db)):
    with state_lock:
        try:
            result = svc.recommend(db, (body.origin.lat, body.origin.lon), (body.destination.lat, body.destination.lon),
                                   top_n=body.top_n, exclude=body.exclude, require_online=body.require_online,
                                   weights=body.weights, max_walk_m=body.max_walk_m)
        except ValueError as e:
            raise HTTPException(422, str(e))
        db.commit()   # expiring old reservations is a write
        return result


@router.post("/candidates")
def candidates(body: CandidatesBody, db: DB = Depends(get_db)):
    """Lot data only, NOT ranked. For a car that ranks locally so that the driver's personal
    places never leave the vehicle. Contains nothing about the driver except the two points
    the car chooses to send (where it is, where it is going)."""
    with state_lock:
        cands, excluded = svc.build_candidates(db, (body.origin.lat, body.origin.lon),
                                               (body.destination.lat, body.destination.lon),
                                               body.exclude, body.require_online, body.max_walk_m)
        db.commit()
        return {"generated_at": iso(now()), "candidates": cands, "excluded": excluded}


@router.post("/reserve")
def reserve(body: ReserveBody, db: DB = Depends(get_db)):
    """The driver picked a lot. Holds a place in the competition count until ETA + grace."""
    with state_lock:
        if db.get(ParkingLot, body.lot_id) is None:
            raise HTTPException(404, "Unknown lot")
        st = svc.lot_state(db, body.lot_id)
        if st["available"] - svc.en_route(db, body.lot_id) <= 0:
            raise HTTPException(409, "Lot is full (counting drivers already heading there)")
        res = svc.create_reservation(db, body.lot_id, body.eta_min)
        db.commit()
        return {"reservation_id": res.id, "lot_id": res.lot_id, "valid_until": iso(res.expires_at)}


@router.post("/reservations/{reservation_id}/cancel")
def cancel(reservation_id: int, db: DB = Depends(get_db)):
    with state_lock:
        res = db.get(Reservation, reservation_id)
        if res is None:
            raise HTTPException(404, "Unknown reservation")
        if res.status == "ACTIVE":
            res.status = "CANCELLED"
        db.commit()
        return {"reservation_id": res.id, "status": res.status}


@router.post("/lots/{lot_id}/feedback")
def feedback(lot_id: str, body: FeedbackBody, db: DB = Depends(get_db)):
    with state_lock:
        prof = db.get(LotProfile, lot_id)
        if prof is None:
            raise HTTPException(404, "Unknown lot")
        prof.review_sum += body.rating
        prof.review_count += 1
        db.commit()
        return {"lot_id": lot_id, "reviews": prof.review_count}


@router.post("/snapshots")
def take_snapshot(x_admin_key: str = Header(default=""), db: DB = Depends(get_db)):
    """Record the current occupancy of every lot now (also done automatically every few minutes)."""
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")
    with state_lock:
        n = svc.snapshot_all(db)
        db.commit()
        return {"lots_recorded": n, "at": iso(now())}

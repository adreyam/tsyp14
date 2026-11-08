"""Read-only availability feed for the City Brain transportation layer."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..database import get_db, state_lock
from ..enums import SpaceStatus
from ..models import ParkingLot, Space
from ..services.common import count_assignable, iso, now

router = APIRouter(prefix="/api/city", tags=["city-brain"])


def lot_counts(db: DB, lot_id: str) -> dict:
    spaces = db.scalars(select(Space).where(Space.lot_id == lot_id)).all()
    return {
        "total": len(spaces),
        "available": count_assignable(db, lot_id),
        "occupied": sum(s.status in (SpaceStatus.OCCUPIED, SpaceStatus.RELEASING) for s in spaces),
        "reserved": sum(s.status == SpaceStatus.RESERVED for s in spaces),
        "out_of_service": sum(s.status == SpaceStatus.OUT_OF_SERVICE or s.sensor_fault for s in spaces),
    }


@router.get("/lots")
def lots(db: DB = Depends(get_db)):
    with state_lock:
        return _lots(db)


def _lots(db: DB):
    out = []
    for lot in db.scalars(select(ParkingLot).order_by(ParkingLot.id)):
        c = lot_counts(db, lot.id)
        c["occupancy_rate"] = round((c["total"] - c["available"]) / c["total"], 3) if c["total"] else None
        out.append({"lot_id": lot.id, "name": lot.name, **c})
    return {"generated_at": iso(now()), "lots": out}

"""Entrance / exit terminal screens (the screen polls its lane state)."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DB

from ..database import get_db, state_lock
from ..enums import LaneState
from ..models import Lane
from ..services import entrance_flow
from ..services.common import FlowError

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


def _lane_view(lane: Lane) -> dict:
    return {
        "lane_id": lane.id, "lot_id": lane.lot_id, "kind": lane.kind, "state": lane.state,
        "message": lane.message, "vehicle_present": lane.vehicle_present,
        "ticket": json.loads(lane.ticket_json) if lane.ticket_json else None,
        "show_request_button": lane.state == LaneState.AWAITING_TICKET_REQUEST,
    }


@router.get("/{lane_id}")
def terminal_state(lane_id: str, db: DB = Depends(get_db)):
    with state_lock:   # consistent view, and never during a demo reset
        lane = db.get(Lane, lane_id)
        if lane is None:
            raise HTTPException(404, "Unknown lane")
        return _lane_view(lane)


@router.post("/{lane_id}/request-ticket")
def request_ticket(lane_id: str, db: DB = Depends(get_db)):
    with state_lock:
        lane = db.get(Lane, lane_id)
        if lane is None:
            raise HTTPException(404, "Unknown lane")
        try:
            entrance_flow.request_ticket(db, lane)
        except FlowError as e:
            raise HTTPException(409, str(e))
        db.commit()
        return _lane_view(lane)

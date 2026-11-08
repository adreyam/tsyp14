"""Parking administrator API (alerts, manual verification, reassignment, configuration)."""
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..database import get_db, state_lock
from ..enums import (ACTIVE_SESSION_STATUSES, CardStatus, LaneState, SessionStatus, SpaceStatus,
                     VehicleCategory)
from ..models import Alert, Lane, ParkingSession, Space, TempCard, Vehicle
from ..schemas import ManualExitBody, NoteBody, ReassignBody, ServiceBody, TempCardsBody, VehicleBody
from ..services.common import (enqueue_command, iso, now, recompute_space_status, resolve_alerts,
                               session_dict, set_lane, space_indicator)
from ..services.exit_flow import close_session
from .city import lot_counts


def require_admin_key(x_admin_key: str = Header(default="")):
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


def _session(db: DB, session_id: int) -> ParkingSession:
    s = db.get(ParkingSession, session_id)
    if s is None:
        raise HTTPException(404, "Unknown session")
    return s


def _alert_dict(a: Alert) -> dict:
    return {"id": a.id, "lot_id": a.lot_id, "type": a.type, "severity": a.severity, "message": a.message,
            "session_id": a.session_id, "space_id": a.space_id, "lane_id": a.lane_id,
            "created_at": iso(a.created_at), "resolved": a.resolved, "resolution_note": a.resolution_note}


# ------------------------------------------------------------------ read
@router.get("/overview")
def overview(lot_id: str, db: DB = Depends(get_db)):
    with state_lock:
        return _overview(lot_id, db)


def _overview(lot_id: str, db: DB):
    spaces = db.scalars(select(Space).where(Space.lot_id == lot_id).order_by(Space.position, Space.code)).all()

    def ticket_of(session_id):
        s = db.get(ParkingSession, session_id) if session_id else None
        return s.ticket_id if s else None

    active = db.scalars(select(ParkingSession).where(ParkingSession.lot_id == lot_id,
                                                     ParkingSession.status.in_(ACTIVE_SESSION_STATUSES))
                        .order_by(ParkingSession.id)).all()
    recent = db.scalars(select(ParkingSession).where(ParkingSession.lot_id == lot_id,
                                                     ParkingSession.status.not_in(ACTIVE_SESSION_STATUSES))
                        .order_by(ParkingSession.id.desc()).limit(10)).all()
    alerts = db.scalars(select(Alert).where(Alert.resolved.is_(False),
                                            (Alert.lot_id == lot_id) | Alert.lot_id.is_(None))
                        .order_by(Alert.id.desc())).all()
    cards = db.scalars(select(TempCard).where(TempCard.lot_id == lot_id).order_by(TempCard.uid)).all()
    lanes = db.scalars(select(Lane).where(Lane.lot_id == lot_id).order_by(Lane.id)).all()
    return {
        "lot_id": lot_id,
        "counts": lot_counts(db, lot_id),
        "lanes": [{"id": l.id, "kind": l.kind, "state": l.state, "message": l.message,
                   "vehicle_present": l.vehicle_present, "session_ticket": ticket_of(l.session_id),
                   "ticket": json.loads(l.ticket_json) if l.ticket_json else None} for l in lanes],
        "spaces": [{"id": s.id, "code": s.code, "status": s.status, "sensor_occupied": s.sensor_occupied,
                    "sensor_fault": s.sensor_fault, "assigned_ticket": ticket_of(s.assigned_session_id),
                    "occupant_ticket": ticket_of(s.occupant_session_id), "last_rfid": s.last_rfid_uid}
                   for s in spaces],
        "sessions": [session_dict(db, s) for s in active],
        "recent_sessions": [session_dict(db, s) for s in recent],
        "cards": [{"uid": c.uid, "status": c.status, "ticket": ticket_of(c.session_id)} for c in cards],
        "alerts": [_alert_dict(a) for a in alerts],
    }


@router.get("/alerts")
def list_alerts(resolved: bool = False, db: DB = Depends(get_db)):
    q = select(Alert).where(Alert.resolved.is_(resolved)).order_by(Alert.id.desc()).limit(200)
    return [_alert_dict(a) for a in db.scalars(q)]


# ------------------------------------------------------------------ alerts / sessions
@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: NoteBody, db: DB = Depends(get_db)):
    with state_lock:
        a = db.get(Alert, alert_id)
        if a is None:
            raise HTTPException(404, "Unknown alert")
        a.resolved, a.resolved_at, a.resolution_note = True, now(), body.note or "Resolved by administrator"
        db.commit()
        return _alert_dict(a)


@router.post("/sessions/{session_id}/reassign")
def reassign(session_id: int, body: ReassignBody, db: DB = Depends(get_db)):
    """Administrator authorizes a MISPLACED vehicle to keep the space it is in (or another free one)."""
    with state_lock:
        s = _session(db, session_id)
        if s.status not in (SessionStatus.MISPLACED, SessionStatus.ASSIGNED):
            raise HTTPException(409, f"Session is {s.status}; only MISPLACED or ASSIGNED can be reassigned")
        new = db.scalar(select(Space).where(Space.lot_id == s.lot_id, Space.code == body.space_code))
        if new is None:
            raise HTTPException(404, "Unknown space")
        if new.status == SpaceStatus.OUT_OF_SERVICE:
            raise HTTPException(409, "Space is out of service")
        if new.assigned_session_id not in (None, s.id):
            raise HTTPException(409, "Space is assigned to another session")
        if new.sensor_occupied and new.occupant_session_id != s.id:
            raise HTTPException(409, "Space is occupied by another vehicle")

        old = db.get(Space, s.space_id) if s.space_id else None
        if old is not None and old.id != new.id:
            old.assigned_session_id = None
            recompute_space_status(db, old)
            space_indicator(db, old, "OFF")
        new.assigned_session_id = s.id
        s.space_id = new.id
        if new.occupant_session_id == s.id and new.sensor_occupied:
            s.status = SessionStatus.PARKED
            s.parked_at = s.parked_at or now()
            space_indicator(db, new, "OK")
        else:
            s.status = SessionStatus.ASSIGNED
            space_indicator(db, new, "ASSIGNED")
        recompute_space_status(db, new)
        s.notes = ((s.notes + "\n") if s.notes else "") + f"Reassigned to {new.code}: {body.note}"
        resolve_alerts(db, types=["MISPLACED"], session_id=s.id, note=f"Reassigned to {new.code}")
        db.commit()
        return session_dict(db, s)


@router.post("/sessions/{session_id}/verify")
def verify(session_id: int, body: NoteBody, db: DB = Depends(get_db)):
    """Administrator checked a VERIFICATION session and confirms the vehicle is in its space."""
    with state_lock:
        s = _session(db, session_id)
        if s.status != SessionStatus.VERIFICATION:
            raise HTTPException(409, "Session is not in VERIFICATION")
        s.status, s.verification_reason, s.left_space_at = SessionStatus.PARKED, None, None
        s.notes = ((s.notes + "\n") if s.notes else "") + f"Verified: {body.note}"
        resolve_alerts(db, types=["VEHICLE_LEFT_SPACE"], session_id=s.id, note="Verified by administrator")
        db.commit()
        return session_dict(db, s)


@router.post("/sessions/{session_id}/cancel")
def cancel(session_id: int, body: NoteBody, db: DB = Depends(get_db)):
    """Close a session that will never exit normally (e.g. vehicle turned back at the entrance)."""
    with state_lock:
        s = _session(db, session_id)
        if s.status not in ACTIVE_SESSION_STATUSES:
            raise HTTPException(409, "Session is already closed")
        close_session(db, s, None, closed_by="ADMIN", final_status=SessionStatus.CANCELLED,
                      note=f"Cancelled: {body.note}")
        db.commit()
        return session_dict(db, s)


@router.post("/manual-exit")
def manual_exit(body: ManualExitBody, db: DB = Depends(get_db)):
    """Lost card / unreadable tag: ticket ID + a manual vehicle check by the administrator.
    An unknown ticket number never authorizes an exit."""
    with state_lock:
        s = db.scalar(select(ParkingSession).where(ParkingSession.ticket_id == body.ticket_id.strip()))
        if s is None or s.status not in ACTIVE_SESSION_STATUSES:
            raise HTTPException(404, "No active session with this ticket ID")
        if not body.manual_vehicle_check_confirmed:
            raise HTTPException(409, "The manual vehicle check must be confirmed")
        lane = db.get(Lane, body.lane_id)
        if lane is None or lane.lot_id != s.lot_id or lane.kind != "EXIT":
            raise HTTPException(409, "Lane must be an exit lane of the session's parking lot")

        vehicle = db.get(Vehicle, s.vehicle_id)
        card_status = CardStatus.AVAILABLE
        if vehicle and vehicle.category == VehicleCategory.VISITOR:
            if body.card_status == "LOST":
                card_status = CardStatus.LOST
            elif body.card_status == "RETURNED":
                s.card_returned = True
            else:
                raise HTTPException(409, "For a visitor, card_status must be RETURNED or LOST")
        close_session(db, s, lane, closed_by="ADMIN", card_status=card_status,
                      note=f"Manual exit: {body.note}")
        set_lane(lane, LaneState.EXIT_AUTHORIZED, "Exit authorized by the parking administrator. Goodbye!",
                 session_id=s.id)
        # The lane alert was raised before the vehicle could be identified: the admin just resolved it.
        resolve_alerts(db, lane_id=lane.id, note=f"Manual exit of ticket {s.ticket_id}")
        db.commit()
        return session_dict(db, s)


# ------------------------------------------------------------------ lanes / spaces / config
@router.post("/lanes/{lane_id}/reset")
def reset_lane(lane_id: str, db: DB = Depends(get_db)):
    with state_lock:
        lane = db.get(Lane, lane_id)
        if lane is None:
            raise HTTPException(404, "Unknown lane")
        set_lane(lane, LaneState.IDLE, "", session_id=None)
        resolve_alerts(db, lane_id=lane.id, note="Lane reset by administrator")
        db.commit()
        return {"ok": True}


@router.post("/lanes/{lane_id}/allow-visitor-card")
def allow_visitor_card(lane_id: str, body: NoteBody, db: DB = Depends(get_db)):
    """The administrator lets a car that was refused as a DUPLICATE (its tag already has an active
    session) request a temporary visitor RFID card at the gate. One-shot: it applies to the car now
    waiting at this lane, the registered tag stays tied to its original session, and the approval is
    written on the alert."""
    with state_lock:
        lane = db.get(Lane, lane_id)
        if lane is None:
            raise HTTPException(404, "Unknown lane")
        if lane.kind != "ENTRANCE":
            raise HTTPException(409, "Only an entrance lane can issue a visitor card")
        if len(body.note.strip()) < 3:
            raise HTTPException(422, "A reason is required")
        alert = db.scalar(select(Alert).where(Alert.lane_id == lane.id, Alert.type == "DUPLICATE_ENTRY",
                                              Alert.resolved.is_(False)).order_by(Alert.id.desc()))
        if alert is None or lane.state != LaneState.BLOCKED or not lane.vehicle_present \
                or lane.session_id is None or lane.session_id != alert.session_id:
            raise HTTPException(409, "No car is waiting at this lane after a duplicate-entry refusal")
        set_lane(lane, LaneState.AWAITING_TICKET_REQUEST,
                 "The parking administrator approved a temporary RFID card for your vehicle. "
                 "Press \"Request a ticket\".", session_id=None)
        alert.message += f" | Visitor RFID card approved by the administrator: {body.note.strip()}"
        db.commit()
        return {"ok": True, "lane_state": lane.state, "alert_id": alert.id}


@router.post("/lanes/{lane_id}/open-barrier")
def open_barrier(lane_id: str, body: NoteBody, db: DB = Depends(get_db)):
    with state_lock:
        lane = db.get(Lane, lane_id)
        if lane is None:
            raise HTTPException(404, "Unknown lane")
        if len(body.note.strip()) < 3:
            raise HTTPException(422, "A reason is required")
        enqueue_command(db, lane.gateway_id, "OPEN_BARRIER", lane_id=lane.id, manual_override=True, note=body.note.strip())
        db.commit()
        return {"ok": True}


@router.post("/spaces/{space_id}/service")
def set_service(space_id: str, body: ServiceBody, db: DB = Depends(get_db)):
    with state_lock:
        sp = db.get(Space, space_id)
        if sp is None:
            raise HTTPException(404, "Unknown space")
        if not body.in_service:
            if sp.assigned_session_id:
                raise HTTPException(409, "Space is assigned to an active session")
            sp.status = SpaceStatus.OUT_OF_SERVICE
        elif sp.status == SpaceStatus.OUT_OF_SERVICE:
            sp.status = SpaceStatus.AVAILABLE
            recompute_space_status(db, sp)
        db.commit()
        return {"id": sp.id, "status": sp.status}


@router.post("/vehicles")
def register_vehicle(body: VehicleBody, db: DB = Depends(get_db)):
    with state_lock:
        uid = body.rfid.strip().upper()
        if db.get(TempCard, uid):
            raise HTTPException(409, "This RFID is a temporary card")
        other = db.scalar(select(Vehicle).where(Vehicle.permanent_rfid == uid))
        if other and other.id != body.id:
            raise HTTPException(409, f"RFID already registered to {other.id}")
        v = db.get(Vehicle, body.id) or Vehicle(id=body.id, category=VehicleCategory.REGISTERED, created_at=now())
        v.permanent_rfid, v.plate, v.owner, v.active = uid, body.plate, body.owner, body.active
        db.add(v)
        db.commit()
        return {"id": v.id, "rfid": v.permanent_rfid, "active": v.active}


@router.post("/temp-cards")
def add_temp_cards(body: TempCardsBody, db: DB = Depends(get_db)):
    with state_lock:
        added = []
        for raw in body.uids:
            uid = raw.strip().upper()
            if not uid or db.get(TempCard, uid) or db.scalar(select(Vehicle).where(Vehicle.permanent_rfid == uid)):
                continue
            db.add(TempCard(uid=uid, lot_id=body.lot_id, status=CardStatus.AVAILABLE))
            added.append(uid)
        db.commit()
        return {"added": added}


@router.post("/cards/{uid}/status")
def set_card_status(uid: str, body: NoteBody, db: DB = Depends(get_db)):
    """body.note = AVAILABLE / OUT_OF_SERVICE / LOST (e.g. a lost card was found)."""
    with state_lock:
        card = db.get(TempCard, uid.upper())
        if card is None:
            raise HTTPException(404, "Unknown card")
        if card.status == CardStatus.IN_USE:
            raise HTTPException(409, "Card is linked to an active session")
        if body.note not in (CardStatus.AVAILABLE, CardStatus.OUT_OF_SERVICE, CardStatus.LOST):
            raise HTTPException(422, "Status must be AVAILABLE, OUT_OF_SERVICE or LOST")
        card.status = body.note
        db.commit()
        return {"uid": card.uid, "status": card.status}


@router.post("/reset-demo")
def reset_demo():
    if not settings.ALLOW_DEMO_RESET:
        raise HTTPException(403, "Demo reset is disabled")
    from ..seed import reset_and_seed
    with state_lock:
        reset_and_seed()
    return {"ok": True}

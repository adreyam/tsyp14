"""Shared helpers: time, alerts, gateway commands, lane display and lookups."""
import json
import secrets
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from ..enums import ACTIVE_SESSION_STATUSES, SessionStatus, SpaceStatus
from ..models import Alert, Command, Lane, ParkingSession, Space

_KEEP = object()


class FlowError(Exception):
    """Request not valid in the current state (returned to the caller as HTTP 409)."""


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _eq(column, value):
    return column.is_(None) if value is None else column == value


# ---------------------------------------------------------------- alerts
def raise_alert(db: DB, lot_id: Optional[str], alert_type: str, severity: str, message: str, *,
                session_id: Optional[int] = None, space_id: Optional[str] = None,
                lane_id: Optional[str] = None) -> Alert:
    """Create an alert, or refresh the identical unresolved one (no duplicates)."""
    existing = db.scalar(select(Alert).where(
        Alert.resolved.is_(False), Alert.type == alert_type,
        _eq(Alert.session_id, session_id), _eq(Alert.space_id, space_id), _eq(Alert.lane_id, lane_id),
    ))
    if existing:
        existing.message = message
        return existing
    alert = Alert(lot_id=lot_id, type=alert_type, severity=severity, message=message,
                  session_id=session_id, space_id=space_id, lane_id=lane_id, created_at=now())
    db.add(alert)
    db.flush()
    return alert


def resolve_alerts(db: DB, *, types: Optional[Iterable[str]] = None, session_id: Optional[int] = None,
                   space_id: Optional[str] = None, lane_id: Optional[str] = None,
                   note: str = "Resolved automatically") -> None:
    if session_id is None and space_id is None and lane_id is None:
        return
    q = select(Alert).where(Alert.resolved.is_(False))
    if types:
        q = q.where(Alert.type.in_(list(types)))
    if session_id is not None:
        q = q.where(Alert.session_id == session_id)
    if space_id is not None:
        q = q.where(Alert.space_id == space_id)
    if lane_id is not None:
        q = q.where(Alert.lane_id == lane_id)
    for alert in db.scalars(q):
        alert.resolved = True
        alert.resolved_at = now()
        alert.resolution_note = note


# ---------------------------------------------------------------- gateway commands
def enqueue_command(db: DB, gateway_id: str, command_type: str, **payload) -> None:
    db.add(Command(gateway_id=gateway_id, type=command_type, payload=json.dumps(payload), created_at=now()))


def space_indicator(db: DB, space: Space, mode: str, text: str = "") -> None:
    """Drive the LED/indicator at a space: OFF, ASSIGNED, OK or WRONG."""
    enqueue_command(db, space.gateway_id, "SPACE_INDICATOR", space_code=space.code, mode=mode, text=text)


# ---------------------------------------------------------------- lanes / terminals
def set_lane(lane: Lane, state: str, message: str = "", *, ticket: Optional[dict] = None,
             session_id=_KEEP) -> None:
    lane.state = state
    lane.message = message
    lane.ticket_json = json.dumps(ticket) if ticket else None
    if session_id is not _KEEP:
        lane.session_id = session_id
    lane.state_changed_at = now()


# ---------------------------------------------------------------- lookups
def active_session_for_vehicle(db: DB, vehicle_id: str, lot_id: Optional[str] = None) -> Optional[ParkingSession]:
    q = select(ParkingSession).where(ParkingSession.vehicle_id == vehicle_id,
                                     ParkingSession.status.in_(ACTIVE_SESSION_STATUSES))
    if lot_id:
        q = q.where(ParkingSession.lot_id == lot_id)
    return db.scalar(q.order_by(ParkingSession.id.desc()))


def active_session_for_rfid(db: DB, uid: str, lot_id: Optional[str] = None) -> Optional[ParkingSession]:
    q = select(ParkingSession).where(ParkingSession.rfid_uid == uid,
                                     ParkingSession.status.in_(ACTIVE_SESSION_STATUSES))
    if lot_id:
        q = q.where(ParkingSession.lot_id == lot_id)
    return db.scalar(q.order_by(ParkingSession.id.desc()))


def assignable_spaces_query(lot_id: str):
    """Spaces that are free, not reserved, in service and confirmed empty by their magnetic sensor."""
    return (select(Space)
            .where(Space.lot_id == lot_id,
                   Space.status == SpaceStatus.AVAILABLE,
                   Space.sensor_occupied.is_(False),
                   Space.sensor_fault.is_(False),
                   Space.assigned_session_id.is_(None))
            .order_by(Space.position, Space.code))


def count_assignable(db: DB, lot_id: str) -> int:
    return db.scalar(select(func.count()).select_from(assignable_spaces_query(lot_id).subquery())) or 0


def recompute_space_status(db: DB, space: Space, releasing: bool = False) -> None:
    """Derive the logical space status from its assignment and its magnetic sensor."""
    if space.status == SpaceStatus.OUT_OF_SERVICE:
        return
    session = db.get(ParkingSession, space.assigned_session_id) if space.assigned_session_id else None
    if session is not None:
        if session.status in (SessionStatus.PARKED, SessionStatus.VERIFICATION):
            space.status = SpaceStatus.OCCUPIED
        else:
            space.status = SpaceStatus.RESERVED
    elif space.sensor_occupied:
        # Physically occupied without an assignment: a vehicle that already exited
        # (waiting for the sensor to confirm empty), a misplaced or an unknown vehicle.
        space.status = SpaceStatus.RELEASING if (releasing or space.status == SpaceStatus.RELEASING) \
            else SpaceStatus.OCCUPIED
    else:
        space.status = SpaceStatus.AVAILABLE


def new_ticket_id(lot_id: str) -> str:
    return f"TKT-{lot_id}-{now():%Y%m%d}-{secrets.token_hex(3).upper()}"


def space_code(db: DB, space_id: Optional[str]) -> Optional[str]:
    if not space_id:
        return None
    space = db.get(Space, space_id)
    return space.code if space else None


def session_dict(db: DB, s: ParkingSession) -> dict:
    from ..models import Vehicle
    vehicle = db.get(Vehicle, s.vehicle_id)
    return {
        "id": s.id, "ticket_id": s.ticket_id, "vehicle_id": s.vehicle_id,
        "category": vehicle.category if vehicle else None, "plate": vehicle.plate if vehicle else None,
        "rfid": s.rfid_uid, "lot_id": s.lot_id, "space": space_code(db, s.space_id),
        "status": s.status, "verification_reason": s.verification_reason,
        "entry_time": iso(s.entry_time), "parked_at": iso(s.parked_at),
        "left_space_at": iso(s.left_space_at), "exit_time": iso(s.exit_time),
        "closed_by": s.closed_by, "card_returned": s.card_returned, "notes": s.notes,
    }

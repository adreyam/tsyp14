"""Parking spaces: magnetic occupancy sensor + local RFID reader.

A parking operation is validated only when BOTH happen at the same space:
  * the magnetic sensor reports the space as occupied, and
  * the local RFID reader identifies a tag belonging to an active session.
Requiring the magnetic confirmation stops a reader from validating a tag that
belongs to a car in the neighbouring space.
"""
from datetime import timedelta

from sqlalchemy.orm import Session as DB

from ..config import settings
from ..enums import SessionStatus, Severity
from ..models import ParkingSession, Space
from .common import (active_session_for_rfid, now, raise_alert, recompute_space_status,
                     resolve_alerts, space_indicator)


def occupancy_changed(db: DB, space: Space, occupied: bool) -> None:
    if space.sensor_occupied == occupied:
        return  # duplicate report
    space.sensor_occupied = occupied
    space.sensor_changed_at = now()
    space.unidentified_alerted = False
    if occupied:
        recompute_space_status(db, space)
        _evaluate(db, space)
    else:
        _vacated(db, space)


def rfid_seen(db: DB, space: Space, uid: str) -> None:
    space.last_rfid_uid = uid
    space.last_rfid_at = now()
    if space.sensor_occupied:
        _evaluate(db, space)
    # A tag read while the magnetic sensor says "empty" is kept for a short time
    # (the car may be just arriving) but never validates anything on its own.


def sensor_fault(db: DB, space: Space, fault: bool, detail: str = "") -> None:
    space.sensor_fault = fault
    if fault:
        raise_alert(db, space.lot_id, "SENSOR_FAULT", Severity.WARNING,
                    f"Magnetic sensor of space {space.code} reports a fault. {detail}".strip(),
                    space_id=space.id)
    else:
        resolve_alerts(db, types=["SENSOR_FAULT"], space_id=space.id, note="Sensor recovered")


# ------------------------------------------------------------------ internals
def _vacated(db: DB, space: Space) -> None:
    occupant_id = space.occupant_session_id
    space.occupant_session_id = None
    space.last_rfid_uid = None   # only reads after this moment can identify the next vehicle
    space.last_rfid_at = None
    resolve_alerts(db, types=["UNIDENTIFIED_VEHICLE", "UNKNOWN_TAG_AT_SPACE"], space_id=space.id,
                   note="Space is empty again")

    if occupant_id:
        session = db.get(ParkingSession, occupant_id)
        if session and session.space_id == space.id and \
                session.status in (SessionStatus.PARKED, SessionStatus.VERIFICATION):
            # The vehicle left its own space. This is normally the way to the exit, so the
            # session is NOT closed; the watchdog flags it if it never reaches the exit.
            session.left_space_at = now()
        elif session and session.status == SessionStatus.MISPLACED:
            space_indicator(db, space, "OFF")   # misplaced vehicle left the wrong space
    recompute_space_status(db, space)


def _evaluate(db: DB, space: Space) -> None:
    if not space.sensor_occupied or not space.last_rfid_uid or not space.last_rfid_at:
        return
    window = timedelta(seconds=settings.SPACE_CORRELATION_WINDOW_S)
    if space.sensor_changed_at and space.last_rfid_at < space.sensor_changed_at - window:
        return  # the read is too old to belong to this arrival

    uid = space.last_rfid_uid
    session = active_session_for_rfid(db, uid, space.lot_id)
    if session is None:
        if space.occupant_session_id is None:
            raise_alert(db, space.lot_id, "UNKNOWN_TAG_AT_SPACE", Severity.WARNING,
                        f"Space {space.code} is occupied by tag {uid}, which has no active session "
                        f"in this lot.", space_id=space.id)
        recompute_space_status(db, space)
        return

    session.last_tag_seen_at = now()
    resolve_alerts(db, types=["TAG_NOT_DETECTED"], session_id=session.id, note="Tag detected again")

    # Periodic presence confirmation of an already validated vehicle
    if space.occupant_session_id == session.id and session.status == SessionStatus.PARKED \
            and session.space_id == space.id:
        return

    if session.space_id == space.id:
        _validate_parking(db, space, session)
    else:
        _misplaced(db, space, session)


def _validate_parking(db: DB, space: Space, session: ParkingSession) -> None:
    """RFID belongs to an active session, its assigned space is this one, sensor says occupied."""
    if session.status not in (SessionStatus.ASSIGNED, SessionStatus.MISPLACED,
                              SessionStatus.VERIFICATION, SessionStatus.PARKED):
        return
    session.status = SessionStatus.PARKED                  # ASSIGNED -> PARKED
    session.parked_at = session.parked_at or now()
    session.left_space_at = None
    session.verification_reason = None
    space.occupant_session_id = session.id
    space.unidentified_alerted = False
    recompute_space_status(db, space)                      # RESERVED -> OCCUPIED
    resolve_alerts(db, types=["MISPLACED", "NOT_ARRIVED", "VEHICLE_LEFT_SPACE"],
                   session_id=session.id, note="Vehicle parked in its assigned space")
    resolve_alerts(db, types=["UNIDENTIFIED_VEHICLE", "UNKNOWN_TAG_AT_SPACE"], space_id=space.id,
                   note="Vehicle identified")
    space_indicator(db, space, "OK")


def _misplaced(db: DB, space: Space, session: ParkingSession) -> None:
    if session.status not in (SessionStatus.ASSIGNED, SessionStatus.PARKED,
                              SessionStatus.VERIFICATION, SessionStatus.MISPLACED):
        return
    assigned = db.get(Space, session.space_id) if session.space_id else None
    assigned_code = assigned.code if assigned else "?"

    session.status = SessionStatus.MISPLACED
    space.occupant_session_id = session.id
    recompute_space_status(db, space)
    if assigned:
        recompute_space_status(db, assigned)               # stays RESERVED for this vehicle

    raise_alert(db, space.lot_id, "MISPLACED", Severity.WARNING,
                f"Vehicle {session.vehicle_id} ({session.rfid_uid}, ticket {session.ticket_id}) parked in "
                f"{space.code} but was assigned {assigned_code}.",
                session_id=session.id, space_id=space.id)
    if space.assigned_session_id and space.assigned_session_id != session.id:
        raise_alert(db, space.lot_id, "RESERVED_SPACE_TAKEN", Severity.CRITICAL,
                    f"Space {space.code} is reserved for session {space.assigned_session_id} but is "
                    f"occupied by {session.vehicle_id}.", space_id=space.id)
    space_indicator(db, space, "WRONG", f"Wrong space - go to {assigned_code}")

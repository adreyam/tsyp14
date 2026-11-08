"""Exit lane: identification, verification, card return and session closure."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..enums import CardStatus, LaneState, SessionStatus, Severity, VerificationReason
from ..models import Lane, ParkingSession, Space, TempCard, Vehicle
from .common import (active_session_for_vehicle, enqueue_command, now, raise_alert,
                     recompute_space_status, resolve_alerts, set_lane, space_indicator)

MANUAL_MSG = ("Your vehicle could not be verified automatically. "
              "Please wait for the parking administrator.")


def exit_allowed(session: ParkingSession) -> bool:
    if session.status == SessionStatus.PARKED:
        return True
    # Flagged only because the vehicle left its space: being at the exit explains it.
    return session.status == SessionStatus.VERIFICATION and \
        session.verification_reason == VerificationReason.SPACE_VACANT


# ------------------------------------------------------------ 7. vehicle approaches the exit
def vehicle_detected(db: DB, lane: Lane) -> None:
    lane.vehicle_present = True
    if lane.state != LaneState.IDLE:
        return
    set_lane(lane, LaneState.WAITING_RFID, "Reading your vehicle tag...", session_id=None)


def rfid_timeout(db: DB, lane: Lane) -> None:
    """A vehicle is counted at the exit but no RFID could be read: never close an arbitrary session."""
    if lane.state == LaneState.WAITING_RFID:
        _manual(db, lane, "EXIT_UNIDENTIFIED",
                f"Vehicle detected at {lane.id} but no RFID identifier could be read. "
                f"Manual verification required.")


def vehicle_cleared(db: DB, lane: Lane) -> None:
    lane.vehicle_present = False
    set_lane(lane, LaneState.IDLE, "", session_id=None)


# ------------------------------------------------------------ 8. verification
def rfid_read(db: DB, lane: Lane, uid: str) -> None:
    if lane.state != LaneState.WAITING_RFID:
        return

    # Case A: registered vehicle
    vehicle = db.scalar(select(Vehicle).where(Vehicle.permanent_rfid == uid))
    if vehicle is not None:
        session = active_session_for_vehicle(db, vehicle.id, lane.lot_id)
        if session is None:
            _manual(db, lane, "NO_ACTIVE_SESSION",
                    f"Registered vehicle {vehicle.id} ({uid}) at {lane.id} has no active session in "
                    f"lot {lane.lot_id}.")
            return
        if not exit_allowed(session):
            _manual(db, lane, "EXIT_STATUS_INVALID",
                    f"Vehicle {vehicle.id} at {lane.id}: session {session.ticket_id} is {session.status}.",
                    session)
            return
        close_session(db, session, lane, closed_by="AUTO")
        set_lane(lane, LaneState.EXIT_AUTHORIZED, "Thank you for your visit. Goodbye!", session_id=session.id)
        return

    # Case B: visitor with a temporary card
    card = db.get(TempCard, uid)
    if card is not None and card.lot_id == lane.lot_id:
        session = db.get(ParkingSession, card.session_id) if card.session_id else None
        if session is None or card.status != CardStatus.IN_USE or \
                session.status not in (SessionStatus.PARKED, SessionStatus.MISPLACED,
                                       SessionStatus.VERIFICATION, SessionStatus.ASSIGNED):
            _manual(db, lane, "NO_ACTIVE_SESSION",
                    f"Temporary card {uid} at {lane.id} is not linked to an active session.")
            return
        if session.rfid_uid != uid:   # the card must match the ticket's RFID
            _manual(db, lane, "TICKET_RFID_MISMATCH",
                    f"Card {uid} does not match the RFID {session.rfid_uid} of ticket {session.ticket_id}.",
                    session)
            return
        if not exit_allowed(session):
            _manual(db, lane, "EXIT_STATUS_INVALID",
                    f"Visitor {session.vehicle_id} at {lane.id}: session {session.ticket_id} is "
                    f"{session.status}.", session)
            return
        session.exit_lane_id = lane.id
        set_lane(lane, LaneState.AWAITING_CARD_RETURN,
                 "Please insert your temporary card into the card-return unit.", session_id=session.id)
        return

    _manual(db, lane, "UNKNOWN_TAG_AT_EXIT", f"Unknown RFID {uid} at {lane.id}.")


def card_returned(db: DB, lane: Lane, uid: str) -> None:
    """The card-return unit's reader confirms the identifier before the session is closed."""
    if lane.state != LaneState.AWAITING_CARD_RETURN or lane.session_id is None:
        return
    session = db.get(ParkingSession, lane.session_id)
    if session is None:
        return
    if uid != session.rfid_uid:
        lane.message = ("This card does not match your parking session. "
                        "Please insert the card you received at the entrance.")
        raise_alert(db, lane.lot_id, "WRONG_CARD_RETURNED", Severity.WARNING,
                    f"{lane.id}: card {uid} returned for ticket {session.ticket_id} "
                    f"(expected {session.rfid_uid}).", session_id=session.id, lane_id=lane.id)
        return
    session.card_returned = True
    close_session(db, session, lane, closed_by="AUTO")
    set_lane(lane, LaneState.EXIT_AUTHORIZED, "Card returned. Thank you for your visit. Goodbye!",
             session_id=session.id)


def _manual(db: DB, lane: Lane, alert_type: str, message: str,
            session: Optional[ParkingSession] = None) -> None:
    set_lane(lane, LaneState.MANUAL_VERIFICATION, MANUAL_MSG,
             session_id=session.id if session else None)
    raise_alert(db, lane.lot_id, alert_type, Severity.CRITICAL, message,
                session_id=session.id if session else None, lane_id=lane.id)


# ------------------------------------------------------------ 9. session closure
def close_session(db: DB, session: ParkingSession, lane: Optional[Lane], *, closed_by: str,
                  card_status: str = CardStatus.AVAILABLE, final_status: str = SessionStatus.EXITED,
                  note: Optional[str] = None) -> None:
    session.exit_time = now()
    session.status = final_status                              # PARKED -> EXITED
    session.closed_by = closed_by
    if lane is not None:
        session.exit_lane_id = lane.id
    if note:
        session.notes = ((session.notes + "\n") if session.notes else "") + note

    # Release every space linked to this session. A space becomes AVAILABLE only
    # when its magnetic sensor confirms it is empty; otherwise it is RELEASING.
    linked = db.scalars(select(Space).where(
        (Space.assigned_session_id == session.id) | (Space.occupant_session_id == session.id))).all()
    for space in linked:
        if space.assigned_session_id == session.id:
            space.assigned_session_id = None
        if space.occupant_session_id == session.id:
            space.occupant_session_id = None
        recompute_space_status(db, space, releasing=True)
        space_indicator(db, space, "OFF")

    card = db.scalar(select(TempCard).where(TempCard.session_id == session.id))
    if card is not None:
        card.status = card_status                              # IN_USE -> AVAILABLE once returned
        card.session_id = None

    resolve_alerts(db, session_id=session.id, note=f"Session closed ({final_status})")
    if lane is not None:
        enqueue_command(db, lane.gateway_id, "OPEN_BARRIER", lane_id=lane.id)

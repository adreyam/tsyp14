"""Entrance lane: vehicle detection, identification, temporary cards, space assignment."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from ..enums import CardStatus, LaneState, SessionStatus, Severity, SpaceStatus, VehicleCategory
from ..models import Lane, ParkingSession, Space, TempCard, Vehicle
from .common import (FlowError, active_session_for_vehicle, assignable_spaces_query, count_assignable,
                     enqueue_command, iso, new_ticket_id, now, raise_alert, set_lane, space_indicator)

LOT_FULL_MSG = "The parking lot is full. Please try again later."


# ------------------------------------------------------------ 1. vehicle arrives
def vehicle_detected(db: DB, lane: Lane) -> None:
    lane.vehicle_present = True
    if lane.state != LaneState.IDLE:
        return  # a transaction is already running for this lane
    set_lane(lane, LaneState.WAITING_RFID, "Welcome. Reading your vehicle tag...", session_id=None)


def rfid_read(db: DB, lane: Lane, uid: str) -> None:
    if lane.state == LaneState.WAITING_RFID:
        _identify(db, lane, uid)
    elif lane.state == LaneState.AWAITING_CARD:
        _temporary_card_presented(db, lane, uid)
    # in any other state a repeated read is ignored


def rfid_timeout(db: DB, lane: Lane) -> None:
    """No tag detected during the configured detection period (scenario B)."""
    if lane.state == LaneState.WAITING_RFID:
        _offer_temporary_card(lane, "No RFID tag detected.")


def vehicle_cleared(db: DB, lane: Lane) -> None:
    """The vehicle left the entrance detection sensor (entered, or reversed away)."""
    lane.vehicle_present = False
    if lane.state == LaneState.AWAITING_CARD:
        raise_alert(db, lane.lot_id, "CARD_NOT_PRESENTED", Severity.WARNING,
                    f"{lane.id}: a temporary card was dispensed but the vehicle left before presenting it.",
                    lane_id=lane.id)
    set_lane(lane, LaneState.IDLE, "", session_id=None)


# ------------------------------------------------------------ 2. scenario A: permanent tag
def _identify(db: DB, lane: Lane, uid: str) -> None:
    vehicle = db.scalar(select(Vehicle).where(Vehicle.permanent_rfid == uid))
    if vehicle is None:
        # Unknown tag: the vehicle is treated as an unregistered visitor.
        _offer_temporary_card(lane, "This tag is not registered.")
        return

    if not vehicle.active:
        set_lane(lane, LaneState.BLOCKED, "Access refused. Please contact the parking administrator.")
        raise_alert(db, lane.lot_id, "VEHICLE_DISABLED", Severity.WARNING,
                    f"Disabled vehicle {vehicle.id} ({uid}) tried to enter at {lane.id}.", lane_id=lane.id)
        return

    existing = active_session_for_vehicle(db, vehicle.id)
    if existing is not None:
        set_lane(lane, LaneState.BLOCKED,
                 "Entry refused: this vehicle already has an active parking session. "
                 "The parking administrator has been notified.",
                 session_id=existing.id)   # remembers which session caused the refusal (admin approval checks it)
        raise_alert(db, lane.lot_id, "DUPLICATE_ENTRY", Severity.CRITICAL,
                    f"Vehicle {vehicle.id} ({uid}) tried to enter at {lane.id} while session "
                    f"{existing.ticket_id or existing.id} is active in lot {existing.lot_id}.",
                    session_id=existing.id, lane_id=lane.id)
        return

    if count_assignable(db, lane.lot_id) == 0:
        set_lane(lane, LaneState.BLOCKED, LOT_FULL_MSG)
        return

    session = ParkingSession(vehicle_id=vehicle.id, rfid_uid=uid, lot_id=lane.lot_id,
                             status=SessionStatus.PENDING, entry_lane_id=lane.id, entry_time=now())
    db.add(session)
    db.flush()
    _assign_space(db, lane, session)


# ------------------------------------------------------------ 3. scenario B: visitor
def _offer_temporary_card(lane: Lane, reason: str) -> None:
    set_lane(lane, LaneState.AWAITING_TICKET_REQUEST,
             f"{reason} Would you like a temporary RFID card? Press \"Request a ticket\".")


def request_ticket(db: DB, lane: Lane) -> None:
    """The driver pressed "Request a ticket" on the entrance terminal."""
    if lane.kind != "ENTRANCE" or lane.state != LaneState.AWAITING_TICKET_REQUEST:
        raise FlowError("No ticket request is expected at this terminal right now.")

    if count_assignable(db, lane.lot_id) == 0:
        set_lane(lane, LaneState.BLOCKED, LOT_FULL_MSG)
        return

    available_cards = db.scalar(select(func.count()).select_from(TempCard).where(
        TempCard.lot_id == lane.lot_id, TempCard.status == CardStatus.AVAILABLE)) or 0
    if available_cards == 0:
        # Never create a session with an RFID identifier that does not physically exist.
        set_lane(lane, LaneState.BLOCKED,
                 "No temporary RFID card is available. Please contact the parking administrator.")
        raise_alert(db, lane.lot_id, "NO_TEMP_CARD", Severity.WARNING,
                    f"{lane.id}: a visitor requested a ticket but no temporary card is available.",
                    lane_id=lane.id)
        return

    enqueue_command(db, lane.gateway_id, "DISPENSE_CARD", lane_id=lane.id)
    set_lane(lane, LaneState.AWAITING_CARD,
             "Please take your temporary card and present it to the RFID reader.")


def dispense_failed(db: DB, lane: Lane, detail: str = "") -> None:
    """The gateway reports the dispenser is physically empty or jammed."""
    set_lane(lane, LaneState.BLOCKED,
             "No temporary RFID card could be issued. Please contact the parking administrator.")
    raise_alert(db, lane.lot_id, "DISPENSER_FAILURE", Severity.CRITICAL,
                f"{lane.id}: card dispenser failed. {detail}".strip(), lane_id=lane.id)


def _temporary_card_presented(db: DB, lane: Lane, uid: str) -> None:
    card = db.get(TempCard, uid)
    if card is None or card.lot_id != lane.lot_id:
        lane.message = ("This is not a temporary card of this parking lot. "
                        "Please present the card you have just received.")
        return
    if card.status != CardStatus.AVAILABLE:
        lane.message = "This card cannot be used. Please contact the parking administrator."
        raise_alert(db, lane.lot_id, "CARD_STATE_MISMATCH", Severity.WARNING,
                    f"{lane.id}: card {uid} was presented at the entrance but its status is {card.status}.",
                    lane_id=lane.id)
        return

    visitor_count = db.scalar(select(func.count()).select_from(Vehicle).where(
        Vehicle.category == VehicleCategory.VISITOR)) or 0
    vehicle = Vehicle(id=f"TEMP_V{visitor_count + 1:04d}", category=VehicleCategory.VISITOR,
                      active=True, created_at=now())
    db.add(vehicle)
    session = ParkingSession(vehicle_id=vehicle.id, rfid_uid=uid, lot_id=lane.lot_id,
                             status=SessionStatus.PENDING, entry_lane_id=lane.id, entry_time=now())
    db.add(session)
    db.flush()
    card.status = CardStatus.IN_USE          # cannot be given to another visitor
    card.session_id = session.id
    _assign_space(db, lane, session)


# ------------------------------------------------------------ 4. space assignment
def _assign_space(db: DB, lane: Lane, session: ParkingSession) -> None:
    space: Space | None = db.scalar(assignable_spaces_query(lane.lot_id))
    if space is None:
        set_lane(lane, LaneState.BLOCKED,
                 "No parking space is available anymore. Please contact the parking administrator.",
                 session_id=session.id)
        raise_alert(db, lane.lot_id, "NO_SPACE_FOR_SESSION", Severity.WARNING,
                    f"Session {session.id} ({session.rfid_uid}) is PENDING: no space could be assigned.",
                    session_id=session.id, lane_id=lane.id)
        return

    t = now()
    space.status = SpaceStatus.RESERVED          # nobody else can receive this space
    space.assigned_session_id = session.id
    session.space_id = space.id
    session.status = SessionStatus.ASSIGNED
    session.assigned_at = t
    session.ticket_id = new_ticket_id(lane.lot_id)

    ticket = {
        "ticket_id": session.ticket_id,       # identifies the session
        "rfid": session.rfid_uid,             # identifies the vehicle / temporary card
        "space": space.code,
        "lot_id": lane.lot_id,
        "entry_time": iso(session.entry_time),
    }
    from .recommend import consume_reservation   # a driver who was heading here has arrived
    consume_reservation(db, lane.lot_id)
    set_lane(lane, LaneState.TICKET_SHOWN, f"Please park in space {space.code}.",
             ticket=ticket, session_id=session.id)
    space_indicator(db, space, "ASSIGNED")
    enqueue_command(db, lane.gateway_id, "OPEN_BARRIER", lane_id=lane.id)

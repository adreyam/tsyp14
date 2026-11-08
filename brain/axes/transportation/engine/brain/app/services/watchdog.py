"""Periodic consistency checks (runs every WATCHDOG_INTERVAL_S seconds)."""
import logging
import threading
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..database import SessionLocal, state_lock
from ..enums import SessionStatus, Severity, VerificationReason
from ..models import Gateway, ParkingSession, Space
from .common import now, raise_alert, recompute_space_status, resolve_alerts, space_code

log = logging.getLogger("parking.watchdog")


def run_checks(db: DB) -> None:
    t = now()

    # Vehicle received a space but never arrived there
    for s in db.scalars(select(ParkingSession).where(ParkingSession.status == SessionStatus.ASSIGNED)):
        if s.assigned_at and t - s.assigned_at > timedelta(seconds=settings.ASSIGNMENT_TIMEOUT_S):
            raise_alert(db, s.lot_id, "NOT_ARRIVED", Severity.WARNING,
                        f"Vehicle {s.vehicle_id} (ticket {s.ticket_id}) has not reached space "
                        f"{space_code(db, s.space_id)}.", session_id=s.id)

    # Parked vehicle left its space and did not exit: flag, never close automatically
    for s in db.scalars(select(ParkingSession).where(ParkingSession.status == SessionStatus.PARKED,
                                                     ParkingSession.left_space_at.is_not(None))):
        if t - s.left_space_at >= timedelta(seconds=settings.LEFT_SPACE_GRACE_S):
            s.status = SessionStatus.VERIFICATION
            s.verification_reason = VerificationReason.SPACE_VACANT
            raise_alert(db, s.lot_id, "VEHICLE_LEFT_SPACE", Severity.WARNING,
                        f"Vehicle {s.vehicle_id} (ticket {s.ticket_id}) is no longer detected in space "
                        f"{space_code(db, s.space_id)} and has not exited.", session_id=s.id)

    # Magnetic sensor says occupied but no tag was identified (missing tag / RFID failure)
    for sp in db.scalars(select(Space).where(Space.sensor_occupied.is_(True),
                                             Space.occupant_session_id.is_(None),
                                             Space.unidentified_alerted.is_(False))):
        if sp.sensor_changed_at and t - sp.sensor_changed_at > timedelta(
                seconds=settings.UNIDENTIFIED_OCCUPANCY_ALERT_S):
            sp.unidentified_alerted = True
            recompute_space_status(db, sp)
            raise_alert(db, sp.lot_id, "UNIDENTIFIED_VEHICLE", Severity.WARNING,
                        f"Space {sp.code} is occupied but no RFID tag was identified.", space_id=sp.id)

    # Periodic tag presence of parked vehicles (informational only)
    if settings.PRESENCE_TIMEOUT_S > 0:
        for s in db.scalars(select(ParkingSession).where(ParkingSession.status == SessionStatus.PARKED,
                                                         ParkingSession.left_space_at.is_(None))):
            seen = s.last_tag_seen_at or s.parked_at
            if seen and t - seen > timedelta(seconds=settings.PRESENCE_TIMEOUT_S):
                raise_alert(db, s.lot_id, "TAG_NOT_DETECTED", Severity.INFO,
                            f"Tag {s.rfid_uid} of vehicle {s.vehicle_id} has not been read in space "
                            f"{space_code(db, s.space_id)} recently (vehicle still detected magnetically).",
                            session_id=s.id)

    # Gateways that stopped polling
    for gw in db.scalars(select(Gateway)):
        if gw.last_seen_at is None:
            continue
        if t - gw.last_seen_at > timedelta(seconds=settings.GATEWAY_OFFLINE_S):
            raise_alert(db, gw.lot_id, "GATEWAY_OFFLINE", Severity.CRITICAL,
                        f"Gateway {gw.id} has not communicated since {gw.last_seen_at:%H:%M:%S} UTC.")


def start(stop_event: threading.Event) -> threading.Thread:
    def loop():
        while not stop_event.wait(settings.WATCHDOG_INTERVAL_S):
            try:
                with state_lock, SessionLocal() as db:
                    run_checks(db)
                    db.commit()
            except Exception:  # keep the watchdog alive whatever happens
                log.exception("watchdog run failed")

    thread = threading.Thread(target=loop, name="parking-watchdog", daemon=True)
    thread.start()
    return thread


def gateway_seen(db: DB, gateway: Gateway) -> None:
    gateway.last_seen_at = now()
    # Offline alerts carry no lane/space/session, so resolve them by type on this lot
    from ..models import Alert
    for a in db.scalars(select(Alert).where(Alert.resolved.is_(False), Alert.type == "GATEWAY_OFFLINE",
                                            Alert.lot_id == gateway.lot_id,
                                            Alert.message.like(f"Gateway {gateway.id} %"))):
        a.resolved = True
        a.resolved_at = now()
        a.resolution_note = "Gateway back online"

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .enums import CardStatus, LaneState, SpaceStatus


class ParkingLot(Base):
    __tablename__ = "parking_lots"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)          # e.g. P01
    name: Mapped[str] = mapped_column(String(100))


class Gateway(Base):
    """One ESP32 / IoT gateway, connected to the sensors and readers of one lot."""
    __tablename__ = "gateways"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # e.g. GW-P01
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Lane(Base):
    """An entrance or exit lane: detection sensor + RFID reader + terminal + barrier."""
    __tablename__ = "lanes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # e.g. P01-ENT1
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    gateway_id: Mapped[str] = mapped_column(ForeignKey("gateways.id"))
    kind: Mapped[str] = mapped_column(String(10))                          # ENTRANCE / EXIT
    state: Mapped[str] = mapped_column(String(30), default=LaneState.IDLE)
    message: Mapped[str] = mapped_column(Text, default="")
    ticket_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vehicle_present: Mapped[bool] = mapped_column(Boolean, default=False)
    state_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Space(Base):
    """A parking space with its magnetic vehicle sensor and local RFID reader."""
    __tablename__ = "spaces"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # e.g. P01-E2
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    code: Mapped[str] = mapped_column(String(10))                          # e.g. E2
    position: Mapped[int] = mapped_column(Integer, default=0)              # assignment order
    gateway_id: Mapped[str] = mapped_column(ForeignKey("gateways.id"))
    status: Mapped[str] = mapped_column(String(20), default=SpaceStatus.AVAILABLE)

    # Magnetic sensor state, as reported by the gateway
    sensor_occupied: Mapped[bool] = mapped_column(Boolean, default=False)
    sensor_fault: Mapped[bool] = mapped_column(Boolean, default=False)
    sensor_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Last tag seen by the local RFID reader since the space was last vacant
    last_rfid_uid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_rfid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    assigned_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    occupant_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    unidentified_alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # V001 / TEMP_V0001
    category: Mapped[str] = mapped_column(String(20))                      # REGISTERED / VISITOR
    permanent_rfid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    plate: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TempCard(Base):
    """Reusable visitor RFID card, stocked in the entrance dispenser of one lot."""
    __tablename__ = "temp_cards"
    uid: Mapped[str] = mapped_column(String(64), primary_key=True)         # e.g. RFID_TEMP_025
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    status: Mapped[str] = mapped_column(String(20), default=CardStatus.AVAILABLE)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ParkingSession(Base):
    """One visit. The ticket identifies the session; the RFID identifies the vehicle/card."""
    __tablename__ = "parking_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"))
    rfid_uid: Mapped[str] = mapped_column(String(64))
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    space_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    verification_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    entry_lane_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    exit_lane_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    parked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    left_space_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_tag_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # AUTO / ADMIN
    card_returned: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    space_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    lane_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Command(Base):
    """Command waiting to be picked up by a gateway (OPEN_BARRIER, DISPENSE_CARD, ...)."""
    __tablename__ = "commands"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_id: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ----------------------------------------------------------------------------
# Ranking (Top-3 recommendation) tables. New tables only: an existing parking.db
# gets them automatically at start-up, no migration needed.
# ----------------------------------------------------------------------------
class LotProfile(Base):
    """Static facts the ranking needs about a lot: where it is and what drivers think of it."""
    __tablename__ = "lot_profiles"
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    review_sum: Mapped[float] = mapped_column(Float, default=0.0)         # sum of 1..5 ratings
    review_count: Mapped[int] = mapped_column(Integer, default=0)


class Reservation(Base):
    """A driver chose this lot and is on the way. Counts as "competition" for the next driver."""
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)                # arrival ETA + grace
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")     # ACTIVE/ARRIVED/EXPIRED/CANCELLED


class OccupancySnapshot(Base):
    """Periodic count of free/occupied spaces per lot: the history the forecast learns from."""
    __tablename__ = "occupancy_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    total: Mapped[int] = mapped_column(Integer)
    occupied: Mapped[int] = mapped_column(Integer)                        # total - available (reserved counts as taken)

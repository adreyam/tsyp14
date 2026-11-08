"""Extra tables of the city system (drivers, their frequent places, messages to the car screens)."""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Driver(Base):
    __tablename__ = "drivers"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)             # d1, d2, ...
    name: Mapped[str] = mapped_column(String(60))
    rfid: Mapped[str] = mapped_column(String(64))                              # the tag physically on the car (NOT unique: d6 and d13 share one)
    plate: Mapped[str] = mapped_column(String(30))
    married: Mapped[bool] = mapped_column(Boolean, default=False)
    kids: Mapped[bool] = mapped_column(Boolean, default=False)
    shares_tag_with: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    registered_tag: Mapped[bool] = mapped_column(Boolean, default=True)        # False: the tag is registered to another driver


class Place(Base):
    """A frequent place of a driver. Labelled with the driver's id: d3-work, d3-school, d3-spouse, d3-cafe, d3-cafe2."""
    __tablename__ = "places"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    driver_id: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(12))                              # work / school / spouse / cafe
    label: Mapped[str] = mapped_column(String(80))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    weight: Mapped[int] = mapped_column(Integer, default=1)                    # 1-3: how much the place matters to the routine criterion
    schedule_json: Mapped[str] = mapped_column(Text)                           # [{days, from, to}]
    edge: Mapped[str] = mapped_column(String(60))
    pos: Mapped[float] = mapped_column(Float, default=0.0)


class CarMessage(Base):
    """A message shown on a car's dash screen (sent by the administration, or by the system)."""
    __tablename__ = "car_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    driver_id: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(20))                              # REPARK / ADMIN / INFO
    text: Mapped[str] = mapped_column(Text)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    space_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    sender: Mapped[str] = mapped_column(String(20), default="administration")


class AdminNotice(Base):
    """What the parking system tells the administration screen when something happens to a car
    (e.g. a car parked in another space than the one on its ticket). Complements the lot alerts."""
    __tablename__ = "admin_notices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lot_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    driver_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)

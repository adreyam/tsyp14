from typing import List, Literal, Optional

from pydantic import BaseModel, Field

EventType = Literal[
    # entrance / exit lanes
    "LANE_VEHICLE_DETECTED", "LANE_RFID_READ", "LANE_RFID_TIMEOUT", "LANE_VEHICLE_CLEARED",
    "DISPENSE_FAILED", "CARD_RETURNED",
    # parking spaces (magnetic sensor + local RFID reader)
    "SPACE_OCCUPANCY", "SPACE_RFID_READ", "SPACE_SENSOR_FAULT", "SPACE_SENSOR_OK",
]


class DeviceEvent(BaseModel):
    type: EventType
    lane_id: Optional[str] = None
    space_code: Optional[str] = None
    uid: Optional[str] = None
    occupied: Optional[bool] = None
    magnitude: Optional[float] = None   # |B - baseline| reported by the magnetic sensor (raw counts)
    detail: Optional[str] = None


class NoteBody(BaseModel):
    note: str = ""


class ReassignBody(BaseModel):
    space_code: str
    note: str = ""


class ManualExitBody(BaseModel):
    ticket_id: str
    lane_id: str
    manual_vehicle_check_confirmed: bool = False
    card_status: Literal["RETURNED", "LOST", "NOT_APPLICABLE"] = "NOT_APPLICABLE"
    note: str = Field(min_length=3)


class ServiceBody(BaseModel):
    in_service: bool


class VehicleBody(BaseModel):
    id: str
    rfid: str
    plate: Optional[str] = None
    owner: Optional[str] = None
    active: bool = True


class TempCardsBody(BaseModel):
    lot_id: str
    uids: List[str]

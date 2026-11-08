"""
Messages City Brain receives from the Twin (garbage-bin axis).
Kept as plain validated models: a malformed message from a partner is
rejected with a clear error instead of crashing the layer.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class BinReading(BaseModel):
    """get_sensor_reading, type=bin_weight  (Twin -> City Brain)."""
    tool: str = "get_sensor_reading"
    sensor_id: str
    type: str = "bin_weight"
    weight_kg: float = Field(ge=0)
    capacity_kg: float = Field(gt=0)
    location: Location
    zone: str
    zone_type: Optional[str] = None   # "open" | "crowded"; sets the pass interval
    timestamp: datetime

    @property
    def fill_pct(self) -> float:
        """Fill level in percent, clamped to [0, 100]."""
        return max(0.0, min(100.0, self.weight_kg / self.capacity_kg * 100.0))


class TruckStatus(str, Enum):
    IDLE = "idle"
    EN_ROUTE = "en_route"
    COLLECTING = "collecting"


class TruckState(BaseModel):
    """get_truck_state  (Twin -> City Brain)."""
    tool: str = "get_truck_state"
    truck_id: str
    location: Location
    status: TruckStatus
    current_load_kg: Optional[float] = Field(default=None, ge=0)  # optional, from the Twin
    timestamp: datetime


class TrustStatus(str, Enum):
    TRUSTED = "trusted"
    SUSPICIOUS = "suspicious"
    NOT_TRUSTED = "not_trusted"


class TrustVerdict(BaseModel):
    """get_trust_verdict  (Guardian -> City Brain)."""
    tool: str = "get_trust_verdict"
    sensor_id: str
    status: TrustStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reason: Optional[str] = None
    checked_at: datetime

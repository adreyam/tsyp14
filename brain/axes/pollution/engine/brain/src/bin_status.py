"""
Garbage-bin status: fill %, fill rate, projected fill, color.

Color rules (thresholds are configurable, see BinConfig):
  RED     current fill >= red_pct            -> collect now
  ORANGE  projected fill >= red_pct          -> will be red before the next
                                                truck pass, so collect now
  GREEN   current fill <  green_pct          -> nothing to do
  YELLOW  everything else                    -> can wait for a later pass

projected = current + rate * horizon, where
  rate    = fill-% gained per hour, from this bin's recent readings
  horizon = the pass interval of the bin's zone type: the time between this
            truck pass and the following one (crowded: 4 h, open: 8 h)

Every status carries a human-readable `reason`, so each color can be
explained to the jury and written to the audit log.
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Deque, Dict, Optional

from .models import BinReading, Location



def _span(minutes: float) -> str:
    """4 h, 90 min, 2 min: readable horizon for the reasons shown to the jury."""
    return f"{minutes / 60:.0f} h" if minutes >= 120 and minutes % 60 == 0 else f"{minutes:.0f} min"


class BinColor(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True)
class BinConfig:
    red_pct: float = 90.0            # at or above: red
    green_pct: float = 10.0          # below: green
    # Truck pass interval per zone type = the horizon used for the projection.
    pass_interval_minutes: Dict[str, float] = field(
        default_factory=lambda: {"crowded": 240.0, "open": 480.0})
    # Used when the zone type is missing or unknown: the SHORTER interval,
    # so an unclassified bin is collected early rather than left to overflow.
    fallback_pass_interval_minutes: float = 240.0
    history_size: int = 6            # readings kept per bin for the rate
    emptied_drop_pct: float = 30.0   # a drop this big = bin was emptied

    def horizon_for(self, zone_type: Optional[str]) -> float:
        return self.pass_interval_minutes.get(
            zone_type, self.fallback_pass_interval_minutes)


@dataclass(frozen=True)
class BinStatus:
    sensor_id: str
    location: Location
    timestamp: datetime
    fill_pct: float
    weight_kg: float
    rate_pct_per_h: Optional[float]   # None until 2 readings since last empty
    projected_pct: Optional[float]    # None when the rate is unknown
    color: BinColor
    reason: str
    just_emptied: bool = False


@dataclass
class _History:
    points: Deque = field(default_factory=deque)   # (timestamp, fill_pct)
    last_status: Optional[BinStatus] = None


class BinTracker:
    """Keeps a short history per bin and classifies each new reading."""

    def __init__(self, config: BinConfig = BinConfig()):
        self.config = config
        self._bins: Dict[str, _History] = {}

    def update(self, reading: BinReading) -> BinStatus:
        hist = self._bins.setdefault(reading.sensor_id, _History())
        fill = reading.fill_pct

        # Ignore stale or duplicate readings: keep the last known status.
        if hist.points and reading.timestamp <= hist.points[-1][0]:
            if hist.last_status is not None:
                return hist.last_status

        just_emptied = False
        if hist.points and hist.points[-1][1] - fill >= self.config.emptied_drop_pct:
            # Sharp drop: a truck emptied the bin. Old history no longer applies.
            hist.points.clear()
            just_emptied = True

        hist.points.append((reading.timestamp, fill))
        while len(hist.points) > self.config.history_size:
            hist.points.popleft()

        horizon = self.config.horizon_for(reading.zone_type)
        rate = self._rate_pct_per_h(hist)
        projected = None
        if rate is not None:
            projected = min(100.0, fill + rate * horizon / 60.0)

        color, reason = self._classify(fill, rate, projected, horizon)
        status = BinStatus(
            sensor_id=reading.sensor_id,
            location=reading.location,
            timestamp=reading.timestamp,
            fill_pct=round(fill, 1),
            weight_kg=reading.weight_kg,
            rate_pct_per_h=None if rate is None else round(rate, 2),
            projected_pct=None if projected is None else round(projected, 1),
            color=color,
            reason=reason,
            just_emptied=just_emptied,
        )
        hist.last_status = status
        return status

    # ------------------------------------------------------------------
    def _rate_pct_per_h(self, hist: _History) -> Optional[float]:
        """Fill-% gained per hour between the oldest and newest kept reading."""
        if len(hist.points) < 2:
            return None
        (t0, f0), (t1, f1) = hist.points[0], hist.points[-1]
        hours = (t1 - t0).total_seconds() / 3600.0
        if hours <= 0:
            return None
        return max(0.0, (f1 - f0) / hours)   # sensor noise must not give < 0

    def _classify(self, fill: float, rate: Optional[float],
                  projected: Optional[float], horizon: float):
        c = self.config
        if fill >= c.red_pct:
            return BinColor.RED, f"fill {fill:.0f}% >= {c.red_pct:.0f}%"
        if projected is not None and projected >= c.red_pct:
            return BinColor.ORANGE, (
                f"fill {fill:.0f}% at +{rate:.1f}%/h reaches "
                f"{projected:.0f}% within {_span(horizon)}"
            )
        if fill < c.green_pct:
            return BinColor.GREEN, f"fill {fill:.0f}% < {c.green_pct:.0f}%"
        if projected is None:
            return BinColor.YELLOW, (
                f"fill {fill:.0f}%, rate unknown yet (need 2 readings)"
            )
        return BinColor.YELLOW, (
            f"fill {fill:.0f}% at +{rate:.1f}%/h only reaches "
            f"{projected:.0f}% within {_span(horizon)}"
        )

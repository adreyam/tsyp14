"""The two data objects of the chain: the radio message and the request built from it."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class PriorityMessage:
    """What the on-board transmitter broadcasts (one packet).

    upcoming: the signals ahead on the vehicle's route, nearest first, as
              (signal id, index of the signal link the vehicle will use, distance in metres to its stop line).
              The vehicle knows this from its navigation map and route.
    """

    vehicle_id: str
    seq: int
    time: float
    x: float
    y: float
    speed: float
    priority: int
    upcoming: Tuple[Tuple[str, int, float], ...]


@dataclass
class PriorityRequest:
    """What a roadside receiver hands to the signal controller."""

    vehicle_id: str
    ts_id: str
    link_index: int  # index in the signal state string that the vehicle needs to be green
    priority: int
    distance: float
    eta: float
    first_heard: float
    last_heard: float
    passed: bool = False
    passed_time: float = -1.0

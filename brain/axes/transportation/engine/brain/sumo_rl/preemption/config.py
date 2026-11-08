"""Settings of the emergency-vehicle priority system."""

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple


@dataclass
class PreemptionConfig:
    """All the knobs of the transmitter / receiver / controller chain (times in seconds, distances in metres).

    Radio:
        radio_range      the receiver only hears a transmitter closer than this (Euclidean distance).
        packet_loss      probability that one broadcast is lost on its way to one receiver (0 = perfect radio).
        tx_period        the on-board transmitter sends one message every tx_period seconds.
        request_timeout  a request is dropped when its transmitter has not been heard for this long.
    Controller:
        lead_time        safety margin: the signal starts the preemption this many seconds before the vehicle would
                         reach the stop line if the road ahead were empty, plus the yellow time and the time
                         needed to discharge the queue that is waiting in front of the vehicle (see discharge_headway).
        discharge_headway  seconds a queued vehicle needs to leave the stop line (about 2 s = 1800 veh/h).
        clearance_time   the green is kept this long after the last request is gone (vehicle crossed or lost).
        max_hold         safety cap: a preemption never lasts longer than this.
        min_speed        speed used to compute the arrival time of a vehicle that is slow or stopped.
    Who is an emergency vehicle:
        emergency_classes  SUMO vehicle classes that carry a transmitter.
        emergency_vtypes   extra vehicle type ids that carry a transmitter.
        priority_by_vtype  priority level per vehicle type (higher wins when two requests conflict, default 1).
    Where:
        equipped           ids of the intersections with a roadside receiver (None = all signalised intersections).
    """

    radio_range: float = 300.0
    packet_loss: float = 0.0
    tx_period: float = 1.0
    request_timeout: float = 3.0
    lead_time: float = 6.0
    discharge_headway: float = 2.0
    clearance_time: float = 2.0
    max_hold: float = 45.0
    min_speed: float = 3.0
    emergency_classes: Tuple[str, ...] = ("emergency",)
    emergency_vtypes: Tuple[str, ...] = ()
    priority_by_vtype: Dict[str, int] = field(default_factory=dict)
    equipped: Optional[Sequence[str]] = None
    max_upcoming: int = 3
    seed: int = 0

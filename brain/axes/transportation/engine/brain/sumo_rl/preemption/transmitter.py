"""On-board priority transmitter (one per emergency vehicle)."""

from typing import Optional

from .config import PreemptionConfig
from .messages import PriorityMessage


class OnboardTransmitter:
    """Broadcasts an approach / priority message every `tx_period` seconds while the vehicle drives."""

    def __init__(self, vehicle_id: str, sumo, config: PreemptionConfig, priority: int = 1):
        self.vehicle_id = vehicle_id
        self.sumo = sumo
        self.config = config
        self.priority = priority
        self.seq = 0
        self._last_tx = -1e9

    def poll(self, now: float) -> Optional[PriorityMessage]:
        """Return the message to send at time `now`, or None (not time yet, or the vehicle is gone)."""
        if now - self._last_tx < self.config.tx_period - 1e-9:
            return None
        veh = self.sumo.vehicle
        try:
            x, y = veh.getPosition(self.vehicle_id)
            speed = veh.getSpeed(self.vehicle_id)
            next_tls = veh.getNextTLS(self.vehicle_id)
        except Exception:  # the vehicle left the simulation
            return None
        upcoming = tuple((str(t[0]), int(t[1]), float(t[2])) for t in next_tls[: self.config.max_upcoming])
        self._last_tx = now
        self.seq += 1
        return PriorityMessage(self.vehicle_id, self.seq, now, x, y, speed, self.priority, upcoming)

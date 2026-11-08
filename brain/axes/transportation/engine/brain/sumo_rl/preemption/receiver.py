"""Roadside receiver (one per equipped intersection)."""

import math
from typing import Dict, List, Tuple

from .config import PreemptionConfig
from .messages import PriorityMessage, PriorityRequest


class RoadsideReceiver:
    """Listens to the transmitters, keeps one request per vehicle and gives them to the signal controller."""

    def __init__(self, ts_id: str, position: Tuple[float, float], config: PreemptionConfig):
        self.ts_id = ts_id
        self.position = position
        self.config = config
        self.requests: Dict[str, PriorityRequest] = {}
        self.messages_received = 0

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(x - self.position[0], y - self.position[1])

    def receive(self, msg: PriorityMessage, now: float) -> None:
        """Process one packet that reached this receiver (range and packet loss are applied by the channel)."""
        self.messages_received += 1
        entry = next((u for u in msg.upcoming if u[0] == self.ts_id), None)
        req = self.requests.get(msg.vehicle_id)
        if entry is None:
            # the vehicle is still heard but this signal is no longer ahead of it: it has crossed it
            if req is not None and not req.passed:
                req.passed = True
                req.passed_time = now
            return
        _, link_index, distance = entry
        eta = distance / max(msg.speed, self.config.min_speed)
        if req is None or req.passed:
            self.requests[msg.vehicle_id] = PriorityRequest(
                msg.vehicle_id, self.ts_id, link_index, msg.priority, distance, eta, now, now
            )
        else:
            req.link_index, req.priority = link_index, msg.priority
            req.distance, req.eta, req.last_heard = distance, eta, now

    def active_requests(self, now: float) -> List[PriorityRequest]:
        """Requests of vehicles that are approaching and were heard recently."""
        timeout = self.config.request_timeout
        for vid in [v for v, r in self.requests.items() if now - r.last_heard > timeout + 5 or (r.passed and now - r.passed_time > 5)]:
            del self.requests[vid]
        return [r for r in self.requests.values() if not r.passed and now - r.last_heard <= timeout]

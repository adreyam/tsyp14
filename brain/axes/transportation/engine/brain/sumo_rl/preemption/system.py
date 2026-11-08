"""Glue: transmitters in the emergency vehicles, receivers at the intersections, controllers on the signals."""

import random
from typing import Dict, List

from .config import PreemptionConfig
from .controller import PreemptionController
from .receiver import RoadsideReceiver
from .transmitter import OnboardTransmitter


class EmergencyPreemptionSystem:
    """Call :meth:`step` once after every ``simulationStep``."""

    def __init__(self, sumo, traffic_signals: dict, config: PreemptionConfig, fixed_ts: bool = False):
        self.sumo = sumo
        self.config = config
        self.rng = random.Random(config.seed)
        self.events: List[dict] = []
        self.transmitters: Dict[str, OnboardTransmitter] = {}
        self._seen: set = set()
        self.messages_sent = 0
        self.messages_lost = 0
        self.message_log: List[dict] = []  # one entry per packet that reached a receiver's range

        equipped = set(config.equipped) if config.equipped is not None else None
        self.receivers: Dict[str, RoadsideReceiver] = {}
        self.controllers: Dict[str, PreemptionController] = {}
        for ts_id, ts in traffic_signals.items():
            if equipped is not None and ts_id not in equipped:
                continue
            rx = RoadsideReceiver(ts_id, self._signal_position(ts_id), config)
            self.receivers[ts_id] = rx
            self.controllers[ts_id] = PreemptionController(ts, sumo, rx, config, fixed_ts, self.log)
        for vid in sumo.vehicle.getIDList():  # vehicles already driving when the system is created
            self._register(vid)

    def _signal_position(self, ts_id: str):
        try:
            x, y = self.sumo.junction.getPosition(ts_id)
            return (x, y)
        except Exception:
            lane = self.sumo.trafficlight.getControlledLanes(ts_id)[0]
            shape = self.sumo.lane.getShape(lane)
            return tuple(shape[-1])

    def log(self, now: float, ts_id: str, event: str, **info) -> None:
        self.events.append({"time": now, "signal": ts_id, "event": event, **info})

    def _is_emergency(self, vid: str) -> bool:
        veh = self.sumo.vehicle
        try:
            return veh.getVehicleClass(vid) in self.config.emergency_classes or veh.getTypeID(vid) in self.config.emergency_vtypes
        except Exception:
            return False

    def _register(self, vid: str) -> None:
        self._seen.add(vid)
        if self._is_emergency(vid):
            prio = self.config.priority_by_vtype.get(self.sumo.vehicle.getTypeID(vid), 1)
            self.transmitters[vid] = OnboardTransmitter(vid, self.sumo, self.config, prio)

    def step(self) -> None:
        sim = self.sumo.simulation
        now = sim.getTime()
        for vid in sim.getDepartedIDList():
            self._register(vid)
        for vid in sim.getArrivedIDList():
            self.transmitters.pop(vid, None)
            self._seen.discard(vid)
        for vid, tx in list(self.transmitters.items()):
            msg = tx.poll(now)
            if msg is None:
                continue
            for rx in self.receivers.values():
                if rx.distance_to(msg.x, msg.y) > self.config.radio_range:
                    continue
                self.messages_sent += 1
                lost = self.rng.random() < self.config.packet_loss
                self.message_log.append({"time": now, "vehicle": vid, "signal": rx.ts_id, "distance": rx.distance_to(msg.x, msg.y), "lost": lost})
                if lost:
                    self.messages_lost += 1
                    continue
                rx.receive(msg, now)
        for ctrl in self.controllers.values():
            ctrl.step(now)

    @property
    def preempting(self) -> int:
        """Number of signals currently under preemption."""
        return sum(c.mode != "IDLE" for c in self.controllers.values())

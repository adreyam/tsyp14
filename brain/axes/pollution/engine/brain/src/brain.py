"""
City Brain, garbage-bin axis: one step() per cycle.

  Twin readings -> validate -> ask the Guardian -> bin colors -> plan -> dispatch

Zero trust: a reading is used only if the Guardian says "trusted". If the
Guardian is down, answers for the wrong sensor, or says anything else, the
reading is ignored and the bin is kept out of the plan until it is trusted
again. A sensor that stops sending is treated the same way.

Nothing here raises on bad partner data: malformed messages are rejected and
logged, and the layer keeps running.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from pydantic import ValidationError

from .audit_log import AuditLog
from .bin_status import BinColor, BinConfig, BinStatus, BinTracker
from .clients import GuardianClient, TwinClient
from .graph import Graph
from .models import BinReading, TrustStatus, TrustVerdict, TruckState
from .planner import Dispatch, Planner, PlannerConfig


@dataclass(frozen=True)
class BrainConfig:
    bin: BinConfig = field(default_factory=BinConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    reading_interval_s: float = 300.0   # how often the Twin sends each bin
    silence_factor: float = 3.0         # silent for this many intervals = suspect
    # "trucks": plan truck routes (A*, capacity, depot) and send dispatch_truck.
    # "crew":   the Twin empties a bin on request (City Ground Zero's empty_waste_bin);
    #           the Brain only decides WHICH bins and WHEN.
    decision: str = "trucks"
    crew_max_orders_per_cycle: int = 3
    crew_reorder_after_s: float = 600.0  # city seconds before ordering the same bin again


class CityBrain:
    def __init__(self, graph: Graph, guardian: GuardianClient, twin: TwinClient,
                 log: AuditLog, config: BrainConfig = BrainConfig()):
        self.config = config
        self.guardian = guardian
        self.twin = twin
        self.log = log
        self.tracker = BinTracker(config.bin)
        self.planner = Planner(graph, config.planner)
        self.status: Dict[str, BinStatus] = {}      # last TRUSTED status per bin
        self.trucks: Dict[str, TruckState] = {}
        self.untrusted: Dict[str, str] = {}         # sensor id -> why
        self._last_seen: Dict[str, datetime] = {}
        self._guardian_down = False
        self.ordered: Dict[str, datetime] = {}       # crew mode: bin -> when it was ordered

    # ---- one cycle ---------------------------------------------------------
    def step(self, bin_messages: List[dict], truck_messages: List[dict],
             now: datetime) -> List[Dispatch]:
        for raw in truck_messages:
            self._ingest_truck(raw, now)
        for raw in bin_messages:
            self._ingest_bin(raw, now)
        self._check_silence(now)

        if self.config.decision == "crew":
            return self._crew_orders(now)
        dispatches = self.planner.replan(self.status, self.trucks, now,
                                         frozenset(self.untrusted))
        for d in dispatches:
            self._send(d, now)
        return dispatches

    # ---- inputs ------------------------------------------------------------
    def _ingest_truck(self, raw: dict, now: datetime) -> None:
        if not isinstance(raw, dict):
            self._reject(now, "get_truck_state", raw, "message is not a JSON object")
            return
        try:
            truck = TruckState(**raw)
        except ValidationError as e:
            self._reject(now, "get_truck_state", raw, e)
            return
        previous = self.trucks.get(truck.truck_id)
        self.trucks[truck.truck_id] = truck
        if previous is None or previous.status != truck.status:
            self.log.log(now, "twin", "truck_state", truck_id=truck.truck_id,
                         status=truck.status, load_kg=truck.current_load_kg,
                         lat=truck.location.lat, lon=truck.location.lon)

    def _ingest_bin(self, raw: dict, now: datetime) -> None:
        if not isinstance(raw, dict):
            self._reject(now, "get_sensor_reading", raw, "message is not a JSON object")
            return
        try:
            reading = BinReading(**raw)
        except ValidationError as e:
            self._reject(now, "get_sensor_reading", raw, e)
            return

        sid = reading.sensor_id
        self._last_seen[sid] = now
        self.log.log(now, "twin", "sensor_reading", sensor_id=sid,
                     weight_kg=reading.weight_kg, capacity_kg=reading.capacity_kg,
                     fill_pct=round(reading.fill_pct, 1), zone=reading.zone)

        verdict = self._ask_guardian(reading, now)
        if verdict is None or verdict.status != TrustStatus.TRUSTED:
            why = "no valid verdict" if verdict is None else (
                verdict.reason or verdict.status.value)
            self.untrusted[sid] = why
            self.log.log(now, "city_brain", "reading_blocked", sensor_id=sid, reason=why)
            return

        self.untrusted.pop(sid, None)
        before = self.status.get(sid)
        status = self.tracker.update(reading)
        self.status[sid] = status
        if status.just_emptied:
            self.ordered.pop(sid, None)              # the order was carried out
        if before is None or before.color != status.color or status.just_emptied:
            self.log.log(now, "city_brain", "bin_status", sensor_id=sid,
                         color=status.color, fill_pct=status.fill_pct,
                         projected_pct=status.projected_pct, reason=status.reason,
                         emptied=status.just_emptied)

    def _ask_guardian(self, reading: BinReading, now: datetime
                      ) -> Optional[TrustVerdict]:
        try:
            verdict = self.guardian.get_trust_verdict(reading)
        except Exception as e:                       # the layer must survive a partner
            if not self._guardian_down:              # log the outage once, not per call
                self._guardian_down = True
                self.log.log(now, "city_brain", "guardian_unavailable", error=str(e))
            return None
        if self._guardian_down:
            self._guardian_down = False
            self.log.log(now, "city_brain", "guardian_recovered")
        if verdict.sensor_id != reading.sensor_id:
            self.log.log(now, "city_brain", "invalid_verdict", sensor_id=reading.sensor_id,
                         got=verdict.sensor_id)
            return None
        self.log.log(now, "guardian", "trust_verdict", sensor_id=reading.sensor_id,
                     status=verdict.status, confidence=verdict.confidence,
                     reason=verdict.reason)
        return verdict

    def _check_silence(self, now: datetime) -> None:
        limit = timedelta(seconds=self.config.reading_interval_s
                          * self.config.silence_factor)
        for sid, seen in self._last_seen.items():
            if now - seen > limit and sid not in self.untrusted:
                self.untrusted[sid] = "silent"
                self.log.log(now, "city_brain", "sensor_silent", sensor_id=sid,
                             last_seen=seen)

    def _reject(self, now: datetime, tool: str, raw, error) -> None:
        sid = (raw.get("sensor_id") or raw.get("truck_id")) if isinstance(raw, dict) else None
        if isinstance(error, ValidationError):       # name the faulty fields
            error = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}"
                              for x in error.errors()[:4])
        self.log.log(now, "city_brain", "rejected_message", tool=tool, id=sid,
                     error=str(error))

    # ---- outputs -----------------------------------------------------------
    def _crew_orders(self, now: datetime) -> List[str]:
        """Crew mode: order the most urgent trusted bins emptied, red before orange."""
        wait = timedelta(seconds=self.config.crew_reorder_after_s)
        rank = {BinColor.RED: 0, BinColor.ORANGE: 1}
        due = [s for sid, s in self.status.items()
               if s.color in rank and sid not in self.untrusted
               and not (sid in self.ordered and now - self.ordered[sid] < wait)]
        due.sort(key=lambda s: (rank[s.color], -(s.projected_pct or s.fill_pct), s.sensor_id))
        sent: List[str] = []
        for s in due[: self.config.crew_max_orders_per_cycle]:
            message = {"tool": "empty_waste_bin", "arguments": {"id": s.sensor_id}}
            try:
                self.twin.send_collection(s.sensor_id)
            except Exception as e:
                self.log.log(now, "city_brain", "twin_unavailable", truck_id="crew",
                             stop=s.sensor_id, error=str(e))
                break                                 # retry next cycle
            self.ordered[s.sensor_id] = now
            sent.append(s.sensor_id)
            self.log.log(now, "city_brain", "dispatch", truck_id="crew", stop=s.sensor_id,
                         kind="bin", color=s.color, next_after=[], reason=s.reason,
                         message=message)
        return sent

    def _send(self, d: Dispatch, now: datetime) -> None:
        message = d.to_message()
        try:
            self.twin.send_dispatch(message)
        except Exception as e:
            # The truck never got the order: forget the commitment, retry next cycle.
            self.planner.plans[d.truck_id].committed = None
            self.log.log(now, "city_brain", "twin_unavailable", truck_id=d.truck_id,
                         error=str(e))
            return
        provisional = [s.bin_id for s in self.planner.plans[d.truck_id].provisional]
        self.log.log(now, "city_brain", "dispatch", truck_id=d.truck_id,
                     stop=d.stop.bin_id, kind=d.stop.kind,
                     color=d.stop.color, next_after=provisional, message=message)

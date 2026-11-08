"""The Brain's side of the interface contract: validates what partners send, applies it, decides, and acts.
Everything a partner can send is declared in INTERFACE.md; anything else is refused with a clear message."""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select

from app.database import SessionLocal, state_lock
from app.models import Space
from app.services import space_flow
from app.services.common import FlowError, now

from .outbound import Outbound
from .timeline import Timeline, iso_now
from .trust import BAD, TrustRegistry

READING_TYPES = ("parking.space.occupancy", "parking.lot.count", "emergency.call")
VERDICTS = ("trusted", "suspect", "untrusted", "quarantined", "released")
DEVICE_RE = r"^[A-Za-z0-9:_\-./]+$"


class Reading(BaseModel):
    device_id: str = Field(min_length=3, max_length=80, pattern=DEVICE_RE)
    type: Literal["parking.space.occupancy", "parking.lot.count", "emergency.call"]
    ts: datetime
    seq: Optional[int] = Field(default=None, ge=0)
    data: dict


class OccupancyData(BaseModel):
    lot_id: str = Field(pattern=r"^P\d{2}$")
    space: str = Field(min_length=1, max_length=6)
    occupied: bool


class LotCountData(BaseModel):
    lot_id: str = Field(pattern=r"^P\d{2}$")
    occupied_total: int = Field(ge=0, le=500)


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class EmergencyData(BaseModel):
    call_id: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9\-]+$")
    kind: Literal["ambulance", "fire", "police"]
    location: Location
    severity: int = Field(default=2, ge=1, le=3)


class Verdict(BaseModel):
    device_id: str = Field(min_length=3, max_length=80, pattern=DEVICE_RE)
    verdict: Literal["trusted", "suspect", "untrusted", "quarantined", "released"]
    reason: str = Field(default="", max_length=300)
    ts: Optional[datetime] = None
    ttl_s: Optional[int] = Field(default=None, ge=1, le=86400)


DATA_MODELS = {"parking.space.occupancy": OccupancyData, "parking.lot.count": LotCountData, "emergency.call": EmergencyData}


class BridgeError(Exception):
    def __init__(self, status, error, message, details=None):
        super().__init__(message)
        self.status, self.error, self.message, self.details = status, error, message, details or []

    def body(self):
        return {"error": self.error, "message": self.message, "details": self.details, "hint": "see INTERFACE.md"}


def explain(ex: ValidationError, prefix=""):
    out = []
    for e in ex.errors():
        field = ".".join(str(p) for p in e["loc"])
        out.append({"field": (prefix + field).strip("."), "message": e["msg"]})
    return out


class Bridge:
    def __init__(self, cfg, engine):
        self.cfg, self.engine = cfg, engine
        self.tl = Timeline(cfg["brain"]["logs_dir"], clock_fn=lambda: engine.clock()[2] if engine.ready.is_set() else "")
        self.trust = TrustRegistry(cfg["trust"]["unknown_device_policy"], cfg["trust"]["verdict_ttl_s"])
        self.out = Outbound(cfg, self.tl)
        self.counters = defaultdict(int)
        self.rate = defaultdict(lambda: deque(maxlen=400))
        self.last_logged = {}
        self.suppressed = defaultdict(int)
        self.last_limit_note = {}
        self.lot_counts = {}
        self.dispatched = {}             # vehicle id -> call id
        self.last_priority = {}
        self.alert_seen = 0
        engine.bridge = self
        threading.Thread(target=self._alert_loop, daemon=True, name="bridge-alerts").start()

    def _alert_loop(self):
        """Security events of the parking system (duplicate RFID, unknown tag...) go to the timeline and are reported to the Guardian."""
        from app.models import Alert
        while True:
            time.sleep(1.5)
            try:
                with state_lock, SessionLocal() as db:
                    rows = db.scalars(select(Alert).where(Alert.id > self.alert_seen).order_by(Alert.id)).all()
                    items = [(a.id, a.type, a.severity, a.lot_id, a.lane_id, a.message) for a in rows]
                for aid, typ, sev, lot, lane, msg in items:
                    self.alert_seen = aid
                    self.tl.add("brain", "alert", f"{typ} at {lane or lot}: {msg[:140]}", ref=lane or lot)
                    self.out.guardian("report", {"report_id": self.out.next_id("rep"), "ts": iso_now(), "device_id": f"lane:{lane}" if lane else f"lot:{lot}",
                                                 "issue": f"parking_alert:{typ}", "detail": msg[:200]})
            except Exception:   # noqa: BLE001
                pass

    def reset_runtime(self):
        """Forget verdicts and rate windows (used between test scenarios)."""
        self.trust.v.clear()
        self.rate.clear()
        self.lot_counts.clear()
        self.dispatched.clear()
        self.counters.clear()

    # ------------------------------------------------------------------ helpers
    def decision(self, action, target, reason, based_on=(), ref=None):
        d = {"decision_id": self.out.next_id("dec"), "ts": iso_now(), "action": action, "target": target, "reason": reason, "based_on": list(based_on)}
        self.out.guardian("decision", d, ref=ref)
        return d

    def actuator_command(self, actuator_id, action, params=None, reason="", manual=False, ref=None):
        """Every action of the Brain on a device goes through here: it is checked against the Guardian's verdict first."""
        status = self.trust.status(actuator_id)
        if not self.trust.usable(actuator_id) and not manual:
            why = self.trust.reason(actuator_id) or status
            self.counters["withheld"] += 1
            self.tl.add("brain", "command_withheld", f"{action} on {actuator_id} NOT sent: device is {status} ({why})", ref=ref)
            self.decision("withhold_command", actuator_id, f"device {status}: {why}", ref=ref)
            return False
        if status in BAD and manual:
            self.tl.add("brain", "manual_override", f"administrator overrides the {status} state of {actuator_id}: {action}", ref=ref)
        body = {"command_id": self.out.next_id("cmd"), "ts": iso_now(), "action": action, "params": params or {}, "reason": reason, "issued_by": "city-brain"}
        self.tl.add("actuator", "command", f"{action} {actuator_id} {params or ''}".strip() + (f" - {reason}" if reason else ""), ref=ref)
        if not self.out.twin_command(actuator_id, body, ref=ref):
            self.tl.add("actuator", "command_local", f"{actuator_id} {action} executed in the Brain's own simulation (Twin link disabled)", ref=ref)
        self.decision(action, actuator_id, reason or "brain decision", ref=ref)
        self.counters["commands"] += 1
        return True

    # ------------------------------------------------------------------ Twin -> Brain
    def ingest(self, raw_items):
        if not isinstance(raw_items, list):
            raw_items = [raw_items]
        if len(raw_items) == 0:
            raise BridgeError(422, "empty_batch", "Send at least one reading.")
        if len(raw_items) > self.cfg["limits"]["max_batch"]:
            raise BridgeError(413, "batch_too_large", f"At most {self.cfg['limits']['max_batch']} readings per request.")
        results = []
        for i, raw in enumerate(raw_items):
            try:
                results.append(self._ingest_one(raw))
            except BridgeError as e:
                results.append({"index": i, "accepted": False, "error": e.error, "message": e.message, "details": e.details})
                self.counters["rejected"] += 1
        if len(results) == 1 and not results[0].get("accepted") and results[0].get("error") not in (None, "device_untrusted", "device_unknown"):
            r = results[0]
            raise BridgeError(422 if r["error"] != "rate_limited" else 429, r["error"], r["message"], r.get("details"))
        return {"results": results}

    def _rate_ok(self, device):
        t = time.time()
        w = self.rate[device]
        w.append(t)
        n = sum(1 for x in w if t - x <= 1.0)
        return n <= self.cfg["limits"]["max_readings_per_s_per_device"]

    def _ingest_one(self, raw):
        if not isinstance(raw, dict):
            raise BridgeError(422, "invalid_reading", "A reading must be a JSON object.")
        try:
            rd = Reading.model_validate(raw)
        except ValidationError as ex:
            types = raw.get("type")
            msg = f"Unsupported reading type {types!r}. Supported: {', '.join(READING_TYPES)}." if types not in READING_TYPES and types is not None else "The reading does not follow the contract."
            raise BridgeError(422, "invalid_reading", msg, explain(ex))
        try:
            data = DATA_MODELS[rd.type].model_validate(rd.data)
        except ValidationError as ex:
            raise BridgeError(422, "invalid_data", f"'data' of a {rd.type} reading is invalid.", explain(ex, "data."))
        if rd.ts.tzinfo is not None:
            age = (datetime.now(timezone.utc) - rd.ts).total_seconds()
            if age > 3600 or age < -300:
                raise BridgeError(422, "stale_timestamp", f"ts is {int(age)} s away from the Brain's clock (allowed: -300 s .. +3600 s). Check the clock of the sender.")
        dev = rd.device_id
        self.counters["readings"] += 1
        if not self._rate_ok(dev):
            self.counters["rate_limited"] += 1
            t = time.time()
            if t - self.last_limit_note.get(dev, 0) > 5:
                self.last_limit_note[dev] = t
                self.tl.add("brain", "rate_limited", f"{dev} sends more than {self.cfg['limits']['max_readings_per_s_per_device']} readings/s: extra readings dropped")
                self.out.guardian("report", {"report_id": self.out.next_id("rep"), "ts": iso_now(), "device_id": dev, "issue": "flood", "detail": "rate limit exceeded at the Brain"})
            raise BridgeError(429, "rate_limited", f"Too many readings from {dev}. Slow down.")
        status = self.trust.status(dev)
        self._log_reading(rd, status)
        if status in BAD:
            self.counters["dropped_untrusted"] += 1
            return {"accepted": False, "error": "device_untrusted", "message": f"{dev} is {status} (Guardian): reading ignored.", "device_status": status}
        if status == "unknown" and self.trust.policy == "reject":
            return {"accepted": False, "error": "device_unknown", "message": f"{dev} has not been rated by the Guardian yet.", "device_status": status}
        if rd.type == "parking.space.occupancy":
            return self._occupancy(rd, data)
        if rd.type == "parking.lot.count":
            self.lot_counts[data.lot_id] = (data.occupied_total, time.time())
            return {"accepted": True, "applied": "recorded"}
        return self._emergency(rd, data)

    def _log_reading(self, rd, status):
        t = time.time()
        key = (rd.device_id, rd.type)
        if t - self.last_logged.get(key, 0) < 1.0:
            self.suppressed[key] += 1
            return
        n = self.suppressed.pop(key, 0)
        self.last_logged[key] = t
        d = rd.data
        brief = {"parking.space.occupancy": lambda: f"space {d.get('lot_id')}-{d.get('space')} occupied={d.get('occupied')}",
                 "parking.lot.count": lambda: f"lot {d.get('lot_id')} counter={d.get('occupied_total')}",
                 "emergency.call": lambda: f"emergency call {d.get('call_id')} ({d.get('kind')})"}[rd.type]()
        self.tl.add("twin", "reading", f"{rd.device_id}: {brief}" + (f" (+{n} similar)" if n else "") + (f" [device {status}]" if status != "unknown" else ""), ref=d.get("call_id"))

    # ---- parking sensor
    def physical_truth(self, lot_id, code):
        return any(c.lot_id == lot_id and c.space == code and c.phase in ("PARKED", "TO_EXIT", "REPARKING") for c in self.engine.parking.cars.values())

    def _apply_occupancy(self, lot_id, code, occupied):
        def fn():
            with state_lock, SessionLocal() as db:
                sp = db.scalar(select(Space).where(Space.lot_id == lot_id, Space.code == code))
                if sp is None:
                    raise BridgeError(422, "unknown_space", f"Lot {lot_id} has no space {code}.")
                was = sp.sensor_occupied
                space_flow.occupancy_changed(db, sp, occupied)
                db.commit()
                return was
        return self.engine.call(fn)

    def _occupancy(self, rd, d):
        was = self._apply_occupancy(d.lot_id, d.space, d.occupied)
        self.tl.add("brain", "reading_applied", f"space {d.lot_id}-{d.space} sensor {'occupied' if d.occupied else 'vacant'} (was {'occupied' if was else 'vacant'}); device {rd.device_id} currently {self.trust.status(rd.device_id)}")
        return {"accepted": True, "applied": "space_sensor"}

    # ---- emergency call
    def _emergency(self, rd, d):
        loc = d.location
        vid = self.engine.call(lambda: self.engine.dispatch_emergency(d.kind, loc.lat, loc.lon, d.call_id))
        if vid is None:
            raise BridgeError(422, "unreachable_location", f"No street within reach of lat={loc.lat}, lon={loc.lon}: choose a point inside the city.")
        self.dispatched[vid] = d.call_id
        self.counters["dispatched"] += 1
        self.tl.add("brain", "decision", f"dispatch {d.kind} {vid} to emergency call {d.call_id}; signals on its route will give it priority", ref=d.call_id)
        self.actuator_command(f"dispatch:{d.kind}", "dispatch", {"call_id": d.call_id, "vehicle_id": vid, "lat": loc.lat, "lon": loc.lon}, reason=f"emergency call {d.call_id}", ref=d.call_id)
        return {"accepted": True, "applied": "dispatched", "vehicle_id": vid}

    # ------------------------------------------------------------------ Guardian -> Brain
    def verdict(self, raw):
        try:
            v = Verdict.model_validate(raw)
        except ValidationError as ex:
            raise BridgeError(422, "invalid_verdict", "The verdict does not follow the contract (device_id, verdict in " + ", ".join(VERDICTS) + ").", explain(ex))
        prev, cur = self.trust.set(v.device_id, v.verdict, v.reason, ttl_s=v.ttl_s)
        self.counters["verdicts"] += 1
        self.tl.add("guardian", "verdict", f"{v.device_id} -> {v.verdict.upper()}" + (f": {v.reason}" if v.reason else ""), ref=v.device_id)
        acted = []
        if v.verdict in BAD:
            if self.cfg["trust"]["rollback_on_untrusted"] and v.device_id.startswith("sensor:"):
                acted += self._rollback(v)
            if v.device_id.startswith("actuator:") or v.device_id.startswith("barrier:") or v.device_id.startswith("signal:") or v.device_id.startswith("dispatch:"):
                self.tl.add("brain", "decision", f"{v.device_id} is {v.verdict}: the Brain will not send it commands; the administrator can still override manually", ref=v.device_id)
                acted.append("actuator_isolated")
            elif not acted:
                self.tl.add("brain", "decision", f"readings of {v.device_id} are now ignored ({v.verdict}: {v.reason or 'no reason given'})", ref=v.device_id)
        elif v.verdict in ("trusted", "released") and prev in BAD:
            self.tl.add("brain", "decision", f"{v.device_id} is trusted again: its readings and commands are accepted", ref=v.device_id)
        return {"accepted": True, "previous": prev, "current": cur, "brain_actions": acted}

    def _rollback(self, v):
        # device ids of parking sensors:  sensor:<lot>:<space>   e.g. sensor:P01:A3
        parts = v.device_id.split(":")
        if len(parts) != 3 or parts[2] == "counter":
            return []
        lot, code = parts[1], parts[2]
        truth = self.physical_truth(lot, code)
        try:
            was = self._apply_occupancy(lot, code, truth)
        except BridgeError:
            return []
        if was != truth:
            self.tl.add("brain", "decision", f"ISOLATED {v.device_id} ({v.verdict}: {v.reason or 'no reason'}); rolled space {lot}-{code} back to its last trusted state "
                        f"({'occupied' if truth else 'vacant'}); the space stays {'unavailable' if truth else 'assignable'}", ref=v.device_id)
            self.decision("ignore_sensor", v.device_id, f"{v.verdict}: {v.reason}", based_on=[v.device_id], ref=v.device_id)
            return ["rolled_back"]
        self.tl.add("brain", "decision", f"ISOLATED {v.device_id} ({v.verdict}); its state already matched the last trusted one, nothing to roll back", ref=v.device_id)
        return ["isolated"]

    # ------------------------------------------------------------------ hooks called by the simulation
    def on_priority(self, signal, vehicle):
        t = time.time()
        key = (signal, vehicle)
        if t - self.last_priority.get(key, 0) < 8:
            return
        self.last_priority[key] = t
        call = next((c for v, c in self.dispatched.items() if v == vehicle), None)
        if call is not None:
            self.tl.add("brain", "decision", f"priority: signal {signal} turns green for {vehicle}", ref=call)
        if call is not None:
            self.actuator_command(f"signal:{signal}", "priority_green", {"vehicle_id": vehicle}, reason="emergency vehicle approaching", ref=call)
        else:
            self._mirror_priority(signal, vehicle)

    def _mirror_priority(self, signal, vehicle):
        # background emergency runs: the command is still sent to the Twin but not logged one by one
        body = {"command_id": self.out.next_id("cmd"), "ts": iso_now(), "action": "priority_green", "params": {"vehicle_id": vehicle},
                "reason": "emergency vehicle approaching", "issued_by": "city-brain"}
        if self.trust.usable(f"signal:{signal}"):
            self.out.twin_command(f"signal:{signal}", body, quiet=True)
            self.counters["commands"] += 1

    def on_arrived(self, vid):
        call = self.dispatched.pop(vid, None)
        if call is not None:
            self.tl.add("brain", "decision", f"{vid} reached the scene of emergency call {call}", ref=call)
            self.out.guardian("report", {"report_id": self.out.next_id("rep"), "ts": iso_now(), "device_id": f"dispatch:{vid}", "issue": "call_completed", "detail": call})

    def barrier_gate(self, lot_id, lane_id, manual):
        """Called when the parking backend wants a barrier to open. False = the barrier must stay closed."""
        return self.actuator_command(f"barrier:{lane_id}", "open", {"lot_id": lot_id}, reason="manual override by the administrator" if manual else "ticket / exit authorised", manual=manual)

    def withheld_notice(self, db, lot_id, lane_id):
        """Written in the caller's own session (the parking poll already holds the database: a second session would lock it)."""
        from citybrain import models as cm
        db.add(cm.AdminNotice(lot_id=lot_id, driver_id=None, kind="ACTUATOR_QUARANTINED",
                              text=f"The Guardian quarantined barrier {lane_id}: the Brain did not open it. Check the car and use 'Open barrier' (manual override) if it is safe.",
                              created_at=now()))

    def status(self):
        return {"counters": dict(self.counters), "outbound": dict(self.out.stats, queued=self.out.q.qsize()), "trust": self.trust.snapshot(),
                "twin": {"enabled": self.cfg["twin"]["enabled"], "url": self.cfg["twin"]["url"]},
                "guardian": {"enabled": self.cfg["guardian"]["enabled"], "url": self.cfg["guardian"]["url"]},
                "scenario": self.tl.scenario["id"] if self.tl.scenario else None, "lot_counts": {k: v[0] for k, v in self.lot_counts.items()}}

"""Cars with a driver, and the parking lots they use. Everything here runs in the backend.

The backend plays the role of every lot's ESP32 gateway: when a car reaches a barrier it sends the same events the
hardware would send (vehicle detected, RFID read, space occupied, ...) to the existing parking flows
(app.services.entrance_flow / space_flow / exit_flow) and obeys the commands they return (open barrier, dispense a card,
space LEDs). Nothing about a decision (ticket, space, refusal, misplaced alert, exit) is made by the web page.
"""
import json
import math
import random
from collections import deque
from datetime import datetime

from sqlalchemy import select

from app.database import SessionLocal, state_lock
from app.enums import CardStatus, LaneState, SessionStatus, SpaceStatus
from app.models import Command, Lane, LotProfile, ParkingSession, Space, TempCard
from app.services import entrance_flow, exit_flow, space_flow
from app.services import recommend as rsvc
from app.services.common import FlowError, now

from . import models as cm
from . import ranking7

LOT_SPEED = 4.0          # m/s inside a lot
BARRIER_OPEN_S = 10
MAX_ACTIVE_DRIVERS = 7


class Car:
    def __init__(self, d, places):
        self.id, self.name, self.rfid, self.plate = d["id"], d["name"], d["rfid"], d["plate"]
        self.married, self.kids, self.shares_tag_with = d["married"], d["kids"], d["shares_tag_with"]
        self.places = places                      # the driver's own frequent places
        self.autopilot = True
        self.phase = "IDLE"
        self.sub = "At home"
        self.next_trip_at = 0.0
        self.reset_trip()
        self.x = self.y = None
        self.angle = 0.0
        self.log = deque(maxlen=14)

    def reset_trip(self):
        self.dest = None
        self.lot_id = None
        self.exclude = []
        self.offers = None
        self.reservation_id = None
        self.session_id = None
        self.ticket = None
        self.assigned_space = None
        self.space = None                          # space the car is physically in
        self.held_card = None
        self.no_tag = False
        self.park_choice = None                    # None / "assigned" / space code
        self.path, self.path_s, self.path_len = [], 0.0, 0.0
        self.gate = {}
        self.stop_tries = 0
        self.t_spawn = 0.0
        self.leave_at = None
        self.leave_requested = False
        self.repark_requested = False
        self.in_sumo = False
        self.requested = False
        self.ticket_requested = False
        self.session_status = None
        self.lane_state = None
        self.lane_message = ""
        self.t_phase = 0.0
        self.misspark = False
        self.stuck_since = None
        self.hold = False
        self.tag_pending = False
        self.target_space = None
        self.stop_set = False
        self.force_misspark = False
        self.lot_id_last = None


def _polyline_len(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


class ParkingSim:
    def __init__(self, engine, world, rng):
        self.eng, self.world, self.rng = engine, world, rng
        self.lots = {l["id"]: l for l in world["lots"]}
        self.places = {p["id"]: p for p in world["places"]}
        by_driver = {}
        for p in world["places"]:
            by_driver.setdefault(p["driver"], []).append(p)
        self.cars = {d["id"]: Car(d, by_driver.get(d["id"], [])) for d in world["drivers"]}
        for i, c in enumerate(self.cars.values()):
            c.next_trip_at = 8 + i * 9 + rng.random() * 10
        self.rt = {lid: {"barrier_in": -1.0, "barrier_out": -1.0, "led": {}, "dispensed": {}, "gate_in": None, "gate_out": None,
                         "parked": {}} for lid in self.lots}
        self._last_snapshot = -1e9
        self.scripted = []

    # ------------------------------------------------------------------ helpers
    @property
    def t(self):
        return self.eng.t

    def note(self, car, text):
        car.log.appendleft({"t": round(self.t), "text": text})

    def lot_space(self, lot_id, code):
        return next((s for s in self.lots[lot_id]["spaces"] if s["code"] == code), None)

    def _set_path(self, car, pts):
        car.path, car.path_s, car.path_len = [tuple(p) for p in pts], 0.0, _polyline_len(pts)
        if car.path:
            car.x, car.y = car.path[0]

    def _advance(self, car, dt):
        """Move along the lot path. Returns True when the end is reached."""
        car.path_s = min(car.path_len, car.path_s + LOT_SPEED * dt)
        if car.path_s >= car.path_len - 1e-6:
            if len(car.path) > 1:
                (ax, ay), (bx, by) = car.path[-2], car.path[-1]
                if (bx, by) != (ax, ay):
                    car.angle = math.degrees(math.atan2(bx - ax, by - ay))
            car.x, car.y = car.path[-1]
            return True
        remaining = car.path_s
        for a, b in zip(car.path, car.path[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if remaining <= seg or seg == 0:
                f = 0 if seg == 0 else remaining / seg
                car.x, car.y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                if seg:
                    car.angle = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))   # SUMO convention: 0 = north, clockwise
                return False
            remaining -= seg
        car.x, car.y = car.path[-1]
        return car.path_s >= car.path_len - 1e-6

    def _poll_commands(self, db, lot_id):
        """Gateway side: pick up the commands the flows queued for this lot and act on them."""
        rt = self.rt[lot_id]
        for c in db.scalars(select(Command).where(Command.gateway_id == f"GW-{lot_id}", Command.delivered_at.is_(None)).order_by(Command.id)):
            c.delivered_at = now()
            p = json.loads(c.payload or "{}")
            if c.type == "OPEN_BARRIER":
                key = "barrier_in" if p.get("lane_id", "").endswith("ENT1") else "barrier_out"
                br = getattr(self.eng, "bridge", None)
                if br is not None and not br.barrier_gate(lot_id, p.get("lane_id", ""), bool(p.get("manual_override"))):
                    br.withheld_notice(db, lot_id, p.get("lane_id", ""))     # the Guardian quarantined this barrier: it stays closed
                    continue
                rt[key] = self.t + BARRIER_OPEN_S
            elif c.type == "DISPENSE_CARD":
                card = db.scalar(select(TempCard).where(TempCard.lot_id == lot_id, TempCard.status == CardStatus.AVAILABLE).order_by(TempCard.uid))
                rt["dispensed"][p.get("lane_id")] = card.uid if card else None
            elif c.type == "SPACE_INDICATOR":
                rt["led"][p["space_code"]] = p["mode"]

    def _lane(self, db, lot_id, kind):
        return db.get(Lane, f"{lot_id}-{'ENT1' if kind == 'in' else 'EXIT1'}")

    # ------------------------------------------------------------------ trips
    def active_count(self):
        return sum(1 for c in self.cars.values() if c.phase != "IDLE")

    def choose_destination(self, car):
        dow, minutes, _ = self.eng.clock()
        active = ranking7.active_places(car.places, dow, minutes)
        pool = active or car.places
        return self.rng.choices(pool, weights=[p["weight"] for p in pool])[0]

    def start_trip(self, car, place_id=None, autopilot=True, no_tag=False):
        if car.phase != "IDLE":
            raise FlowError(f"{car.id} is already on a trip ({car.phase}).")
        place = self.places[place_id] if place_id else self.choose_destination(car)
        if place["driver"] != car.id:
            raise FlowError("That place belongs to another driver.")
        car.reset_trip()
        car.dest, car.autopilot, car.no_tag = place, autopilot, no_tag
        origin = self.rng.choice(self.world["border_in"])
        if not self.eng.add_driver(car, origin, place["edge"]):
            raise FlowError("Could not insert the car (no route).")
        car.phase, car.sub, car.t_spawn, car.in_sumo = "DRIVING", f"Driving to {place['label']}", self.t, True
        self.note(car, f"Trip started to {place['label']} ({'autopilot' if autopilot else 'manual'})")
        self.eng.event(f"{car.id} starts a trip to {place['label']}")

    # ------------------------------------------------------------------ parking offers / choice
    def offers(self, car, exclude=None):
        """Top-3 lots for this driver, computed here with the 7 criteria (the 7th = the driver's routine)."""
        if car.dest is None:
            raise FlowError("Choose a destination first.")
        exclude = list(exclude if exclude is not None else car.exclude)
        origin_edge = self.eng.current_edge(car.id) or self.world["border_in"][0]
        lat, lon = self._latlon_of_edge(origin_edge, car)
        etas = {lid: self.eng.eta_min(origin_edge, l["access_edge"]) for lid, l in self.lots.items()}
        dow, minutes, clock_txt = self.eng.clock()
        with state_lock, SessionLocal() as db:
            cands, excluded = rsvc.build_candidates(db, (lat, lon), (car.dest["lat"], car.dest["lon"]), exclude=exclude,
                                                    eta_fn=lambda lid: etas.get(lid))
            res = ranking7.rank(cands, car.places, dow, minutes, top_n=3)
            db.commit()
        res["excluded"] = excluded
        res["clock"] = clock_txt
        res["destination"] = {"id": car.dest["id"], "label": car.dest["label"]}
        car.offers = res
        return res

    def _latlon_of_edge(self, edge, car):
        x, y = (car.x, car.y) if car.x is not None and car.in_sumo else (None, None)
        if x is None:
            from sumolib.net import readNet  # noqa: F401
            x, y = self.eng.edge_midpoint(edge)
        from .worldgen import to_latlon
        return to_latlon(x, y)

    def choose(self, car, lot_id):
        if car.phase not in ("DRIVING", "TO_LOT", "AT_GATE") or lot_id not in self.lots:
            raise FlowError("The car cannot choose a lot right now.")
        if car.phase == "AT_GATE":
            self._back_to_road(car, self.lots[lot_id]["access_edge"])
        if car.reservation_id:
            self._cancel_reservation(car)
        origin_edge = self.eng.current_edge(car.id) or self.world["border_in"][0]
        eta = self.eng.eta_min(origin_edge, self.lots[lot_id]["access_edge"]) or 3.0
        with state_lock, SessionLocal() as db:
            if rsvc.lot_state(db, lot_id)["available"] - rsvc.en_route(db, lot_id) <= 0:
                raise FlowError("That lot is full (counting drivers already heading there). Pick another one.")
            res = rsvc.create_reservation(db, lot_id, eta)
            db.commit()
            car.reservation_id = res.id
        car.lot_id, car.phase, car.stop_tries, car.requested = lot_id, "TO_LOT", 0, True
        car.sub = f"Heading to {lot_id} {self.lots[lot_id]['name']}"
        self.eng.route_to_lot(car, self.lots[lot_id])
        self.note(car, f"Chose {lot_id} {self.lots[lot_id]['name']} (ETA {eta:.1f} min)")
        self.eng.event(f"{car.id} chose {lot_id}")

    def _cancel_reservation(self, car):
        from app.models import Reservation
        with state_lock, SessionLocal() as db:
            r = db.get(Reservation, car.reservation_id)
            if r and r.status == "ACTIVE":
                r.status = "CANCELLED"
            db.commit()
        car.reservation_id = None

    def auto_choose(self, car, delay_ok=True):
        try:
            res = self.offers(car)
        except Exception as e:   # noqa: BLE001
            self.note(car, f"No offer: {e}")
            return
        if res["ranking"]:
            self.choose(car, res["ranking"][0]["lot_id"])
        else:
            self.note(car, "No lot available right now")
            car.sub = "No lot available: waiting"

    # ------------------------------------------------------------------ driver actions from the dash
    def request_ticket(self, car):
        if car.phase != "AT_GATE":
            raise FlowError("The car is not at an entrance.")
        with state_lock, SessionLocal() as db:
            lane = self._lane(db, car.lot_id, "in")
            entrance_flow.request_ticket(db, lane)
            self._poll_commands(db, car.lot_id)
            db.commit()
        car.ticket_requested = True
        car.gate["step"], car.gate["t"] = "decide", self.t
        self.note(car, "Pressed 'Request a ticket'")

    def set_park_choice(self, car, choice):
        if car.phase not in ("CHOOSE_SPACE", "IN_LOT_IN", "TO_SPACE", "PARKED"):
            raise FlowError("The car is not inside a lot.")
        car.park_choice = choice
        self.note(car, f"Parking choice: {'assigned space' if choice == 'assigned' else 'space ' + choice}")

    def request_leave(self, car):
        if car.phase != "PARKED":
            raise FlowError("The car is not parked.")
        car.leave_requested = True

    def request_repark(self, car):
        if car.phase != "PARKED" or car.session_status != SessionStatus.MISPLACED:
            raise FlowError("The car is not in a wrong space.")
        car.repark_requested = True

    def leave_without_parking(self, car):
        if car.phase == "AT_GATE":
            self._back_to_road(car, None)
        elif car.phase in ("TO_LOT", "DRIVING"):
            self.eng.send_away(car)
        else:
            return
        if car.reservation_id:
            self._cancel_reservation(car)
        car.phase, car.sub, car.lot_id = "LEAVING", "Leaving the area", None
        car.t_phase = self.t
        self.note(car, "Gave up parking and left the area")

    def redirect(self, car):
        """Lot refused the car (full): back to the lot choice, without that lot."""
        lot = car.lot_id
        if car.phase == "AT_GATE":
            self._back_to_road(car, car.dest["edge"] if car.dest else None)
        if lot and lot not in car.exclude:
            car.exclude.append(lot)
        car.phase, car.lot_id, car.offers, car.sub = "DRIVING", None, None, "Looking for another lot"
        car.t_spawn = self.t
        self.note(car, f"Redirected away from {lot}")
        if car.autopilot:
            self.auto_choose(car)

    def _back_to_road(self, car, dest_edge):
        """The car turns back at the entrance barrier: the lane is released and the car re-enters the road."""
        lot_id = car.lot_id
        with state_lock, SessionLocal() as db:
            self._leave_gate_in_db(db, car)
            db.commit()
        self.eng.add_from_lot(car, self.lots[lot_id], dest_edge)
        car.in_sumo = True
        car.gate = {}

    # ------------------------------------------------------------------ scripted test scenarios
    def scenario_duplicate(self):
        a, b = self.cars["d6"], self.cars["d13"]
        if a.phase != "IDLE" or b.phase != "IDLE":
            raise FlowError("d6 and d13 must both be at home (idle) to start this scenario.")
        self.start_trip(a, autopilot=True)
        a.hold = True
        self.scripted.append({"id": "dup", "text": "d13 (same RFID as d6) will start as soon as d6 is parked", "wait_for": a.id,
                              "go": lambda: self._go_duplicate(b, a)})
        self.eng.event("Scenario: d6 parks first, then d13 (same RFID tag) tries to enter. Click d13 and request parking.")

    def _go_duplicate(self, b, a):
        self.start_trip(b, autopilot=False)
        b.sub = "Driving: choose a parking lot on this car's screen (same RFID as d6!)"

    def scenario_wrongspace(self, driver="d1"):
        car = self.cars[driver]
        if car.phase != "IDLE":
            raise FlowError(f"{driver} is already on a trip.")
        self.start_trip(car, autopilot=True)
        car.force_misspark = True              # after start_trip: reset_trip() clears the flag
        self.eng.event(f"Scenario: {driver} will park in a wrong space; the administration gets the notification.")

    def _run_scripted(self):
        for sc in list(self.scripted):
            w = self.cars[sc["wait_for"]]
            if w.phase == "PARKED" and w.session_status == SessionStatus.PARKED:
                self.scripted.remove(sc)
                try:
                    sc["go"]()
                except Exception as e:   # noqa: BLE001
                    self.eng.event(f"Scenario step failed: {e}")

    # ------------------------------------------------------------------ tick
    def tick(self):
        t = self.t
        self._run_scripted()
        # 1. start trips
        for car in self.cars.values():
            if car.phase == "IDLE" and t >= car.next_trip_at and self.active_count() < MAX_ACTIVE_DRIVERS and self.eng.auto_trips:
                try:
                    self.start_trip(car)
                except Exception as e:   # noqa: BLE001
                    car.next_trip_at = t + 30
                    self.eng.event(f"{car.id}: trip could not start ({e})")
        # 2. each active car
        for car in list(self.cars.values()):
            if car.phase == "IDLE":
                continue
            getattr(self, f"_ph_{car.phase.lower()}")(car)
        # 3. lots' bookkeeping every few seconds
        if t - self._last_snapshot >= 120:
            self._last_snapshot = t
            with state_lock, SessionLocal() as db:
                rsvc.snapshot_all(db)
                db.commit()

    # ---- phases ----------------------------------------------------------------------
    def _sync_from_sumo(self, car):
        v = self.eng.veh.get(car.id)
        if v:
            car.x, car.y, car.angle = v[0], v[1], v[2]
        return v

    def _ph_driving(self, car):
        v = self._sync_from_sumo(car)
        if v is None:
            if self.t - car.t_spawn > 3:       # arrived at the end of its route without parking: cruise again
                if not self.eng.add_driver(car, self.rng.choice(self.world["border_in"]), car.dest["edge"]):
                    car.phase, car.sub = "IDLE", "At home"
            return
        if car.autopilot and not car.requested and self.t - car.t_spawn > 12:
            self.auto_choose(car)
            if not car.requested:
                car.t_spawn = self.t - 6   # try again in 6 s
        self.eng.keep_cruising(car)

    def _ph_to_lot(self, car):
        v = self._sync_from_sumo(car)
        lot = self.lots[car.lot_id]
        if v is None:
            if self.t - car.t_phase > 3:
                self.note(car, "Lost the route to the lot")
                car.phase, car.t_spawn = "DRIVING", self.t
            return
        car.t_phase = self.t
        if self.eng.at_entry(car, lot):
            rt = self.rt[car.lot_id]
            if rt["gate_in"] in (None, car.id):
                rt["gate_in"] = car.id
                self.eng.remove_vehicle(car.id)          # the car pulls off the road, up to the barrier
                car.in_sumo = False
                car.x, car.y = lot["gate_in"][0], lot["gate_in"][1]
                car.phase, car.sub = "AT_GATE", "At the entrance barrier"
                car.gate = {"step": "arrive", "t": self.t}
                car.t_phase = self.t
                self.note(car, f"Arrived at the entrance of {car.lot_id}")

    def _ph_at_gate(self, car):
        g, lot_id, t = car.gate, car.lot_id, self.t
        rt = self.rt[lot_id]
        with state_lock, SessionLocal() as db:
            lane = self._lane(db, lot_id, "in")
            if g["step"] == "arrive":
                entrance_flow.vehicle_detected(db, lane)
                g["step"], g["t"] = "read", t + 2
            elif g["step"] == "read" and t >= g["t"]:
                if car.no_tag:
                    entrance_flow.rfid_timeout(db, lane)
                else:
                    entrance_flow.rfid_read(db, lane, car.rfid.upper())
                g["step"], g["t"] = "decide", t
            elif g["step"] == "present_card" and t >= g["t"]:
                uid = g["card"]
                entrance_flow.rfid_read(db, lane, uid)
                car.held_card = uid
                g["step"], g["t"] = "decide", t
            if g["step"] == "decide":
                self._poll_commands(db, lot_id)
                self._decide_gate(db, car, lane, g, rt)
            else:
                self._poll_commands(db, lot_id)
            car.lane_state, car.lane_message = lane.state, lane.message
            db.commit()

    def _decide_gate(self, db, car, lane, g, rt):
        t = self.t
        st = lane.state
        car.sub = lane.message or car.sub
        if st == LaneState.TICKET_SHOWN and lane.ticket_json:
            car.ticket = json.loads(lane.ticket_json)
            car.session_id, car.assigned_space = lane.session_id, car.ticket["space"]
            car.reservation_id = None
            if rt["barrier_in"] > t:
                g["barrier_seen"] = g.get("barrier_seen", t)
                if t - g["barrier_seen"] >= 2:      # the arm is up: drive in
                    self._enter_lot(car, lane, db)
            return
        if st == LaneState.AWAITING_CARD:
            uid = rt["dispensed"].pop(lane.id, None)
            if uid:
                g["step"], g["card"], g["t"] = "present_card", uid, t + 2
                self.note(car, f"Took temporary card {uid} from the dispenser")
            return
        if st == LaneState.AWAITING_TICKET_REQUEST:
            if car.autopilot and not car.ticket_requested and t - car.t_phase > 4:
                try:
                    entrance_flow.request_ticket(db, lane)
                    self._poll_commands(db, car.lot_id)
                    car.ticket_requested = True
                    self.note(car, "Requested a visitor ticket")
                except FlowError:
                    pass
            return
        if st == LaneState.BLOCKED:
            msg = (lane.message or "").lower()
            if "full" in msg or "no parking space" in msg or "no temporary" in msg:
                if car.autopilot and t - car.t_phase > 6:
                    self._redirect_after(db, car)
            elif car.autopilot and t - car.t_phase > 40:
                # refused (e.g. duplicate tag) and nobody helped for a while: give up and leave
                self._leave_gate_in_db(db, car)
                self.leave_without_parking(car)
            return

    def _redirect_after(self, db, car):
        self._leave_gate_in_db(db, car)
        self.redirect(car)

    def _leave_gate_in_db(self, db, car):
        rt = self.rt[car.lot_id]
        if rt["gate_in"] == car.id:
            rt["gate_in"] = None
        lane = self._lane(db, car.lot_id, "in")
        entrance_flow.vehicle_cleared(db, lane)
        car.gate = {}

    def _enter_lot(self, car, lane, db):
        lot = self.lots[car.lot_id]
        car.phase, car.sub = "IN_LOT_IN", f"Entering {car.lot_id}"
        self._set_path(car, [lot["gate_in"], lot["aisle_in"]])
        entrance_flow.vehicle_cleared(db, lane)
        rt = self.rt[car.lot_id]
        rt["gate_in"] = None
        car.gate = {}
        self.note(car, f"Barrier open: entered {car.lot_id}, ticket {car.ticket['ticket_id']}, space {car.ticket['space']}")
        self.eng.event(f"{car.id} entered {car.lot_id}, assigned space {car.ticket['space']}")

    def _ph_in_lot_in(self, car):
        if self._advance(car, 1.0):
            car.phase, car.sub = "CHOOSE_SPACE", f"Ticket: space {car.assigned_space}"
            car.t_phase = self.t
            if car.autopilot:
                car.misspark = getattr(car, "force_misspark", False) or self.rng.random() < self.eng.misspark_prob
                car.force_misspark = False
                if car.misspark:
                    car.park_choice = "other"

    def _free_other_space(self, car):
        with state_lock, SessionLocal() as db:
            q = select(Space).where(Space.lot_id == car.lot_id, Space.status == SpaceStatus.AVAILABLE, Space.sensor_occupied.is_(False),
                                    Space.assigned_session_id.is_(None), Space.code != car.assigned_space)
            spaces = db.scalars(q).all()
        return self.rng.choice(spaces).code if spaces else None

    def _ph_choose_space(self, car):
        choice = car.park_choice
        if choice is None and car.autopilot and self.t - car.t_phase > 3:
            choice = "assigned"
        if choice is None:
            return
        if choice == "other":
            choice = self._free_other_space(car) or "assigned"
            car.park_choice = choice
        self._go_to_space(car, car.assigned_space if choice == "assigned" else choice)

    def _go_to_space(self, car, code):
        lot = self.lots[car.lot_id]
        sp = self.lot_space(car.lot_id, code)
        if sp is None:
            raise FlowError(f"Unknown space {code}")
        self._set_path(car, [(car.x, car.y), self.aisle_point(lot, sp), (sp["x"], sp["y"])])
        car.phase, car.target_space = "TO_SPACE", code
        car.sub = f"Going to space {code}"
        self.note(car, f"Driving to space {code}" + ("" if code == car.assigned_space else f" (assigned: {car.assigned_space})"))

    @staticmethod
    def lot_point(lot, u, v):
        (x0, y0), (x1, y1), (_, _), (x3, y3) = lot["corners"]
        return (x0 + (x1 - x0) * u / lot["L"] + (x3 - x0) * v / lot["D"], y0 + (y1 - y0) * u / lot["L"] + (y3 - y0) * v / lot["D"])

    def aisle_point(self, lot, sp):
        return self.lot_point(lot, sp["u"], lot["aisle_v"])

    def _ph_to_space(self, car):
        if not self._advance(car, 1.0):
            return
        lot_id, code = car.lot_id, car.target_space
        car.space = code
        with state_lock, SessionLocal() as db:
            space = db.scalar(select(Space).where(Space.lot_id == lot_id, Space.code == code))
            space_flow.occupancy_changed(db, space, True)          # the magnetic sensor sees the car
            self._poll_commands(db, lot_id)
            db.commit()
        car.phase, car.sub, car.t_phase = "PARKED", f"Parked in {code}", self.t
        car.tag_pending = True
        self.rt[lot_id]["parked"][code] = car.id
        self.note(car, f"Stopped in space {code}")

    def _ph_parked(self, car):
        lot_id, t = car.lot_id, self.t
        with state_lock, SessionLocal() as db:
            space = db.scalar(select(Space).where(Space.lot_id == lot_id, Space.code == car.space))
            if getattr(car, "tag_pending", False) and t - car.t_phase >= 1:
                space_flow.rfid_seen(db, space, (car.held_card or car.rfid).upper())   # the local reader reads the tag
                car.tag_pending = False
                self._poll_commands(db, lot_id)
                s = db.get(ParkingSession, car.session_id) if car.session_id else None
                if s is not None and s.status == SessionStatus.MISPLACED:
                    self._misparked(db, car, s)
                elif s is not None:
                    car.leave_at = t + self.eng.dwell_seconds(car)
                    self.note(car, "Parked in the assigned space: session PARKED")
                    self.eng.event(f"{car.id} parked in {lot_id}-{car.space}")
            s = db.get(ParkingSession, car.session_id) if car.session_id else None
            if s is not None:
                car.session_status = s.status
                sp = db.get(Space, s.space_id) if s.space_id else None
                if sp is not None:
                    car.assigned_space = sp.code
                if s.status == SessionStatus.PARKED and car.leave_at is None:
                    car.leave_at = t + self.eng.dwell_seconds(car)
            msgs = db.scalars(select(cm.CarMessage).where(cm.CarMessage.driver_id == car.id, cm.CarMessage.acknowledged.is_(False))).all()
            repark_msg = next((m for m in msgs if m.kind == "REPARK"), None)
            db.commit()
        car.sub = ("WRONG SPACE: " if car.session_status == SessionStatus.MISPLACED else "") + f"Parked in {car.space}"
        if car.session_status == SessionStatus.MISPLACED and (car.repark_requested or (car.autopilot and repark_msg and t - car.t_phase > 6)):
            self._repark(car)
            return
        if car.session_status == SessionStatus.MISPLACED:
            return                                          # waits for the administration (or the driver)
        if car.leave_requested or (car.autopilot and not getattr(car, "hold", False) and car.leave_at is not None and t >= car.leave_at):
            self._start_leaving(car)

    def _misparked(self, db, car, s):
        lot = self.lots[car.lot_id]
        text = (f"{car.id} ({car.rfid}) parked in space {car.space} but was assigned {car.assigned_space} in {lot['name']} "
                f"(ticket {s.ticket_id}).")
        db.add(cm.AdminNotice(lot_id=car.lot_id, driver_id=car.id, kind="WRONG_SPACE", text=text, created_at=now()))
        db.add(cm.CarMessage(driver_id=car.id, kind="INFO", sender="system", created_at=now(), session_id=s.id, space_code=car.assigned_space,
                             text=f"You parked in {car.space} but your ticket says {car.assigned_space}. The administration was notified."))
        self.note(car, f"Parked in the WRONG space {car.space} (ticket says {car.assigned_space}): administration notified")
        self.eng.event(f"{car.id} parked in the wrong space ({car.space} instead of {car.assigned_space}) in {car.lot_id}")

    def _repark(self, car):
        lot = self.lots[car.lot_id]
        old = car.space
        with state_lock, SessionLocal() as db:
            space = db.scalar(select(Space).where(Space.lot_id == car.lot_id, Space.code == old))
            space_flow.occupancy_changed(db, space, False)
            for m in db.scalars(select(cm.CarMessage).where(cm.CarMessage.driver_id == car.id, cm.CarMessage.kind == "REPARK")):
                m.acknowledged = True
            self._poll_commands(db, car.lot_id)
            db.commit()
        self.rt[car.lot_id]["parked"].pop(old, None)
        sp_old = self.lot_space(car.lot_id, old)
        car.repark_requested, car.park_choice = False, "assigned"
        car.x, car.y = sp_old["x"], sp_old["y"]
        car.phase, car.t_phase = "CHOOSE_SPACE", self.t
        self.note(car, f"Leaving space {old} to re-park in {car.assigned_space}")
        self._set_path(car, [(sp_old["x"], sp_old["y"]), self.aisle_point(lot, sp_old)])
        car.phase = "REPARKING"

    def _ph_reparking(self, car):
        if self._advance(car, 1.0):
            car.phase = "CHOOSE_SPACE"
            car.park_choice = "assigned"

    def _start_leaving(self, car):
        lot = self.lots[car.lot_id]
        sp = self.lot_space(car.lot_id, car.space)
        with state_lock, SessionLocal() as db:
            space = db.scalar(select(Space).where(Space.lot_id == car.lot_id, Space.code == car.space))
            space_flow.occupancy_changed(db, space, False)
            self._poll_commands(db, car.lot_id)
            db.commit()
        self.rt[car.lot_id]["parked"].pop(car.space, None)
        pts = [(sp["x"], sp["y"]), self.aisle_point(lot, sp), tuple(lot["aisle_out"]), tuple(lot["gate_out"])]
        self._set_path(car, pts)
        car.phase, car.sub, car.leave_requested = "TO_EXIT", f"Leaving {car.lot_id}", False
        self.note(car, "Left its space, driving to the exit barrier")

    def _ph_to_exit(self, car):
        if self._advance(car, 1.0):
            rt = self.rt[car.lot_id]
            if rt["gate_out"] in (None, car.id):
                rt["gate_out"] = car.id
                car.phase, car.sub = "AT_EXIT", "At the exit barrier"
                car.gate, car.t_phase = {"step": "arrive", "t": self.t}, self.t

    def _ph_at_exit(self, car):
        g, lot_id, t = car.gate, car.lot_id, self.t
        rt = self.rt[lot_id]
        with state_lock, SessionLocal() as db:
            lane = self._lane(db, lot_id, "out")
            if g["step"] == "arrive":
                exit_flow.vehicle_detected(db, lane)
                g["step"], g["t"] = "read", t + 2
            elif g["step"] == "read" and t >= g["t"]:
                if car.held_card is None and car.no_tag:
                    exit_flow.rfid_timeout(db, lane)
                else:
                    exit_flow.rfid_read(db, lane, (car.held_card or car.rfid).upper())
                g["step"] = "decide"
            elif g["step"] == "return_card" and t >= g["t"]:
                exit_flow.card_returned(db, lane, car.held_card.upper())
                g["step"] = "decide"
            if g["step"] == "decide":
                self._poll_commands(db, lot_id)
                st = lane.state
                car.sub = lane.message or car.sub
                if st == LaneState.AWAITING_CARD_RETURN and g.get("step") != "return_card":
                    g["step"], g["t"] = "return_card", t + 2
                elif st == LaneState.EXIT_AUTHORIZED and rt["barrier_out"] > t:
                    g["barrier_seen"] = g.get("barrier_seen", t)
                    if t - g["barrier_seen"] >= 2:
                        exit_flow.vehicle_cleared(db, lane)
                        rt["gate_out"] = None
                        car.held_card = None
                        db.commit()
                        self._exit_lot(car)
                        return
            else:
                self._poll_commands(db, lot_id)
            car.lane_state, car.lane_message = lane.state, lane.message
            db.commit()

    def _exit_lot(self, car):
        lot = self.lots[car.lot_id]
        self.note(car, f"Exit barrier open: left {car.lot_id}")
        self.eng.event(f"{car.id} left {car.lot_id}")
        self.eng.add_leaving(car, lot)
        car.in_sumo = True
        car.phase, car.sub = "LEAVING", "Driving out of the area"
        car.lot_id_last, car.lot_id = car.lot_id, None
        car.ticket = car.session_id = car.assigned_space = car.space = None
        car.session_status = None

    def _ph_leaving(self, car):
        v = self._sync_from_sumo(car)
        if v is None and self.t - car.t_phase > 2:
            car.phase, car.sub, car.in_sumo = "IDLE", "At home", False
            car.x = car.y = None
            car.next_trip_at = self.t + self.rng.uniform(60, 200)
            self.note(car, "Trip finished")
        elif v is not None:
            car.t_phase = self.t

    # ------------------------------------------------------------------ views
    def car_view(self, car, full=False):
        v = {"id": car.id, "name": car.name, "phase": car.phase, "sub": car.sub, "x": car.x, "y": car.y, "angle": round(car.angle),
             "lot": car.lot_id, "space": car.space, "assigned": car.assigned_space, "autopilot": car.autopilot,
             "status": car.session_status, "in_sumo": car.in_sumo}
        if full:
            dow, minutes, clock = self.eng.clock()
            v.update({"rfid": car.rfid, "plate": car.plate, "married": car.married, "kids": car.kids, "shares_tag_with": car.shares_tag_with,
                      "dest": {"id": car.dest["id"], "label": car.dest["label"]} if car.dest else None,
                      "ticket": car.ticket, "offers": car.offers, "held_card": car.held_card, "lane_state": car.lane_state,
                      "lane_message": car.lane_message, "log": list(car.log), "no_tag": car.no_tag,
                      "park_choice": car.park_choice, "ticket_requested": car.ticket_requested,
                      "places": [{"id": p["id"], "label": p["label"], "kind": p["kind"], "weight": p["weight"], "schedule": p["schedule"],
                                  "active": ranking7.is_active(p["schedule"], dow, minutes)} for p in car.places],
                      "actions": self.actions(car)})
            with state_lock, SessionLocal() as db:
                msgs = db.scalars(select(cm.CarMessage).where(cm.CarMessage.driver_id == car.id).order_by(cm.CarMessage.id.desc()).limit(6)).all()
                v["messages"] = [{"id": m.id, "kind": m.kind, "text": m.text, "sender": m.sender, "acknowledged": m.acknowledged,
                                  "at": m.created_at.isoformat(timespec="seconds") + "Z"} for m in msgs]
            if car.lot_id:
                lot = self.lots[car.lot_id]
                v["lot_info"] = {"id": lot["id"], "name": lot["name"]}
        return v

    def actions(self, car):
        a = []
        p = car.phase
        if p == "IDLE":
            a += ["start_trip"]
        if p in ("DRIVING", "TO_LOT", "AT_GATE") and car.dest:
            a += ["request_parking"]
        if p in ("DRIVING", "TO_LOT", "AT_GATE", "LEAVING"):
            pass
        if p in ("TO_LOT", "AT_GATE", "DRIVING"):
            a += ["give_up"]
        if p == "AT_GATE" and car.lane_state == LaneState.AWAITING_TICKET_REQUEST and not car.ticket_requested:
            a += ["request_ticket"]
        if p == "AT_GATE" and car.lane_state == LaneState.BLOCKED:
            a += ["redirect"]
        if p in ("CHOOSE_SPACE", "IN_LOT_IN", "TO_SPACE"):
            a += ["park_assigned", "park_other"]
        if p == "PARKED":
            a += ["leave", "unhold" if getattr(car, "hold", False) else "hold"]
            if car.session_status == SessionStatus.MISPLACED:
                a += ["repark"]
        return a

"""The live city: one continuous SUMO simulation that also trains the traffic signals, runs the background traffic, the
emergency vehicles (ambulances, police cars, fire trucks, all with signal priority) and the drivers of the parking system.

Everything touching SUMO (TraCI) happens in ONE thread (the simulation thread). Web requests ask it to do things with
`call(fn)`; the web pages only read the last published snapshot.
"""
import json
import logging
import math
import os
import queue
import random
import threading
import time
from collections import deque

import numpy as np
import sumolib
import traci
import traci.constants as tc
from sqlalchemy import select

import sumo_rl
from app.database import SessionLocal, state_lock
from app.enums import ACTIVE_SESSION_STATUSES, SpaceStatus
from app.models import Space
from sumo_rl import PreemptionConfig
from training.mock_data import VTYPES

from . import rl
from .parkingsim import ParkingSim

log = logging.getLogger("city.engine")
SUB_VARS = [tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_WAITING_TIME, tc.VAR_ACCUMULATED_WAITING_TIME, tc.VAR_ROAD_ID, tc.VAR_LANEPOSITION]
EMERGENCY_TYPES = {"ambulance": "amb", "police": "pol", "fire": "fire"}
KIND_CODE = {"car": "C", "truck": "T", "driver": "D", "ambulance": "A", "police": "P", "fire": "F"}

TYPES_XML = """<routes>
{vtypes}
    <vType id="dcar" vClass="passenger" color="0,200,255" length="4.6" width="1.9" guiShape="passenger" accel="2.8" decel="4.5" maxSpeed="13.9"/>
    <vType id="police" vClass="emergency" guiShape="police" color="30,80,255" length="5.0" width="2.0" accel="4.0" decel="6.0" maxSpeed="22" speedFactor="1.25">
        <param key="has.bluelight.device" value="true"/></vType>
    <vType id="fire" vClass="emergency" guiShape="firebrigade" color="255,90,0" length="9.0" width="2.5" accel="3.0" decel="5.0" maxSpeed="20" speedFactor="1.15">
        <param key="has.bluelight.device" value="true"/></vType>
</routes>
"""


class Engine:
    def __init__(self, world_path="world/world.json", run_dir="runs/city", fresh=False, seed=1):
        with open(world_path) as fh:
            self.world = json.load(fh)
        self.run_dir, self.fresh = run_dir, fresh
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.cmd_q = queue.Queue()
        self.thread = None
        self.stop_flag = threading.Event()
        self.ready = threading.Event()
        self.error = None
        # controls
        self.speed = 6.0
        self.paused = False
        self.auto_trips = True
        self.misspark_prob = 0.15
        self.traffic_level = 1.0
        self.traffic_peak = 300
        self.time_scale = 30.0                # simulated clock seconds per SUMO second
        self.clock_start_min = 1 * 1440 + 6 * 60 + 30     # Tuesday 06:30
        self.emergency_target = dict(self.world["emergency"])
        # state
        self.t = 0.0
        self.veh = {}
        self.vkind = {}
        self.depart_t = {}
        self.sysinfo = {"mean_wait": 0.0, "mean_speed": 0.0, "stopped": 0, "running": 0}
        self.events = deque(maxlen=60)
        self.pub = {"t": 0, "vehicles": [], "signals": {}, "cars": [], "lots": {}, "ready": False}
        self.counters = {"bg": 0, "emergency": {k: 0 for k in EMERGENCY_TYPES}}
        self.ids = {"bg": 0, "amb": 0, "pol": 0, "fire": 0, "route": 0}
        self.jam = None                       # live traffic-jam test (see start_jam)
        self.jam_routes = None
        self.next_emergency = {k: 5.0 for k in EMERGENCY_TYPES}
        self.route_cache = {}
        self.pre_seen = 0
        self.learner = None
        self.bridge = None
        self.last_pub_real = 0.0
        self.last_lot_view = -1e9
        self.lot_view = {}
        self._deadline = None
        self.stats = {"emergency_served": 0, "emergency_wait_sum": 0.0, "preemptions": 0}

    # ------------------------------------------------------------------ clock
    def clock(self):
        m = (self.clock_start_min + self.t * self.time_scale / 60.0) % (7 * 1440)
        dow, minutes = int(m // 1440), int(m % 1440)
        return dow, minutes, f"{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][dow]} {minutes // 60:02d}:{minutes % 60:02d}"

    def set_clock(self, dow, hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        self.clock_start_min = (dow * 1440 + h * 60 + m) - self.t * self.time_scale / 60.0

    def event(self, text):
        self.events.appendleft({"t": round(self.t), "clock": self.clock()[2], "text": text})

    # ------------------------------------------------------------------ thread control
    def start(self):
        self.thread = threading.Thread(target=self._run, name="city-sim", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=20)

    def call(self, fn, timeout=30.0):
        """Run fn() in the simulation thread and return its result (or raise its exception)."""
        if threading.current_thread() is self.thread:
            return fn()
        if not self.ready.wait(timeout=60):
            raise RuntimeError("The simulation is not ready yet.")
        done = threading.Event()
        box = {}
        self.cmd_q.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("The simulation did not answer in time.")
        if "err" in box:
            raise box["err"]
        return box.get("ok")

    def _drain_queue(self):
        while True:
            try:
                fn, box, done = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            try:
                box["ok"] = fn()
            except BaseException as e:   # noqa: BLE001
                box["err"] = e
            done.set()

    # ------------------------------------------------------------------ main thread body
    def _run(self):
        try:
            self._setup()
            self.ready.set()
            obs = self.obs
            while not self.stop_flag.is_set():
                acts = self.learner.act(obs)
                obs, rew, dones, _ = self.env.step(acts)
                self.env.metrics.clear()
                self.learner.learn(obs, rew, self.sysinfo)
                for ts in obs:                          # keep the latest observation of signals not asked this round
                    pass
        except BaseException as e:   # noqa: BLE001
            log.exception("simulation thread crashed")
            self.error = repr(e)
            self.ready.set()
        finally:
            try:
                if self.learner:
                    self.learner.save()
                self.env.close()
            except Exception:   # noqa: BLE001
                pass

    def _setup(self):
        w = self.world
        os.makedirs("world", exist_ok=True)
        types = os.path.join("world", "types.rou.xml")
        with open(types, "w") as fh:
            fh.write(TYPES_XML.format(vtypes=VTYPES.replace("<routes>", "").replace("</routes>", "")))
        pre = PreemptionConfig(emergency_vtypes=tuple(EMERGENCY_TYPES), priority_by_vtype={"ambulance": 3, "fire": 2, "police": 1},
                               equipped=None)
        self.env = sumo_rl.SumoEnvironment(net_file=w["net_file"], route_file=types, use_gui=False, num_seconds=10 ** 9, delta_time=5,
                                           yellow_time=2, min_green=5, max_green=50, sumo_warnings=False, emergency_preemption=pre,
                                           add_system_info=False, add_per_agent_info=False, sumo_seed=7, time_to_teleport=60,
                                           additional_sumo_cmd="--no-step-log --end 100000000")
        self.net = sumolib.net.readNet(w["net_file"])
        self.inner_edges = [e.getID() for e in self.net.getEdges() if e.getFromNode().getType() == "traffic_light"
                            and e.getToNode().getType() == "traffic_light"]
        orig = self.env._sumo_step
        env = self.env

        def hooked_step():
            orig()
            self._tick()

        env._sumo_step = hooked_step
        self.sumo = None
        self.learner = None
        self.obs = None
        # the env connects to SUMO in reset(); the learner needs the env's signals
        self.parking = ParkingSim(self, w, self.rng)
        env_obs = env.reset(seed=7)
        self.sumo = env.sumo
        for ts in env.ts_ids:
            self.sumo.trafficlight.subscribe(ts, [tc.TL_RED_YELLOW_GREEN_STATE])
        self.learner = rl.OnlineLearner(env, self.run_dir, fresh=self.fresh, log=log.info)
        self.obs = env_obs
        self.event("City started. Signals use the saved training." if self.learner.warm else "City started.")

    # ------------------------------------------------------------------ SUMO helpers
    def _new_route(self, edges):
        self.ids["route"] += 1
        rid = f"r{self.ids['route']}"
        self.sumo.route.add(rid, list(edges))
        return rid

    def find_route(self, o, d, vtype="car"):
        st = self.sumo.simulation.findRoute(o, d, vType=vtype)
        return list(st.edges), st.travelTime

    def eta_min(self, o, d):
        try:
            edges, tt = self.find_route(o, d)
            return max(0.3, tt / 60.0) if edges else None
        except Exception:   # noqa: BLE001
            return None

    def edge_midpoint(self, edge):
        e = self.net.getEdge(edge)
        sh = e.getShape()
        return ((sh[0][0] + sh[-1][0]) / 2, (sh[0][1] + sh[-1][1]) / 2)

    def current_edge(self, vid):
        v = self.veh.get(vid)
        if v is None:
            return None
        road = v[6]
        if road.startswith(":"):
            try:
                route, idx = self.sumo.vehicle.getRoute(vid), self.sumo.vehicle.getRouteIndex(vid)
                return route[min(max(idx, 0), len(route) - 1)]
            except Exception:   # noqa: BLE001
                return None
        return road

    def add_driver(self, car, origin, dest):
        edges, _ = self.find_route(origin, dest)
        if not edges:
            return False
        try:
            self.sumo.vehicle.add(car.id, self._new_route(edges), typeID="dcar", depart="now", departLane="best", departSpeed="max")
        except traci.TraCIException as e:
            log.warning("add_driver %s: %s", car.id, e)
            return False
        self.vkind[car.id] = "driver"
        car.stop_set = False
        return True

    def route_to_lot(self, car, lot):
        try:
            self.sumo.vehicle.changeTarget(car.id, lot["access_edge"])
        except traci.TraCIException as e:
            log.warning("changeTarget %s: %s", car.id, e)

    def at_entry(self, car, lot):
        """True when the car is level with the lot's entrance (it then leaves the road through the gate)."""
        v = self.veh.get(car.id)
        return v is not None and v[6] == lot["access_edge"] and lot["entry_pos"] - 10 <= v[7] <= lot["entry_pos"] + 25

    def remove_vehicle(self, vid):
        try:
            self.sumo.vehicle.remove(vid)
        except traci.TraCIException:
            pass
        self.veh.pop(vid, None)

    def send_away(self, car):
        try:
            self.sumo.vehicle.changeTarget(car.id, self.rng.choice(self.world["border_out"]))
        except traci.TraCIException:
            pass

    def add_from_lot(self, car, lot, dest_edge=None):
        """Put a car that is at the lot's barrier back on the road, just after the lot's exit, heading to dest_edge."""
        dest = dest_edge or self.rng.choice(self.world["border_out"])
        edges, _ = self.find_route(lot["access_edge"], dest)
        if not edges:
            edges = [lot["access_edge"]]
        self.sumo.vehicle.add(car.id, self._new_route(edges), typeID="dcar", depart="now", departLane="0", departPos=str(lot["exit_pos"]),
                              departSpeed="0")
        self.vkind[car.id] = "driver"
        car.t_phase = self.t

    def add_leaving(self, car, lot):
        self.add_from_lot(car, lot)

    def keep_cruising(self, car):
        if car.id not in self.veh:
            return
        try:
            route, idx = self.sumo.vehicle.getRoute(car.id), self.sumo.vehicle.getRouteIndex(car.id)
            if idx >= len(route) - 1:
                self.sumo.vehicle.changeTarget(car.id, self.rng.choice(self.inner_edges))
        except traci.TraCIException:
            pass

    def dwell_seconds(self, car):
        lo, hi = car.dest["dwell_clock_min"] if car.dest else (30, 60)
        return self.rng.uniform(lo, hi) * 60.0 / self.time_scale

    # ------------------------------------------------------------------ per simulated second
    def _tick(self):
        sumo = self.sumo
        if sumo is None or self.learner is None:
            return                      # still starting up
        self.t = sumo.simulation.getTime()
        while self.paused and not self.stop_flag.is_set():
            self._drain_queue()
            time.sleep(0.05)
        self._drain_queue()
        # subscriptions of new vehicles, arrivals of finished ones
        for vid in sumo.simulation.getDepartedIDList():
            sumo.vehicle.subscribe(vid, SUB_VARS)
            if vid not in self.vkind:
                self.vkind[vid] = sumo.vehicle.getTypeID(vid)
            self.depart_t[vid] = self.t
        prev = self.veh
        for vid in sumo.simulation.getArrivedIDList():
            self._arrived(vid, prev.get(vid))
        res = sumo.vehicle.getAllSubscriptionResults()
        veh = {}
        n_wait = n_stop = 0
        wait_sum = speed_sum = 0.0
        for vid, r in res.items():
            if tc.VAR_POSITION not in r:
                continue
            x, y = r[tc.VAR_POSITION]
            sp = r[tc.VAR_SPEED]
            if sp < 0 or r[tc.VAR_WAITING_TIME] < 0 or abs(x) > 1e7:
                continue                        # just inserted: SUMO has no valid values yet
            veh[vid] = (x, y, r[tc.VAR_ANGLE], sp, self.vkind.get(vid, "car"), r[tc.VAR_WAITING_TIME], r[tc.VAR_ROAD_ID],
                        r[tc.VAR_LANEPOSITION], r[tc.VAR_ACCUMULATED_WAITING_TIME])
            wait_sum += r[tc.VAR_WAITING_TIME]
            speed_sum += sp
            n_stop += sp < 0.1
        n = max(1, len(veh))
        self.veh = veh
        self.sysinfo = {"mean_wait": wait_sum / n, "mean_speed": speed_sum / n, "stopped": n_stop, "running": len(veh)}
        # traffic
        self._count_kinds()
        self._jam_tick()
        self._spawn_background()
        self._spawn_emergency()
        # parking world
        self.parking.tick()
        self._preemption_stats()
        row = self.learner.maybe_close_window(self.t, self.stats["preemptions"])
        if row:
            self.event(f"Training window {row['window']} ({row['mode']}): mean wait {row['mean_wait']} s, stopped {row['mean_stopped']}")
        if self.t - self.last_lot_view >= 2:
            self._refresh_lot_view()
        if time.time() - self.last_pub_real > 0.08:
            self._publish()
        self._pace()

    def _arrived(self, vid, last):
        if self.bridge is not None:
            self.bridge.on_arrived(vid)
        kind = self.vkind.pop(vid, "car")
        t0 = self.depart_t.pop(vid, self.t)
        wait = last[8] if last else 0.0
        if kind in EMERGENCY_TYPES:
            self.learner.vehicle_arrived("emergency", wait, self.t - t0)
            self.stats["emergency_served"] += 1
            self.stats["emergency_wait_sum"] += wait
        else:
            self.learner.vehicle_arrived(kind, wait, self.t - t0)

    def _count_kinds(self):
        bg = 0
        em = {k: 0 for k in EMERGENCY_TYPES}
        for vid, v in self.veh.items():
            k = v[4]
            if k in em:
                em[k] += 1
            elif k in ("car", "truck"):
                bg += 1
        self.counters = {"bg": bg, "emergency": em}

    def bg_target(self):
        _, minutes, _ = self.clock()
        h = minutes / 60.0
        base = 0.25 + 0.75 * (math.exp(-((h - 8.0) / 1.6) ** 2) + math.exp(-((h - 17.5) / 1.8) ** 2)) + 0.30 * math.exp(-((h - 13.0) / 2.2) ** 2)
        return int(self.traffic_peak * min(1.0, base) * self.traffic_level)

    # ------------------------------------------------------------------ traffic-jam test
    def _pick_jam_routes(self):
        """The longest border-to-border routes of a sample: they cross the middle of the city and each other."""
        if self.jam_routes:
            return self.jam_routes
        cand = {}
        for _ in range(60):
            o, d = self.rng.choice(self.world["border_in"]), self.rng.choice(self.world["border_out"])
            if (o, d) in cand:
                continue
            edges, _ = self.find_route(o, d)
            if len(edges) >= 8:
                cand[(o, d)] = edges
        best = sorted(cand.values(), key=len, reverse=True)[:5]
        self.jam_routes = [self._new_route(e) for e in best]
        return self.jam_routes

    def _jam_note(self, text):
        self.event(text)
        if self.bridge is not None:
            try:
                self.bridge.tl.add("brain", "jam", text)
            except Exception:   # noqa: BLE001
                pass

    def start_jam(self, size=300, seconds=60):
        """Push `size` extra cars into the city within `seconds` simulated seconds, on a few long crossing routes.
        Records how many cars stand still at the worst moment and how long the city needs to get moving again."""
        if self.jam and self.jam["state"] in ("loading", "clearing"):
            raise ValueError("a jam test is already running")
        routes = self._pick_jam_routes()
        if not routes:
            raise ValueError("no suitable routes found")
        size, seconds = max(20, min(1500, int(size))), max(10, int(seconds))
        base = self.sysinfo["stopped"]
        self.jam = {"state": "loading", "size": size, "seconds": seconds, "t0": self.t, "released": 0, "baseline_stopped": base,
                    "peak_stopped": base, "peak_wait": 0.0, "peak_at": 0, "recovered_after": None, "sum_stopped": 0, "ticks": 0,
                    "mode": self.learner.mode if self.learner else None, "calm_ticks": 0}
        self._jam_note(f"TRAFFIC JAM TEST: {size} extra cars enter within {seconds} s (signals: {self.jam['mode']} mode)")
        return self.jam_view()

    def _jam_tick(self):
        j = self.jam
        if not j or j["state"] in ("done",):
            return
        age = self.t - j["t0"]
        if j["state"] == "loading":
            due = min(j["size"], int(j["size"] * age / j["seconds"]) + 1)
            while j["released"] < due:
                rid = self.jam_routes[j["released"] % len(self.jam_routes)]
                self.ids["bg"] += 1
                try:
                    self.sumo.vehicle.add(f"j{self.ids['bg']}", rid, typeID="mix", depart="now", departLane="best", departSpeed="max")
                except traci.TraCIException:
                    pass
                j["released"] += 1
            if j["released"] >= j["size"]:
                j["state"] = "clearing"
        st = self.sysinfo["stopped"]
        j["ticks"] += 1
        j["sum_stopped"] += st
        if st > j["peak_stopped"]:
            j["peak_stopped"], j["peak_at"] = st, round(age)
        j["peak_wait"] = max(j["peak_wait"], self.sysinfo["mean_wait"])
        if j["state"] == "clearing":
            # recovered = the number of standing cars is back near what it was before (and stays there for 20 s)
            limit = max(j["baseline_stopped"] * 1.5, j["baseline_stopped"] + 10)
            j["calm_ticks"] = j["calm_ticks"] + 1 if st <= limit else 0
            if j["calm_ticks"] >= 20 and j["peak_stopped"] > limit:
                j["state"], j["recovered_after"] = "done", round(age - 20)
                self._jam_note(f"jam test finished: worst moment {j['peak_stopped']} cars standing still, city moving again after {j['recovered_after']} s")
            elif age > 3600:
                j["state"] = "done"
                self._jam_note(f"jam test stopped after 60 min: city still not free ({st} cars standing)")

    def jam_view(self):
        j = self.jam
        if not j:
            return None
        out = {k: j[k] for k in ("state", "size", "released", "baseline_stopped", "peak_stopped", "peak_wait", "peak_at", "recovered_after", "mode")}
        out["elapsed"] = round(self.t - j["t0"])
        out["mean_stopped"] = round(j["sum_stopped"] / max(1, j["ticks"]), 1)
        out["peak_wait"] = round(out["peak_wait"], 1)
        return out

    def _bg_route(self):
        for _ in range(8):
            o, d = self.rng.choice(self.world["border_in"]), self.rng.choice(self.world["border_out"])
            if o.split("__")[0].replace("B", "") == d.split("__")[1].replace("B", ""):
                continue
            key = (o, d)
            if key not in self.route_cache:
                edges, _ = self.find_route(o, d)
                self.route_cache[key] = self._new_route(edges) if len(edges) >= 5 else None
            if self.route_cache[key]:
                return self.route_cache[key]
        return None

    def _spawn_background(self):
        deficit = self.bg_target() - self.counters["bg"]
        if deficit <= 0:
            return
        for _ in range(self.np_rng.poisson(min(4.0, deficit / 15.0))):
            rid = self._bg_route()
            if rid is None:
                continue
            self.ids["bg"] += 1
            try:
                self.sumo.vehicle.add(f"b{self.ids['bg']}", rid, typeID="mix", depart="now", departLane="best", departSpeed="max")
            except traci.TraCIException:
                pass

    def _spawn_emergency(self):
        for typ, prefix in EMERGENCY_TYPES.items():
            if self.counters["emergency"][typ] >= self.emergency_target.get(typ, 0) or self.t < self.next_emergency[typ]:
                continue
            for _ in range(6):
                o, d = self.rng.choice(self.world["border_in"]), self.rng.choice(self.world["border_out"])
                edges, _ = self.find_route(o, d, vtype="car")
                if len(edges) >= 9:
                    break
            else:
                continue
            self.ids[prefix] += 1
            vid = f"{prefix}_{self.ids[prefix]}"
            try:
                self.sumo.vehicle.add(vid, self._new_route(edges), typeID=typ, depart="now", departLane="best", departSpeed="max")
                self.vkind[vid] = typ
                self.counters["emergency"][typ] += 1
                self.next_emergency[typ] = self.t + self.rng.uniform(15, 70)
                self.event(f"{typ} {vid} on an emergency run")
            except traci.TraCIException:
                pass

    def dispatch_emergency(self, kind, lat, lon, call_id):
        """An emergency call from the Twin: send a vehicle from the city border to the street nearest to (lat, lon)."""
        from .worldgen import from_latlon
        x, y = from_latlon(lat, lon)
        cands = [(d, e) for e, d in self.net.getNeighboringEdges(x, y, r=150) if e.getID() in set(self.inner_edges)]
        if not cands:
            return None
        target = min(cands, key=lambda c: c[0])[1].getID()
        prefix = EMERGENCY_TYPES[kind]
        for _ in range(12):
            o = self.rng.choice(self.world["border_in"])
            edges, _tt = self.find_route(o, target, vtype="car")
            if len(edges) >= 3:
                break
        else:
            return None
        vid = f"{prefix}_{''.join(ch for ch in call_id if ch.isalnum())}"
        if vid in self.sumo.vehicle.getIDList():
            vid += "x"
        self.sumo.vehicle.add(vid, self._new_route(edges), typeID=kind, depart="now", departLane="best", departSpeed="max")
        self.vkind[vid] = kind
        self.event(f"{kind} {vid} dispatched to emergency call {call_id}")
        return vid

    def _preemption_stats(self):
        pre = self.env.preemption
        if pre is None:
            return
        self.stats["preemptions"] = sum(c.count for c in pre.controllers.values())
        ev = pre.events
        new = ev[self.pre_seen:] if self.pre_seen <= len(ev) else ev
        for e in new:
            if e.get("event") == "PREEMPT_START":
                self.event(f"Priority: signal {e['signal']} turns green for {e.get('vehicle', 'an emergency vehicle')}")
                if self.bridge is not None:
                    self.bridge.on_priority(e["signal"], e.get("vehicle", ""))
        self.pre_seen = len(ev)
        if len(ev) > 400:
            del ev[:300]
            self.pre_seen = len(ev)
        if len(pre.message_log) > 400:
            del pre.message_log[:300]

    def _refresh_lot_view(self):
        self.last_lot_view = self.t
        with state_lock, SessionLocal() as db:
            from app.models import Gateway
            from app.services.watchdog import gateway_seen
            for gw in db.scalars(select(Gateway)):
                gateway_seen(db, gw)         # the in-process "gateways" are alive as long as the simulation runs
            db.commit()
            rows = db.scalars(select(Space)).all()
            view = {}
            for s in rows:
                v = view.setdefault(s.lot_id, {"spaces": {}, "free": 0, "total": 0})
                v["spaces"][s.code] = {"st": s.status, "occ": s.sensor_occupied, "fault": s.sensor_fault}
                v["total"] += 1
                v["free"] += (s.status == SpaceStatus.AVAILABLE and not s.sensor_occupied and not s.sensor_fault and s.assigned_session_id is None)
        self.lot_view = view

    def _pace(self):
        if self.speed <= 0:
            self._deadline = None
            return
        now = time.time()
        step = 1.0 / self.speed
        if self._deadline is None or self._deadline < now - 1.0:
            self._deadline = now
        self._deadline += step
        wait = self._deadline - now
        if wait > 0:
            time.sleep(min(wait, 0.5))

    # ------------------------------------------------------------------ published view
    def _publish(self):
        self.last_pub_real = time.time()
        sig = {}
        try:
            res = self.sumo.trafficlight.getAllSubscriptionResults()
            for ts, r in res.items():
                sig[ts] = [r.get(tc.TL_RED_YELLOW_GREEN_STATE, ""), int(self.env.traffic_signals[ts].preempted)]
        except traci.TraCIException:
            pass
        vehicles = []
        for vid, v in self.veh.items():
            if v[4] == "driver":
                continue                       # drawn from the car list (they have a label and are clickable)
            vehicles.append([round(v[0], 1), round(v[1], 1), round(v[2]), KIND_CODE.get(v[4], "C"), round(v[3], 1), vid if v[4] in EMERGENCY_TYPES else ""])
        cars = [self.parking.car_view(c) for c in self.parking.cars.values()]
        lots = {}
        for lid, rt in self.parking.rt.items():
            lv = self.lot_view.get(lid, {"spaces": {}, "free": 0, "total": 0})
            lots[lid] = {"spaces": lv["spaces"], "free": lv["free"], "total": lv["total"], "bin": rt["barrier_in"] > self.t,
                         "bout": rt["barrier_out"] > self.t, "led": rt["led"], "parked": rt["parked"]}
        dow, minutes, clock = self.clock()
        em = self.counters["emergency"]
        self.pub = {"ready": True, "t": round(self.t), "clock": clock, "dow": dow, "minutes": minutes, "speed": self.speed, "paused": self.paused,
                    "vehicles": vehicles, "signals": sig, "cars": cars, "lots": lots, "events": list(self.events)[:25],
                    "counts": {"background": self.counters["bg"], "driver_cars": sum(1 for v in self.veh.values() if v[4] == "driver"),
                               "ambulance": em["ambulance"], "police": em["police"], "fire": em["fire"], "target": self.bg_target(),
                               "stopped": self.sysinfo["stopped"], "mean_wait": round(self.sysinfo["mean_wait"], 1),
                               "mean_speed": round(self.sysinfo["mean_speed"], 1)},
                    "controls": {"auto_trips": self.auto_trips, "misspark_prob": self.misspark_prob, "traffic_level": self.traffic_level,
                                 "time_scale": self.time_scale, "mode": self.learner.mode if self.learner else None,
                                 "emergency_target": self.emergency_target},
                    "jam": self.jam_view(),
                    "learning": self.learning_view(light=True)}

    def learning_view(self, light=False):
        L = self.learner
        if L is None:
            return {}
        out = {"mode": L.mode, "episode": L.episode, "epsilon": round(L.epsilon, 4), "q_states": len(L.q.q), "window_s": rl.WINDOW_S,
               "window_progress": round(0 if L.window_start_sim is None else min(1.0, (self.t - L.window_start_sim) / rl.WINDOW_S), 3),
               "session_start_episode": L.session_start_episode, "warm": L.warm, "started_from": L.started_from,
               "last_saved_at": L.last_saved_at, "preemptions": self.stats["preemptions"],
               "emergency_served": self.stats["emergency_served"],
               "emergency_wait_avg": round(self.stats["emergency_wait_sum"] / self.stats["emergency_served"], 2) if self.stats["emergency_served"] else None}
        if not light:
            out["history"] = L.history[-300:]
        return out

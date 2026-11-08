"""
Twin stand-in: a small city that answers the Godot Twin's device API with the same methods and shapes
(twin/scripts/sim_api.gd), with the same device ids, positions and zones as the real city.

    read_all, read, list_devices, list_incidents, list_emergency_units, list_parking_lots, command, ping, info

It is also the actuator stand-in: `command` changes the mock city exactly as the Twin's own commands
change the real one (a closed valve reads 0 %, a cut zone reads power_supplied 0 %, ...), and answers
{"id", "applied", "before", "after"} like the Twin.

Emergency units drive from their station to the scene along the roads, as in the Twin (mocks/fleet.py): no unit
ever appears at a scene; the Twin's own stations, bays and unit ids; responding units slow at red lights.

Used in-process by MockTwinSource, or as a TCP server that the *live* adapter can talk to:
    python -m mocks.twin_standin --serve --port 8820
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import random
import socketserver
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from mocks.fleet import Fleet, heading
except ImportError:                                    # run from inside brain/mocks
    from fleet import Fleet, heading

ZONES = "ABCDEFGHIJKLMNOP"
BANDS = ["North", "NorthCentral", "SouthCentral", "South"]


def zone_of(x: float, z: float) -> str:
    col = min(3, max(0, int((x + 380) // 190)))
    row = min(3, max(0, int((z + 380) // 190)))
    return ZONES[row * 4 + col]


def zone_centre(zone: str) -> tuple:
    i = ZONES.index(zone)
    return (-380 + (i % 4 + 0.5) * 190, -380 + (i // 4 + 0.5) * 190)


def service_zone_id(zone: str) -> str:
    """Twin service clusters (utility_resource_system.gd): Zone_<North|NorthCentral|SouthCentral|South>_<1..4>."""
    i = ZONES.index(zone)
    return f"ZONE-Zone_{BANDS[i // 4]}_{i % 4 + 1}"


# Positions read from the Twin scenes (twin/scenes/Sensors.tscn, Actuators.tscn) and
# twin/scripts/smart_city_asset_system.gd. Ids follow the Twin's numbering order.
TRAFFIC = [(-184, -184), (-89, -89), (6, 6), (101, 101), (196, 196), (-184, 196), (-89, 101), (6, -89),
           (101, -184), (196, -89), (-184, 6), (196, 101)]
AIR = [(-137, -130), (-42, -35), (53, 60), (148, 155), (-137, 155), (-42, 60), (53, -35), (148, -130)]
UTIL = [(-150, 47), (-55, 142), (40, -142), (135, -47), (-150, -47), (-55, -142), (40, 142), (135, 47)]
NOISE = [(-176, -62), (-81, 128), (109, 33), (176, 128)]
WEATHER = [(-166, 166), (-71, -119), (119, 71), (166, -166)]
FLOOD = [(-119, -176), (-24, 109), (71, -81), (166, 109)]
VALVES = [(-174, -142), (-79, 47), (16, 142), (111, -47), (176, 47)]
SWITCHES = [(-111, -47), (79, 47), (111, 142)]
LOTS = [("LOT-01", -123.5, -297.0), ("LOT-02", 83.0, -256.5), ("LOT-03", -297.0, -66.5), ("LOT-04", 83.0, -125.5),
        ("LOT-05", 297.0, -123.5), ("LOT-06", -83.0, 66.5), ("LOT-07", 273.0, 123.5), ("LOT-08", -66.5, 297.0)]
BINS = [(-182.05, -136.5), (-95, -40), (12, -150), (140, -95), (-160, 60), (-30, 20), (60, 120), (170, 20),
        (-120, 180), (30, 240), (230, -230), (-250, 250)]


def junction_xz(row: int, col: int) -> tuple:
    return ((col - 1) * 95.0 - 380.0, (row - 1) * 95.0 - 380.0)


# Where the mock places an incident requested with INCIDENTS trigger (the real Twin chooses its own place).
TRIGGER_SPOTS = {"building_fire": (110.0, -300.0), "vehicle_fire": (60.0, -250.0), "water_main_break": (100.0, -60.0),
                 "lost_load": (6.0, -40.0), "breakdown": (6.0, -40.0), "rear_end": (101.0, 60.0),
                 "red_light_tbone": (-95.0, 0.0), "unsafe_overtake": (-190.0, 120.0), "signal_failure": (0.0, 10.0),
                 "garbage_collection": (-182.05, -136.5)}

INCIDENT_UNITS = {"building_fire": ["fire", "fire", "ambulance", "police"], "vehicle_fire": ["fire", "police"],
                  "rear_end": ["police", "tow"], "red_light_tbone": ["police", "ambulance", "tow"],
                  "unsafe_overtake": ["police", "ambulance", "tow"], "breakdown": ["tow"], "lost_load": ["police", "tow"],
                  "water_main_break": ["utility", "police"], "signal_failure": ["police"], "garbage_collection": ["garbage"]}
AUTO_RESOLVE = {"garbage_collection": 45.0, "signal_failure": 40.0, "lost_load": 50.0, "breakdown": 50.0, "building_fire": 50.0,
                "vehicle_fire": 45.0, "water_main_break": 60.0, "rear_end": 60.0, "red_light_tbone": 120.0, "unsafe_overtake": 90.0}


class Device:
    def __init__(self, id: str, family: str, kind: str, name: str, type: str, x: float, z: float,
                 metrics: Dict[str, tuple], state: Optional[dict] = None, commands: Optional[List[str]] = None):
        self.id, self.family, self.kind, self.name, self.type = id, family, kind, name, type
        self.x, self.z = float(x), float(z)
        self.zone = zone_of(x, z)
        self.metrics = metrics            # metric -> (unit, baseline function(t, dev) -> value)
        self.values: Dict[str, float] = {}
        self.state = state or {}
        self.commands = commands or []
        self.override: Dict[str, float] = {}
        self.ramps: Dict[str, tuple] = {}  # metric -> (start_value, target, t0, duration)
        self.stuck: Dict[str, float] = {}
        self.seed = random.Random(id).random() * 100

    def row(self) -> dict:
        r = {"id": self.id, "family": self.family, "kind": self.kind, "name": self.name, "type": self.type,
             "zone": self.zone, "location": {"x": round(self.x, 2), "y": 0.17, "z": round(self.z, 2)},
             "readings": {m: {"value": round(self.values.get(m, 0.0), 2), "unit": u} for m, (u, _) in self.metrics.items()}}
        public = {k: v for k, v in self.state.items() if not k.startswith("_")}
        if public:
            r["state"] = public
        return r


class MockCity:
    """Answers request(method, params) like the Godot device API. All times are wall-clock seconds x speed."""

    name = "mock"
    host = "in-process"
    port = "-"

    def __init__(self, speed: float = 1.0, seed: int = 14):
        self.speed = speed
        self.rng = random.Random(seed)
        self.t0 = time.monotonic()
        self.t = 0.0
        self.lock = threading.RLock()
        self.weather = "clear"
        self.minute_of_day = 9 * 60
        self.quakes: List[dict] = []
        self.incidents: Dict[str, dict] = {}
        self.resolved: List[dict] = []
        self.view: Dict[str, Any] = {"zones": [], "camera": "overview", "following": None, "focused": None, "watching": None}
        self.incident_kinds: Dict[str, str] = {}   # every incident id ever seen -> kind (the fleet reads it after resolution)
        self.commands_log: List[dict] = []
        self.down = False                    # scenario switch: the city stops answering (connection lost)
        self.refuse_commands = False         # scenario switch: actuators refuse commands
        self.auto_recharge = True            # UTILITY auto_recharge_on/off (Twin: refill every 300 s)
        self.recharge_every = 300.0
        self.next_recharge = 300.0
        self.lights = "auto"
        self._inc_seq = 9000
        self.devices: Dict[str, Device] = {}
        # vehicles driving the 9 x 9 road grid (95 m blocks), like the Twin's SUMO traffic
        self.cars = [{"id": f"veh{n:03d}", "class": "bus" if n % 15 == 0 else "truck" if n % 11 == 0 else "car",
                      "axis": n % 2, "line": -380 + 95 * (n % 9), "pos": (n * 53.0) % 760 - 380, "dir": 1 if n % 4 < 2 else -1,
                      "v": 7.0 + (n % 5)} for n in range(70)]
        self._build()
        self.fleet = Fleet(self)
        self.tick()

    # ------------------------------------------------------------------ the city
    def _n(self, d: Device, scale: float) -> float:
        return math.sin(self.t * 0.37 + d.seed) * scale * 0.6 + (self.rng.random() - 0.5) * scale * 0.4

    def _build(self) -> None:
        add = self._add
        rush = lambda: 1.0 + 0.35 * math.exp(-((self.minute_of_day - 8.5 * 60) / 50.0) ** 2) \
            + 0.35 * math.exp(-((self.minute_of_day - 17.5 * 60) / 60.0) ** 2)
        self.rush = rush
        for i, (x, z) in enumerate(TRAFFIC, 1):
            add(Device(f"TRAF-{i:02d}", "traffic", "sensor", f"TrafficSensor_{i:02d}", "Traffic radar detector", x, z, {
                "vehicle_count": ("vehicles", lambda t, d: max(0, 6 * self.rush() + self._n(d, 2.5))),
                "speed": ("km/h", lambda t, d: max(0, 34 - 6 * (self.rush() - 1) + self._n(d, 4))),
                "occupancy": ("%", lambda t, d: max(0, min(100, 24 * self.rush() + self._n(d, 6)))),
            }))
        for i, (x, z) in enumerate(AIR, 1):
            add(Device(f"AIR-{i:02d}", "air", "sensor", f"EnvironmentalSensor_{i:02d}", "Air quality monitor", x, z, {
                "pm2_5": ("µg/m³", lambda t, d: max(2, 6.0 * self.rush() + self._n(d, 1.6) - self._rain() * 0.35)),
                "pm10": ("µg/m³", lambda t, d: max(4, 13 * self.rush() + self._n(d, 2.5) - self._rain() * 0.45)),
                "nitrogen_dioxide": ("µg/m³", lambda t, d: max(2, 10 * self.rush() + self._n(d, 2))),
                "vehicle_smoke": ("%", lambda t, d: max(0, min(100, 4 + self._n(d, 2)))),
            }))
        for i, (x, z) in enumerate(UTIL, 1):
            add(Device(f"UTIL-{i:02d}", "utility", "sensor", f"InfrastructureSensor_{i:02d}", "Utility telemetry gateway", x, z, {
                "voltage": ("V", lambda t, d: (229 + self._n(d, 1.2)) if self._zone_powered(d.zone) else 0.0),
                "water_pressure": ("bar", lambda t, d: max(0, 4.1 + self._n(d, 0.1))),
                "cabinet_temperature": ("°C", lambda t, d: 27 + self._n(d, 0.8)),
            }))
        for i, (x, z) in enumerate(NOISE, 1):
            add(Device(f"NOISE-{i:02d}", "noise", "sensor", f"NoiseMonitor_{i:02d}", "Noise monitor", x, z, {
                "db_a": ("dBA", lambda t, d: 52 + 6 * (self.rush() - 1) + self._n(d, 3)),
                "peak_noise": ("dBA", lambda t, d: 63 + 6 * (self.rush() - 1) + abs(self._n(d, 4))),
                "traffic_noise": ("dBA", lambda t, d: 49 + self._n(d, 3)),
            }))
        for i, (x, z) in enumerate(WEATHER, 1):
            add(Device(f"WX-{i:02d}", "weather", "sensor", f"WeatherStation_{i:02d}", "Weather station", x, z, {
                "wind_speed": ("m/s", lambda t, d: (15.8 if self.weather == "storm" else 6.8 if self.weather == "rain" else 3.2) + abs(self._n(d, 1.5))),
                "wind_direction": ("°", lambda t, d: (210 + t * 0.4 + self._n(d, 10)) % 360),
                "rainfall": ("mm/h", lambda t, d: max(0, self._rain() + (self._n(d, 1.2) if self._rain() else 0))),
                "temperature": ("°C", lambda t, d: 23 + self._n(d, 0.4) - (2.5 if self._rain() else 0)),
            }))
        for i, (x, z) in enumerate(FLOOD, 1):
            add(Device(f"FLOOD-{i:02d}", "flood", "sensor", f"FloodLevelSensor_{i:02d}", "Flood level sensor", x, z, {
                "surface_water_depth": ("mm", lambda t, d: max(0, d.state.get("_depth", 0.0) + self._n(d, 0.15))),
                "rise_rate": ("mm/min", lambda t, d: d.state.get("_rise", 0.0)),
            }))
        for i, zone in enumerate(ZONES, 1):
            cx, cz = zone_centre(zone)
            add(Device(f"SEIS-{i:02d}", "seismic", "sensor", f"SeismicSensor_{zone}", "Seismic sensor", cx + 15, cz + 15, {
                "ground_acceleration": ("m/s²", lambda t, d: self._quake(d)[0]),
                "ground_velocity": ("mm/s", lambda t, d: self._quake(d)[1]),
                "shaking_intensity": ("MMI", lambda t, d: self._quake(d)[2]),
            }))
        for r in range(2, 9):
            for c in range(2, 9):
                x, z = junction_xz(r, c)
                add(Device(f"SIG-Junction_r{r}_c{c}", "signal", "actuator", f"TrafficSignal_r{r}_c{c}", "Traffic signal", x + 5.4, z - 5.4, {
                    "ns_signal": ("state", lambda t, d: self._signal(d, "ns")),
                    "ew_signal": ("state", lambda t, d: self._signal(d, "ew")),
                }, state={"mode": "cycle"}, commands=["ns_green", "ns_yellow", "all_red", "ew_green", "ew_yellow", "reset_cycle"]))
        for i, (x, z) in enumerate(VALVES, 1):
            add(Device(f"VLV-{i:02d}", "water_valve", "actuator", f"WaterValve_{i:02d}", "Water valve", x, z, {
                "valve_open": ("%", lambda t, d: 100.0 if d.state["state"] == "open" else 0.0)}, state={"state": "open"}, commands=["open", "close"]))
        for i, (x, z) in enumerate(SWITCHES, 1):
            add(Device(f"SWT-{i:02d}", "power_switch", "actuator", f"PowerSwitch_{i:02d}", "Power switch", x, z, {
                "switch_closed": ("%", lambda t, d: 100.0 if d.state["state"] == "closed" else 0.0),
                "powered": ("%", lambda t, d: 100.0 if d.state["state"] == "closed" else 0.0)}, state={"state": "closed"}, commands=["open", "close"]))
        for lot, x, z in LOTS:
            add(Device(lot, "parking", "actuator", f"Parking {lot}", "Smart car park", x, z, {
                "occupancy": ("cars", lambda t, d: d.state["_occ"]), "capacity": ("cars", lambda t, d: 12.0),
                "occupancy_percent": ("%", lambda t, d: d.state["_occ"] / 12 * 100),
                "denied_entries": ("cars", lambda t, d: d.state.get("_denied", 0.0)),
                "entered": ("cars", lambda t, d: 4.0), "left": ("cars", lambda t, d: 0.0),
            }, state={"barrier_mode": "auto", "gate_open": True, "warning": False, "_occ": 4.0}, commands=["auto", "open", "closed"]))
        for zone in ZONES:
            cx, cz = zone_centre(zone)
            add(Device(service_zone_id(zone), "service_zone", "actuator", service_zone_id(zone)[5:], "Service zone", cx, cz, {
                "power_supplied": ("%", lambda t, d: 0.0 if d.state["power_cut"] else 100.0),
                "water_supplied": ("%", lambda t, d: 0.0 if d.state["water_cut"] else 100.0),
                "zone_power": ("kW", lambda t, d: 0.0 if d.state["power_cut"] else (38 + 9 * (self.rush() - 1) + self._n(d, 1.5)) * d.state["_load"]),
                "zone_water": ("m³/h", lambda t, d: 0.0 if d.state["water_cut"] else 14 + self._n(d, 0.6)),
            }, state={"power_cut": False, "water_cut": False, "_load": 0.7 + (ZONES.index(zone) % 5) * 0.15},
                commands=["cut_power", "restore_power", "cut_water", "restore_water"]))
            add(Device(f"PWR-{ZONES.index(zone) + 1:02d}", "power_meter", "sensor", f"PowerMeter_{zone}", "Power meter", cx - 29, cz - 29, {
                "power_demand": ("kW", lambda t, d: self.devices[service_zone_id(d.zone)].values.get("zone_power", 0.0)),
                "supply_status": ("%", lambda t, d: 100.0 if self._zone_powered(d.zone) else 0.0)}))
        add(Device("GRID-01", "grid", "sensor", "Power grid", "Grid storage", -330, -330, {
            "energy_level": ("%", lambda t, d: d.state["_level"]),
            "energy_available": ("kWh", lambda t, d: d.state["_level"] * 48.0),
            "grid_demand": ("kW", lambda t, d: sum(dv.values.get("zone_power", 0.0) for dv in self.devices.values() if dv.family == "service_zone")),
        }, state={"_level": 72.0, "_drain": 0.0}))
        for i, (x, z) in enumerate(BINS, 1):
            add(Device(f"WGT-T{i:03d}", "waste", "sensor", f"Street_trash_{i}", "Bin Weight Sensor", x, z, {
                "bin_weight": ("kg", lambda t, d: d.state["_fill"] * 0.3),
                "fill_level": ("%", lambda t, d: d.state["_fill"]),
                "deposits": ("items", lambda t, d: 0.0)}, state={"_fill": 20 + (i * 7) % 40}, commands=["empty"]))

    def _add(self, d: Device) -> None:
        self.devices[d.id] = d

    def _rain(self) -> float:
        return {"storm": 12.5, "rain": 4.2}.get(self.weather, 0.0)

    def _zone_powered(self, zone: str) -> bool:
        z = self.devices.get(service_zone_id(zone))
        return not (z and z.state.get("power_cut"))

    def _signal(self, d: Device, axis: str) -> float:
        mode = d.state.get("mode", "cycle")
        if mode == "cycle":
            phase = int((self.t + d.seed) // 16) % 2
            return (2.0 if phase == 0 else 0.0) if axis == "ns" else (0.0 if phase == 0 else 2.0)
        table = {"ns_green": (2, 0), "ns_yellow": (1, 0), "all_red": (0, 0), "ew_green": (0, 2), "ew_yellow": (0, 1)}
        ns, ew = table.get(mode, (0, 0))
        return float(ns if axis == "ns" else ew)

    def _quake(self, d: Device) -> tuple:
        best = (0.002 + abs(self._n(d, 0.002)), 0.05 + abs(self._n(d, 0.04)), 1.0)
        for q in self.quakes:
            age = self.t - q["t"]
            if age < 0 or age > 25:
                continue
            cx, cz = zone_centre(q["zone"]) if q["zone"] != "ALL" else (d.x, d.z)
            dist = max(100.0, math.hypot(d.x - cx, d.z - cz))
            mmi = max(1.0, 1.5 * q["magnitude"] - 2.6 - 2.2 * math.log10(dist / 100.0)) * (1.0 if age < 15 else (25 - age) / 10)
            if mmi > best[2]:
                pga = round(0.003 * 10 ** (mmi / 3.0), 4)
                best = (pga, round(pga * 120, 3), round(max(1.0, mmi), 1))
        return best

    def tick(self) -> None:
        with self.lock:
            now = time.monotonic()
            dt = (now - self.t0) * self.speed - self.t
            self.t += max(0.0, dt)
            self.minute_of_day = int(9 * 60 + self.t / 60) % 1440
            for d in self.devices.values():
                if d.family == "flood":
                    gain = 1.6 if d.id == "FLOOD-02" else 1.0          # FLOOD-02 sits at the low point (zone J)
                    rate = (2.5 * gain if self.weather == "storm" else 0.6 * gain if self.weather == "rain" else -1.5) * max(dt, 0)
                    old = d.state.get("_depth", 0.0)
                    # only the low point floods; elsewhere the rain drains (puddles, at most 40 mm like the real Twin's
                    # surface water): a flood stays in its zone
                    new = min(400.0 if d.id == "FLOOD-02" else 40.0, max(0.0, old + rate))
                    d.state["_depth"] = new
                    d.state["_rise"] = round((new - old) / max(dt, 1e-6) * 60, 1) if dt > 0 else 0.0
                if d.family == "grid":
                    drain = d.state.get("_drain", 0.0)
                    if not self.auto_recharge:
                        drain += d.values.get("grid_demand", 0.0) * 0.0025          # % per city second at the current load
                    d.state["_level"] = max(0.0, min(100.0, d.state["_level"] - drain * max(dt, 0)))
                    if self.auto_recharge and self.t >= self.next_recharge:
                        d.state["_level"], self.next_recharge = 84.0, self.t + self.recharge_every
                if d.family == "parking":
                    target = 12.0 if d.state.get("_fill_up") else 4.0
                    if d.state["barrier_mode"] == "closed":
                        target = max(0.0, d.state["_occ"] - 0.02 * max(dt, 0))
                    d.state["_occ"] += (target - d.state["_occ"]) * min(1.0, 0.05 * max(dt, 0))
                for m, (unit, fn) in d.metrics.items():
                    if m in d.stuck:
                        d.values[m] = d.stuck[m]
                    elif m in d.override:
                        d.values[m] = d.override[m]
                    elif m in d.ramps:
                        v0, target, t0, dur = d.ramps[m]
                        f = 1.0 if dur <= 0 else max(0.0, min(1.0, (self.t - t0) / dur))
                        d.values[m] = v0 + (target - v0) * f
                    else:
                        d.values[m] = float(fn(self.t, d))
            for c in self.cars:
                jam = 0.35 if self.rush() > 1.2 else 1.0
                c["pos"] += c["dir"] * c["v"] * jam * max(dt, 0)
                c["pos"] = (c["pos"] + 380.0) % 760.0 - 380.0      # wraps at the city edge, whatever the step
            self.fleet.step(max(dt, 0.0), self.t)
            for iid, inc in list(self.incidents.items()):
                age = self.t - inc["created"]
                first = inc.get("_first_on_scene")
                inc["phase"] = "reported" if age < 3 else "on_scene" if first is not None else "responding"
                for r in inc["units"]:
                    u = self.fleet.units.get(r["unit"])
                    if u is not None and r.get("status") != "on_scene":
                        r["status"] = {"mobilizing": "dispatched", "inserting": "dispatched"}.get(u.status, u.status)
                if not inc.get("_resolve_at") and inc["kind"] in AUTO_RESOLVE:
                    # the work starts when the first unit arrives (units drive there: it takes time)
                    if first is not None:
                        inc["_resolve_at"] = max(inc["created"] + AUTO_RESOLVE[inc["kind"]], first + AUTO_RESOLVE[inc["kind"]] * 0.5)
                    elif age > AUTO_RESOLVE[inc["kind"]] * 3:
                        inc["_resolve_at"] = self.t
                if inc.get("_resolve_at") and self.t >= inc["_resolve_at"]:
                    inc["phase"] = "resolved"
                    self.resolved.insert(0, self._public(inc))
                    self.resolved = self.resolved[:10]
                    del self.incidents[iid]

    # ------------------------------------------------------------------ scenario hooks (mock only)
    def set_reading(self, device: str, metric: str, value: Optional[float]) -> None:
        d = self.devices[device]
        if value is None:
            d.override.pop(metric, None)
        else:
            d.override[metric] = float(value)

    def ramp(self, device: str, metric: str, to: float, over: float) -> None:
        d = self.devices[device]
        d.override.pop(metric, None)
        d.ramps[metric] = (d.values.get(metric, 0.0), float(to), self.t, float(over))

    def clear(self, device: Optional[str] = None) -> None:
        for d in ([self.devices[device]] if device else self.devices.values()):
            d.override.clear(); d.ramps.clear(); d.stuck.clear()

    def stick(self, device: str, metric: str) -> None:
        d = self.devices[device]
        d.stuck[metric] = d.values.get(metric, 0.0)

    def set_state(self, device: str, key: str, value: Any) -> None:
        self.devices[device].state[key] = value

    # ------------------------------------------------------------------ the device API
    def request(self, method: str, params: Optional[dict] = None, request_id: Any = None) -> Any:
        from citybrain.twin import TwinError           # same error type as the TCP client
        params = params or {}
        if self.down:
            raise TwinError("city_unreachable", "the mock Twin is down (scenario: connection lost)")
        with self.lock:
            handler = {
                "ping": lambda: {"pong": True},
                "info": lambda: {"view": dict(self.view), "weather": self.weather, "clock": self._clock(), "devices": len(self.devices), "speed": self.speed,
                                 "period": "rush" if self.rush() > 1.2 else "day", "paused": False,
                                 "traffic": {"simulated_vehicles": len(self.cars),
                                             "stopped_vehicles": sum(1 for c in self.cars if self.rush() > 1.2 and c["v"] < 8)},
                                 "earthquakes_active": len([q for q in self.quakes if self.t - q["t"] < 25])},
                "list_vehicles": lambda: self._vehicles(params),
                "read_all": lambda: self._read_all(params),
                "read": lambda: self._read(str(params.get("id", ""))),
                "list_devices": lambda: {"total": len(self.devices), "devices": [d.row() for d in self.devices.values()]},
                "list_incidents": lambda: {"incidents": [self._public(i) for i in self.incidents.values()],
                                           "resolved_recently": self.resolved[:5], "closures": [], "time_s": round(self.t, 1)},
                "list_emergency_units": lambda: {"count": len(self.fleet.units), "units": self.fleet.rows()},
                "list_parking_lots": lambda: {"count": 8, "lots": [self._lot(d) for d in self.devices.values() if d.family == "parking"]},
                "list_earthquakes": lambda: {"earthquakes": copy.deepcopy(self.quakes)},
                "command": lambda: self._command(str(params.get("id", "")), str(params.get("command", "")), params.get("value")),
            }.get(method)
            if handler is None:
                raise TwinError("unknown_method", f"unknown method '{method}'")
            result = handler()
            if isinstance(result, dict) and "error" in result and len(result) == 1:
                raise TwinError(result["error"]["code"], result["error"]["message"])
            return result

    def close(self) -> None:
        pass

    def _vehicles(self, params: dict) -> dict:
        rows = []
        for c in self.cars:
            x, z = (c["pos"], c["line"]) if c["axis"] == 0 else (c["line"], c["pos"])
            x, z = x + (3 if c["dir"] > 0 else -3) * (c["axis"] == 1), z + (3 if c["dir"] > 0 else -3) * (c["axis"] == 0)
            rows.append({"id": c["id"], "class": c["class"], "type": c["class"], "location": {"x": round(x, 2), "y": 0.0, "z": round(z, 2)},
                         "zone": zone_of(x, z), "speed_mps": round(c["v"] * (0.35 if self.rush() > 1.2 else 1.0), 2),
                         "heading_deg": (90 if c["dir"] > 0 else 270) if c["axis"] == 0 else (180 if c["dir"] > 0 else 0),
                         "road": "", "stopped": False, "stop_reason": "", "unit": "", "incident": ""})
        for u in self.fleet.units.values():
            if u.status in ("inserting", "en_route", "on_scene", "returning", "patrol", "entering_depot"):
                row = u.row()
                loc = row["location"]
                rows.append({"id": u.id, "class": "emergency" if u.kind in ("fire", "ambulance", "police") else u.kind,
                             "type": u.kind, "location": loc, "zone": zone_of(loc["x"], loc["z"]), "speed_mps": round(u.speed, 2),
                             "heading_deg": heading(*u.dir), "road": "", "stopped": u.speed < 0.1,
                             "stop_reason": "scene" if u.status == "on_scene" else ("signal" if u.speed < 0.1 else ""),
                             "unit": u.id, "incident": u.incident})
        limit = int(params.get("limit", 50))
        return {"total": len(rows), "offset": 0, "count": min(limit, len(rows)), "vehicles": rows[:limit], "simulated": len(rows), "time_s": round(self.t, 1)}

    def _clock(self) -> str:
        return f"{self.minute_of_day // 60:02d}:{self.minute_of_day % 60:02d}"

    def _sampled(self) -> dict:
        return {"frame": int(self.t * 60), "unix_msec": int(time.time() * 1000), "clock": self._clock()}

    def _read_all(self, params: dict) -> dict:
        fam, zone = str(params.get("family", "")), str(params.get("zone", "")).upper()
        rows = [d.row() for d in self.devices.values() if (not fam or d.family == fam) and (not zone or d.zone == zone)]
        return {"count": len(rows), "devices": rows, "sampled_at": self._sampled()}

    def _read(self, device: str) -> dict:
        d = self.devices.get(device)
        if d is None:
            return {"error": {"code": "unknown_device", "message": f"no device with id '{device}' (use list_devices to see ids)"}}
        return dict(d.row(), sampled_at=self._sampled())

    def _lot(self, d: Device) -> dict:
        occ = int(round(d.state["_occ"]))
        return {"id": d.id, "zone": d.zone, "capacity": 12, "occupied": occ, "free": 12 - occ, "barrier_mode": d.state["barrier_mode"],
                "gate_open": d.state["barrier_mode"] != "closed", "warning": occ >= 12, "location": {"x": d.x, "y": 0.17, "z": d.z},
                "denied_entries": int(d.state.get("_denied", 0)), "entered_total": 4, "left_total": 0}

    def _public(self, inc: dict) -> dict:
        return {k: copy.deepcopy(v) for k, v in inc.items() if not k.startswith("_")}

    def new_incident(self, kind: str, x: float, z: float, iid: Optional[str] = None, resolve_after: Optional[float] = None) -> str:
        with self.lock:
            self._inc_seq += 1
            iid = iid or f"INC-{self._inc_seq}"
            self.incident_kinds[iid] = kind
            self.incidents[iid] = {"id": iid, "kind": kind, "title": kind.replace("_", " ").capitalize(), "category": "incident",
                                   "phase": "reported", "severity": "severe" if kind in ("building_fire", "red_light_tbone") else "moderate",
                                   "created": self.t, "location": {"x": float(x), "z": float(z)}, "units": [], "timeline": [],
                                   "_resolve_at": (self.t + resolve_after) if resolve_after else None}
            for k in INCIDENT_UNITS.get(kind, ["police"]):
                u = self.fleet.dispatch(k, iid, float(x), float(z), self.t)     # from its station, along the roads
                self.incidents[iid]["units"].append({"unit": u.id, "role": "collection" if k == "garbage" else "responder",
                                                     "kind": k, "status": "dispatched"})
            return iid

    def resolve(self, iid: str) -> None:
        if iid in self.incidents:
            self.incidents[iid]["_resolve_at"] = self.t

    def _command(self, target: str, action: str, value: Any) -> dict:
        def fail(code, msg):
            return {"error": {"code": code, "message": msg}}
        if not target or not action:
            return fail("bad_request", "command needs an 'id' and a 'command'")
        self.commands_log.append({"t": round(self.t, 1), "id": target, "command": action, "value": value})
        if self.refuse_commands:
            return fail("unavailable", f"{target} did not answer (scenario: actuator failure)")
        if target == "WEATHER":
            if action != "set" or value not in ("clear", "cloudy", "rain", "fog", "storm", "auto"):
                return fail("bad_value", "WEATHER set needs one of: clear, cloudy, rain, fog, storm, auto")
            self.weather = "clear" if value == "auto" else value
            return {"id": target, "applied": f"weather -> {value}"}
        if target == "CLOCK":
            if action == "set_time":
                text = str(value)
                minute = int(text.split(":")[0]) * 60 + int(text.split(":")[1]) if ":" in text else int(float(text))
                self.t0 -= ((minute - self.minute_of_day) % 1440) * 60 / max(self.speed, 1e-6)
                self.tick()
                return {"id": target, "applied": f"set_time {value}"}
            if action in ("set_speed", "set_rate", "pause", "resume"):
                return {"id": target, "applied": f"{action} {value if value is not None else ''}".strip()}
            return fail("bad_command", "CLOCK accepts set_time, set_speed, set_rate, pause, resume")
        if target == "UTILITY":
            if action == "auto_recharge_off":
                self.auto_recharge = False
            elif action == "auto_recharge_on":
                self.auto_recharge, self.next_recharge = True, self.t + self.recharge_every
            elif action == "recharge_all":
                self.devices["GRID-01"].state["_level"] = 100.0
                self.devices["GRID-01"].state["_drain"] = 0.0
            elif action == "set_reserve":
                # as the Twin (sim_api.gd UTILITY set_reserve): the grid storage set to a percentage, the automatic
                # recharge postponed by a full interval so the low reserve lasts
                try:
                    level = float(value)
                except (TypeError, ValueError):
                    return fail("bad_value", "set_reserve needs a percentage 0-100")
                if not 0.0 <= level <= 100.0:
                    return fail("bad_value", "set_reserve needs a percentage 0-100")
                self.devices["GRID-01"].state["_level"] = level
                self.devices["GRID-01"].state["_drain"] = 0.0
                self.next_recharge = self.t + self.recharge_every
            else:
                return fail("bad_command", "UTILITY supports auto_recharge_on / auto_recharge_off / recharge_all / set_reserve")
            return {"id": target, "applied": action}
        if target == "SIGNALS":
            if action != "phase":
                return fail("bad_value", "SIGNALS phase needs a phase")
            for d in self.devices.values():
                if d.family == "signal":
                    d.state["mode"] = "cycle" if value == "reset_cycle" else str(value)
            return {"id": target, "applied": f"all signals -> {value} (sent to the SUMO controller)"}
        if target == "EARTHQUAKE":
            if action == "clear":
                self.quakes.clear()
                return {"id": target, "applied": "earthquake damage cleaned up"}
            spec = value if isinstance(value, dict) else {}
            q = {"id": f"EQ-{len(self.quakes) + 1}", "zone": str(spec.get("zone", "ALL")).upper(),
                 "magnitude": float(spec.get("magnitude", 6.0)), "t": self.t, "status": "active"}
            self.quakes.append(q)
            return {"id": target, "applied": f"earthquake {q['id']} triggered", "earthquake": q}
        if target == "INCIDENTS":
            if action == "trigger":
                kind = value if isinstance(value, str) else (value or {}).get("kind", "")
                spot = (value or {}) if isinstance(value, dict) else {}
                x, z = TRIGGER_SPOTS.get(kind, (6.0, -89.0))
                m = re.match(r"^Junction_r(\d+)_c(\d+)$", str(spot.get("junction", "")))
                if m:                                          # the place, as the Twin takes it (sim_api.gd)
                    x, z = (int(m.group(2)) - 1) * 95.0 - 380.0 + 6.0, (int(m.group(1)) - 1) * 95.0 - 380.0 + 6.0
                elif spot.get("zone") and str(spot["zone"]).upper() in "ABCDEFGHIJKLMNOP":
                    cx, cz = zone_centre(str(spot["zone"]).upper())
                    x, z = cx + 25.0, cz - 70.0                # a building in that zone, by a road
                x, z = float(spot.get("x", x)), float(spot.get("z", z))
                iid = self.new_incident(kind, x, z)
                return {"id": target, "applied": f"incident {kind} requested", "request": iid}
            if action == "dispatch":
                spec = value if isinstance(value, dict) else {}
                inc = self.incidents.get(str(spec.get("id", "")))
                if inc is None or spec.get("kind") not in ("police", "ambulance", "fire", "tow"):
                    return fail("bad_value", "dispatch needs {id, kind: police|ambulance|fire|tow}")
                u = self.fleet.dispatch(spec["kind"], inc["id"], inc["location"]["x"], inc["location"]["z"], self.t)
                inc["units"].append({"unit": u.id, "role": "extra", "kind": spec["kind"], "status": "dispatched"})
                return {"id": target, "applied": f"extra {spec['kind']} {u.id} dispatched to {inc['id']} from {u.depot}"}
            if action == "resolve":
                self.resolve(str(value))
                return {"id": target, "applied": f"resolve {value} requested"}
            if action == "send_unit":
                spec = value if isinstance(value, dict) else {}
                if spec.get("kind") not in ("police", "ambulance", "fire", "tow") or not spec.get("edge"):
                    return fail("bad_value", "send_unit needs {kind: police|ambulance|fire|tow, edge, pos, tag}")
                u = self.fleet.send_to(spec["kind"], str(spec["edge"]), float(spec.get("pos", 30.0)), str(spec.get("tag") or "manual"), self.t)
                if u is None:
                    return fail("bad_value", f"no edge '{spec['edge']}' in the road grid")
                return {"id": target, "applied": f"{spec['kind']} {u.id} sent to {spec['edge']}", "request": u.id}
            if action == "recall_unit":
                if not self.fleet.recall(str(value), self.t):
                    return fail("unknown_unit", f"no unit '{value}' sent with send_unit")
                return {"id": target, "applied": f"recall {value} requested"}
            if action == "watch":
                if str(value) not in self.incidents:
                    return fail("unknown_incident", f"no open incident '{value}'")
                self.view["watching"] = str(value)
                return {"id": target, "applied": f"watching {value}"}
            return fail("bad_command", "INCIDENTS accepts trigger, resolve_all, resolve, dispatch and watch")
        # the control room's view (presentation only, as in the Twin: twin/scripts/sim_api.gd)
        if target == "ZONES":
            if action in ("lockdown", "reopen"):
                # as the Twin (sim_api.gd ZONES lockdown/reopen): the zone's four entrances blocked / reopened
                spec = value if isinstance(value, dict) else {"zone": value}
                zone = str(spec.get("zone", "")).upper()
                if action == "lockdown" and zone not in "ABCDEFGHIJKLMNOP" or (zone and len(zone) != 1):
                    return fail("bad_value", "value: {zone: A..P, reason}")
                locked = set(self.view.get("lockdowns", []))
                locked = (locked | {zone}) if action == "lockdown" else (locked - {zone} if zone else set())
                self.view["lockdowns"] = sorted(locked)
                return {"id": target, "applied": f"zone {zone} {'closed: 4 entrances blocked' if action == 'lockdown' else 'reopened'}"}
            if action not in ("show", "hide"):
                return fail("bad_command", "ZONES accepts show and hide")
            letters = "ABCDEFGHIJKLMNOP"
            want = set(letters) if value == "ALL" else {str(z).upper() for z in (value if isinstance(value, list) else [value])}
            if not want <= set(letters):
                return fail("bad_value", "value: a zone letter A..P, a list of letters or \"ALL\"")
            self.view["zones"] = sorted((set(self.view["zones"]) | want) if action == "show" else (set(self.view["zones"]) - want))
            return {"id": target, "applied": f"{action} {value}", "ui": dict(self.view)}
        if target == "CAMERA":
            if action == "view":
                if str(value).lower() not in ("overview", "city", "street", "reset") and not str(value).upper().startswith("CAM-"):
                    return fail("bad_value", "view must be CAM-01..CAM-16, overview, street, reset or city")
                self.view.update(camera=str(value).lower(), following=None)
                return {"id": target, "applied": f"view {value}", "ui": dict(self.view)}
            if action == "follow":
                vid = str(value)
                known = {u.id for u in self.fleet.units.values()} | {c["id"] for c in self.cars}
                if vid not in ("person", "car") and vid not in known:
                    return fail("unknown_target", f"'{vid}' is not a citizen or vehicle that can be followed right now")
                self.view.update(camera="follow", following=vid)
                return {"id": target, "applied": f"follow {vid}", "ui": dict(self.view)}
            return fail("bad_command", "CAMERA does not accept '%s'" % action)
        if target == "FOCUS":
            if action != "device" or str(value) not in self.devices:
                return fail("unknown_device", f"no device with id '{value}'")
            self.view.update(camera="focus", following=None, focused=str(value))
            return {"id": target, "applied": f"focused device {value}", "ui": dict(self.view)}
        if target == "LIGHTS":
            if action not in ("on", "off", "auto", "dim"):
                return fail("bad_command", "LIGHTS accepts on, off, auto, dim")
            self.lights = f"dim {value}" if action == "dim" else action
            return {"id": target, "applied": f"256 street lights -> {self.lights}", "lights": 256}
        d = self.devices.get(target)
        if d is None:
            return fail("unknown_device", f"no device with id '{target}' (use list_devices to see ids)")
        if action not in d.commands:
            return fail("bad_command", f"{target} does not accept '{action}' (accepts: {', '.join(d.commands) or 'no commands: it is a read-only sensor'})")
        before = d.row()["readings"]
        if d.family == "signal":
            d.state["mode"] = "cycle" if action == "reset_cycle" else action
            applied = f"{target} set to {action} (sent to the SUMO controller)"
        elif d.family == "water_valve":
            d.state["state"] = "open" if action == "open" else "closed"
            applied = f"{d.name} {'opened' if action == 'open' else 'closed'}"
        elif d.family == "power_switch":
            d.state["state"] = "closed" if action == "close" else "open"
            applied = f"{d.name} {'closed' if action == 'close' else 'opened'}"
        elif d.family == "parking":
            d.state["barrier_mode"] = action
            d.state["gate_open"] = action != "closed"
            applied = f"{target} barrier set to {action}"
        elif d.family == "service_zone":
            kind = "power" if action.endswith("power") else "water"
            d.state[f"{kind}_cut"] = action.startswith("cut")
            applied = f"{kind} {'cut' if action.startswith('cut') else 'restored'} for {d.name}"
        elif d.family == "waste":
            removed = d.values.get("fill_level", d.state["_fill"]) * 0.3
            d.state["_fill"] = 3.0
            d.override.pop("fill_level", None); d.ramps.pop("fill_level", None)
            d.override.pop("bin_weight", None); d.ramps.pop("bin_weight", None)
            applied = f"{target} emptied ({removed:.2f} kg removed)"
        else:
            applied = f"{target} {action}"
        self.tick()
        return {"id": target, "applied": applied, "before": before, "after": d.row()["readings"]}


# ---------------------------------------------------------------------- optional TCP server (same wire format as Godot)
def serve(port: int, speed: float) -> None:
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
    from citybrain.twin import TwinError
    city = MockCity(speed=speed)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                if not raw.strip():
                    continue
                try:
                    req = json.loads(raw)
                    city.tick()
                    result = city.request(req.get("method", ""), req.get("params") or {})
                    out = {"id": req.get("id"), "ok": True, "result": result}
                except TwinError as e:
                    out = {"id": req.get("id") if isinstance(req, dict) else None, "ok": False, "error": {"code": e.code, "message": e.message}}
                except ValueError:
                    out = {"id": None, "ok": False, "error": {"code": "bad_request", "message": "request must be a JSON object"}}
                self.wfile.write((json.dumps(out) + "\n").encode())

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as srv:
        print(f"mock Twin device API on 127.0.0.1:{port} (same protocol as twin/scripts/sim_api.gd)")
        srv.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true", help="serve the Twin protocol over TCP")
    ap.add_argument("--port", type=int, default=8820)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    if a.serve:
        serve(a.port, a.speed)
    else:
        ap.print_help()

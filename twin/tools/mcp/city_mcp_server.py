#!/usr/bin/env python3
"""MCP server for the City Ground Zero simulation (sensors, actuators and infrastructure).

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0): initialize, tools/list, tools/call,
resources/list, resources/read, ping. It owns no simulation data: every tool call is forwarded to the running Godot
city (scripts/sim_api.gd) and answered from the live scene at that instant. Only the Python standard library is used.

    CITY_SIM_API_PORT=<port> python3 city_mcp_server.py
"""
from __future__ import annotations

import datetime
import json
import os
import socket
import queue
import re
import sys
import threading
import time
from typing import Any, Callable

SERVER_NAME = "city-ground-zero"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
MAX_WATCH_SECONDS = 120.0
TICK_S = 0.1          # one MCP tick: live subscriptions update every 100 ms

WEATHERS = ["clear", "cloudy", "rain", "fog", "storm", "auto"]
SIGNAL_PHASES = ["ns_green", "ns_yellow", "all_red", "ew_green", "ew_yellow", "reset_cycle"]
SOURCE = "city_ground_zero"
SUBSCRIBABLE = ["city_status", "read_sensor", "read_sensors", "read_all", "sensor_summary", "waste_bin_report", "read_zone", "read_vehicle", "read_citizen", "list_earthquakes", "read_parking_lot",
                "list_vehicles", "list_citizens", "list_incidents", "list_emergency_units", "list_parking_lots", "list_zones", "read_event_log", "read_ui_state", "read_city"]
ZONES = list("ABCDEFGHIJKLMNOP")
INCIDENT_KINDS = ["rear_end", "red_light_tbone", "unsafe_overtake", "breakdown", "vehicle_fire", "lost_load", "building_fire", "water_main_break", "signal_failure", "garbage_collection"]
FAMILIES = [
    "traffic", "camera", "air", "noise", "weather", "flood", "utility", "power_meter", "water_meter", "waste",
    "signal", "water_valve", "power_switch", "lighting", "irrigation", "reservoir", "grid", "service_zone",
]


DEFAULT_PORT = 8820


def port_file() -> str:
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), "city_ground_zero_api_port")


def resolve_port() -> int:
    """Where the running city listens: CITY_SIM_API_PORT, else the port file the city writes, else the default."""
    text = os.environ.get("CITY_SIM_API_PORT", "")
    if text.isdigit() and int(text) > 0:
        return int(text)
    try:
        with open(port_file()) as handle:
            value = handle.read().strip()
        if value.isdigit() and int(value) > 0:
            return int(value)
    except OSError:
        pass
    return DEFAULT_PORT


class SimError(Exception):
    """The simulation refused or could not answer a request."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SimClient:
    """Line-delimited JSON over one local TCP connection to the Godot city (reconnects on failure)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._next_id = 0
        self._lock = threading.RLock()

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._buffer = b""

    def _connect(self) -> None:
        if self._socket is not None:
            return
        port = self.port or resolve_port()       # looked up again on every connect: the city may start after this server
        try:
            self._socket = socket.create_connection((self.host, port), timeout=self.timeout)
        except OSError as error:
            self._socket = None
            raise SimError("city_unreachable", f"cannot reach the city on {self.host}:{port} ({error}). Start it with ./run_control_room.sh") from error
        self._socket.settimeout(self.timeout)
        self._buffer = b""

    def request(self, method: str, params: dict | None = None) -> Any:
        with self._lock:
            return self._request(method, params)

    def _request(self, method: str, params: dict | None = None) -> Any:
        for attempt in (0, 1):
            self._connect()
            assert self._socket is not None
            self._next_id += 1
            request_id = self._next_id
            payload = json.dumps({"id": request_id, "method": method, "params": params or {}}, separators=(",", ":")) + "\n"
            try:
                self._socket.sendall(payload.encode("utf-8"))
                response = self._read_response(request_id)
            except (OSError, ConnectionError):
                self.close()
                if attempt == 0:
                    continue
                raise SimError("city_unreachable", "the city closed the connection")
            if not response.get("ok"):
                error = response.get("error") or {}
                raise SimError(str(error.get("code", "error")), str(error.get("message", "request failed")))
            return response.get("result")
        raise SimError("city_unreachable", "the city is not answering")

    def _read_response(self, request_id: int) -> dict:
        assert self._socket is not None
        while True:
            while b"\n" not in self._buffer:
                chunk = self._socket.recv(65536)
                if not chunk:
                    raise ConnectionError("closed")
                self._buffer += chunk
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip():
                continue
            response = json.loads(line.decode("utf-8"))
            if response.get("id") == request_id:
                return response


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


_ID = {"type": "string", "description": "Device id, e.g. WGT-T001, AIR-01, SWT-01 (see list_sensors / list_actuators)"}
_FAMILY = {"type": "string", "enum": FAMILIES, "description": "Device family"}
_QUERY = {"type": "string", "description": "Case-insensitive text matched against id, name and type"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
_STREAM = {
    "once": {"type": "boolean", "description": "Return a single reading instead of streaming"},
    "duration_s": {"type": "number", "minimum": 0.5, "maximum": 86400, "description": "Stop streaming after this many seconds (default: stream until the call is cancelled)"},
    "interval_s": {"type": "number", "minimum": 0.1, "maximum": 60, "default": TICK_S, "description": "Seconds between live updates (default 0.1 = one 100 ms tick)"},
}
LIVE = " LIVE: when the client asks for progress it streams a live update every interval_s as progress notifications until the call is cancelled (or duration_s ends); the final result holds every sample. once=true returns a single reading."
_OFFSET = {"type": "integer", "minimum": 0, "default": 0}


class Tool:
    def __init__(self, name: str, description: str, schema: dict, handler: Callable[["Server", dict], Any], read_only: bool = True):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler
        self.read_only = read_only

    def describe(self) -> dict:
        return {
            "name": self.name, "description": self.description, "inputSchema": self.schema,
            "annotations": {"readOnlyHint": self.read_only, "openWorldHint": False},
        }


class Server:
    def __init__(self, sim: SimClient, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.sim = sim
        self.sleep = sleep
        self.clock = clock
        self.initialized = False
        self.protocol = SUPPORTED_PROTOCOLS[0]
        self.notify: Callable[[dict], None] = lambda message: None
        self.cancelled: set = set()
        self.subscriptions: set = set()
        self._last_seen: dict = {}
        self.tools = {tool.name: tool for tool in self._build_tools()}
        for name in SUBSCRIBABLE:       # every live data tool can be streamed, once-only, or limited by duration
            tool = self.tools.get(name)
            if tool is not None:
                for key, spec in _STREAM.items():
                    tool.schema["properties"].setdefault(key, spec)
                if "(LIVE:" not in tool.description and " LIVE:" not in tool.description:
                    tool.description += LIVE

    # ------------------------------------------------------------------ tools

    def _build_tools(self) -> list[Tool]:
        return [
            Tool("city_status", "Live city status: clock, weather, simulation speed, and how many devices exist per family."+LIVE+"", _schema(dict(_STREAM)),
                 lambda s, a: s.sim.request("info")),
            Tool("list_sensors", "List sensors (every device that reports readings) with their ids, family and metric names. Filter by family or text.",
                 _schema({"family": _FAMILY, "query": _QUERY, "limit": _LIMIT, "offset": _OFFSET}),
                 lambda s, a: s.sim.request("list_devices", {k: a[k] for k in ("family", "query", "limit", "offset") if k in a})),
            Tool("read_sensor", "Read one device's readings in real time, by id. Optionally restrict to some metrics."+LIVE+"",
                 _schema({"id": _ID, "metrics": {"type": "array", "items": {"type": "string"}, "description": "Metric names; default all"}, **_STREAM}, ["id"]),
                 lambda s, a: s.sim.request("read", {"id": a["id"], "metrics": a.get("metrics", [])})),
            Tool("read_all", "Every device's live readings in one answer (optionally one family or one zone): sensors and actuator telemetry, with zone, location and state."+LIVE,
                 _schema({"family": _FAMILY, "zone": {"type": "string", "enum": ZONES}, **_STREAM}),
                 lambda s, a: s.sim.request("read_all", {k: a[k] for k in ("family", "zone") if k in a})),
            Tool("read_city", "The whole city in one live answer: status, every device, vehicles, citizens, incidents, emergency units, car parks, earthquakes, event log and control-room state. One request per tick - what a dashboard streams."+LIVE,
                 _schema({**_STREAM}), lambda s, a: s.sim.request("snapshot")),
            Tool("read_sensors", "Read several devices at once (one consistent frame)."+LIVE+"",
                 _schema({"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100}, **_STREAM}, ["ids"]),
                 lambda s, a: s.sim.request("read_many", {"ids": a["ids"]})),
            Tool("watch_sensor", "Sample a device repeatedly for a few seconds and return the live time series (and stream progress notifications).",
                 _schema({"id": _ID, "metric": {"type": "string", "description": "Metric to follow; default the first one"},
                          "duration_s": {"type": "number", "minimum": 0.5, "maximum": MAX_WATCH_SECONDS, "default": 10},
                          "interval_s": {"type": "number", "minimum": 0.1, "maximum": 30, "default": TICK_S}}, ["id"]),
                 lambda s, a: s._watch(a)),
            Tool("list_zones", "The 16 city zones A to P (a 4 x 4 grid of 2 x 2 blocks): centre, neighbours and how many devices and buildings each holds.",
                 _schema({}), lambda s, a: s.sim.request("list_zones")),
            Tool("read_zone", "Everything about one zone: devices by family, sensor summary (min / mean / max), bins, buildings and collapsed buildings, earthquakes and parking lots in it."+LIVE,
                 _schema({"zone": {"type": "string", "enum": ZONES}, **_STREAM}, ["zone"]), lambda s, a: s.sim.request("read_zone", {"zone": a["zone"]})),
            Tool("show_zones", "Show or hide the zone tint and letter on the ground, for chosen zones or ALL (hidden by default).",
                 _schema({"zones": {"type": "array", "items": {"type": "string", "enum": ZONES + ["ALL"]}, "minItems": 1}, "visible": {"type": "boolean", "default": True}}, ["zones"]),
                 lambda s, a: s._command("ZONES", "show" if a.get("visible", True) else "hide", a["zones"]), read_only=False),
            Tool("list_buildings", "Buildings with their zone, location and state (standing, collapsing, collapsed).",
                 _schema({"zone": {"type": "string", "enum": ZONES}, "collapsed_only": {"type": "boolean"}, "limit": _LIMIT, "offset": _OFFSET}),
                 lambda s, a: s.sim.request("list_buildings", {k: a[k] for k in ("zone", "collapsed_only", "limit", "offset") if k in a})),
            Tool("list_vehicles", "Road vehicles right now (from SUMO): class, location, zone, speed, heading, road, stop reason.",
                 _schema({"class": {"type": "string", "enum": ["car", "taxi", "bus", "police", "ambulance", "fire", "tow", "utility"]}, "zone": {"type": "string", "enum": ZONES}, "limit": _LIMIT, "offset": _OFFSET}),
                 lambda s, a: s.sim.request("list_vehicles", {k: a[k] for k in ("class", "zone", "limit", "offset") if k in a})),
            Tool("read_vehicle", "One road vehicle (or emergency unit) by id."+LIVE,
                 _schema({"id": {"type": "string", "description": "Vehicle id or emergency unit id (see list_vehicles)"}, **_STREAM}, ["id"]), lambda s, a: s.sim.request("read_vehicle", {"id": a["id"]})),
            Tool("list_emergency_units", "Emergency fleet (police, ambulance, fire, tow, utility, sanitation trucks): status, depot, incident, location, speed.",
                 _schema({"kind": {"type": "string", "description": "police, ambulance, fire, tow, utility, garbage"}, "status": {"type": "string", "description": "garaged, en_route, on_scene, returning ..."}}),
                 lambda s, a: s.sim.request("list_emergency_units", {k: a[k] for k in ("kind", "status") if k in a})),
            Tool("list_citizens", "Citizens currently shown in the city: what each is doing, why, where, in which zone.",
                 _schema({"zone": {"type": "string", "enum": ZONES}, "role": {"type": "string"}, "limit": _LIMIT, "offset": _OFFSET}),
                 lambda s, a: s.sim.request("list_citizens", {k: a[k] for k in ("zone", "role", "limit", "offset") if k in a})),
            Tool("read_citizen", "One citizen by id (e.g. citizen_0001): live action and the society record (household, job, funds, agenda)."+LIVE,
                 _schema({"id": {"type": "string"}, **_STREAM}, ["id"]), lambda s, a: s.sim.request("read_citizen", {"id": a["id"]})),
            Tool("list_incidents", "Open and recently resolved incidents with phase, severity, location, participants, units and timeline, and the road closures.",
                 _schema({}), lambda s, a: s.sim.request("list_incidents")),
            Tool("trigger_incident", "Start an incident; units are dispatched and traffic reacts.",
                 _schema({"kind": {"type": "string", "enum": INCIDENT_KINDS}}, ["kind"]), lambda s, a: s._command("INCIDENTS", "trigger", a["kind"]), read_only=False),
            Tool("resolve_all_incidents", "Fast-forward every open incident through its normal clearance.", _schema({}),
                 lambda s, a: s._command("INCIDENTS", "resolve_all"), read_only=False),
            Tool("list_earthquakes", "Earthquakes so far: zone, magnitude, status, buildings planned and collapsed."+LIVE, _schema({**_STREAM}),
                 lambda s, a: s.sim.request("list_earthquakes")),
            Tool("trigger_earthquake", "Shake one zone (A to P) or the whole city (ALL). Seismic sensors record the waveform and buildings in the area collapse.",
                 _schema({"zone": {"type": "string", "enum": ZONES + ["ALL"]}, "magnitude": {"type": "number", "minimum": 4.0, "maximum": 8.0, "default": 6.0}}, ["zone"]),
                 lambda s, a: s._command("EARTHQUAKE", "trigger", {"zone": a["zone"], "magnitude": a.get("magnitude", 6.0)}), read_only=False),
            Tool("clear_earthquakes", "Remove every earthquake and instantly restore every collapsed building, as if nothing had happened.", _schema({}),
                 lambda s, a: s._command("EARTHQUAKE", "clear"), read_only=False),
            Tool("list_parking_lots", "Smart car parks: zone, occupancy (parked / capacity), barrier state, denied entries.",
                 _schema({"zone": {"type": "string", "enum": ZONES}}), lambda s, a: s.sim.request("list_parking_lots", {k: a[k] for k in ("zone",) if k in a})),
            Tool("read_parking_lot", "One car park by id (LOT-01 ...): occupancy, bays, barrier modes, cars parked and counters."+LIVE,
                 _schema({"id": _ID, **_STREAM}, ["id"]), lambda s, a: s.sim.request("read_parking_lot", {"id": a["id"]})),
            Tool("set_parking_barrier", "Set a car park's entry barrier: auto (opens for cars when there is room), closed (refuses everyone), open (admits everyone while there is room).",
                 _schema({"id": _ID, "mode": {"type": "string", "enum": ["auto", "closed", "open"]}}, ["id", "mode"]), lambda s, a: s._command(a["id"], a["mode"]), read_only=False),
            Tool("sensor_summary", "Min / mean / max of every metric, grouped by device family (optionally one family)."+LIVE+"",
                 _schema({"family": _FAMILY, **_STREAM}), lambda s, a: s.sim.request("summary", {"family": a.get("family", "")})),
            Tool("waste_bin_report", "Weight and fill of the city's waste bins (street cans WGT-T…, green containers WGT-C…), fullest first."+LIVE,
                 _schema({"min_fill_percent": {"type": "number", "minimum": 0, "maximum": 120, "default": 0}, "limit": _LIMIT, **_STREAM}),
                 lambda s, a: s._bin_report(a)),
            Tool("list_actuators", "List controllable devices (signals, valves, switches, street lights, irrigation, service zones) and the commands each accepts.",
                 _schema({"family": _FAMILY, "query": _QUERY, "limit": _LIMIT, "offset": _OFFSET}),
                 lambda s, a: s._list_actuators(a)),
            Tool("list_commands", "Commands accepted by one device, or by every global control (weather, clock, signals, utility) when no id is given.",
                 _schema({"id": {"type": "string", "description": "Device id or a global control: WEATHER, CLOCK, SIGNALS, UTILITY, CAMERA, HIGHLIGHT, LIGHTS, REPLAY, ZONES, EARTHQUAKE, INCIDENTS"}}),
                 lambda s, a: s.sim.request("list_commands", {"id": a.get("id", "")})),
            Tool("command_actuator", "Send a command to any actuator or global control. Returns the readings before and after.",
                 _schema({"id": _ID, "command": {"type": "string"}, "value": {"description": "Optional command value (number, string)"}}, ["id", "command"]),
                 lambda s, a: s._command(a["id"], a["command"], a.get("value")), read_only=False),
            Tool("set_weather", "Set the city weather.", _schema({"weather": {"type": "string", "enum": WEATHERS}}, ["weather"]),
                 lambda s, a: s._command("WEATHER", "set", a["weather"]), read_only=False),
            Tool("set_time_of_day", "Set the city clock to HH:MM (e.g. 22:30) or a minute of day.", _schema({"time": {"type": ["string", "integer"]}}, ["time"]),
                 lambda s, a: s._command("CLOCK", "set_time", a["time"]), read_only=False),
            Tool("set_simulation_speed", "Set simulation speed 1-8.", _schema({"speed": {"type": "number", "minimum": 1, "maximum": 8}}, ["speed"]),
                 lambda s, a: s._command("CLOCK", "set_speed", a["speed"]), read_only=False),
            Tool("pause_simulation", "Pause the SUMO traffic and city simulation.", _schema({}), lambda s, a: s._command("CLOCK", "pause"), read_only=False),
            Tool("resume_simulation", "Resume the simulation.", _schema({}), lambda s, a: s._command("CLOCK", "resume"), read_only=False),
            Tool("set_signal_phase", "Force one phase on every traffic signal (through the SUMO controller).",
                 _schema({"phase": {"type": "string", "enum": SIGNAL_PHASES}}, ["phase"]),
                 lambda s, a: s._command("SIGNALS", "phase", a["phase"]), read_only=False),
            Tool("set_signal", "Force one traffic signal (SIG-...) into a phase; the bridge applies it unless the junction is under incident police control.",
                 _schema({"id": _ID, "phase": {"type": "string", "enum": SIGNAL_PHASES}}, ["id", "phase"]),
                 lambda s, a: s._command(a["id"], a["phase"]), read_only=False),
            Tool("set_power_switch", "Open (isolate) or close (energise) a power switch cabinet, e.g. SWT-01.",
                 _schema({"id": _ID, "state": {"type": "string", "enum": ["open", "close"]}}, ["id", "state"]),
                 lambda s, a: s._command(a["id"], a["state"]), read_only=False),
            Tool("set_valve", "Open or close a water distribution valve (VLV-…) or park irrigation valve (IRR-…).",
                 _schema({"id": _ID, "state": {"type": "string", "enum": ["open", "close"]}}, ["id", "state"]),
                 lambda s, a: s._command(a["id"], a["state"]), read_only=False),
            Tool("set_street_light", "Control a smart street light (LGT-…): on, off, auto, or dim with a 0-1 level.",
                 _schema({"id": _ID, "mode": {"type": "string", "enum": ["on", "off", "auto", "dim"]}, "level": {"type": "number", "minimum": 0, "maximum": 1}}, ["id", "mode"]),
                 lambda s, a: s._command(a["id"], a["mode"], a.get("level")), read_only=False),
            Tool("set_service_zone", "Cut or restore power or water for a service zone (ZONE-…).",
                 _schema({"id": _ID, "service": {"type": "string", "enum": ["power", "water"]}, "state": {"type": "string", "enum": ["cut", "restore"]}}, ["id", "service", "state"]),
                 lambda s, a: s._command(a["id"], f"{a['state']}_{a['service']}"), read_only=False),
            Tool("set_clock_rate", "Set how many clock seconds pass per simulated second (the day/night pace).", _schema({"rate": {"type": "number", "minimum": 0.01}}, ["rate"]),
                 lambda s, a: s._command("CLOCK", "set_rate", a["rate"]), read_only=False),
            Tool("set_camera", "Switch the viewport: a street camera CAM-01..CAM-16, or overview, street, reset (the city camera positions).",
                 _schema({"view": {"type": "string", "description": "CAM-01 .. CAM-16, overview, street, reset or city"}}, ["view"]),
                 lambda s, a: s._command("CAMERA", "view", a["view"]), read_only=False),
            Tool("follow", "Make the viewport follow a random person or car, or a given citizen / vehicle id.",
                 _schema({"target": {"type": "string", "description": "person, car, or a citizen / vehicle id"}}, ["target"]),
                 lambda s, a: s._command("CAMERA", "follow", a["target"]), read_only=False),
            Tool("highlight_assets", "Frame one kind of city asset in the viewport (the panel's Asset Map), or clear the highlight with kind=clear.",
                 _schema({"kind": {"type": "string", "description": "e.g. water_network, power_network, traffic_signals, street_lights, hospitals ... or clear (see read_ui_state.highlight_kinds)"}}, ["kind"]),
                 lambda s, a: s._command("HIGHLIGHT", "clear") if a["kind"] == "clear" else s._command("HIGHLIGHT", "show", a["kind"]), read_only=False),
            Tool("focus_device", "Select a physical sensor or actuator in the Godot panel and frame its model in the simulation viewport.",
                 _schema({"id": _ID}, ["id"]), lambda s, a: s._command("FOCUS", "device", a["id"]), read_only=False),
            Tool("set_viewport_layout", "Make the simulation canvas fill the Godot window, or restore the control-room panels (the Ctrl+F action).",
                 _schema({"layout": {"type":"string", "enum":["full", "panel"]}}, ["layout"]),
                 lambda s, a: s._command("VIEWPORT", a["layout"]), read_only=False),
            Tool("recharge_utilities", "Refill every power and water reserve now.", _schema({}), lambda s, a: s._command("UTILITY", "recharge_all"), read_only=False),
            Tool("set_auto_recharge", "Turn the automatic utility recharge on or off.", _schema({"enabled": {"type": "boolean"}}, ["enabled"]),
                 lambda s, a: s._command("UTILITY", "auto_recharge_on" if a["enabled"] else "auto_recharge_off"), read_only=False),
            Tool("set_all_street_lights", "Control every smart street light at once: on, off, auto, or dim with a 0-1 level.",
                 _schema({"mode": {"type": "string", "enum": ["on", "off", "auto", "dim"]}, "level": {"type": "number", "minimum": 0, "maximum": 1}}, ["mode"]),
                 lambda s, a: s._command("LIGHTS", a["mode"], a.get("level")), read_only=False),
            Tool("replay_incident", "Replay an incident's recorded collision in the viewport (id from list_incidents), or stop the replay with stop=true.",
                 _schema({"id": {"type": "string"}, "stop": {"type": "boolean", "default": False}}),
                 lambda s, a: s._command("REPLAY", "stop") if a.get("stop") else s._command("REPLAY", "play", a.get("id", "")), read_only=False),
            Tool("control_incident", "Panel actions for one active incident: watch it in the viewport, resolve it, or dispatch an extra police, ambulance, fire or tow unit.",
                 _schema({"id": _ID, "action":{"type":"string", "enum":["watch", "resolve", "dispatch_extra"]}, "unit_kind":{"type":"string", "enum":["police", "ambulance", "fire", "tow"]}}, ["id", "action"]),
                 lambda s, a: s._command("INCIDENTS", "dispatch", {"id":a["id"], "kind":a.get("unit_kind", "")}) if a["action"] == "dispatch_extra" else s._command("INCIDENTS", a["action"], a["id"]), read_only=False),
            Tool("control_replay", "Control the collision replay deck: pause, resume, restart, seek, set speed, choose its camera, or stop.",
                 _schema({"action":{"type":"string", "enum":["pause", "resume", "restart", "seek", "speed", "camera", "stop"]}, "value":{"description":"Seconds for seek, 0.25-4 for speed, or orbit/chase/driver/street/overhead for camera"}}, ["action"]),
                 lambda s, a: s._command("REPLAY", a["action"], a.get("value")), read_only=False),
            Tool("read_event_log", "The control room's event log (newest first): every operator and system event."+LIVE,
                 _schema({"limit": _LIMIT, **_STREAM}), lambda s, a: s.sim.request("event_log", {"limit": a.get("limit", 60)})),
            Tool("read_ui_state", "What the control room is showing: active camera, followed target, highlighted asset kind, full-window view."+LIVE,
                 _schema({**_STREAM}), lambda s, a: s.sim.request("ui_state")),
            Tool("empty_waste_bin", "Send a crew to empty one waste bin (WGT-…): its weight returns to the liner residue.",
                 _schema({"id": _ID}, ["id"]), lambda s, a: s._command(a["id"], "empty"), read_only=False),
        ]

    def _command(self, target: str, command: str, value: Any = None) -> Any:
        params = {"id": target, "command": command}
        if value is not None:
            params["value"] = value
        return self.sim.request("command", params)

    def _list_actuators(self, args: dict) -> dict:
        params = {"limit": 500, "kind": "actuator"}
        for key in ("family", "query"):
            if key in args:
                params[key] = args[key]
        listing = self.sim.request("list_devices", params)
        devices = listing["devices"]
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 50))
        page = devices[offset: offset + limit]
        return {"total": listing["total"], "offset": offset, "count": len(page), "devices": page,
                "global_controls": self.sim.request("list_commands", {})["global"]}

    def _bin_report(self, args: dict) -> dict:
        listing = self.sim.request("list_devices", {"family": "waste", "limit": 500})
        ids = [device["id"] for device in listing["devices"]]
        readings = self.sim.request("read_many", {"ids": ids})["devices"]
        rows = []
        for device in readings:
            r = device["readings"]
            rows.append({"id": device["id"], "name": device["name"], "kind": "container" if device["id"].startswith("WGT-C") else "street can",
                         "weight_kg": r["bin_weight"]["value"], "fill_percent": r["fill_level"]["value"], "deposits": int(r["deposits"]["value"]),
                         "position": device["position"]})
        rows.sort(key=lambda row: -row["fill_percent"])
        minimum = float(args.get("min_fill_percent", 0))
        rows = [row for row in rows if row["fill_percent"] >= minimum]
        total = sum(row["weight_kg"] for row in rows)
        return {"bins": rows[: int(args.get("limit", 50))], "matching": len(rows), "total_weight_kg": round(total, 2)}

    def _watch(self, args: dict) -> dict:
        sensor_id = args["id"]
        duration = min(float(args.get("duration_s", 10)), MAX_WATCH_SECONDS)
        interval = max(float(args.get("interval_s", TICK_S)), 0.1)
        metric = args.get("metric")
        samples = []
        started = self.clock()
        token = args.get("_progress_token")
        while True:
            reading = self.sim.request("read", {"id": sensor_id, "metrics": [metric] if metric else []})
            readings = reading["readings"]
            chosen = metric or next(iter(readings))
            sample = {"t": round(self.clock() - started, 3), "clock": reading["sampled_at"]["clock"], "frame": reading["sampled_at"]["frame"],
                      "metric": chosen, "value": readings[chosen]["value"], "unit": readings[chosen]["unit"]}
            samples.append(sample)
            if token is not None:
                self.notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                    "progressToken": token, "progress": round(sample["t"], 3), "total": duration,
                    "message": f"{sensor_id} {chosen} = {sample['value']} {sample['unit']} at {sample['clock']}"}})
            if self.clock() - started + interval > duration:
                break
            self.sleep(interval)
        values = [s["value"] for s in samples]
        return {"id": sensor_id, "metric": samples[0]["metric"], "unit": samples[0]["unit"], "samples": samples,
                "min": min(values), "max": max(values), "change": round(values[-1] - values[0], 4)}

    def _subscribe(self, args: dict) -> dict:
        tool_name = args["tool"]
        inner_args = dict(args.get("arguments") or {})
        problem = _validate(inner_args, self.tools[tool_name].schema)
        if problem:
            raise SimError("invalid_arguments", f"{tool_name}: {problem}")
        interval = float(args.get("interval_s", TICK_S))
        token = args.get("_progress_token")
        request_id = args.get("_request_id")
        duration = float(args.get("duration_s", 0 if token is not None else 10))
        # The dashboard's permanent whole-city feed must keep the MCP cadence even when a heavily rendered Godot
        # frame takes longer than 100 ms. Sample the city on a worker, while the protocol publishes the latest
        # complete atomic snapshot on every MCP tick. Commands still go through the same SimClient lock, and the
        # next completed snapshot immediately reflects them.
        if tool_name == "read_city" and token is not None and duration == 0:
            return self._subscribe_latest(args, inner_args, interval, token, request_id)
        started = self.clock()
        updates = 0
        data: Any = None
        samples: list = []
        while True:
            data = self.tools[tool_name].handler(self, dict(inner_args))
            updates += 1
            samples.append({"t": round(self.clock() - started, 3), "data": data})
            del samples[:-600]                  # keep the latest 600 samples (one minute at 100 ms)
            if token is not None:
                self.notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                    "progressToken": token, "progress": updates, "message": json.dumps({"tool": tool_name, "t": round(self.clock() - started, 3), "data": data}, separators=(",", ":"), ensure_ascii=False)}})
            waited = 0.0
            while waited < interval:
                if request_id in self.cancelled:
                    self.cancelled.discard(request_id)
                    return {"tool": tool_name, "updates": updates, "cancelled": True, "samples": samples, "last": data}
                step = min(0.05, interval - waited)
                self.sleep(step)
                waited += step
            if duration and self.clock() - started >= duration:
                return {"tool": tool_name, "updates": updates, "cancelled": False, "samples": samples, "last": data}

    def _subscribe_latest(self, args: dict, inner_args: dict, interval: float, token: Any, request_id: Any) -> dict:
        tool_name = args["tool"]
        state: dict[str, Any] = {"data": None, "error": None, "samples": 0}
        stopped = threading.Event()
        sampler = SimClient(self.sim.host, self.sim.port, self.sim.timeout)

        def sample() -> None:
            while not stopped.is_set():
                try:
                    state["data"] = sampler.request("snapshot")
                    state["samples"] += 1
                    state["error"] = None
                    time.sleep(min(0.02, interval))
                except SimError as error:
                    state["error"] = error
                    time.sleep(min(0.25, interval))

        threading.Thread(target=sample, daemon=True).start()
        started = self.clock()
        updates = 0
        try:
            while state["data"] is None:
                if request_id in self.cancelled:
                    self.cancelled.discard(request_id)
                    return {"tool": tool_name, "updates": 0, "samples_taken": 0, "cancelled": True, "last": None}
                if state["error"] is not None:
                    raise state["error"]
                self.sleep(min(0.05, interval))
            deadline = self.clock()
            while True:
                data = state["data"]
                updates += 1
                self.notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                    "progressToken": token, "progress": updates,
                    "message": json.dumps({"tool": tool_name, "t": round(self.clock() - started, 3), "data": data}, separators=(",", ":"), ensure_ascii=False)}})
                deadline += interval
                while self.clock() < deadline:
                    if request_id in self.cancelled:
                        self.cancelled.discard(request_id)
                        return {"tool": tool_name, "updates": updates, "samples_taken": state["samples"], "cancelled": True, "last": state["data"]}
                    self.sleep(min(0.02, deadline - self.clock()))
        finally:
            stopped.set()
            sampler.close()

    # -------------------------------------------------------------- protocol

    def handle(self, message: dict) -> dict | None:
        """One JSON-RPC message in, the response (or None for notifications) out."""
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if method is None:
            return None   # a response to a request we never send
        if message_id is None:
            if method == "notifications/initialized":
                self.initialized = True
            elif method == "notifications/cancelled":
                self.cancelled.add(params.get("requestId"))
            return None
        if method == "tools/call":
            params = dict(params, _request_id=message_id)
        try:
            result = self._dispatch(str(method), params)
        except SimError as error:
            return self._result(message_id, self._error_result(str(method), error.code, error.message))
        except KeyError as error:
            return self._error(message_id, -32602, f"Missing parameter {error}")
        except _MethodNotFound:
            return self._error(message_id, -32601, f"Method not found: {method}")
        except _InvalidParams as error:
            return self._error(message_id, -32602, str(error))
        return self._result(message_id, result)

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            requested = str(params.get("protocolVersion", SUPPORTED_PROTOCOLS[0]))
            self.protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            return {
                "protocolVersion": self.protocol,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": True, "listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Live control of the City Ground Zero simulation. Start with city_status, list_sensors and list_actuators; "
                                "every reading is taken from the running city at the moment of the call.",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool.describe() for tool in self.tools.values()]}
        if method == "tools/call":
            return self._call_tool(params)
        if method == "resources/list":
            return {"resources": [
                {"uri": "city://status", "name": "City status", "mimeType": "application/json", "description": "Clock, weather and device counts"},
                {"uri": "city://sensors", "name": "All sensors", "mimeType": "application/json", "description": "First 100 sensors with live readings"},
            ]}
        if method == "resources/templates/list":
            return {"resourceTemplates": [{"uriTemplate": "city://sensor/{id}", "name": "Sensor reading", "mimeType": "application/json",
                                           "description": "Live readings of one device by id"}]}
        if method == "resources/read":
            return self._read_resource(str(params["uri"]))
        if method == "resources/subscribe":
            uri = str(params["uri"])
            self._last_seen[uri] = self._digest(uri)           # unknown uri -> error now; only later changes are pushed
            self.subscriptions.add(uri)
            return {}
        if method == "resources/unsubscribe":
            self.subscriptions.discard(str(params["uri"]))
            self._last_seen.pop(str(params["uri"]), None)
            return {}
        if method in ("prompts/list",):
            return {"prompts": []}
        raise _MethodNotFound()

    def _call_tool(self, params: dict) -> dict:
        name = str(params.get("name", ""))
        if name not in self.tools:
            raise _InvalidParams(f"Unknown tool: {name}")
        arguments = dict(params.get("arguments") or {})
        problem = _validate(arguments, self.tools[name].schema)
        if problem:
            return self._error_result(name, "invalid_arguments", f"invalid arguments for {name}: {problem}")
        meta = params.get("_meta") or {}
        if "progressToken" in meta:
            arguments["_progress_token"] = meta["progressToken"]
        arguments["_request_id"] = params.get("_request_id")
        try:
            if name in SUBSCRIBABLE:
                # Data tools are live: with a progress token (the client can show a stream) or a duration they stream by
                # themselves; once=true, or no way to watch a stream, gives one reading.
                once = bool(arguments.get("once", False))
                inner = {k: v for k, v in arguments.items() if k not in ("once", "duration_s", "interval_s") and not k.startswith("_")}
                token = arguments.get("_progress_token")
                if not once and (token is not None or "duration_s" in arguments):
                    data = self._subscribe({"tool": name, "arguments": inner, "interval_s": arguments.get("interval_s", TICK_S),
                                            "duration_s": arguments.get("duration_s", 0 if token is not None else 10),
                                            "_progress_token": token, "_request_id": arguments["_request_id"]})
                else:
                    data = self.tools[name].handler(self, inner)
            else:
                data = self.tools[name].handler(self, arguments)
        except SimError as error:
            return self._error_result(name, error.code, error.message)
        return self._structured(name, data)

    def _digest(self, uri: str) -> str:
        text = self._read_resource(uri)["contents"][0]["text"]
        digest = json.dumps(json.loads(text), sort_keys=True)
        digest = re.sub(r'"(frame|unix_msec|requests_served)": \d+', "", digest)      # ignore the per-sample counters
        digest = re.sub(r'"timestamp": "[^"]*"', "", digest)
        return re.sub(r'"clock": "[^"]*"', "", digest)

    def push_updates(self) -> None:
        """notifications/resources/updated for every subscribed resource whose live value changed (called every tick)."""
        for uri in list(self.subscriptions):
            try:
                digest = self._digest(uri)
            except (SimError, _InvalidParams):
                continue
            if self._last_seen.get(uri) != digest:
                self._last_seen[uri] = digest
                self.notify({"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": uri}})

    def _read_resource(self, uri: str) -> dict:
        if uri == "city://status":
            data = self.sim.request("info")
        elif uri == "city://sensors":
            data = self.sim.request("list_devices", {"limit": 100})
        elif uri.startswith("city://sensor/"):
            data = self.sim.request("read", {"id": uri.removeprefix("city://sensor/")})
        else:
            raise _InvalidParams(f"Unknown resource: {uri}")
        enveloped = {**self.envelope("resources/read"), "uri": uri, **(data if isinstance(data, dict) else {"result": data})}
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(enveloped, indent=2)}]}

    @staticmethod
    def envelope(tool: str) -> dict:
        return {"source": SOURCE, "tool": tool, "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}

    def _structured(self, name: str, data: Any) -> dict:
        """Every result is one JSON object: source, tool, timestamp, then the data. The same object is the text content and the structured content."""
        body = data if isinstance(data, dict) else {"result": data}
        out = {**self.envelope(name), **body}
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}], "structuredContent": out, "isError": False}

    def _error_result(self, name: str, code: str, message: str) -> dict:
        out = {**self.envelope(name), "error": {"code": code, "message": message}}
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}], "structuredContent": out, "isError": True}

    @staticmethod
    def _tool_error(text: str) -> dict:
        return {"content": [{"type": "text", "text": json.dumps({"source": SOURCE, "error": {"code": "error", "message": text}})}], "structuredContent": {"source": SOURCE, "error": {"code": "error", "message": text}}, "isError": True}

    @staticmethod
    def _result(message_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    @staticmethod
    def _error(message_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


class _MethodNotFound(Exception):
    pass


class _InvalidParams(Exception):
    pass


def _validate(arguments: dict, schema: dict) -> str:
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in arguments:
            return f"missing required argument '{key}'"
    for key, value in arguments.items():
        if key not in properties:
            return f"unknown argument '{key}' (expected: {', '.join(properties) or 'none'})"
        spec = properties[key]
        kind = spec.get("type")
        kinds = kind if isinstance(kind, list) else [kind] if kind else []
        if kinds and not any(_is_type(value, k) for k in kinds):
            return f"'{key}' must be {' or '.join(kinds)}"
        if "enum" in spec and value not in spec["enum"]:
            return f"'{key}' must be one of {', '.join(map(str, spec['enum']))}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                return f"'{key}' must be >= {spec['minimum']}"
            if "maximum" in spec and value > spec["maximum"]:
                return f"'{key}' must be <= {spec['maximum']}"
    return ""


def _is_type(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def serve(stdin=sys.stdin, stdout=sys.stdout, sim: SimClient | None = None) -> int:
    sim = sim or SimClient()
    server = Server(sim)
    write_lock = threading.Lock()

    def send(message: Any) -> None:
        with write_lock:
            stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
            stdout.flush()

    server.notify = send
    inbox: queue.Queue = queue.Queue()

    def read_input() -> None:
        # Cancellations are applied the moment they arrive, even while a long-running (streaming) call is executing.
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if isinstance(message, dict) and message.get("method") == "notifications/cancelled":
                server.cancelled.add((message.get("params") or {}).get("requestId"))
                continue
            inbox.put(message)
        inbox.put(None)

    def push_loop() -> None:
        while True:
            time.sleep(TICK_S)
            if server.subscriptions:
                try:
                    server.push_updates()
                except Exception:      # a push failure must never stop the server
                    pass

    def respond(request: dict) -> None:
        response = server.handle(request)
        if response is not None:
            send(response)

    threading.Thread(target=read_input, daemon=True).start()
    threading.Thread(target=push_loop, daemon=True).start()
    while True:
        message = inbox.get()
        if message is None:
            break
        if isinstance(message, dict) and message.get("method") == "tools/call":
            # Calls run side by side, so a long live stream never holds up the next request on the same connection.
            threading.Thread(target=respond, args=(message,), daemon=True).start()
            continue
        if isinstance(message, list):
            responses = [r for r in (server.handle(m) for m in message if isinstance(m, dict)) if r is not None]
            if responses:
                send(responses)
            continue
        response = server.handle(message)
        if response is not None:
            send(response)
    return 0


if __name__ == "__main__":
    sys.exit(serve())

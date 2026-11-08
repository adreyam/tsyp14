"""
The Twin's own protocol, and the one normaliser that turns Twin answers into Brain events.

The Godot Twin exposes its device API as line-delimited JSON over TCP
(twin/scripts/sim_api.gd):  {"id", "method", "params"}  ->  {"id", "ok", "result"} | {"id", "ok": false, "error"}.

`TwinClient` is that request/answer pair. Two implementations exist:
    TcpTwinClient            the real city (live)
    mocks.twin_standin.MockCity   a stand-in that answers the same methods with the same shapes (mock)

Both go through `Normaliser`, so live and mock events are built by the very same code.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .contracts import (Action, Event, Incident, Location, Origin, Reading, explain_validation, new_id, zone_of)

FAMILY_BY_PREFIX = {
    "TRAF": "traffic", "CAM": "camera", "AIR": "air", "NOISE": "noise", "WX": "weather", "FLOOD": "flood",
    "UTIL": "utility", "PWR": "power_meter", "WTR": "water_meter", "WGT": "waste",
    "SIG": "signal", "VLV": "water_valve", "SWT": "power_switch", "LGT": "lighting", "IRR": "irrigation",
    "RSV": "reservoir", "GRID": "grid", "ZONE": "service_zone", "SEIS": "seismic", "LOT": "parking",
}
ACTUATOR_FAMILIES = {"signal", "water_valve", "power_switch", "lighting", "irrigation", "service_zone", "parking"}
BLOCK_M = 95.0


def family_of(device_id: str) -> str:
    return FAMILY_BY_PREFIX.get(device_id.split("-", 1)[0], "other")


def junction_xz(row: int, col: int) -> tuple:
    """SUMO Junction_r{row}_c{col} -> Twin x, z (twin/scripts/build_sumo_city.py: 95 m blocks, origin at -380)."""
    return ((col - 1) * BLOCK_M - 380.0, (row - 1) * BLOCK_M - 380.0)


_JUNCTION = re.compile(r"Junction_r(\d+)_c(\d+)")
_EDGE = re.compile(r"E_r(\d+)c(\d+)_r(\d+)c(\d+)")


def xz_from_twin_location(loc: Dict[str, Any]) -> Optional[tuple]:
    """Incident locations carry x/z once known, else only a junction or an edge name: place them on the grid."""
    if not isinstance(loc, dict):
        return None
    if isinstance(loc.get("x"), (int, float)) and isinstance(loc.get("z"), (int, float)):
        return (float(loc["x"]), float(loc["z"]))
    m = _JUNCTION.search(str(loc.get("junction", "")))
    if m:
        return junction_xz(int(m.group(1)), int(m.group(2)))
    m = _EDGE.search(str(loc.get("edge", "")))
    if m:
        a = junction_xz(int(m.group(1)), int(m.group(2)))
        b = junction_xz(int(m.group(3)), int(m.group(4)))
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return None


class TwinError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class TwinClient:
    """request(method, params) -> result, or raises TwinError."""

    name = "twin"

    def request(self, method: str, params: Optional[dict] = None, request_id: Any = None) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        pass


class TcpTwinClient(TwinClient):
    """One local TCP connection to the Godot city; reconnects on failure (same behaviour as the Twin's MCP server)."""

    name = "live"

    def __init__(self, host: str, port_resolver, timeout: float = 3.0):
        self.host = host
        self.port_resolver = port_resolver
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._lock = threading.RLock()
        self._next = 0
        self.port = None

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock, self._buf = None, b""

    def _connect(self) -> None:
        if self._sock is not None:
            return
        self.port = self.port_resolver()
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            self._sock = None
            raise TwinError("city_unreachable",
                            f"cannot reach the Twin on {self.host}:{self.port} ({e}). Start it with twin/run_control_room.sh")
        self._sock.settimeout(self.timeout)
        self._buf = b""

    def request(self, method: str, params: Optional[dict] = None, request_id: Any = None) -> Any:
        with self._lock:
            for attempt in (0, 1):
                self._connect()
                self._next += 1
                rid = request_id if request_id is not None else self._next
                line = json.dumps({"id": rid, "method": method, "params": params or {}}, separators=(",", ":")) + "\n"
                try:
                    self._sock.sendall(line.encode("utf-8"))
                    return self._read(rid)
                except (OSError, ConnectionError) as e:
                    self.close()
                    if attempt == 1:
                        raise TwinError("city_unreachable", f"the Twin connection dropped ({e})")
            raise TwinError("city_unreachable", "no answer")

    def _read(self, rid: Any) -> Any:
        deadline = time.monotonic() + self.timeout
        while True:
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                if not raw.strip():
                    continue
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise TwinError("bad_answer", "the Twin sent a line that is not JSON")
                if not isinstance(msg, dict):
                    raise TwinError("bad_answer", "the Twin answer is not a JSON object")
                if msg.get("id") != rid:
                    continue
                if msg.get("ok"):
                    return msg.get("result")
                err = msg.get("error") or {}
                raise TwinError(str(err.get("code", "error")), str(err.get("message", "the Twin refused the request")))
            if time.monotonic() > deadline:
                raise TwinError("timeout", f"no answer from the Twin within {self.timeout:.1f} s")
            chunk = self._sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("the Twin closed the connection")
            self._buf += chunk


def _ts_from_sampled(sampled: Optional[dict]) -> datetime:
    try:
        return datetime.fromtimestamp(int(sampled["unix_msec"]) / 1000.0, tz=timezone.utc)
    except (TypeError, KeyError, ValueError):
        return datetime.now(timezone.utc)


class Normaliser:
    """Twin rows -> validated Events. Keeps a deadband so a calm city does not flood the Brain."""

    def __init__(self, source: str, families: List[str], deadband: Dict[str, float], heartbeat_s: float):
        self.source = source
        self.families = set(families) if families else None
        self.deadband = deadband or {"default": 0.5}
        self.heartbeat_s = heartbeat_s
        self._last: Dict[str, tuple] = {}            # device -> (readings, monotonic time)
        self._incidents: Dict[str, str] = {}         # incident id -> phase
        self.rejected: List[str] = []                # last validation problems (shown on /v1/status)
        self.scenario_id: Optional[str] = None

    def reset(self) -> None:
        self._last.clear()
        self._incidents.clear()

    def _changed(self, device: str, readings: Dict[str, float]) -> bool:
        prev = self._last.get(device)
        now = time.monotonic()
        if prev is None or now - prev[1] >= self.heartbeat_s:
            return True
        old = prev[0]
        for m, v in readings.items():
            band = self.deadband.get(m, self.deadband.get("default", 0.5))
            if m not in old or abs(v - old[m]) >= band:
                return True
        return False

    def rows(self, result: dict) -> List[Event]:
        out: List[Event] = []
        if not isinstance(result, dict):
            self._reject("read_all: answer is not an object")
            return out
        rows = result.get("devices")
        if not isinstance(rows, list):
            self._reject("read_all: 'devices' is missing or not a list")
            return out
        ts = _ts_from_sampled(result.get("sampled_at"))
        clock = str((result.get("sampled_at") or {}).get("clock", "")) or None
        for row in rows:
            ev = self.row(row, ts, clock)
            if ev is not None:
                out.append(ev)
        return out

    def row(self, row: Any, ts: datetime, clock: Optional[str]) -> Optional[Event]:
        if not isinstance(row, dict) or "id" not in row:
            self._reject("read_all: a device row without 'id'")
            return None
        device = str(row["id"])
        family = str(row.get("family") or family_of(device))
        if self.families is not None and family not in self.families:
            return None
        values: Dict[str, float] = {}
        readings: Dict[str, Reading] = {}
        for metric, r in (row.get("readings") or {}).items():
            if isinstance(r, dict) and isinstance(r.get("value"), (int, float)):
                values[metric] = float(r["value"])
                readings[metric] = Reading(value=float(r["value"]), unit=str(r.get("unit", ""))[:16])
        if not readings or not self._changed(device, values):
            return None
        loc = row.get("location") or {}
        try:
            location = Location(zone=str(row.get("zone") or zone_of(loc.get("x", 0), loc.get("z", 0))),
                                x=float(loc.get("x", 0.0)), z=float(loc.get("z", 0.0)))
            state = row.get("state") if isinstance(row.get("state"), dict) else {}
            ev = Event(source=self.source, type="reading", ts=ts, twin_clock=clock, scenario_id=self.scenario_id,
                       origin=Origin(layer="twin", device_id=device, family=family,
                                     kind="actuator" if family in ACTUATOR_FAMILIES else "sensor"),
                       location=location, readings=readings, state={k: v for k, v in state.items() if k != "legend"})
        except (ValidationError, TypeError, ValueError) as e:
            self._reject(explain_validation(e, f"twin.{device}") if isinstance(e, ValidationError) else f"twin.{device}: {e}")
            return None
        self._last[device] = (values, time.monotonic())
        return ev

    def incidents(self, result: dict) -> List[Event]:
        out: List[Event] = []
        if not isinstance(result, dict):
            return out
        live = list(result.get("incidents") or []) + [dict(i, phase="resolved") for i in (result.get("resolved_recently") or [])]
        for inc in live:
            if not isinstance(inc, dict) or not inc.get("id"):
                continue
            iid, phase = str(inc["id"]), str(inc.get("phase") or "active")
            if self._incidents.get(iid) == phase:
                continue
            if iid not in self._incidents and phase == "resolved":
                self._incidents[iid] = phase
                continue
            self._incidents[iid] = phase
            xz = xz_from_twin_location(inc.get("location") or {})
            try:
                ev = Event(source=self.source, type="incident", scenario_id=self.scenario_id,
                           origin=Origin(layer="twin", device_id="INCIDENTS", family="incident", kind="system"),
                           location=Location.at(*xz) if xz else None,
                           incident=Incident(id=iid[:48], kind=str(inc.get("kind", "unknown"))[:40],
                                             status="resolved" if phase == "resolved" else "active",
                                             phase=phase[:48], title=str(inc.get("title", ""))[:120] or None),
                           state={"severity": inc.get("severity"),
                                  "units": [u.get("kind") for u in inc.get("units", []) if isinstance(u, dict)]})
            except ValidationError as e:
                self._reject(explain_validation(e, f"twin.incident.{iid}"))
                continue
            out.append(ev)
        return out

    def feedback(self, action: Action, answer: Optional[dict], error: Optional[TwinError]) -> Event:
        """The Twin's answer to a command, as an event on the same trace (closes the timeline)."""
        ok = error is None
        state = {"ok": ok, "action_id": action.action_id, "command": action.target.command,
                 "applied": (answer or {}).get("applied") if ok else None,
                 "error": None if ok else {"code": error.code, "message": error.message}}
        return Event(source=self.source, type="actuator_feedback", trace_id=action.trace_id,
                     scenario_id=self.scenario_id,
                     origin=Origin(layer="twin", device_id=action.target.actuator_id,
                                   family=family_of(action.target.actuator_id) if "-" in action.target.actuator_id else "system",
                                   kind="actuator" if "-" in action.target.actuator_id else "system"),
                     location=action.location, state=state,
                     readings={"applied": Reading(value=1.0 if ok else 0.0, unit="bool")})

    def _reject(self, msg: str) -> None:
        self.rejected = (self.rejected + [msg])[-20:]

"""
TwinSource: the single input/output interface between the Brain and the city.

    LiveTwinSource   the Godot Twin over its TCP device API (twin/run_control_room.sh)
    MockTwinSource   brain/mocks/twin_standin.MockCity, which answers the same methods with the same shapes

Both are a `ProtocolTwinSource` around a different `TwinClient`, so they emit exactly the same events
(built by twin.Normaliser) and send actions in exactly the same way (Action.twin_request()).
The orchestrator only ever sees this interface.
"""
from __future__ import annotations

import time
from typing import List, Optional

from .config import Config
from .contracts import Action, Event, iso, utc_now
from .twin import Normaliser, TcpTwinClient, TwinClient, TwinError


class TwinSource:
    name: str = "?"                 # "live" or "mock"

    def poll(self) -> List[Event]:
        raise NotImplementedError

    def send(self, action: Action) -> Event:
        raise NotImplementedError

    def devices(self) -> List[dict]:
        """Device registry (id, family, kind, zone, location) for the map."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Light view of the city for the map: open incidents, units, parking lots."""
        return {}

    def status(self) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ProtocolTwinSource(TwinSource):
    def __init__(self, cfg: Config, client: TwinClient, name: str):
        self.cfg = cfg
        self.client = client
        self.name = name
        self.norm = Normaliser(name, cfg.twin.families, cfg.twin.deadband, cfg.twin.heartbeat_s)
        self._last_poll = 0.0
        self._last_inc = 0.0
        self._connected: Optional[bool] = None
        self._down_since: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_ok: Optional[str] = None
        self._registry: List[dict] = []
        self._registry_at = 0.0
        self._snapshot: dict = {}
        self.state_changes: List[dict] = []    # read by the orchestrator: connection up/down notices
        # Test Book fault injection on the Brain's side of the link (CB14, CB15): works on the live Twin too.
        self.link_cut_until = 0.0
        self.actuator_fault = False

    # -------------------------------------------------------------- connection state
    def _ok(self) -> None:
        if self._connected is not True:
            self.state_changes.append({"connected": True, "message": f"{self.name} Twin connected"})
        self._connected, self._down_since, self._last_ok = True, None, iso(utc_now())

    def _fail(self, err: TwinError) -> None:
        self._last_error = err.message
        if self._connected is not False:
            self._down_since = time.monotonic()
            self.state_changes.append({"connected": False, "message": f"{self.name} Twin unreachable: {err.message}"})
        self._connected = False

    def down_for(self) -> float:
        return 0.0 if self._down_since is None else time.monotonic() - self._down_since

    # -------------------------------------------------------------- interface
    def poll(self) -> List[Event]:
        now = time.monotonic()
        out: List[Event] = []
        if now < self.link_cut_until:
            if now - self._last_poll >= self.cfg.twin.poll_s:
                self._last_poll = now
                self._fail(TwinError("city_unreachable", "link to the Twin cut (Test Book fault injection on the Brain side)"))
            return out
        if now - self._last_poll >= self.cfg.twin.poll_s:
            self._last_poll = now
            try:
                result = self.client.request("read_all", {})
                self._ok()
                out += self.norm.rows(result)
                # the whole city, every poll: what the dashboard map and the domain pages draw (live readings and states)
                self._registry = [
                    {"id": r.get("id"), "family": r.get("family"), "kind": r.get("kind"), "name": r.get("name"),
                     "zone": r.get("zone"), "location": r.get("location"), "state": r.get("state"),
                     "readings": {m: v.get("value") for m, v in (r.get("readings") or {}).items() if isinstance(v, dict)}}
                    for r in (result or {}).get("devices", []) if isinstance(r, dict)]
                self._registry_at = now
                self._snapshot["sampled_at"] = (result or {}).get("sampled_at")
                self._poll_city()
            except TwinError as e:
                self._fail(e)
                return out
        if now - self._last_inc >= self.cfg.twin.incidents_poll_s:
            self._last_inc = now
            try:
                inc = self.client.request("list_incidents", {})
                out += self.norm.incidents(inc)
                self._snapshot["incidents"] = (inc or {}).get("incidents", [])
                for method, key, field in (("list_parking_lots", "parking", "lots"),):
                    try:
                        self._snapshot[key] = (self.client.request(method, {}) or {}).get(field, [])
                    except TwinError:
                        pass
            except TwinError as e:
                if e.code == "city_unreachable":
                    self._fail(e)
        return out

    def _poll_city(self) -> None:
        """Moving parts of the simulation for the map: every vehicle (cars, buses, trucks, emergency units) and the
        city clock, weather and traffic summary. A failure here never stops the Brain."""
        now = time.monotonic()
        try:
            v = self.client.request("list_vehicles", {"limit": 500}) or {}
            self._snapshot["vehicles"] = [
                {"id": x.get("id"), "class": x.get("class"), "location": x.get("location"), "zone": x.get("zone"),
                 "speed_mps": x.get("speed_mps"), "heading_deg": x.get("heading_deg"), "stopped": x.get("stopped"),
                 "unit": x.get("unit"), "incident": x.get("incident")} for x in v.get("vehicles", []) if isinstance(x, dict)]
            self._snapshot["vehicles_total"] = v.get("total", len(self._snapshot["vehicles"]))
        except TwinError:
            pass
        try:                                   # the fleet every poll: green waves follow the units as they drive
            self._snapshot["units"] = (self.client.request("list_emergency_units", {}) or {}).get("units", [])
        except TwinError:
            pass
        if now - getattr(self, "_info_at", 0.0) >= 3.0:
            self._info_at = now
            try:
                info = self.client.request("info", {}) or {}
                self._snapshot["info"] = {k: info.get(k) for k in ("clock", "time", "day", "weather", "period", "paused",
                                                                    "speed", "traffic", "earthquakes_active")}
            except TwinError:
                pass

    def send(self, action: Action) -> Event:
        req = action.twin_request()
        if self.actuator_fault:
            return self.norm.feedback(action, None, TwinError("unavailable", f"{action.target.actuator_id} did not answer "
                                                              "(actuator channel fault injected by the Test Book)"))
        try:
            answer = self.client.request(req["method"], req["params"], request_id=req["id"])
            self._ok()
            return self.norm.feedback(action, answer if isinstance(answer, dict) else {"applied": str(answer)}, None)
        except TwinError as e:
            if e.code == "city_unreachable":
                self._fail(e)
            return self.norm.feedback(action, None, e)

    def devices(self) -> List[dict]:
        return self._registry

    def snapshot(self) -> dict:
        return self._snapshot

    def status(self) -> dict:
        return {"source": self.name, "connected": bool(self._connected),
                "last_ok": self._last_ok, "last_error": self._last_error,
                "down_for_s": round(self.down_for(), 1),
                "endpoint": f"{getattr(self.client, 'host', 'in-process')}:{getattr(self.client, 'port', '-')}",
                "rejected_messages": self.norm.rejected[-5:]}

    def close(self) -> None:
        self.client.close()


class LiveTwinSource(ProtocolTwinSource):
    def __init__(self, cfg: Config):
        client = TcpTwinClient(cfg.twin.host, cfg.twin.resolve_port, cfg.twin.timeout_s)
        super().__init__(cfg, client, "live")


class MockTwinSource(ProtocolTwinSource):
    """The Twin stand-in (brain/mocks/twin_standin.py) behind the same protocol. Scenarios drive it."""

    def __init__(self, cfg: Config, city=None):
        from mocks.twin_standin import MockCity      # brain/mocks (on sys.path, see run_brain.py)
        self.city = city or MockCity()
        super().__init__(cfg, self.city, "mock")

    def poll(self) -> List[Event]:
        self.city.tick()
        return super().poll()


def make_source(cfg: Config, kind: Optional[str] = None) -> TwinSource:
    kind = kind or cfg.source
    if kind == "live":
        return LiveTwinSource(cfg)
    if kind == "mock":
        return MockTwinSource(cfg)
    raise ValueError(f"unknown source '{kind}' (use live or mock)")

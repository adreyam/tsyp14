"""A tiny stand-in for the Godot device API (same line-delimited JSON protocol) used by the MCP unit tests."""
from __future__ import annotations

import json
import socket
import threading


class FakeCity:
    def __init__(self):
        self.bins = {"WGT-T001": 4.0, "WGT-T002": 20.0, "WGT-C001": 120.0}
        self.switch_closed = True
        self.weather = "clear"
        self.requests: list[dict] = []
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(4)
        self.port = self._server.getsockname()[1]
        self._running = True
        self.connections: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._running = False
        try:
            self._server.close()
        except OSError:
            pass

    def drop_connections(self):
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass
        self.connections.clear()

    def _accept(self):
        while self._running:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            self.connections.append(connection)
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        buffer = b""
        while True:
            try:
                chunk = connection.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                request = json.loads(line)
                self.requests.append(request)
                connection.sendall((json.dumps(self.handle(request)) + "\n").encode())

    # ---- model
    def device(self, device_id):
        if device_id in self.bins:
            kg = self.bins[device_id]
            cap = 240.0 if device_id.startswith("WGT-C") else 30.0
            return {"id": device_id, "kind": "sensor", "family": "waste", "name": device_id, "type": "Bin Weight Sensor", "position": [1, 0, 2],
                    "metrics": ["bin_weight", "fill_level", "deposits"], "commands": ["empty"],
                    "readings": {"bin_weight": {"value": kg, "unit": "kg"}, "fill_level": {"value": round(kg / cap * 100, 1), "unit": "%"}, "deposits": {"value": 0.0, "unit": "items"}}}
        if device_id == "SWT-01":
            return {"id": device_id, "kind": "actuator", "family": "power_switch", "name": "PowerSwitch_01", "type": "Power switch", "position": [0, 0, 0],
                    "metrics": ["switch_closed"], "commands": ["open", "close"],
                    "readings": {"switch_closed": {"value": 100.0 if self.switch_closed else 0.0, "unit": "%"}}}
        if device_id == "AIR-01":
            return {"id": device_id, "kind": "sensor", "family": "air", "name": "AirQualityMonitor_01", "type": "Air quality", "position": [0, 0, 0],
                    "metrics": ["pm2_5"], "commands": [], "readings": {"pm2_5": {"value": 7.5, "unit": "µg/m³"}}}
        return None

    def all_devices(self):
        return [self.device(i) for i in list(self.bins) + ["SWT-01", "AIR-01"]]

    def handle(self, request):
        method, params, rid = request["method"], request.get("params", {}), request.get("id")
        ok = lambda result: {"id": rid, "ok": True, "result": result}
        err = lambda code, message: {"id": rid, "ok": False, "error": {"code": code, "message": message}}
        if method == "info":
            return ok({"name": "Fake City", "frame": 1, "device_count": 5, "families": {"waste": 3, "power_switch": 1, "air": 1}, "clock": "09:00:00", "time": "09:00", "day": 1,
                       "weather": self.weather, "period": "Morning", "paused": False, "speed": 1.0, "clock_rate": 1.0, "requests_served": len(self.requests)})
        if method == "list_devices":
            devices = [d for d in self.all_devices() if (not params.get("family") or d["family"] == params["family"])
                       and (not params.get("kind") or d["kind"] == params["kind"])]
            slim = [{k: v for k, v in d.items() if k != "readings"} for d in devices]
            return ok({"total": len(slim), "offset": 0, "count": len(slim), "devices": slim})
        if method == "read":
            device = self.device(params.get("id", ""))
            if device is None:
                return err("unknown_device", f"no device with id '{params.get('id')}'")
            device = dict(device)
            device["sampled_at"] = {"frame": len(self.requests), "unix_msec": 0, "clock": "09:00:00"}
            metrics = params.get("metrics") or []
            if metrics:
                device["readings"] = {m: device["readings"][m] for m in metrics}
            return ok(device)
        if method == "read_many":
            found = [self.device(i) for i in params["ids"] if self.device(i)]
            return ok({"devices": [dict(d, sampled_at={"frame": 1, "unix_msec": 0, "clock": "09:00:00"}) for d in found], "unknown_ids": [i for i in params["ids"] if not self.device(i)]})
        if method == "read_all":
            rows = [dict(d, zone="A", location={"x": 1.0, "y": 0.0, "z": 2.0}) for d in self.all_devices()]
            return ok({"count": len(rows), "devices": rows, "sampled_at": {"frame": len(self.requests), "unix_msec": 0, "clock": "09:00:00"}})
        if method == "summary":
            return ok({"families": {"air": {"pm2_5": {"devices": 1, "min": 7.5, "max": 7.5, "mean": 7.5, "unit": "µg/m³"}}}})
        if method == "list_commands":
            return ok({"global": {"WEATHER": {"set": "value"}}, "by_family": {"power_switch": {"open": "o", "close": "c"}}})
        if method == "snapshot":
            return ok({"status": self.handle({"method": "info", "id": 1})["result"], "devices": self.handle({"method": "read_all", "id": 1})["result"], "vehicles": {"total": 0, "vehicles": []},
                       "citizens": {"citizens": []}, "incidents": {"incidents": []}, "units": {"units": []}, "parking": {"lots": [], "parked": 0, "capacity": 0},
                       "earthquakes": {"earthquakes": [], "active": 0}, "events": self.handle({"method": "event_log", "id": 1})["result"], "ui": self.handle({"method": "ui_state", "id": 1})["result"]})
        if method == "event_log":
            return ok({"count": 2, "events": ["09:00:01  Test event two", "09:00:00  Test event one"]})
        if method == "ui_state":
            return ok({"camera_status": "City overview camera active", "highlight": "", "highlight_kinds": ["water_network", "power_network", "hospitals"], "city_camera_active": True, "focus_view": False,
                       "replay": {"playing": False, "paused": False, "speed": 1.0, "time": 0.0, "duration": 0.0, "camera_mode": "orbit"}})
        if method == "command":
            target, command = params["id"], params["command"]
            if target in ("CAMERA", "HIGHLIGHT", "FOCUS", "VIEWPORT", "LIGHTS", "REPLAY", "UTILITY", "CLOCK", "INCIDENTS"):
                return ok({"id": target, "applied": f"{command} {params.get('value', '')}".strip()})
            if target == "WEATHER":
                self.weather = params["value"]
                return ok({"id": target, "applied": f"weather -> {self.weather}"})
            device = self.device(target)
            if device is None:
                return err("unknown_device", f"no device with id '{target}'")
            if command not in device["commands"]:
                return err("bad_command", f"{target} does not accept '{command}'")
            before = device["readings"]
            if target == "SWT-01":
                self.switch_closed = command == "close"
            if command == "empty":
                self.bins[target] = 0.2
            return ok({"id": target, "applied": f"{target} {command}", "before": before, "after": self.device(target)["readings"]})
        return err("unknown_method", method)

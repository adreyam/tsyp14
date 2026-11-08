"""
The Twin team's city (City Ground Zero) seen through its MCP server.

Reading: one call to waste_bin_report per cycle gives every bin's weight, fill
and position; read_all(family="waste") gives the zone of each bin (asked once,
then again only for bins not seen before). Each row is turned into the Brain's
own get_sensor_reading message, so the rest of the Brain is unchanged.

Acting: the Twin has no tool yet to send a refuse truck to one bin. The only
per-bin action is empty_waste_bin(id) ("send a crew"), so that is the order
the Brain gives. TO AGREE with the Twin team: a truck dispatch tool.

Conversions (TO CONFIRM with the Twin team)
- capacity_kg = weight_kg / fill_percent * 100 when the fill is high enough to
  be precise, else the last value learned for that bin, else a default per kind.
- position {x, y, z} in metres (Godot: x east, z south, y up) is turned into
  lat/lon around a reference point from the config, because the Brain's model
  stores positions as lat/lon. Only distances matter, not the real place.
"""
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .mcp_client import McpError, McpStdioClient

M_PER_DEG_LAT = 111_320.0
# ug/m3 -> ppb at 25 C and 1 atm (molar volume 24.45 L): ppb = ug/m3 * 24.45 / M
UGM3_PER_PPB = {"o3": 48.00 / 24.45, "no2": 46.01 / 24.45}
METRIC_ALIASES = {"pm25": "pm25", "pm2p5": "pm25", "pm2": "pm25", "pm25ugm3": "pm25",
                  "o3": "o3", "ozone": "o3", "o3ppb": "o3",
                  "no2": "no2", "nitrogendioxide": "no2", "no2ppb": "no2"}


def _metric_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _find_list(data, keys=("lots", "parking_lots", "devices", "items")) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


class McpTwinClient:
    def __init__(self, mcp: McpStdioClient, origin_lat: float = 36.80, origin_lon: float = 10.18,
                 default_capacity_kg: Optional[Dict[str, float]] = None,
                 zone_types: Optional[Dict[str, str]] = None, max_bins: int = 500,
                 focus_on_order: bool = False):
        self.mcp = mcp
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.default_capacity_kg = default_capacity_kg or {"WGT-T": 25.0, "WGT-C": 120.0}
        self.zone_types = zone_types or {}
        self.max_bins = max_bins
        self.focus_on_order = focus_on_order      # demo: frame the bin in the Godot viewport
        self.zones: Dict[str, str] = {}
        self.capacity: Dict[str, float] = {}
        self.warnings: List[str] = []
        self._zones_loaded = False

    # ---- conversions ---------------------------------------------------------
    def to_latlon(self, position) -> Tuple[float, float]:
        x = z = None
        if isinstance(position, dict):
            x, z = position.get("x"), position.get("z", position.get("y"))
        elif isinstance(position, (list, tuple)) and len(position) >= 2:
            x, z = position[0], position[2] if len(position) >= 3 else position[1]
        if not isinstance(x, (int, float)) or not isinstance(z, (int, float)):
            raise ValueError(f"unrecognised position {position!r}")
        lat = self.origin_lat - z / M_PER_DEG_LAT          # z grows southwards
        lon = self.origin_lon + x / (M_PER_DEG_LAT * math.cos(math.radians(self.origin_lat)))
        return round(lat, 7), round(lon, 7)

    def _capacity(self, bin_id: str, weight: float, fill: float) -> float:
        if fill >= 5.0 and weight > 0:                     # precise enough to learn from
            self.capacity[bin_id] = round(weight / fill * 100.0, 2)
        if bin_id in self.capacity:
            return self.capacity[bin_id]
        for prefix, kg in self.default_capacity_kg.items():
            if bin_id.startswith(prefix):
                return kg
        return max(self.default_capacity_kg.values())

    def _load_zones(self) -> None:
        try:
            data = self.mcp.call_tool("read_all", {"family": "waste", "once": True})
        except McpError as e:
            self.warnings.append(f"zones unavailable: {e}")
            return
        for dev in data.get("devices", []) if isinstance(data, dict) else []:
            if isinstance(dev, dict) and dev.get("id") and dev.get("zone"):
                self.zones[str(dev["id"])] = str(dev["zone"])
        self._zones_loaded = True

    # ---- what the Brain service calls ------------------------------------------
    def fetch_readings(self) -> List[dict]:
        """One reading per bin, in the Brain's get_sensor_reading format. Rows the
        Twin sends incomplete are passed on as they are, so the Brain's own
        validation rejects and logs them with the faulty field named."""
        report = self.mcp.call_tool("waste_bin_report", {"once": True, "limit": self.max_bins})
        stamp = report.get("timestamp") or datetime.now(timezone.utc).isoformat()
        rows = report.get("bins", [])
        if not self._zones_loaded or any(r.get("id") not in self.zones for r in rows
                                         if isinstance(r, dict)):
            self._load_zones()
        out = []
        for r in rows:
            if not isinstance(r, dict):
                out.append(r)
                continue
            sid = r.get("id")
            msg = {"tool": "get_sensor_reading", "sensor_id": sid, "type": "bin_weight",
                   "weight_kg": r.get("weight_kg"), "timestamp": stamp,
                   "zone": self.zones.get(sid, "unknown")}
            zt = self.zone_types.get(msg["zone"])
            if zt:
                msg["zone_type"] = zt
            try:
                weight, fill = float(r["weight_kg"]), float(r["fill_percent"])
                msg["capacity_kg"] = self._capacity(sid, weight, fill)
                lat, lon = self.to_latlon(r.get("position"))
                msg["location"] = {"lat": lat, "lon": lon}
            except (KeyError, TypeError, ValueError) as e:
                msg["conversion_error"] = str(e)           # location missing -> rejected, logged
            out.append(msg)
        return out

    def send_collection(self, bin_id: str) -> dict:
        result = self.mcp.call_tool("empty_waste_bin", {"id": bin_id})
        if self.focus_on_order:                     # best effort, never blocks the order
            try:
                self.mcp.call_tool("focus_device", {"id": bin_id})
            except McpError as e:
                self.warnings.append(f"focus_device {bin_id}: {e}")
        return result

    # ---- air-quality axis ---------------------------------------------------------
    def fetch_air_readings(self) -> List[dict]:
        """Every AIR- monitor as a get_sensor_reading, type=air_quality message.
        Metric names are matched loosely (pm2_5, PM2.5, pm25...) and O3 / NO2 given in
        ug/m3 are converted to ppb. TO CONFIRM with the Twin team: names and units."""
        data = self.mcp.call_tool("read_all", {"family": "air", "once": True})
        stamp = data.get("timestamp") if isinstance(data, dict) else None
        stamp = stamp or datetime.now(timezone.utc).isoformat()
        out = []
        for dev in _find_list(data):
            if not isinstance(dev, dict) or not dev.get("id"):
                continue
            msg = {"tool": "get_sensor_reading", "sensor_id": dev["id"], "type": "air_quality",
                   "zone": dev.get("zone", "unknown"), "timestamp": stamp}
            for name, reading in (dev.get("readings") or {}).items():
                key = METRIC_ALIASES.get(_metric_key(name))
                if not key or not isinstance(reading, dict):
                    continue
                value, unit = reading.get("value"), str(reading.get("unit", "")).lower()
                if isinstance(value, (int, float)) and key in UGM3_PER_PPB and \
                        ("ug" in unit or "\u00b5g" in unit):
                    value = round(value / UGM3_PER_PPB[key], 2)
                msg[key] = value
            try:
                lat, lon = self.to_latlon(dev.get("position"))
                msg["location"] = {"lat": lat, "lon": lon}
            except ValueError:
                pass                              # location is optional for air readings
            out.append(msg)                       # missing metrics -> rejected, field named
        return out

    def list_parking_lots(self) -> Dict[str, str]:
        data = self.mcp.call_tool("list_parking_lots", {})
        return {str(l["id"]): str(l["zone"]) for l in _find_list(data)
                if isinstance(l, dict) and "id" in l and "zone" in l}

    def set_parking_barrier(self, lot_id: str, mode: str) -> dict:
        return self.mcp.call_tool("set_parking_barrier", {"id": lot_id, "mode": mode})

    def send_dispatch(self, message: dict) -> None:
        raise McpError("the Twin has no truck dispatch tool yet (TO AGREE); "
                       "use decision mode 'crew'")

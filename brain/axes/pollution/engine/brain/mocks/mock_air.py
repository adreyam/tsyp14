"""
Stand-in for the Twin's air-quality side (R1): a small deterministic city with
air monitors (PM2.5 in ug/m3, O3 and NO2 in ppb) and car parks.

Pollution = background + traffic (morning and evening rush hours) + optional
events (a factory release, a scenario peak). Closing a zone's car parks lowers
its traffic share, so the Brain's action has a visible effect.
Scenarios can tamper with what a sensor REPORTS (spoof, stuck value, silence)
while the true air stays known to the mock, as the real Twin should do.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

START = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)     # a Monday in the cold season
TICK_S = 600.0                                                # one reading per sensor every 10 min


@dataclass
class AirSensor:
    id: str
    zone: str
    lat: float
    lon: float
    pm: float          # background PM2.5, ug/m3
    o3: float          # background O3, ppb
    no2: float         # background NO2, ppb
    traffic: float     # how much rush-hour traffic adds here (0..1.5)


def default_sensors() -> List[AirSensor]:
    return [
        AirSensor("AIR-01", "A", 36.800, 10.180, 9.0, 14.0, 10.0, 0.4),   # residential
        AirSensor("AIR-02", "A", 36.802, 10.183, 9.5, 13.0, 11.0, 0.4),
        AirSensor("AIR-03", "B", 36.810, 10.190, 11.0, 10.0, 16.0, 1.2),  # downtown
        AirSensor("AIR-04", "B", 36.812, 10.192, 10.5, 10.0, 15.0, 1.1),
        AirSensor("AIR-05", "C", 36.790, 10.200, 8.0, 15.0, 9.0, 0.3),    # schools, clinic
        AirSensor("AIR-06", "C", 36.791, 10.203, 8.5, 15.0, 9.5, 0.3),
        AirSensor("AIR-07", "D", 36.780, 10.170, 14.0, 9.0, 14.0, 0.5),   # industrial
    ]


DEFAULT_LOTS = {"LOT-01": "A", "LOT-02": "B", "LOT-03": "C", "LOT-04": "D"}


def rush(t: datetime) -> float:
    """0..1 traffic intensity: peaks at 08:00 and 18:00."""
    h = t.hour + t.minute / 60.0
    return math.exp(-((h - 8.0) / 0.9) ** 2) + 0.8 * math.exp(-((h - 18.0) / 1.0) ** 2)


class MockAirCity:
    def __init__(self, sensors: Optional[List[AirSensor]] = None,
                 lots: Optional[Dict[str, str]] = None, start: datetime = START,
                 tick_s: float = TICK_S, rush_pm: float = 16.0, rush_no2: float = 22.0):
        self.sensors: Dict[str, AirSensor] = {s.id: s for s in (sensors or default_sensors())}
        self.lots: Dict[str, str] = dict(lots or DEFAULT_LOTS)
        self.barriers: Dict[str, str] = {lot: "auto" for lot in self.lots}
        self.now = start
        self.tick_s = tick_s
        self.rush_pm, self.rush_no2 = rush_pm, rush_no2
        self.event: Dict[str, float] = {}          # zone -> extra PM2.5 (factory, fire...)
        self.reported: Dict[str, Dict[str, float]] = {}   # sensor -> values it reports instead
        self.silent: Set[str] = set()
        self.offline = False
        self.barrier_log: List[Tuple[datetime, str, str]] = []
        self.k = 0

    # ---- what the City Brain calls ------------------------------------------
    def list_parking_lots(self) -> Dict[str, str]:
        if self.offline:
            raise ConnectionError("twin unreachable")
        return dict(self.lots)

    def set_parking_barrier(self, lot: str, mode: str) -> None:
        if self.offline:
            raise ConnectionError("twin unreachable")
        if lot not in self.lots or mode not in ("auto", "open", "closed"):
            raise ValueError(f"bad barrier command {lot} {mode}")
        self.barriers[lot] = mode
        self.barrier_log.append((self.now, lot, mode))

    # ---- truth -------------------------------------------------------------------
    def closed_share(self, zone: str) -> float:
        lots = [l for l, z in self.lots.items() if z == zone]
        return sum(self.barriers[l] == "closed" for l in lots) / len(lots) if lots else 0.0

    def true_air(self, s: AirSensor, t: datetime) -> Tuple[float, float, float]:
        traffic = s.traffic * rush(t) * (1.0 - 0.35 * self.closed_share(s.zone))
        wiggle = 0.8 * math.sin(self.k * 1.9 + len(s.id))           # small, deterministic
        pm = s.pm + self.rush_pm * traffic + self.event.get(s.zone, 0.0) + wiggle
        no2 = s.no2 + self.rush_no2 * traffic + 0.4 * self.event.get(s.zone, 0.0)
        sun = max(0.0, math.sin(math.pi * (t.hour + t.minute / 60 - 9) / 8))   # 9h..17h
        o3 = max(0.0, s.o3 + 8.0 * sun - 4.0 * traffic)             # NO titration near traffic
        return round(pm, 2), round(o3, 2), round(no2, 2)

    # ---- simulation ------------------------------------------------------------
    def tick(self) -> Tuple[List[dict], datetime]:
        self.now += timedelta(seconds=self.tick_s)
        self.k += 1
        out = []
        for s in self.sensors.values():
            if s.id in self.silent:
                continue
            pm, o3, no2 = self.true_air(s, self.now)
            fake = self.reported.get(s.id, {})
            out.append({"tool": "get_sensor_reading", "sensor_id": s.id, "type": "air_quality",
                        "pm25": fake.get("pm25", pm), "o3": fake.get("o3", o3),
                        "no2": fake.get("no2", no2), "zone": s.zone,
                        "location": {"lat": s.lat, "lon": s.lon},
                        "timestamp": self.now.isoformat().replace("+00:00", "Z")})
        return out, self.now

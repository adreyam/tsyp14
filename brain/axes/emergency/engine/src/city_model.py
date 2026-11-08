"""
City model for the City Brain dashboard.

Single source of truth for the simulated city the dashboard draws and the
scenario events it sends to the orchestrator:

  * 10x10 blocks (rows A-J, columns 1-10) with a zone or facility type
  * road graph: 11x11 intersections ("nodes"), node (i, j) is the corner
    north-west of block (row i, col j)
  * facilities: police, fire stations, hospitals, sanitation depot, substations...
  * underground layer: metro lines, road tunnel, sewer mains, power ducts
  * smart waste bins on block sides and garbage-truck collection routes
  * scenario builders that turn a dashboard click into a sensor event (CityEvent JSON)

Coordinates: block (r, c) centre is lat = BASE_LAT + (9 - r) * STEP, lon = BASE_LON + c * STEP.
Everything here is simulated until the Twin layer provides the real feeds.
"""
from __future__ import annotations

import datetime
import math
import random
import string
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from mocks import mock_city_scenarios as MOCK  # noqa: E402  (scenario readings: mock data only)

ROWS = list(string.ascii_uppercase[:10])  # A..J
N = 10
BASE_LAT, BASE_LON, STEP = 36.8000, 10.1800, 0.001

# --------------------------------------------------------------------------- #
# Zoning (one letter per block, rows A..J, columns 1..10)
#   R residential  C commercial  I industrial  P park
#   H hospital     L police      F fire station
#   D sanitation depot   S substation   E school   K parking garage
# --------------------------------------------------------------------------- #
ZONING = [
    "RRRRRRRSII",  # A
    "RRPERFRRII",  # B
    "RLRRPRRHRR",  # C
    "RRRCCCCRRR",  # D
    "RRCCCCCKRP",  # E
    "RRCCCCCRFR",  # F
    "IIIRRERLRR",  # G
    "ISIRHRPRRR",  # H
    "IIFRRRRRRR",  # I
    "IIIDRRRRRR",  # J
]

TYPE_OF = {
    "R": "residential", "C": "commercial", "I": "industrial", "P": "park",
    "H": "hospital", "L": "police", "F": "fire_station", "D": "depot",
    "S": "substation", "E": "school", "K": "parking",
}

FACILITY_NAMES = {
    "A8": "North Substation (66/11 kV)",
    "H2": "South Substation (66/11 kV)",
    "C8": "Central General Hospital",
    "H5": "South Medical Center",
    "C2": "North Police Precinct",
    "G8": "South Police Precinct",
    "B6": "Fire Station 1 (North)",
    "F9": "Fire Station 2 (East)",
    "I3": "Fire Station 3 (Industrial / Hazmat)",
    "J4": "Municipal Sanitation Depot",
    "B4": "North Secondary School",
    "G6": "South Primary School",
    "E8": "Central Parking Garage",
    "B3": "North Gardens",
    "C5": "Central Park",
    "E10": "East Park",
    "H7": "South Park",
}

SHORT_NAMES = {
    "A8": "N. Substation", "H2": "S. Substation", "C8": "Central Hospital", "H5": "South Medical",
    "C2": "North Police", "G8": "South Police", "B6": "Fire St. 1", "F9": "Fire St. 2", "I3": "Fire St. 3",
    "J4": "Waste Depot", "B4": "N. School", "G6": "S. School", "E8": "Parking",
}

# Named roads (node index of the road line)
ROAD_NAMES = {
    "row": {3: "North Road", 5: "Central Avenue", 7: "South Road"},
    "col": {2: "West Street", 5: "Market Street", 8: "East Street"},
}

# District labels: (title, top-left block, bottom-right block)
DISTRICTS = [
    ("COMMERCIAL CORE", "D3", "F7"), ("INDUSTRIAL WEST", "G1", "J3"), ("INDUSTRIAL NORTH-EAST", "A9", "B10"),
    ("RESIDENTIAL NORTH", "A1", "C7"), ("RESIDENTIAL SOUTH", "G4", "J10"),
]

FACILITY_ASSETS = {
    "hospital": ["emergency_department", "ambulance_bay", "backup_generator", "helipad"],
    "police": ["patrol_cars", "acoustic_gunshot_array", "cctv_hub"],
    "fire_station": ["fire_engines", "rescue_unit", "hydrant_network"],
    "depot": ["garbage_trucks", "fleet_telematics"],
    "substation": ["transformers", "scada_meters", "feeder_breakers"],
    "school": ["school_zone_signals", "air_quality_station"],
    "parking": ["entry_exit_sensors", "vms_sign"],
    "industrial": ["air_quality_station", "gas_sensors"],
    "commercial": ["cctv", "street_lights"],
    "residential": ["street_lights"],
    "park": ["pedestrian_lights", "air_quality_station"],
}


def block_id(r: int, c: int) -> str:
    return f"{ROWS[r]}{c + 1}"


def parse_block(bid: str) -> tuple[int, int] | None:
    if not bid or bid[0] not in ROWS:
        return None
    try:
        c = int(bid[1:]) - 1
    except ValueError:
        return None
    return (ROWS.index(bid[0]), c) if 0 <= c < N else None


def block_latlon(r: int, c: int) -> tuple[float, float]:
    return round(BASE_LAT + (9 - r) * STEP, 4), round(BASE_LON + c * STEP, 4)


def node_latlon(i: float, j: float) -> tuple[float, float]:
    # node (i, j) is the north-west corner of block (i, j): half a step off the block centre
    return round(BASE_LAT + (9 - i + 0.5) * STEP, 5), round(BASE_LON + (j - 0.5) * STEP, 5)


def _build_blocks() -> list[dict]:
    blocks = []
    for r in range(N):
        for c in range(N):
            code = ZONING[r][c]
            btype = TYPE_OF[code]
            bid = block_id(r, c)
            lat, lon = block_latlon(r, c)
            name = FACILITY_NAMES.get(bid) or f"{btype.capitalize()} block {bid}"
            blocks.append({
                "id": bid, "row": r, "col": c, "type": btype, "name": name,
                "assets": FACILITY_ASSETS.get(btype, []), "lat": lat, "lon": lon, "short": SHORT_NAMES.get(bid),
            })
    return blocks


BLOCKS = _build_blocks()
BLOCKS_BY_ID = {b["id"]: b for b in BLOCKS}

# --------------------------------------------------------------------------- #
# Underground layer (polylines in node coordinates [i, j], fractional allowed)
# --------------------------------------------------------------------------- #
TUNNELS = [
    {
        "id": "M1", "kind": "metro", "name": "Metro Line 1 (East-West)", "depth_m": -14,
        "path": [[4, 0], [4, 10]],
        "stations": [{"at": [4, 1], "name": "West Gate"}, {"at": [4, 3], "name": "Medina"},
                     {"at": [4, 6], "name": "Central (L1/L2)"}, {"at": [4, 9], "name": "East Market"}],
    },
    {
        "id": "M2", "kind": "metro", "name": "Metro Line 2 (North-South)", "depth_m": -18,
        "path": [[0, 6], [4, 6], [6, 6], [7.5, 5], [10, 5]],
        "stations": [{"at": [1, 6], "name": "North Hills"}, {"at": [4, 6], "name": "Central (L1/L2)"},
                     {"at": [7.5, 5], "name": "Hospital South"}, {"at": [9.5, 5], "name": "Depot Road"}],
    },
    {
        "id": "T1", "kind": "road", "name": "Boulevard Road Tunnel", "depth_m": -8,
        "path": [[3, 2], [3.5, 3.2], [6.5, 6.8], [7, 8]],
        "portals": [[3, 2], [7, 8]],
        "flood_sensor": {"id": "HYD-T1-LOWPOINT", "at": [5, 5], "critical_cm": 30},
    },
    {"id": "SW1", "kind": "sewer", "name": "West Sewer Main", "depth_m": -5, "path": [[0, 2], [10, 2]]},
    {"id": "SW2", "kind": "sewer", "name": "Central Sewer Main", "depth_m": -5, "path": [[0, 5], [10, 5]]},
    {"id": "SW3", "kind": "sewer", "name": "East Sewer Main", "depth_m": -5, "path": [[0, 8], [10, 8]]},
    {"id": "SW4", "kind": "sewer", "name": "North Interceptor", "depth_m": -6, "path": [[3, 0], [3, 10]]},
    {"id": "SW5", "kind": "sewer", "name": "South Interceptor", "depth_m": -6, "path": [[7, 0], [7, 10], ]},
    {"id": "SW6", "kind": "sewer", "name": "Outfall to Treatment Plant", "depth_m": -7, "path": [[7, 0], [10, 0]]},
    {"id": "PD-N1", "kind": "power", "name": "Feeder N1 (North Substation)", "substation": "A8",
     "depth_m": -1.5, "path": [[1, 7.5], [1, 3], [2, 3]]},
    {"id": "PD-N2", "kind": "power", "name": "Feeder N2 (North Substation)", "substation": "A8",
     "depth_m": -1.5, "path": [[1, 7.5], [1, 8], [6, 8], [6, 9]]},
    {"id": "PD-S1", "kind": "power", "name": "Feeder S1 (South Substation)", "substation": "H2",
     "depth_m": -1.5, "path": [[7.5, 1.5], [6, 1.5], [6, 4], [3, 4]]},
    {"id": "PD-S2", "kind": "power", "name": "Feeder S2 (South Substation)", "substation": "H2",
     "depth_m": -1.5, "path": [[7.5, 1.5], [8, 1.5], [8, 6], [5, 6]]},
    {"id": "PD-S3", "kind": "power", "name": "Feeder S3 (South Substation)", "substation": "H2",
     "depth_m": -1.5, "path": [[7.5, 1.5], [9, 1.5], [9, 9]]},
]
TUNNELS_BY_ID = {t["id"]: t for t in TUNNELS}

# --------------------------------------------------------------------------- #
# Smart waste bins (placed on a side of a block) and truck routes
# --------------------------------------------------------------------------- #
def _build_bins() -> list[dict]:
    rng = random.Random(14)
    bins = []
    for b in BLOCKS:
        if b["type"] not in {"residential", "commercial", "school", "park", "parking"}:
            continue
        if rng.random() < 0.55:
            side = rng.choice("nsew")
            bins.append({
                "id": f"BIN-{b['id']}", "block": b["id"], "side": side,
                "fill_pct": round(rng.uniform(8, 82), 1), "capacity_l": 1100,
            })
    return bins


BINS = _build_bins()
BINS_BY_ID = {b["id"]: b for b in BINS}

DEPOT_NODE = [9, 3]  # north-west corner of block J4

# Fixed collection rounds (waypoints, expanded to Manhattan paths by the client).
# This is the "before" picture: fixed schedules, no fill-level awareness.
TRUCKS = [
    {"id": "GT-01", "name": "Garbage truck 01 (North round)",
     "route": [[9, 3], [5, 3], [5, 0], [0, 0], [0, 10], [2, 10], [2, 0], [3, 0], [3, 10], [5, 10], [5, 3], [9, 3]]},
    {"id": "GT-02", "name": "Garbage truck 02 (Centre round)",
     "route": [[9, 3], [6, 3], [6, 10], [4, 10], [4, 2], [5, 2], [5, 9], [6, 9], [6, 3], [9, 3]]},
    {"id": "GT-03", "name": "Garbage truck 03 (South round)",
     "route": [[9, 3], [9, 10], [7, 10], [7, 3], [8, 3], [8, 10], [10, 10], [10, 3], [9, 3]]},
]


# --------------------------------------------------------------------------- #
# Traffic signals: 3 neighbouring junctions on the avenue between rows E and F
# (node row 5, columns 3-5). Fixed-time plan with an eastbound green wave
# (100 m between junctions at ~36 km/h -> 10 s offset). The Mobility axis
# (sumo-rl adaptive control + emergency priority) will replace this plan.
# --------------------------------------------------------------------------- #
SIGNAL_PLAN = {"cycle_s": 50, "ns_green_s": 21, "ew_green_s": 21, "yellow_s": 3, "all_red_s": 1}


def _corner_blocks(i: int, j: int) -> list[str]:
    return [block_id(r, c) for r in (i - 1, i) for c in (j - 1, j) if 0 <= r < N and 0 <= c < N]


SIGNALS = [
    {"id": f"SIG-0{k + 1}", "node": [5, 3 + k], "offset_s": 10 * k, "control": "fixed_time",
     "plan": SIGNAL_PLAN, "junction_of": _corner_blocks(5, 3 + k)}
    for k in range(3)
]


# --------------------------------------------------------------------------- #
# Seismic station network (accelerometers) used by the full PLUM engine
# --------------------------------------------------------------------------- #
SEISMIC_STATIONS = []
for _k, _bid in enumerate(["B2", "B6", "B9", "E2", "E9", "H3", "H7", "J9"]):
    _r, _c = (ROWS.index(_bid[0]), int(_bid[1:]) - 1)
    _lat, _lon = block_latlon(_r, _c)
    SEISMIC_STATIONS.append({"sensor_id": f"SEIS-{_k + 1:02d}", "block": _bid, "lat": _lat, "lon": _lon})


def synth_station_network(epi_lat: float, epi_lon: float, **kw) -> dict:
    """Mock accelerograms for this city's station network (see mocks/mock_city_scenarios.py)."""
    return MOCK.synth_station_network(SEISMIC_STATIONS, epi_lat, epi_lon, **kw)


# --------------------------------------------------------------------------- #
# Catastrophic-hazard geography (flood, storm, regional quake, tsunami engines)
# --------------------------------------------------------------------------- #
# Low-lying south-west quarter along the wadi: first to flood when the river leaves its bed.
FLOOD_ZONE = ["G1", "G2", "G3", "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "J1", "J2", "J3", "J4", "J5"]
RIVER_GAUGE = {"id": "HYD-OUED-01", "name": "Wadi gauge (south-west outfall)", "at": [10, 0], "flood_stage_m": 4.5}
# The city's east edge faces the Gulf of Tunis.
COAST_BLOCKS = [f"{r}10" for r in ROWS]
TIDE_GAUGE = {"id": "TG-PORT-01", "name": "Port tide gauge", "at": [5, 10], "baseline_m": 0.80}
WEATHER_STATION = {"id": "WX-CITY-01", "name": "City weather station", "block": "C5"}
CITY_CENTER = {"lat": round(BASE_LAT + 4.5 * STEP, 5), "lon": round(BASE_LON + 4.5 * STEP, 5)}


def regional_target(lat: float, lon: float) -> dict:
    """Distance (km) and compass bearing from the city centre to a point outside the map."""
    c_lat, c_lon = CITY_CENTER["lat"], CITY_CENTER["lon"]
    p1, p2, dl = math.radians(c_lat), math.radians(lat), math.radians(lon - c_lon)
    bearing = (math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                       math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) + 360) % 360
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist = 2 * 6371.0 * math.asin(math.sqrt(a))
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return {"lat": lat, "lon": lon, "distance_km": round(dist, 1), "bearing_deg": round(bearing, 1),
            "compass": names[int((bearing + 11.25) // 22.5) % 16]}


def city_payload() -> dict:
    return {
        "grid": {"rows": ROWS, "cols": N, "base_lat": BASE_LAT, "base_lon": BASE_LON, "step_deg": STEP},
        "blocks": BLOCKS, "tunnels": TUNNELS, "bins": BINS, "trucks": TRUCKS, "depot_node": DEPOT_NODE,
        "signals": SIGNALS, "seismic_stations": SEISMIC_STATIONS,
        "road_names": ROAD_NAMES, "districts": DISTRICTS,
        "flood_zone": FLOOD_ZONE, "river_gauge": RIVER_GAUGE, "coast_blocks": COAST_BLOCKS,
        "tide_gauge": TIDE_GAUGE, "weather_station": WEATHER_STATION, "city_center": CITY_CENTER,
        "simulated": True,
    }


# --------------------------------------------------------------------------- #
# Scenarios: mock readings (mocks/mock_city_scenarios.py) placed on the city.
# The catalogue holds no decision: which axes act is computed by the orchestrator's
# rules at request time (see preview_axes), the response by the agents and engines.
# --------------------------------------------------------------------------- #
SCENARIOS = {
    "fire": {"title": "Building fire", "icon": "🔥", "default_block": "F4"},
    "gunshot": {"title": "Gunshot", "icon": "🎯", "default_block": "D7"},
    # Catastrophic hazards: a short sequence of readings from independent sources,
    # correlated by the hazard engines (a 'critical' level needs >= 2 sources).
    "earthquake": {"title": "Local earthquake", "icon": "🌐", "default_block": "E6", "group": "hazard",
                   "engine": "PLUM + catalog"},
    "seismic_spike": {"title": "Seismic false trigger", "icon": "📈", "default_block": "H7", "group": "hazard",
                      "engine": "PLUM"},
    "river_flood": {"title": "River flood", "icon": "🏞️", "default_block": "H3", "group": "hazard", "engine": "FloodEngine"},
    "storm": {"title": "Severe storm", "icon": "⛈️", "default_block": "E5", "group": "hazard", "engine": "StormEngine"},
    "regional_quake": {"title": "Regional earthquake", "icon": "🛰️", "default_block": "E5", "group": "hazard",
                       "engine": "PLUM + catalog"},
    "tsunami": {"title": "Offshore quake → tsunami", "icon": "🌊", "default_block": "E10", "group": "hazard",
                "engine": "TsunamiEngine"},
}

# Live external feeds (the adapters in src/modules/feeds); the server polls them on request.
FEEDS = {
    "open_meteo": {"title": "Open-Meteo weather", "icon": "🌦️", "adapter": "OpenMeteoAdapter",
                   "detail": "Current conditions + 24 h rain forecast for the city centre"},
    "usgs": {"title": "USGS quakes (last hour)", "icon": "🌍", "adapter": "USGSAdapter",
             "detail": "Global catalog; only M4.5+ events are evaluated"},
    "emsc": {"title": "EMSC quakes (Mediterranean)", "icon": "🗺️", "adapter": "EMSCAdapter",
             "detail": "Latest Euro-Mediterranean catalog solutions, M4.0+"},
}

BLOCK_M = 100.0  # one block is about 100 m


def _dist_to_type_m(r: int, c: int, btype: str) -> float:
    """Street (Manhattan) distance in metres from block (r, c) to the nearest block of a type."""
    d = min(abs(b["row"] - r) + abs(b["col"] - c) for b in BLOCKS if b["type"] == btype)
    return d * BLOCK_M


def _historical_crime_count(bid: str) -> int:
    """Simulated incident history per block (stand-in until real records come from the Twin layer)."""
    base = {"commercial": 18, "industrial": 14, "residential": 9, "park": 12}.get(BLOCKS_BY_ID[bid]["type"], 6)
    return base + random.Random(bid).randint(0, 16)


def _nearest(cands: list[str], r: int, c: int) -> str:
    return min(cands, key=lambda bid: abs(parse_block(bid)[0] - r) + abs(parse_block(bid)[1] - c))


def _reading(mock: dict, lat: float, lon: float, block: str, ts: float | None = None, **extra) -> dict:
    """A CityEvent dict from a mock reading: copies the observations, adds where/when, flags it as mock."""
    data = {**mock.get("data", {}), **extra, "block_id": block, "mock": True}
    ev = {"source": mock["source"], "kind": mock["kind"], "lat": lat, "lon": lon, "data": data}
    if ts is not None:
        ev["ts"] = ts
    if mock.get("label"):
        ev["_step"] = mock["label"]
    return ev


def _place(spot: str, bid: str) -> tuple[float, float, str]:
    """Coordinates and block of a named sensor location for hazard readings."""
    if spot == "river_gauge":
        lat, lon = node_latlon(*RIVER_GAUGE["at"])
        return lat, lon, "J1"
    if spot == "tide_gauge":
        lat, lon = node_latlon(*TIDE_GAUGE["at"])
        return lat, lon, "E10"
    if spot == "weather_station":
        b = BLOCKS_BY_ID[WEATHER_STATION["block"]]
        return b["lat"], b["lon"], b["id"]
    if spot == "block":
        b = BLOCKS_BY_ID[bid]
        return b["lat"], b["lon"], bid
    return CITY_CENTER["lat"], CITY_CENTER["lon"], "city"


def _sensor_meta(spot: str) -> dict:
    """Installation metadata of the sensor (gauge datum, flood stage), not a decision."""
    if spot == "river_gauge":
        return {"station_id": RIVER_GAUGE["id"], "flood_stage_m": RIVER_GAUGE["flood_stage_m"]}
    if spot == "tide_gauge":
        return {"station_id": TIDE_GAUGE["id"], "baseline_m": TIDE_GAUGE["baseline_m"]}
    if spot == "weather_station":
        return {"station_id": WEATHER_STATION["id"]}
    return {}


def _hazard_steps(steps: list[dict], bid: str, now: float) -> list[dict]:
    out = []
    for st in steps:
        lat, lon, blk = _place(st.get("at", "city"), bid)
        extra = _sensor_meta(st.get("at", "city"))
        if st["kind"] == "official_weather_alert":
            extra["valid_until"] = now + st["data"].get("valid_for_s", 3600)
        out.append(_reading(st, lat, lon, blk, ts=now - st.get("dt", 0), **extra))
    return out


def _catalog_reading(spec: dict, bid: str, now: float, magnitude: float | None) -> dict:
    if spec["epicenter"] == "block":
        b = BLOCKS_BY_ID[bid]
        lat, lon = b["lat"], b["lon"]
    else:
        lat, lon = spec["epicenter"]
    mag = float(magnitude or spec["default_magnitude"])
    mock = {"source": spec["source"], "kind": spec["kind"], "label": f"{spec['label']} M{mag:.1f}",
            "data": {"magnitude": mag, "depth_km": spec["depth_km"], "epicenter_lat": lat, "epicenter_lon": lon,
                     "event_time": now - spec["origin_age_s"], "catalog_source": spec["source"].split("_")[0],
                     "title": f"M {mag:.1f} - {spec['region']}"}}
    return _reading(mock, lat, lon, bid, ts=now - spec.get("dt", 0))


def _hazard_river_flood(bid: str, magnitude: float | None) -> tuple[list[dict], dict]:
    steps = _hazard_steps(MOCK.RIVER_FLOOD_STEPS, bid, time.time())
    gauge = steps[-1]["data"]
    return steps, {"type": "river_flood", "block": "H3", "zone": FLOOD_ZONE, "gauge": RIVER_GAUGE,
                   "river_level_m": gauge["river_level_m"], "flood_stage_m": gauge["flood_stage_m"]}


def _hazard_storm(bid: str, magnitude: float | None) -> tuple[list[dict], dict]:
    steps = _hazard_steps(MOCK.STORM_STEPS, bid, time.time())
    return steps, {"type": "storm", "block": bid, "gust_kmh": steps[-1]["data"].get("gust_kmh")}


def _quake_steps(spec: dict, bid: str, magnitude: float | None) -> list[dict]:
    """Station-network waveforms recorded by the city (PLUM input), then the catalog solution."""
    now = time.time()
    cat = _catalog_reading(spec["catalog"], bid, now, magnitude)
    d = cat["data"]
    wf = spec["waveforms"]
    ts = now - wf["dt"]
    net = synth_station_network(d["epicenter_lat"], d["epicenter_lon"], magnitude=d["magnitude"], depth_km=d["depth_km"])
    net["startTime"] = ts - net["durationS"]
    b = BLOCKS_BY_ID[bid]
    waves = _reading({k: wf[k] for k in ("source", "kind", "label")}, b["lat"], b["lon"], bid, ts=ts, stationNetwork=net)
    return [waves, cat]


def _hazard_local_quake(bid: str, magnitude: float | None) -> tuple[list[dict], dict]:
    steps = _quake_steps(MOCK.LOCAL_QUAKE, bid, magnitude)
    d = steps[-1]["data"]
    return steps, {"type": "earthquake", "block": bid, "magnitude": d["magnitude"], "depth_km": d["depth_km"]}


def _hazard_regional_quake(bid: str, magnitude: float | None) -> tuple[list[dict], dict]:
    spec = MOCK.REGIONAL_QUAKE
    steps = _quake_steps(spec, bid, magnitude)
    d = steps[-1]["data"]
    return steps, {"type": "regional_quake", "block": bid, "magnitude": d["magnitude"],
                   "depth_km": d["depth_km"], **regional_target(*spec["catalog"]["epicenter"])}


def _hazard_tsunami(bid: str, magnitude: float | None) -> tuple[list[dict], dict]:
    now = time.time()
    quake_spec, gauge_spec = MOCK.TSUNAMI_STEPS
    mag = magnitude if magnitude and float(magnitude) >= 6.5 else None   # below M6.5 the mock's default is used
    steps = [_catalog_reading(quake_spec, "E10", now, mag)] + _hazard_steps([gauge_spec], "E10", now)
    return steps, {"type": "tsunami", "block": "E10", "magnitude": steps[0]["data"]["magnitude"], "coast": COAST_BLOCKS,
                   "gauge": TIDE_GAUGE, **regional_target(*quake_spec["epicenter"])}


HAZARD_BUILDERS = {
    "earthquake": _hazard_local_quake,
    "river_flood": _hazard_river_flood,
    "storm": _hazard_storm,
    "regional_quake": _hazard_regional_quake,
    "tsunami": _hazard_tsunami,
}


def build_scenario_steps(scenario: str, block: str | None = None, bin_id: str | None = None,
                         magnitude: float | None = None) -> tuple[list[dict], dict]:
    """Returns ([events in order], target). Single-sensor scenarios have one event; hazard
    scenarios send several readings from independent sources, one after the other.
    `target` only tells the dashboard where to draw; it holds no decision."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario '{scenario}'")
    if scenario in HAZARD_BUILDERS:
        return HAZARD_BUILDERS[scenario](block if block in BLOCKS_BY_ID else SCENARIOS[scenario]["default_block"], magnitude)
    event, target = build_scenario(scenario, block, bin_id, magnitude)
    return [event], target


def build_scenario(scenario: str, block: str | None = None, bin_id: str | None = None,
                   magnitude: float | None = None) -> tuple[dict, dict]:
    """Returns (event, target) for a single-sensor scenario; for multi-reading hazard
    scenarios the last reading (see build_scenario_steps)."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario '{scenario}'")
    if scenario in HAZARD_BUILDERS:
        steps, target = build_scenario_steps(scenario, block, bin_id, magnitude)
        return {k: v for k, v in steps[-1].items() if not k.startswith("_")}, target
    spec = SCENARIOS[scenario]
    bid = block if block in BLOCKS_BY_ID else spec["default_block"]

    if scenario == "accident":
        r, c = parse_block(bid)
        lat, lon = node_latlon(r, c + 0.5)
        event = _reading(MOCK.ACCIDENT, lat, lon, bid, road_segment=f"ROAD-{bid}-N")
        return event, {"type": "accident", "block": bid, "edge": [[r, c], [r, c + 1]]}

    if scenario == "fire":
        if BLOCKS_BY_ID[bid]["type"] in {"park"}:
            bid = spec["default_block"]
        b = BLOCKS_BY_ID[bid]
        return _reading(MOCK.FIRE, b["lat"], b["lon"], bid, camera_id=f"CAM-{bid}-02"), {"type": "fire", "block": bid}

    if scenario == "load_spike":
        r, c = parse_block(bid)
        sub = bid if BLOCKS_BY_ID[bid]["type"] == "substation" else _nearest(["A8", "H2"], r, c)
        feeder = "PD-S2" if sub == "H2" else "PD-N2"
        b = BLOCKS_BY_ID[sub]
        event = _reading(MOCK.LOAD_SPIKE, b["lat"], b["lon"], sub, substation_id=sub, feeder_id=feeder)
        return event, {"type": "load_spike", "block": sub, "feeder": feeder}

    if scenario == "garbage":
        cand = None
        if bin_id in BINS_BY_ID:
            cand = BINS_BY_ID[bin_id]
        elif block:
            in_block = [x for x in BINS if x["block"] == block]
            cand = in_block[0] if in_block else None
        cand = cand or max(BINS, key=lambda x: x["fill_pct"])
        b = BLOCKS_BY_ID[cand["block"]]
        event = _reading(MOCK.GARBAGE_BIN, b["lat"], b["lon"], b["id"], bin_id=cand["id"], capacity_l=cand["capacity_l"])
        return event, {"type": "garbage", "block": b["id"], "bin_id": cand["id"]}

    if scenario == "seismic_spike":
        r, c = parse_block(bid)
        st = min(SEISMIC_STATIONS, key=lambda x: abs(parse_block(x["block"])[0] - r) + abs(parse_block(x["block"])[1] - c))
        b = BLOCKS_BY_ID[st["block"]]
        event = _reading({"source": "seismic_network", "kind": "seismic_sensor"}, b["lat"], b["lon"], st["block"],
                         stationNetwork=synth_station_network(b["lat"], b["lon"], spike_station=st["sensor_id"]))
        return event, {"type": "seismic_spike", "block": st["block"], "station": st["sensor_id"]}

    if scenario == "gunshot":
        r, c = parse_block(bid)
        b = BLOCKS_BY_ID[bid]
        now = datetime.datetime.now()
        context = {  # risk-terrain context of the block (simulated city records)
            "dist_to_police_station_m": _dist_to_type_m(r, c, "police"),
            "dist_to_hospital_m": _dist_to_type_m(r, c, "hospital"),
            "historical_crime_count": _historical_crime_count(bid),
            "is_weekend": now.weekday() >= 5,
            "is_night": now.hour >= 20 or now.hour < 6,
            "street_lighting_status": "ok",
        }
        return _reading(MOCK.GUNSHOT, b["lat"], b["lon"], bid, associatedContext=context), {"type": "gunshot", "block": bid}

    # tunnel_flood
    s = TUNNELS_BY_ID["T1"]["flood_sensor"]
    lat, lon = node_latlon(*s["at"])
    event = _reading(MOCK.TUNNEL_FLOOD, lat, lon, "E5", sensor_id=s["id"], tunnel_id="T1",
                     critical_threshold_cm=float(s["critical_cm"]))
    return event, {"type": "tunnel_flood", "block": "E5", "tunnel": "T1", "at": s["at"]}

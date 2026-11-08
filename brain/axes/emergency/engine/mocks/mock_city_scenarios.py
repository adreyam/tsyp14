"""
Mock sensor readings for the City Brain control-room scenarios.

MOCK DATA ONLY. These are the raw readings a sensor or an external feed would send, until the
Twin layer provides the real ones. They contain observations and sensor metadata only:
no severity, risk level, axis, priority, units or any other decision. Every decision shown on the
dashboard is computed by the algorithms (orchestrator rules, PLUM, crime hotspot engine, flood /
storm / catalog quake / tsunami engines and the emergency agent playbooks).

src/city_model.py only places these readings on the simulated city (block, coordinates, time).
"""
from __future__ import annotations

import math

# --------------------------------------------------------------------------- #
# Single-sensor scenarios: (source, kind, data)
# --------------------------------------------------------------------------- #
ACCIDENT = {
    "source": "traffic_cctv_edge", "kind": "accident",
    "data": {"vehicles_involved": 3, "injuries_reported": 2, "lanes_blocked": 2, "confidence": 0.91},
}

FIRE = {
    "source": "cv_camera_edge", "kind": "vision_fire_smoke",
    # CV model output on the camera: detections and their confidence
    "data": {"fire_detected": True, "smoke_detected": True, "confidence": 0.94, "flame_area_pct": 14.2},
}

LOAD_SPIKE = {
    "source": "scada_feeder_meter", "kind": "grid_load",
    "data": {"load_pct": 148.0, "previous_load_pct": 42.0, "rate_of_change_pct_per_s": 106.0,
             "neighbour_feeders_load_pct": [41.0, 44.0], "current_a_measured": 212.0,
             "telemetry_signature_valid": False},
}

GARBAGE_BIN = {
    "source": "smart_bin_iot", "kind": "smart_waste_bin",
    "data": {"fill_pct": 96.0, "odor_voc_ppm": 45.0},
}

TUNNEL_FLOOD = {
    "source": "hydrology_sensor", "kind": "flood_sensor",
    # critical_threshold_cm is the sensor's installation setting, not a decision
    "data": {"water_level_cm": 46.0, "submerged_lane_count": 2},
}

GUNSHOT = {
    "source": "acoustic_array", "kind": "acoustic_gunshot_triangulation",
    "data": {"decibelLevel": 118.2},
}

# --------------------------------------------------------------------------- #
# Catastrophic-hazard scenarios: ordered readings from independent sources.
# "dt" = seconds before the last reading; "label" is display text only.
# --------------------------------------------------------------------------- #
RIVER_FLOOD_STEPS = [
    {"dt": 40, "label": "24 h rain forecast (numerical model)", "source": "open_meteo_forecast", "kind": "weather_forecast",
     "at": "city", "data": {"rain_24h_forecast_mm": 96.0, "rain_6h_forecast_mm": 41.0, "horizon_h": 24}},
    {"dt": 20, "label": "Weather station: rain on wet soil", "source": "city_weather_station", "kind": "weather_observation",
     "at": "weather_station", "data": {"rain_rate_mm_h": 31.0, "rain_24h_mm": 62.0, "soil_saturation": 0.93,
                                       "wind_speed_kmh": 28.0, "gust_kmh": 46.0}},
    {"dt": 0, "label": "River gauge reading", "source": "wadi_river_gauge", "kind": "hydro_observation",
     "at": "river_gauge", "data": {"river_level_m": 5.05, "rate_of_rise_m_h": 0.42, "drainage_load": 0.94}},
]

STORM_STEPS = [
    {"dt": 30, "label": "National met service bulletin", "source": "inm_tunisia", "kind": "official_weather_alert",
     "at": "city", "data": {"alert_type": "severe_storm", "alert_level": "red", "valid_for_s": 6 * 3600,
                            "issuer": "Institut National de la Météorologie (INM)",
                            "description": "Destructive wind squalls, hail and intense rainfall expected over Greater Tunis."}},
    {"dt": 0, "label": "Weather station reading", "source": "city_weather_station", "kind": "weather_observation",
     "at": "block", "data": {"gust_kmh": 118.0, "wind_speed_kmh": 84.0, "pressure_hpa": 991.4, "pressure_drop_hpa_3h": 6.8,
                             "rain_rate_mm_h": 44.0, "hail": True, "lightning_rate": 34.0, "radar_rotation": False}},
]

# Earthquakes: the city's PLUM station network records the shaking first (waveforms only),
# a catalog solution (magnitude, epicentre) follows about a minute later. Both feed one
# unified earthquake assessment. epicenter "block" = under the targeted block.
LOCAL_QUAKE = {
    "waveforms": {"label": "PLUM station network waveforms", "source": "seismic_network", "kind": "seismic_sensor", "dt": 60},
    "catalog": {"label": "USGS catalog solution", "source": "usgs_catalog", "kind": "seismic_catalog", "dt": 0,
                "epicenter": "block", "depth_km": 8.0, "origin_age_s": 75, "default_magnitude": 6.0,
                "region": "Greater Tunis (simulated)"},
}

REGIONAL_QUAKE = {
    "waveforms": {"label": "PLUM station network waveforms", "source": "seismic_network", "kind": "seismic_sensor", "dt": 60},
    "catalog": {"label": "USGS catalog solution", "source": "usgs_catalog", "kind": "seismic_catalog", "dt": 0,
                "epicenter": (36.40, 10.14),            # inland, Zaghouan range (~45 km)
                "depth_km": 11.0, "origin_age_s": 90, "default_magnitude": 6.5,
                "region": "Zaghouan, Tunisia (simulated)"},
}

TSUNAMI_STEPS = [
    {"dt": 30, "label": "EMSC catalog solution", "source": "emsc_catalog", "kind": "seismic_catalog",
     "epicenter": (37.55, 11.25), "depth_km": 14.0, "origin_age_s": 240, "default_magnitude": 7.1,
     "region": "SICILY CHANNEL (simulated)"},
    {"dt": 0, "label": "Tide gauge reading", "source": "port_tide_gauge", "kind": "sea_level_observation",
     "at": "tide_gauge", "data": {"sea_level_m": 1.48, "change_m_per_min": 0.41}},
]


# --------------------------------------------------------------------------- #
# Synthetic accelerograms for the PLUM station network
# --------------------------------------------------------------------------- #
def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111.19
    dlon = (lon2 - lon1) * 111.19 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlon)


def synth_station_network(stations: list[dict], epi_lat: float, epi_lon: float, magnitude: float = 6.0,
                          depth_km: float = 8.0, fs: int = 40, duration_s: int = 24,
                          spike_station: str | None = None, seed: int = 0) -> dict:
    """Synthetic 3-axis accelerograms (m/s^2) for each station (same model as plum_engine._demo).

    Earthquake: S-wave arrives at 2 s + hypocentral distance / 3 km/s, amplitude decays with distance
    and scales with magnitude. spike_station: no earthquake, only an isolated 1-s spike on that
    station (e.g. a truck hitting the sensor housing). Only waveforms are returned: magnitude and
    epicentre are NOT part of the reading, PLUM has to work from the ground motion alone.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    t = np.arange(fs * duration_s) / fs
    f0 = 3.0
    out = []
    for st in stations:
        if spike_station is None:
            rh = math.hypot(_km(epi_lat, epi_lon, st["lat"], st["lon"]), depth_km)
            t_arr = 2.0 + rh / 3.0
            amp = 3.0 * (5.0 / (5.0 + rh)) ** 1.5 * 10 ** (0.5 * (magnitude - 6.0))   # PGA, m/s^2
            env = np.clip((t - t_arr) / 1.0, 0, 1) * np.exp(-np.clip(t - t_arr, 0, None) / 6.0)
            v = amp / (2 * math.pi * f0) * env * np.sin(2 * math.pi * f0 * (t - t_arr))
            a = np.gradient(v, 1 / fs)
        else:
            a = np.zeros_like(t)
            if st["sensor_id"] == spike_station:
                i0 = int(3 * fs)
                a[i0:i0 + fs] = 3.0 * np.sin(2 * math.pi * 5 * t[:fs])
        scale = (0.5, 0.8, 0.6) if spike_station is None else (1.0, 0.1, 0.1)
        out.append({
            "sensor_id": st["sensor_id"], "lat": st["lat"], "lon": st["lon"],
            "accel": {ax: [round(float(x), 5) for x in (a * k + 1e-4 * rng.standard_normal(len(t)))]
                      for ax, k in zip(("vertical_z", "east_west_e", "north_south_n"), scale)},
        })
    return {"samplingRateHz": fs, "units": "m/s^2", "durationS": duration_s, "stations": out}


# Fields that would carry a decision instead of an observation; mock readings must never contain them.
DECISION_FIELDS = {"severity", "risk_level", "risk_score", "priority", "axis", "axes", "units", "actions",
                   "requires_human_approval", "offshore", "magnitude_sim", "depth_km_sim"}

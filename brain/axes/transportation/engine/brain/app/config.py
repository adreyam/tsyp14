"""Runtime settings. Every value can be overridden with an environment variable."""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


class Settings:
    DATABASE_URL = os.getenv("PARKING_DB_URL", "sqlite:///./parking.db")

    # Shared secrets (change them for any real deployment)
    DEVICE_KEY = os.getenv("PARKING_DEVICE_KEY", "dev-device-key")   # ESP32 gateways
    ADMIN_KEY = os.getenv("PARKING_ADMIN_KEY", "dev-admin-key")      # admin dashboard / API
    ALLOW_DEMO_RESET = os.getenv("PARKING_ALLOW_DEMO_RESET", "1") == "1"

    # A local RFID read and a magnetic "occupied" event at the same space are
    # considered to belong to the same arrival if they are this close in time.
    SPACE_CORRELATION_WINDOW_S = _int("PARKING_SPACE_CORRELATION_WINDOW_S", 30)

    # Magnetic sensor says "occupied" but no RFID tag was identified after this delay.
    UNIDENTIFIED_OCCUPANCY_ALERT_S = _int("PARKING_UNIDENTIFIED_OCCUPANCY_ALERT_S", 30)

    # Vehicle received a space but never arrived there.
    ASSIGNMENT_TIMEOUT_S = _int("PARKING_ASSIGNMENT_TIMEOUT_S", 600)

    # A parked vehicle left its space (magnetic sensor -> vacant). If it has not
    # reached the exit after this delay the session is flagged for verification.
    # Set to 0 to flag on the next watchdog tick.
    LEFT_SPACE_GRACE_S = _int("PARKING_LEFT_SPACE_GRACE_S", 600)

    # Parked vehicle whose tag has not been seen by the local reader for this long.
    # 0 disables the periodic tag-presence check.
    PRESENCE_TIMEOUT_S = _int("PARKING_PRESENCE_TIMEOUT_S", 300)

    GATEWAY_OFFLINE_S = _int("PARKING_GATEWAY_OFFLINE_S", 60)
    WATCHDOG_INTERVAL_S = _int("PARKING_WATCHDOG_INTERVAL_S", 5)



    # ---- Top-3 ranking -------------------------------------------------------
    RANK_RESERVATION_GRACE_S = _int("PARKING_RANK_RESERVATION_GRACE_S", 300)   # hold = ETA + 5 min
    RANK_AVG_SPEED_KMH = float(os.getenv("PARKING_RANK_AVG_SPEED_KMH", "30"))  # urban speed for the ETA estimate
    RANK_DETOUR_FACTOR = float(os.getenv("PARKING_RANK_DETOUR_FACTOR", "1.3")) # road distance ~ 1.3 x straight line
    RANK_OSRM_URL = os.getenv("PARKING_RANK_OSRM_URL", "")                     # e.g. http://router.project-osrm.org ("" = off)
    RANK_FORECAST_HORIZON_MIN = _int("PARKING_RANK_FORECAST_HORIZON_MIN", 30)
    RANK_SNAPSHOT_INTERVAL_S = _int("PARKING_RANK_SNAPSHOT_INTERVAL_S", 300)   # history sampling period
    RANK_DEMO_EXTRA_LOTS = os.getenv("PARKING_RANK_DEMO_EXTRA_LOTS", "1") == "1"  # add demo lots P03-P05 so a Top 3 exists


settings = Settings()

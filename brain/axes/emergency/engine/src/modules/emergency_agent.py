"""
Emergency Axis Agent.

Serves as the operational dispatch layer for the Emergency axis. It integrates
standalone physics/analytic modules (PLUM Engine & Crime Hotspot Engine, Flood Engine,
Catalog Quake Engine, Tsunami Engine, and Storm Engine) to construct standardized dispatch
orders and public alerts.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .plum_engine import PlumStream, StreamReport, WaveformTelemetry
from .earthquake_engine import EarthquakeEngine
from .crime_hotspot import CrimeHotspotEngine, RawCrimeEvent, IncidentContext
from .hazard_common import (
    HazardAssessment,
    load_hazard_config,
    map_risk_level_to_severity,
)
from .flood_engine import FloodEngine
from .catalog_quake_engine import CatalogQuakeEngine
from .tsunami_engine import TsunamiEngine
from .storm_engine import StormEngine
from .hazard_playbooks import (
    get_playbook_actions,
    run_llm_planner_if_enabled,
)


class ActionItem(BaseModel):
    type: str          # "dispatch_units", "alert", "corridor_clear", "structural_check", "evacuate", "monitor"
    resource: str      # "units:nearest", "channel:public", "road:corridor"
    detail: str
    priority: int = 1  # 0 = Critical / Immediate, 1 = High, 2 = Medium, 3 = Low
    requires_human_approval: bool = False
    # Units requested by a dispatch_units order, e.g. ["fire_engine", "fire_engine", "ambulance"].
    # Known types: fire_engine, ambulance, police, rescue. The dashboard simulation reads this list.
    units: List[str] = Field(default_factory=list)


# Units sent by a hazard playbook "dispatch_units" action, per hazard and risk level.
HAZARD_UNITS: Dict[str, Dict[str, List[str]]] = {
    "flood": {"critical": ["rescue", "rescue", "ambulance"], "warning": ["rescue", "ambulance"]},
    "earthquake": {"critical": ["rescue", "fire_engine", "ambulance", "police"], "warning": ["rescue", "ambulance"]},
    "tsunami": {"critical": ["rescue", "police", "ambulance"], "warning": ["rescue", "police"]},
    "storm": {"critical": ["rescue", "fire_engine", "ambulance"], "warning": ["rescue", "fire_engine"]},
}


class EmergencyAgentResponse(BaseModel):
    axis: str = "emergency"
    severity: int
    confidence: float
    status: str = "ok"
    plumAlertTriggered: bool = False
    peakMMI: float = 0.0
    plumMode: Optional[str] = None                 # "network" (full PLUM) or "single_station"
    plumDetections: List[Dict[str, Any]] = Field(default_factory=list)
    plumSuppressed: List[Dict[str, Any]] = Field(default_factory=list)
    plumPending: List[Dict[str, Any]] = Field(default_factory=list)   # triggers awaiting a neighbour
    stationPeakMMI: Dict[str, float] = Field(default_factory=dict)
    hazard: Optional[str] = None
    risk_level: Optional[str] = None
    risk_score: Optional[float] = None
    requires_human_approval: bool = False
    hazard_assessment: Optional[HazardAssessment] = None
    actions: List[ActionItem]
    evidence: List[str]


class EmergencyAgent:
    """Operational agent coordinating response logic and underlying domain engines."""

    def __init__(
        self,
        plum_stream: Optional[PlumStream] = None,
        crime_engine: Optional[CrimeHotspotEngine] = None,
        flood_engine: Optional[FloodEngine] = None,
        catalog_quake_engine: Optional[CatalogQuakeEngine] = None,
        tsunami_engine: Optional[TsunamiEngine] = None,
        storm_engine: Optional[StormEngine] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.config = config or load_hazard_config()
        self.plum = plum_stream or PlumStream()   # full PLUM network algorithm, fed packet by packet
        self.crime = crime_engine or CrimeHotspotEngine()
        self.flood = flood_engine or FloodEngine(self.config)
        self.catalog_quake = catalog_quake_engine or CatalogQuakeEngine(self.config)
        self.tsunami = tsunami_engine or TsunamiEngine(self.config)
        self.storm = storm_engine or StormEngine(self.config)
        self.earthquake = EarthquakeEngine(self.config)   # merges PLUM + catalog into one result

        # In-memory short-term buffer for multi-event correlation
        self._event_buffer: List[Dict[str, Any]] = []
        self._last_plum_report: Optional[StreamReport] = None
        self._last_plum_ts: float = 0.0
        self._last_catalog_assessment: Optional[HazardAssessment] = None
        self._last_catalog_ts: float = 0.0

    # Only the hazard engines read the buffer. Other events (PLUM waveforms in particular,
    # hundreds of KB each) are not kept, so memory stays bounded on a long-running server.
    BUFFERED_KINDS = {
        "hydro_observation", "weather_forecast", "weather_observation",
        "official_weather_alert", "sea_level_observation", "seismic_catalog",
    }

    def _buffer_event(self, event_dict: Dict[str, Any]) -> None:
        """Stores hazard events in memory and purges records older than 6 hours."""
        now = time.time()
        if event_dict.get("kind") in self.BUFFERED_KINDS:
            self._event_buffer.append(dict(event_dict))
        # Keep only events within last 6 hours (21600 seconds)
        cutoff = now - 21600
        self._event_buffer = [
            e for e in self._event_buffer if float(e.get("ts", now)) >= cutoff
        ]

    def reset_hazard_state(self) -> None:
        """Forget buffered hazard readings and the last catalog quake (start a fresh drill)."""
        self._event_buffer = []
        self.plum.reset()
        self._last_plum_report, self._last_plum_ts = None, 0.0
        self._last_catalog_assessment, self._last_catalog_ts = None, 0.0

    # ------------------------------------------------------------------ earthquakes
    def _feed_plum(self, event_dict: Dict[str, Any]) -> StreamReport:
        """Adds the waveforms of a seismic event to the PLUM stream and re-runs the network algorithm."""
        data = event_dict.get("data", {})
        ts = float(event_dict.get("ts") or time.time())
        lat, lon = event_dict.get("lat"), event_dict.get("lon")
        if "stationNetwork" in data:   # gateway packet: several stations, same start time
            net = data["stationNetwork"]
            dur = float(net.get("durationS") or 0) or (
                len(net["stations"][0]["accel"]["vertical_z"]) / float(net.get("samplingRateHz", 100)))
            self.plum.add_network(net, start=ts - dur)
        else:                          # one sensor's packet
            tel = WaveformTelemetry(**data.get("rawWaveformTelemetry", data))
            sid = tel.sensorId or data.get("sensor_id") or f"{event_dict.get('source', 'sensor')}@{lat},{lon}"
            if (tel.lat is None and lat is None) or (tel.lon is None and lon is None):
                raise ValueError(f"{sid}: sensor position (lat/lon) is required to find its neighbours")
            n = len(tel.components.get("vertical_z", []))
            self.plum.add_telemetry(tel, sid, lat, lon, start=ts - n / tel.samplingRateHz)
        rep = self.plum.evaluate()
        self._last_plum_report, self._last_plum_ts = rep, ts
        return rep

    def _plum_fields(self, rep: Optional[StreamReport]) -> Dict[str, Any]:
        if rep is None:
            return {}
        res = rep.result
        return {
            "plumMode": "network",
            "plumAlertTriggered": bool(res.detections),
            "peakMMI": max(rep.station_peak_mmi.values()) if rep.station_peak_mmi else 0.0,
            "plumDetections": [asdict(d) for d in res.detections],
            "plumSuppressed": [asdict(x) for x in res.suppressed],
            "plumPending": [asdict(x) for x in res.pending],
            "stationPeakMMI": rep.station_peak_mmi,
        }

    def _recent(self, ts: float, ref: float) -> bool:
        return ts > 0 and abs(ref - ts) <= self.earthquake.fusion_window_s

    def _earthquake_response(self, event_dict: Dict[str, Any], now_ts: float) -> Dict[str, Any]:
        plum = self._last_plum_report if self._recent(self._last_plum_ts, now_ts) else None
        cat = self._last_catalog_assessment if self._recent(self._last_catalog_ts, now_ts) else None
        assessment = self.earthquake.assess(plum, cat, catalog_ts=self._last_catalog_ts if cat else None,
                                            plum_ts=self._last_plum_ts if plum else None)
        extra_actions: List[ActionItem] = []
        if plum is not None and not plum.result.detections and plum.result.suppressed:
            sup = plum.result.suppressed[0]
            extra_actions.append(ActionItem(
                type="sensor_check", resource=f"sensor:{sup.station}",
                detail=(f"PLUM suppressed an isolated trigger at {sup.station} (MMI {sup.trigger_mmi:.2f}): "
                        f"no neighbour confirmation. No public alert; schedule a sensor inspection."),
                priority=3))
        return self._hazard_assessment_to_response(assessment, event_dict, extra=self._plum_fields(plum),
                                                   extra_actions=extra_actions)

    def _get_buffered_by_kinds(self, kinds: set[str]) -> List[Dict[str, Any]]:
        return [e for e in self._event_buffer if e.get("kind") in kinds]

    def _hazard_assessment_to_response(
        self,
        assessment: HazardAssessment,
        raw_event: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
        extra_actions: Optional[List[ActionItem]] = None,
    ) -> Dict[str, Any]:
        """Converts a HazardAssessment into a standardized EmergencyAgentResponse dictionary."""
        playbook_actions = get_playbook_actions(assessment)
        refined_actions = run_llm_planner_if_enabled(assessment, playbook_actions)

        action_items: List[ActionItem] = []
        for act in refined_actions:
            action_items.append(
                ActionItem(
                    type=act.type,
                    resource=act.resource,
                    detail=act.detail,
                    priority=act.priority,
                    requires_human_approval=act.requires_human_approval,
                    units=(
                        HAZARD_UNITS.get(assessment.hazard, {}).get(assessment.risk_level, [])
                        if act.type == "dispatch_units" else []
                    ),
                )
            )

        action_items.extend(extra_actions or [])
        severity = map_risk_level_to_severity(assessment.risk_level, self.config)
        requires_approval = (
            assessment.needs_human_confirmation
            or any(a.requires_human_approval for a in action_items)
            or assessment.risk_level == "critical"
        )

        return EmergencyAgentResponse(
            axis="emergency",
            severity=severity,
            confidence=assessment.confidence,
            status="ok",
            **{"plumAlertTriggered": False, "peakMMI": 0.0, **(extra or {})},
            hazard=assessment.hazard,
            risk_level=assessment.risk_level,
            risk_score=assessment.risk_score,
            requires_human_approval=requires_approval,
            hazard_assessment=assessment,
            actions=action_items,
            evidence=assessment.evidence,
        ).model_dump()

    def process_event(self, event_dict: dict) -> dict:
        """Main entry point compatible with LangGraph Orchestrator payload schema."""
        self._buffer_event(event_dict)

        actions: List[ActionItem] = []
        evidence: List[str] = []
        severity = 1
        confidence = 0.8
        plum_alert = False
        peak_mmi = 0.0

        kind = event_dict.get("kind", "")
        data = event_dict.get("data", {})
        source = event_dict.get("source", "unknown")

        # ------------------------------------------------------------------- #
        # 1. Seismic waveforms -> full PLUM network algorithm -> unified earthquake result
        # ------------------------------------------------------------------- #
        if "stationNetwork" in data or "rawWaveformTelemetry" in data or kind == "seismic_sensor":
            ts = float(event_dict.get("ts") or time.time())
            try:
                self._feed_plum(event_dict)
            except Exception as e:
                return EmergencyAgentResponse(
                    severity=1, confidence=0.3, status="ok", plumMode="network",
                    actions=[ActionItem(type="sensor_check", resource="sensor:seismic",
                                        detail="Seismic packet rejected; check the sensor's data feed.", priority=3)],
                    evidence=[f"PLUM input error: {e}"],
                ).model_dump()
            return self._earthquake_response(event_dict, ts)

        # ------------------------------------------------------------------- #
        # 2. Acoustic / Crime Incident Path (Crime Hotspot Engine) - Existing
        # ------------------------------------------------------------------- #
        elif kind in {"acoustic_gunshot_triangulation", "crime_hotspot"}:
            try:
                crime_event = RawCrimeEvent(
                    precursorEventId=event_dict.get("id", "EVT-RAW"),
                    category=kind,
                    sensorSource=source,
                    decibelLevel=data.get("decibelLevel"),
                    associatedContext=IncidentContext(**data.get("associatedContext", {}))
                )
                risk_score, classification, crime_evidence = self.crime.analyze_event(crime_event)
                evidence.extend(crime_evidence)

                severity = 5 if risk_score >= 0.75 else (3 if risk_score >= 0.5 else 2)
                confidence = 0.85

                actions.extend([
                    ActionItem(
                        type="dispatch_units",
                        resource="units:nearest",
                        detail=f"Dispatch Tactical/Police units for {classification} (Score: {risk_score}).",
                        priority=0 if severity >= 4 else 2,
                        requires_human_approval=(severity >= 5),
                        units=(["police", "police", "ambulance"] if severity >= 5 else ["police"]),
                    ),
                    ActionItem(
                        type="alert",
                        resource="channel:public",
                        detail=f"Broadcast active incident notification for {kind}.",
                        priority=1,
                        requires_human_approval=False,
                    ),
                ])
            except Exception as e:
                evidence.append(f"Crime hotspot processing error: {str(e)}")
                severity = 3

            return EmergencyAgentResponse(
                severity=severity,
                confidence=confidence,
                plumAlertTriggered=plum_alert,
                peakMMI=peak_mmi,
                requires_human_approval=(severity >= 5),
                actions=actions,
                evidence=evidence,
            ).model_dump()

        # ------------------------------------------------------------------- #
        # 3. Hydrological & Flood Hazard Path (NEW)
        # ------------------------------------------------------------------- #
        elif kind in {"hydro_observation", "weather_forecast"}:
            recent_events = self._get_buffered_by_kinds({
                "hydro_observation", "weather_forecast", "weather_observation"
            })
            assessment = self.flood.evaluate(recent_events)
            return self._hazard_assessment_to_response(assessment, event_dict)

        # ------------------------------------------------------------------- #
        # 4. Seismic Catalog & Tsunami Path (NEW)
        # ------------------------------------------------------------------- #
        elif kind == "seismic_catalog":
            # PLUM is merged by the EarthquakeEngine (not passed as plum_active, which would count it twice)
            quake_assessment = self.catalog_quake.evaluate(event_dict)
            ts = float(event_dict.get("ts") or time.time())
            self._last_catalog_assessment, self._last_catalog_ts = quake_assessment, ts

            # If offshore candidate, evaluate tsunami potential
            if quake_assessment.metadata.get("offshore") and quake_assessment.metadata.get("magnitude", 0) >= 6.5:
                recent_sea_level = self._get_buffered_by_kinds({"sea_level_observation"})
                tsunami_assessment = self.tsunami.evaluate(quake_assessment, recent_sea_level)
                if tsunami_assessment.risk_level in {"warning", "critical"}:
                    # the tsunami leads the response; the earthquake (PLUM + catalog) stays visible in it
                    quake = self._earthquake_response(event_dict, ts)
                    tsunami_assessment.evidence.append(
                        f"Same earthquake: {str(quake.get('risk_level')).upper()} "
                        f"(index {quake.get('risk_score')}, PLUM peak MMI {quake.get('peakMMI', 0):.2f}).")
                    plum = self._last_plum_report if self._recent(self._last_plum_ts, ts) else None
                    return self._hazard_assessment_to_response(tsunami_assessment, event_dict, extra=self._plum_fields(plum))

            return self._earthquake_response(event_dict, ts)

        # ------------------------------------------------------------------- #
        # 5. Sea Level Gauge Observation Path (NEW)
        # ------------------------------------------------------------------- #
        elif kind == "sea_level_observation":
            if self._last_catalog_assessment is not None:
                # Correlate with recent catalog earthquake
                recent_sea_level = self._get_buffered_by_kinds({"sea_level_observation"})
                tsunami_assessment = self.tsunami.evaluate(self._last_catalog_assessment, recent_sea_level)
                if tsunami_assessment.metadata.get("candidate"):
                    return self._hazard_assessment_to_response(tsunami_assessment, event_dict)

            # Normal baseline sea-level telemetry
            evidence.append(f"Recorded sea-level telemetry from station {data.get('station_id', 'unknown')}.")
            return EmergencyAgentResponse(
                severity=1,
                confidence=0.8,
                status="ok",
                hazard="tsunami",
                risk_level="low",
                risk_score=0.05,
                actions=[ActionItem(type="monitor", resource="sensor:tide_gauge", detail="Sea-level within baseline limits.", priority=3)],
                evidence=evidence,
            ).model_dump()

        # ------------------------------------------------------------------- #
        # 6. Meteorological & Storm Hazard Path (NEW)
        # ------------------------------------------------------------------- #
        elif kind in {"weather_observation", "official_weather_alert"}:
            recent_weather = self._get_buffered_by_kinds({
                "weather_observation", "weather_forecast", "official_weather_alert"
            })
            storm_assessment = self.storm.evaluate(recent_weather)

            # Also check if heavy rain triggers flood assessment
            recent_hydro = self._get_buffered_by_kinds({
                "hydro_observation", "weather_forecast", "weather_observation"
            })
            flood_assessment = self.flood.evaluate(recent_hydro)

            # Select dominant hazard
            if flood_assessment.risk_score > storm_assessment.risk_score and flood_assessment.risk_level in {"warning", "critical"}:
                return self._hazard_assessment_to_response(flood_assessment, event_dict)

            return self._hazard_assessment_to_response(storm_assessment, event_dict)

        # ------------------------------------------------------------------- #
        # 7a. Vision Smoke & Fire Detection (Edge CV Camera)
        # ------------------------------------------------------------------- #
        elif kind in {"vision_fire_smoke", "cv_fire"}:
            conf = data.get("confidence", 0.8)
            fire_detected = data.get("fire_detected", False)
            smoke_detected = data.get("smoke_detected", False)
            flame_pct = data.get("flame_area_pct", 0.0)

            if fire_detected:
                severity = 5 if conf >= 0.85 else 4
                evidence.append(f"CV camera confirmed active fire (confidence={conf:.2f}, flame_area={flame_pct}%).")
                actions.extend([
                    ActionItem(
                        type="dispatch_units",
                        resource="units:nearest",
                        detail=f"Dispatch Fire Brigade & EMS to fire zone ({event_dict.get('lat')}, {event_dict.get('lon')}).",
                        priority=0,
                        units=["fire_engine", "fire_engine", "ambulance"] if severity >= 5 else ["fire_engine", "ambulance"],
                    ),
                    ActionItem(
                        type="alert",
                        resource="channel:public",
                        detail="CRITICAL: Structural/Active fire detected via computer vision. Evacuate immediate sector.",
                        priority=0,
                    ),
                    ActionItem(
                        type="corridor_clear",
                        resource="road:corridor",
                        detail="Request Mobility Axis to clear emergency fire transit corridor.",
                        priority=1,
                    ),
                ])
            elif smoke_detected:
                severity = 3
                evidence.append(f"CV camera detected dense smoke plume (confidence={conf:.2f}).")
                actions.extend([
                    ActionItem(
                        type="dispatch_units",
                        resource="units:nearest",
                        detail="Dispatch reconnaissance fire unit for smoke inspection.",
                        priority=2,
                        units=["fire_engine"],
                    ),
                    ActionItem(
                        type="alert",
                        resource="channel:public",
                        detail="Advisory: Smoke plume detected in district.",
                        priority=2,
                    ),
                ])
            else:
                evidence.append(f"CV camera event without fire or smoke confirmation (confidence={conf:.2f}).")
                actions.append(ActionItem(
                    type="monitor",
                    resource="sensor:camera",
                    detail="No fire or smoke confirmed; keep camera feed under watch.",
                    priority=3,
                ))

        # ------------------------------------------------------------------- #
        # 7b. Street-level Flood Sensor (ultrasonic water depth)
        # ------------------------------------------------------------------- #
        elif kind in {"flood_sensor", "flood"}:
            water_lvl = data.get("water_level_cm", 0.0)
            critical_thresh = data.get("critical_threshold_cm", 50.0)
            submerged_lanes = data.get("submerged_lane_count", 0)

            if water_lvl >= critical_thresh:
                severity = 5
                evidence.append(f"Critical flood stage breached: water level {water_lvl}cm >= threshold {critical_thresh}cm.")
                actions.extend([
                    ActionItem(
                        type="dispatch_units",
                        resource="units:nearest",
                        detail="Deploy Civil Protection & Water Rescue teams to flooded zone.",
                        priority=0,
                        units=["rescue", "rescue"],
                    ),
                    ActionItem(
                        type="alert",
                        resource="channel:public",
                        detail=f"URGENT: Flash flood warning. Water depth at {water_lvl}cm. Avoid underpasses.",
                        priority=0,
                    ),
                    ActionItem(
                        type="corridor_clear",
                        resource="road:corridor",
                        detail=f"Block flooded roadway ({submerged_lanes} lanes submerged) and divert all traffic.",
                        priority=0,
                    ),
                ])
            else:
                severity = 2
                evidence.append(f"Water level elevated ({water_lvl}cm) but below critical threshold ({critical_thresh}cm).")
                actions.append(ActionItem(
                    type="monitor",
                    resource="sensor:hydrology",
                    detail="Continue automated hydrological monitoring of water drainage rate.",
                    priority=3,
                ))

        # ------------------------------------------------------------------- #
        # 7. Direct Incident Triggers (Accident, Fire, Gas Leak, Collapse) - Existing
        # ------------------------------------------------------------------- #
        elif kind in {"accident", "fire_alarm", "collapse", "gas_leak"}:
            if kind == "accident":
                # Severity from what the camera observed: injuries and blocked lanes
                injuries = int(data.get("injuries_reported", 0) or 0)
                lanes = int(data.get("lanes_blocked", 0) or 0)
                severity = min(5, 2 + (injuries >= 1) + (injuries >= 3) + (lanes >= 2))
                evidence.append(
                    f"Accident severity {severity}/5 from observations: {injuries} injured, "
                    f"{lanes} lane(s) blocked, {data.get('vehicles_involved', '?')} vehicle(s)."
                )
            else:
                severity = data.get("severity", 4)
            evidence.append(f"Received direct emergency trigger: kind={kind} from source={source}.")

            actions.extend([
                ActionItem(
                    type="dispatch_units",
                    resource="units:nearest",
                    detail=f"Dispatch Police & EMS to reported {kind} event.",
                    priority=0,
                    requires_human_approval=(severity >= 5),
                    units={
                        "accident": ["police", "ambulance"],
                        "fire_alarm": ["fire_engine", "ambulance"],
                        "collapse": ["rescue", "fire_engine", "ambulance"],
                        "gas_leak": ["fire_engine", "police"],
                    }[kind] + (["ambulance"] if kind == "accident" and data.get("injuries_reported", 0) >= 3 else []),
                ),
                ActionItem(
                    type="alert",
                    resource="channel:public",
                    detail=f"Broadcast active incident notification for {kind}.",
                    priority=1,
                    requires_human_approval=False,
                ),
            ])

        # ------------------------------------------------------------------- #
        # 8. Fallback Path - Existing
        # ------------------------------------------------------------------- #
        else:
            evidence.append(f"Received generic telemetry event ({kind}).")
            severity = 1
            actions.append(ActionItem(
                type="monitor",
                resource="channel:internal",
                detail="Logging raw metric; no emergency dispatch required.",
                priority=4,
                requires_human_approval=False,
            ))

        return EmergencyAgentResponse(
            severity=severity,
            confidence=confidence,
            plumAlertTriggered=plum_alert,
            peakMMI=peak_mmi,
            requires_human_approval=(severity >= 5),
            actions=actions,
            evidence=evidence,
        ).model_dump()

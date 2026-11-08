"""
Unified earthquake assessment: one result per quake from two independent detectors.

  * PLUM (plum_engine.PlumStream): local ground shaking measured by the city's station network.
    Seconds after the shaking starts; no magnitude or epicentre.
  * Catalog (catalog_quake_engine.CatalogQuakeEngine): USGS / EMSC solution with magnitude,
    depth and epicentre. Minutes after the quake; also covers quakes too far to shake the city.

When both see the same quake (within `fusion_window_s`), they are merged into a single
HazardAssessment(hazard="earthquake"). Source counting for the 'critical' rule: every PLUM
station that confirmed the shaking (MMI >= secondary threshold in a confirmed event) is one
independent instrument, the catalog solution is one more. Shaking without a confirmed PLUM
detection (pending or below the trigger) raises the level to 'watch' but counts as no source. A PLUM detection on its own (trigger
+ confirming station) can therefore reach 'critical' before any catalog solution arrives.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .hazard_common import (
    AffectedArea,
    GeoPoint,
    HazardAssessment,
    RiskDriver,
    apply_critical_source_cap,
    load_hazard_config,
)
from .plum_engine import PlumConfig, StreamReport

_ORDER = {"low": 0, "watch": 1, "warning": 2, "critical": 3}


class EarthquakeEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None, plum_cfg: PlumConfig = PlumConfig()):
        self.config = config or load_hazard_config()
        eq = self.config.get("earthquake", {})
        self.critical_mmi = float(eq.get("plum_critical_mmi", 6.0))
        self.watch_mmi = float(eq.get("plum_watch_mmi", 3.0))
        self.fusion_window_s = float(eq.get("fusion_window_s", 600))
        self.min_sources = self.config.get("source_agreement", {}).get("min_sources_for_critical", 2)
        self.city = self.config.get("city_center", {"lat": 36.8065, "lon": 10.1815})
        self.plum_cfg = plum_cfg

    # ------------------------------------------------------------------ PLUM part
    def plum_part(self, rep: Optional[StreamReport]) -> Optional[Dict[str, Any]]:
        """Level, score, sources and evidence implied by the PLUM network alone."""
        if rep is None:
            return None
        res, peaks = rep.result, rep.station_peak_mmi
        peak_sid = max(peaks, key=peaks.get)
        peak = peaks[peak_sid]
        evidence = [f"PLUM network: {len(peaks)} station(s) reporting, peak MMI {peak:.2f} at {peak_sid}"]
        evidence += rep.qc
        if res.detections:
            members = {d.trigger_station for d in res.detections} | {d.confirming_station for d in res.detections}
            members |= {sid for sid, m in peaks.items() if m >= self.plum_cfg.secondary_mmi}
            level = "critical" if peak >= self.critical_mmi else "warning"
            sources = len(members)
            for d in res.detections:
                evidence += d.evidence
            evidence.append(f"Confirmed by {sources} station(s): " + ", ".join(sorted(members)))
        elif res.pending:
            # unconfirmed: raises the level to watch but is not an independent confirmation
            level, sources, members = "watch", 0, {p.station for p in res.pending}
            evidence += [f"Trigger at {p.station} (MMI {p.trigger_mmi:.2f}) {p.reason}" for p in res.pending]
        else:
            members = set()
            # a suppressed trigger is not trusted shaking: only the other stations count
            rejected = {x.station for x in res.suppressed}
            trusted = [m for sid, m in peaks.items() if sid not in rejected]
            peak = max(trusted) if trusted else 1.0
            level = "watch" if peak >= self.watch_mmi else "low"
            sources = 0   # shaking without a confirmed PLUM detection does not corroborate anything
            evidence += [f"Suppressed trigger at {x.station} (MMI {x.trigger_mmi:.2f}): {x.reason}" for x in res.suppressed]
            if rejected:
                evidence.append(f"Isolated trigger(s) ignored; peak MMI elsewhere {peak:.2f}")
            elif level == "low":
                evidence.append(f"No station reached the trigger threshold (MMI {self.plum_cfg.primary_mmi})")
        score = round(min(1.0, max(0.0, (peak - 2.0) / 5.0)), 2)
        return {"level": level, "score": score, "sources": sources, "peak_mmi": peak, "peak_station": peak_sid,
                "stations": sorted(members), "evidence": evidence, "detected": bool(res.detections),
                "detection_time": rep.abs_time(res.detections[0].detection_time_s) if res.detections else None}

    # ------------------------------------------------------------------ fusion
    def assess(self, plum: Optional[StreamReport], catalog: Optional[HazardAssessment],
               catalog_ts: Optional[float] = None, plum_ts: Optional[float] = None) -> HazardAssessment:
        p = self.plum_part(plum)
        if catalog is not None and p is not None and catalog_ts is not None and plum_ts is not None \
                and abs(catalog_ts - plum_ts) > self.fusion_window_s:
            # not the same quake: keep the most recent observation only
            if catalog_ts > plum_ts:
                p = None
            else:
                catalog = None
        if p is None and catalog is None:
            raise ValueError("no earthquake input")

        drivers: List[RiskDriver] = []
        evidence: List[str] = []
        meta: Dict[str, Any] = {}
        levels, scores, sources, confidences = [], [], 0, []

        if catalog is not None:
            cm = catalog.metadata or {}
            levels.append(cm.get("uncapped_risk_level", catalog.risk_level))
            scores.append(float(cm.get("uncapped_risk_score", catalog.risk_score)))
            sources += 1   # the catalog solution itself
            confidences.append(catalog.confidence)
            drivers += catalog.drivers
            evidence += [e for e in catalog.evidence if not e.startswith("Safety constraint applied")]
            meta.update({k: v for k, v in cm.items() if not k.startswith("uncapped_")})
        if p is not None:
            levels.append(p["level"])
            scores.append(p["score"])
            sources += p["sources"]
            confidences.append(0.95 if p["detected"] else 0.6)
            drivers += [RiskDriver(factor="plum_peak_mmi", value=p["peak_mmi"], contribution=p["score"]),
                        RiskDriver(factor="plum_confirming_stations", value=len(p["stations"]) if p["detected"] else 0,
                                   contribution=0.2 if p["detected"] else 0.0)]
            evidence = p["evidence"] + evidence
            meta.update({"plum_peak_mmi": p["peak_mmi"], "plum_peak_station": p["peak_station"],
                         "plum_detected": p["detected"], "plum_stations": p["stations"],
                         "plum_detection_time": p["detection_time"]})
        both = catalog is not None and p is not None and p["detected"]
        if both:
            evidence.append("PLUM network and catalog solution describe the same earthquake (merged).")

        level = max(levels, key=lambda lv: _ORDER.get(lv, 0))
        score = round(max(scores), 2)
        confidence = round(min(0.99, max(confidences) + (0.04 if both else 0.0)), 2)
        if catalog is not None and catalog.affected_area is not None:
            area = catalog.affected_area
        else:
            area = AffectedArea(center=GeoPoint(lat=float(self.city["lat"]), lon=float(self.city["lon"])), radius_km=10.0)

        assessment = HazardAssessment(
            hazard="earthquake",
            risk_level=level,
            risk_score=score,
            confidence=confidence,
            drivers=drivers,
            affected_area=area,
            eta_minutes=None,
            independent_source_count=sources,
            evidence=evidence,
            metadata=meta,
        )
        assessment = apply_critical_source_cap(assessment, self.min_sources)
        parts = (["catalog solution"] if catalog is not None else []) + \
            ([f"{p['sources']} PLUM station(s)"] if p and p["sources"] else [])
        assessment.evidence.append(f"Earthquake {assessment.risk_level.upper()} (index {assessment.risk_score:.2f}; "
                                   f"independent sources: {', '.join(parts) if parts else 'none'})")
        return assessment

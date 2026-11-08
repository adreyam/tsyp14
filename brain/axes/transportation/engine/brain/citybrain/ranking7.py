"""Top-3 ranking with the driver's routine as a 7th criterion, computed on the BACKEND.

Same maths as app/services/ranking.py (min-max -> CRITIC -> weighted sum) plus "routine_m": the weighted mean
distance from a lot to the driver's frequent places that are ACTIVE at the simulated clock (their schedule window is
open). The routine weight is capped at 25 %; with no active place the result is exactly the six-criteria ranking.
"""
from __future__ import annotations

import math
from typing import Dict, List

from app.services import ranking as base

CRITERIA = list(base.CRITERIA)
COST = set(base.COST) | {"routine_m"}
ROUTINE_CAP = 0.25
DEFAULT_ROUTINE_WEIGHT = 0.10


def _min(h):
    return int(h[:2]) * 60 + int(h[3:])


def is_active(schedule: List[dict], dow: int, minutes: int) -> bool:
    """dow: 0 = Monday ... 6 = Sunday."""
    weekend = dow >= 5
    for w in schedule:
        day_ok = w["days"] == "all" or (w["days"] == "weekend") == weekend
        if day_ok and _min(w["from"]) <= minutes < _min(w["to"]):
            return True
    return False


def active_places(places: List[dict], dow: int, minutes: int) -> List[dict]:
    return [p for p in places if is_active(p["schedule"], dow, minutes)]


def routine_metres(lot: dict, active: List[dict]) -> float:
    wsum = sum(p["weight"] for p in active)
    return sum(p["weight"] * base.haversine_m(lot["lat"], lot["lon"], p["lat"], p["lon"]) for p in active) / wsum


def _cap(w: Dict[str, float], name: str, cap: float) -> bool:
    if w[name] <= cap:
        return False
    excess = w[name] - cap
    others = [k for k in w if k != name]
    osum = sum(w[k] for k in others)
    w[name] = cap
    for k in others:
        w[k] += excess * (w[k] / osum) if osum > 0 else excess / len(others)
    return True


def rank(candidates: List[dict], places: List[dict], dow: int, minutes: int, top_n: int = 3) -> dict:
    active = active_places(places, dow, minutes)
    use = bool(active)
    lots = [dict(c) for c in candidates]
    if use:
        for c in lots:
            c["routine_m"] = round(routine_metres(c, active))
    names = CRITERIA + (["routine_m"] if use else [])
    if not lots:
        return {"ranking": [], "weights": {}, "weights_source": "none", "routine": {"used": False, "active": []}}
    for c in lots:
        for k in names:
            if c.get(k) is None or not math.isfinite(c[k]):
                raise ValueError(f"lot {c.get('lot_id')}: criterion {k!r} is missing")
    marks = {k: base.min_max([c[k] for c in lots], k in COST) for k in names}
    if len(lots) >= base.MIN_FOR_CRITIC:
        w, source = base.critic_weights(marks), "critic"
    else:
        w, source = dict(base.DEFAULT_WEIGHTS), "default"
        if use:
            w = {k: v * (1 - DEFAULT_ROUTINE_WEIGHT) for k, v in w.items()}
            w["routine_m"] = DEFAULT_ROUTINE_WEIGHT
    capped = _cap(w, "routine_m", ROUTINE_CAP) if use else False
    out = []
    for i, c in enumerate(lots):
        raw = {k: w[k] * marks[k][i] for k in names}
        out.append({**c, "score": round(min(1.0, sum(raw.values())), 4), "marks": {k: round(marks[k][i], 4) for k in names},
                    "contributions": {k: round(v, 4) for k, v in raw.items()}})
    out.sort(key=lambda r: (-r["score"], -r["free_now"], r["drive_min"], r["lot_id"]))
    for n, r in enumerate(out, 1):
        r["rank"] = n
    return {"ranking": out[:top_n], "weights": {k: round(v, 4) for k, v in w.items()}, "weights_source": source,
            "routine": {"used": use, "active": [p["label"] for p in active], "cap_applied": capped}}

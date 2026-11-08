"""Top-3 parking-lot ranking: normalise -> CRITIC weights -> weighted sum.

Pure Python (no numpy, no database): give it candidate dicts, get a ranked list back.
Fully explainable: every result carries its raw values, 0-1 marks, weights and
each criterion's contribution to the score.

Six criteria (from the PFE's recommendation module):
    drive_min   minutes to reach the lot            cost    (shorter is better)
    walk_m      metres from the lot to the goal     cost
    free_now    free spaces now                     benefit
    free_30     predicted free spaces in 30 min     benefit
    pressure    drivers already heading there per free space   cost (competition)
    reputation  driver feedback, pulled to 0.5 when there are few reviews   benefit
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

CRITERIA = ("drive_min", "walk_m", "free_now", "free_30", "pressure", "reputation")
COST = {"drive_min", "walk_m", "pressure"}            # smaller raw value = better

# Used when CRITIC cannot work (fewer than MIN_FOR_CRITIC candidates). ASSUMPTION: tune with your team.
DEFAULT_WEIGHTS = {"drive_min": 0.20, "walk_m": 0.15, "free_now": 0.20,
                   "free_30": 0.20, "pressure": 0.15, "reputation": 0.10}
MIN_FOR_CRITIC = 3
REVIEWS_FOR_FULL_TRUST = 10


# ------------------------------------------------------------------ geometry
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ reputation
def shrunk_reputation(review_sum: float, review_count: int) -> float:
    """Average rating (1..5) mapped to 0..1, pulled toward the neutral 0.5 until 10 reviews exist."""
    if review_count <= 0:
        return 0.5
    raw = (review_sum / review_count - 1.0) / 4.0
    raw = min(1.0, max(0.0, raw))
    trust = min(review_count, REVIEWS_FOR_FULL_TRUST) / REVIEWS_FOR_FULL_TRUST
    return 0.5 + trust * (raw - 0.5)


# ------------------------------------------------------------------ maths
def min_max(values: Sequence[float], cost: bool) -> List[float]:
    """0..1 where 1 is always the best. A criterion identical for all lots gives 0.5 everywhere
    (it cannot separate the lots, and CRITIC will give it weight 0 anyway)."""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values)
    return [((hi - v) if cost else (v - lo)) / (hi - lo) for v in values]


def _std(xs: Sequence[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    sa, sb = _std(a), _std(b)
    if sa < 1e-12 or sb < 1e-12:
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * sa * sb)


def critic_weights(columns: Dict[str, List[float]]) -> Dict[str, float]:
    """CRITIC: weight_j ~ std_j * sum_k (1 - corr_jk).

    A criterion that barely varies gets a small weight; two criteria that say the same
    thing share their weight. `columns` are the already-normalised 0..1 marks.
    """
    names = list(columns)
    info = {}
    for j in names:
        s = _std(columns[j])
        conflict = sum(1.0 - _corr(columns[j], columns[k]) for k in names if k != j)
        info[j] = s * conflict
    total = sum(info.values())
    if total < 1e-12:
        return {j: 1.0 / len(names) for j in names}
    return {j: info[j] / total for j in names}


# ------------------------------------------------------------------ ranking
def rank_lots(candidates: List[dict], top_n: int = 3, weights: Optional[Dict[str, float]] = None) -> dict:
    """candidates: [{"lot_id": str, <one numeric value per criterion>, ...}]

    Returns {"ranking": [...], "weights": {...}, "weights_source": "critic|default|custom"}.
    Each ranking entry has the original candidate plus score, rank, marks, contributions.
    """
    if not candidates:
        return {"ranking": [], "weights": {}, "weights_source": "none"}
    for c in candidates:
        for k in CRITERIA:
            v = c.get(k)
            if v is None or not math.isfinite(v):
                raise ValueError(f"lot {c.get('lot_id')}: criterion {k!r} is missing or not finite")

    marks = {k: min_max([c[k] for c in candidates], k in COST) for k in CRITERIA}

    if weights is not None:
        total = sum(weights.get(k, 0.0) for k in CRITERIA)
        if total <= 0:
            raise ValueError("custom weights must sum to a positive number")
        w, source = {k: weights.get(k, 0.0) / total for k in CRITERIA}, "custom"
    elif len(candidates) >= MIN_FOR_CRITIC:
        w, source = critic_weights(marks), "critic"
    else:
        w, source = dict(DEFAULT_WEIGHTS), "default"   # CRITIC is meaningless with 1-2 lots

    out = []
    for i, c in enumerate(candidates):
        m = {k: round(marks[k][i], 4) for k in CRITERIA}
        raw = {k: w[k] * marks[k][i] for k in CRITERIA}
        contrib = {k: round(v, 4) for k, v in raw.items()}
        out.append({**c, "score": round(min(1.0, sum(raw.values())), 4), "marks": m, "contributions": contrib})
    # ties: the lot with more free spaces now, then the closer one, then the id (deterministic)
    out.sort(key=lambda r: (-r["score"], -r["free_now"], r["drive_min"], r["lot_id"]))
    for n, r in enumerate(out, 1):
        r["rank"] = n
    return {"ranking": out[:top_n], "weights": {k: round(v, 4) for k, v in w.items()}, "weights_source": source}

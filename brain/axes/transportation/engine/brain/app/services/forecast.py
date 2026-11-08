"""Occupancy forecast (+30 min) with a fallback ladder, as in the PFE.

The PFE trains LightGBM on months of history. This system has no history yet, so the
ladder below is what can run today; `predict_occupancy_rate` is the single place to
plug a trained model in later (level 1).

  level 1  seasonal change : now + (typical rate at target time - typical rate now),
                             needs >= MIN_SAMPLES snapshots in both time windows (same weekday)
  level 2  persistence     : "it will look like now"           (always possible while the lot reports)
  level 3  lot average     : mean of all its snapshots          (when there is no live reading)
  level 4  default         : 50 %                                (no data at all)
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..models import OccupancySnapshot
from .common import now

MIN_SAMPLES = 6          # snapshots needed in a window before its average is trusted
WINDOW_MIN = 15          # +/- minutes around a time of day
LOOKBACK_DAYS = 28


def record_snapshot(db: DB, lot_id: str, total: int, available: int, ts: Optional[datetime] = None) -> None:
    db.add(OccupancySnapshot(lot_id=lot_id, ts=ts or now(), total=total, occupied=max(0, total - available)))


def _typical_rate(db: DB, lot_id: str, when: datetime) -> Tuple[Optional[float], int]:
    """Mean occupancy rate at this time of day, on the same weekday, over the last weeks."""
    since = when - timedelta(days=LOOKBACK_DAYS)
    rows = db.scalars(select(OccupancySnapshot).where(
        OccupancySnapshot.lot_id == lot_id, OccupancySnapshot.ts >= since, OccupancySnapshot.ts < when)).all()
    mins = when.hour * 60 + when.minute
    rates = []
    for r in rows:
        if r.total <= 0 or r.ts.weekday() != when.weekday():
            continue
        d = abs((r.ts.hour * 60 + r.ts.minute) - mins)
        if min(d, 1440 - d) <= WINDOW_MIN:
            rates.append(r.occupied / r.total)
    return (sum(rates) / len(rates), len(rates)) if rates else (None, 0)


def predict_occupancy_rate(db: DB, lot_id: str, current_rate: Optional[float], horizon_min: int,
                           at: Optional[datetime] = None) -> Tuple[float, int, str]:
    """Return (predicted occupancy rate 0..1, fallback level 1-4, explanation)."""
    t = at or now()
    target = t + timedelta(minutes=horizon_min)

    if current_rate is not None:
        past, n_past = _typical_rate(db, lot_id, t)
        fut, n_fut = _typical_rate(db, lot_id, target)
        if past is not None and fut is not None and n_past >= MIN_SAMPLES and n_fut >= MIN_SAMPLES:
            rate = min(1.0, max(0.0, current_rate + (fut - past)))
            return rate, 1, f"seasonal change {fut - past:+.2f} from {n_past}/{n_fut} past snapshots"
        return min(1.0, max(0.0, current_rate)), 2, "persistence (not enough history for this weekday/time)"

    rows = db.scalars(select(OccupancySnapshot).where(OccupancySnapshot.lot_id == lot_id)).all()
    rates = [r.occupied / r.total for r in rows if r.total > 0]
    if rates:
        return sum(rates) / len(rates), 3, "lot average (no live reading)"
    return 0.5, 4, "default 50% (no data at all)"

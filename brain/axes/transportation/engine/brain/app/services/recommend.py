"""Glue: read the live lot state, build candidates, rank, reserve, give feedback."""
import json
import logging
import urllib.request
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..models import Gateway, LotProfile, ParkingLot, Reservation, Space
from . import forecast, ranking
from .common import iso, now

log = logging.getLogger("parking.recommend")


# ------------------------------------------------------------------ reservations (competition)
def expire_reservations(db: DB) -> None:
    for r in db.scalars(select(Reservation).where(Reservation.status == "ACTIVE", Reservation.expires_at < now())):
        r.status = "EXPIRED"


def en_route(db: DB, lot_id: str) -> int:
    expire_reservations(db)
    return db.scalar(select(func.count()).select_from(Reservation).where(
        Reservation.lot_id == lot_id, Reservation.status == "ACTIVE")) or 0


def create_reservation(db: DB, lot_id: str, eta_min: float) -> Reservation:
    t = now()
    res = Reservation(lot_id=lot_id, created_at=t,
                      expires_at=t + timedelta(minutes=eta_min, seconds=settings.RANK_RESERVATION_GRACE_S))
    db.add(res)
    db.flush()
    return res


def consume_reservation(db: DB, lot_id: str) -> None:
    """A vehicle just entered this lot: the oldest driver who was heading there has arrived."""
    expire_reservations(db)
    r = db.scalar(select(Reservation).where(Reservation.lot_id == lot_id, Reservation.status == "ACTIVE")
                  .order_by(Reservation.id))
    if r:
        r.status = "ARRIVED"


# ------------------------------------------------------------------ travel estimate
def _osrm_minutes(o_lat, o_lon, d_lat, d_lon) -> Optional[float]:
    if not settings.RANK_OSRM_URL:
        return None
    url = (f"{settings.RANK_OSRM_URL.rstrip('/')}/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}"
           "?overview=false")
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return json.loads(r.read())["routes"][0]["duration"] / 60.0
    except Exception as e:                       # network down, bad answer: fall back to the estimate
        log.warning("OSRM failed (%s), using straight-line estimate", e)
        return None


def drive_minutes(o_lat, o_lon, d_lat, d_lon) -> tuple:
    m = _osrm_minutes(o_lat, o_lon, d_lat, d_lon)
    if m is not None:
        return m, "osrm"
    km = ranking.haversine_m(o_lat, o_lon, d_lat, d_lon) / 1000.0 * settings.RANK_DETOUR_FACTOR
    return km / settings.RANK_AVG_SPEED_KMH * 60.0, "straight-line x detour"


# ------------------------------------------------------------------ live lot state
def lot_state(db: DB, lot_id: str) -> dict:
    spaces = db.scalars(select(Space).where(Space.lot_id == lot_id)).all()
    from .common import count_assignable
    total = len(spaces)
    in_service = sum(1 for s in spaces if s.status != "OUT_OF_SERVICE" and not s.sensor_fault)
    available = count_assignable(db, lot_id)
    gws = db.scalars(select(Gateway).where(Gateway.lot_id == lot_id)).all()
    seen = [g.last_seen_at for g in gws if g.last_seen_at]
    online = bool(seen) and (now() - max(seen)).total_seconds() <= settings.GATEWAY_OFFLINE_S
    return {"total": total, "in_service": in_service, "available": available, "gateway_online": online}


def snapshot_all(db: DB) -> int:
    n = 0
    for lot in db.scalars(select(ParkingLot)):
        st = lot_state(db, lot.id)
        if st["in_service"] > 0:
            forecast.record_snapshot(db, lot.id, st["in_service"], st["available"])
            n += 1
    return n


# ------------------------------------------------------------------ the recommendation
def build_candidates(db: DB, origin: tuple, destination: tuple, exclude: Optional[List[str]] = None,
                     require_online: bool = False, max_walk_m: float = 2000.0, eta_fn=None) -> tuple:
    """Eligible lots with their six raw criterion values (no scoring). Returns (candidates, excluded)."""
    exclude = set(exclude or [])
    candidates, excluded = [], []
    profiles = {p.lot_id: p for p in db.scalars(select(LotProfile))}
    for lot in db.scalars(select(ParkingLot).order_by(ParkingLot.id)):
        prof = profiles.get(lot.id)
        if lot.id in exclude:
            excluded.append({"lot_id": lot.id, "reason": "excluded by the caller (redirect)"}); continue
        if prof is None:
            excluded.append({"lot_id": lot.id, "reason": "no location registered for this lot"}); continue
        st = lot_state(db, lot.id)
        if require_online and not st["gateway_online"]:
            excluded.append({"lot_id": lot.id, "reason": "gateway offline: availability cannot be trusted"}); continue
        if st["in_service"] == 0:
            excluded.append({"lot_id": lot.id, "reason": "no space in service"}); continue
        walk_check = ranking.haversine_m(prof.lat, prof.lon, destination[0], destination[1])
        if walk_check > max_walk_m:
            excluded.append({"lot_id": lot.id, "reason": f"too far from the destination ({walk_check:.0f} m > {max_walk_m:.0f} m)"}); continue
        heading = en_route(db, lot.id)
        if st["available"] - heading <= 0:
            excluded.append({"lot_id": lot.id, "reason": f"full: {st['available']} free, {heading} drivers already heading there"}); continue

        eta, eta_src = (eta_fn(lot.id), "traffic simulation route") if eta_fn and eta_fn(lot.id) is not None \
            else drive_minutes(origin[0], origin[1], prof.lat, prof.lon)
        walk = ranking.haversine_m(prof.lat, prof.lon, destination[0], destination[1])
        current_rate = (st["in_service"] - st["available"]) / st["in_service"]
        pred_rate, level, why = forecast.predict_occupancy_rate(
            db, lot.id, current_rate, settings.RANK_FORECAST_HORIZON_MIN)
        free_30 = max(0.0, st["in_service"] * (1 - pred_rate) - heading)
        candidates.append({
            "lot_id": lot.id, "name": lot.name, "lat": prof.lat, "lon": prof.lon,
            "drive_min": round(eta, 2), "walk_m": round(walk, 1),
            "free_now": float(st["available"]), "free_30": round(free_30, 2),
            "pressure": round(heading / max(st["available"], 1), 3),
            "reputation": round(ranking.shrunk_reputation(prof.review_sum, prof.review_count), 3),
            "details": {"total_spaces": st["total"], "drivers_heading_there": heading,
                        "reviews": prof.review_count, "forecast_level": level, "forecast_note": why,
                        "route_source": eta_src, "gateway_online": st["gateway_online"]},
        })
    return candidates, excluded


def recommend(db: DB, origin: tuple, destination: tuple, top_n: int = 3, exclude: Optional[List[str]] = None,
              require_online: bool = False, weights: Optional[dict] = None,
              max_walk_m: float = 2000.0) -> dict:
    candidates, excluded = build_candidates(db, origin, destination, exclude, require_online, max_walk_m)
    res = ranking.rank_lots(candidates, top_n=top_n, weights=weights)
    res.update({"generated_at": iso(now()), "candidates_considered": len(candidates), "excluded": excluded})
    return res

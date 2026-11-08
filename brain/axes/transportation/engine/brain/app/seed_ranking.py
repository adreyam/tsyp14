"""Demo data for the ranking: lot locations, and (optionally) three extra demo lots.

The ranking needs at least 3 lots to return a Top 3, and the base seed has only two.
Extra lots P03-P05 are DEMO data (invented places and occupancy). Turn them off with
PARKING_RANK_DEMO_EXTRA_LOTS=0 when you connect real lots; then register a location for
each real lot in the lot_profiles table (see README).
"""
from sqlalchemy import select

from .config import settings
from .enums import CardStatus, LaneKind, SpaceStatus
from .models import Gateway, Lane, LotProfile, ParkingLot, Space, TempCard

# lot: (name, lat, lon, spaces, occupied_count, reviews_sum, reviews_count)   -- centre of Tunis, INVENTED coordinates
PROFILES = {
    "P01": (36.8065, 10.1815),
    "P02": (36.7948, 10.1800),
}
EXTRA = {
    "P03": ("Medina Parking P03", 36.7990, 10.1700, 12, 5, 38, 9),
    "P04": ("Market Parking P04", 36.8100, 10.1900, 20, 17, 80, 20),
    "P05": ("Lake Parking P05", 36.8300, 10.2300, 15, 3, 4, 1),
}


def seed_ranking(db) -> None:
    if settings.RANK_DEMO_EXTRA_LOTS:
        for lot_id, (name, lat, lon, n_spaces, n_occ, rsum, rcount) in EXTRA.items():
            if db.get(ParkingLot, lot_id):
                continue
            gw = f"GW-{lot_id}"
            db.add(ParkingLot(id=lot_id, name=name))
            db.add(Gateway(id=gw, lot_id=lot_id))
            db.add(Lane(id=f"{lot_id}-ENT1", lot_id=lot_id, gateway_id=gw, kind=LaneKind.ENTRANCE))
            db.add(Lane(id=f"{lot_id}-EXIT1", lot_id=lot_id, gateway_id=gw, kind=LaneKind.EXIT))
            for i in range(n_spaces):
                taken = i < n_occ
                db.add(Space(id=f"{lot_id}-S{i + 1}", lot_id=lot_id, code=f"S{i + 1}", position=i, gateway_id=gw,
                             status=SpaceStatus.OCCUPIED if taken else SpaceStatus.AVAILABLE,
                             sensor_occupied=taken))
            db.add(LotProfile(lot_id=lot_id, lat=lat, lon=lon, review_sum=rsum, review_count=rcount))
    for lot_id, (lat, lon) in PROFILES.items():
        if db.get(ParkingLot, lot_id) and not db.get(LotProfile, lot_id):
            db.add(LotProfile(lot_id=lot_id, lat=lat, lon=lon))
    db.commit()

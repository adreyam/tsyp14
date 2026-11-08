"""Demo data: two parking lots, their lanes, spaces, temporary cards and two registered vehicles.

For real hardware, replace the RFID identifiers with the UIDs your readers print
on the serial monitor (e.g. "04A1B2C3"), or register them via the admin API.
"""
from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .enums import CardStatus, LaneKind, VehicleCategory
from .models import Gateway, Lane, ParkingLot, Space, TempCard, Vehicle
from .services.common import now

LOTS = {
    # lot: (name, gateway, space codes, temporary cards)
    "P01": ("Downtown Parking P01", "GW-P01", ["E1", "E2", "E3"],
            [f"RFID_TEMP_{n:03d}" for n in range(21, 31)]),
    "P02": ("Station Parking P02", "GW-P02", ["A1", "A2", "A3"],
            [f"RFID_TEMP_{n:03d}" for n in range(101, 106)]),
}

VEHICLES = [
    ("V001", "RFID_123", "123 TU 4567", "Registered owner 1"),
    ("V002", "RFID_456", "88 TU 1200", "Registered owner 2"),
]


def seed(db) -> None:
    if db.scalar(select(ParkingLot).limit(1)):
        return
    for lot_id, (name, gw_id, codes, cards) in LOTS.items():
        db.add(ParkingLot(id=lot_id, name=name))
        db.add(Gateway(id=gw_id, lot_id=lot_id))
        db.add(Lane(id=f"{lot_id}-ENT1", lot_id=lot_id, gateway_id=gw_id, kind=LaneKind.ENTRANCE))
        db.add(Lane(id=f"{lot_id}-EXIT1", lot_id=lot_id, gateway_id=gw_id, kind=LaneKind.EXIT))
        for i, code in enumerate(codes):
            db.add(Space(id=f"{lot_id}-{code}", lot_id=lot_id, code=code, position=i, gateway_id=gw_id))
        for uid in cards:
            db.add(TempCard(uid=uid, lot_id=lot_id, status=CardStatus.AVAILABLE))
    for vid, rfid, plate, owner in VEHICLES:
        db.add(Vehicle(id=vid, category=VehicleCategory.REGISTERED, permanent_rfid=rfid, plate=plate,
                       owner=owner, active=True, created_at=now()))
    db.commit()


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        from .seed_ranking import seed_ranking
        seed_ranking(db)


def reset_and_seed() -> None:
    Base.metadata.drop_all(engine)
    init_db()


if __name__ == "__main__":
    import sys
    reset_and_seed() if "--reset" in sys.argv else init_db()
    print("Database ready.")

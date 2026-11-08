"""Loads world/world.json (lots, spaces, drivers, places) into the database."""
import json

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.enums import CardStatus, LaneKind, VehicleCategory
from app.models import Gateway, Lane, LotProfile, ParkingLot, Space, TempCard, Vehicle
from app.services.common import now

from . import models as cm  # noqa: F401  (registers the tables)

CARDS_PER_LOT = 8


def seed(db, world: dict) -> None:
    if db.scalar(select(ParkingLot).limit(1)):
        return
    for lot in world["lots"]:
        lid, gw = lot["id"], f"GW-{lot['id']}"
        db.add(ParkingLot(id=lid, name=lot["name"]))
        db.add(Gateway(id=gw, lot_id=lid))
        db.add(Lane(id=f"{lid}-ENT1", lot_id=lid, gateway_id=gw, kind=LaneKind.ENTRANCE))
        db.add(Lane(id=f"{lid}-EXIT1", lot_id=lid, gateway_id=gw, kind=LaneKind.EXIT))
        for sp in lot["spaces"]:
            db.add(Space(id=f"{lid}-{sp['code']}", lot_id=lid, code=sp["code"], position=sp["position"], gateway_id=gw))
        for n in range(1, CARDS_PER_LOT + 1):
            db.add(TempCard(uid=f"RFID_TEMP_{lid}_{n:02d}", lot_id=lid, status=CardStatus.AVAILABLE))
        db.add(LotProfile(lot_id=lid, lat=lot["lat"], lon=lot["lon"], review_sum=lot["review_sum"], review_count=lot["review_count"]))
    for d in world["drivers"]:
        db.add(cm.Driver(id=d["id"], name=d["name"], rfid=d["rfid"], plate=d["plate"], married=d["married"], kids=d["kids"],
                         shares_tag_with=d["shares_tag_with"], registered_tag=d["registered_tag"]))
        if d["registered_tag"]:
            db.add(Vehicle(id=d["id"], category=VehicleCategory.REGISTERED, permanent_rfid=d["rfid"], plate=d["plate"],
                           owner=f"{d['name']} ({d['id']})", active=True, created_at=now()))
    for p in world["places"]:
        db.add(cm.Place(id=p["id"], driver_id=p["driver"], kind=p["kind"], label=p["label"], x=p["x"], y=p["y"], lat=p["lat"], lon=p["lon"],
                        weight=p["weight"], schedule_json=json.dumps(p["schedule"]), edge=p["edge"], pos=p["pos"]))
    db.commit()


def init_db(world: dict) -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, world)


def reset_and_seed(world: dict) -> None:
    Base.metadata.drop_all(engine)
    init_db(world)

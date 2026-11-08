"""API used by the ESP32 gateways: push sensor/reader events, poll actuator commands."""
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..database import get_db, state_lock
from ..enums import LaneKind
from ..models import Command, Gateway, Lane, Space
from ..schemas import DeviceEvent
from ..services import entrance_flow, exit_flow, space_flow
from ..services.common import now
from ..services.watchdog import gateway_seen

router = APIRouter(prefix="/api/gateways", tags=["gateways"])

LANE_EVENTS = {"LANE_VEHICLE_DETECTED", "LANE_RFID_READ", "LANE_RFID_TIMEOUT", "LANE_VEHICLE_CLEARED",
               "DISPENSE_FAILED", "CARD_RETURNED"}


def require_device_key(x_device_key: str = Header(default="")):
    if x_device_key != settings.DEVICE_KEY:
        raise HTTPException(401, "Invalid device key")


def _gateway(db: DB, gateway_id: str) -> Gateway:
    gw = db.get(Gateway, gateway_id)
    if gw is None:
        raise HTTPException(404, f"Unknown gateway {gateway_id}")
    return gw


@router.post("/{gateway_id}/events", dependencies=[Depends(require_device_key)])
def post_event(gateway_id: str, ev: DeviceEvent, db: DB = Depends(get_db)):
    with state_lock:
        gw = _gateway(db, gateway_id)
        gateway_seen(db, gw)
        uid = ev.uid.strip().upper() if ev.uid else None

        if ev.type in LANE_EVENTS:
            lane = db.get(Lane, ev.lane_id or "")
            if lane is None or lane.gateway_id != gw.id:
                raise HTTPException(404, f"Lane {ev.lane_id} is not connected to {gw.id}")
            flow = entrance_flow if lane.kind == LaneKind.ENTRANCE else exit_flow
            if ev.type == "LANE_VEHICLE_DETECTED":
                flow.vehicle_detected(db, lane)
            elif ev.type == "LANE_RFID_READ":
                if not uid:
                    raise HTTPException(422, "uid is required")
                flow.rfid_read(db, lane, uid)
            elif ev.type == "LANE_RFID_TIMEOUT":
                flow.rfid_timeout(db, lane)
            elif ev.type == "LANE_VEHICLE_CLEARED":
                flow.vehicle_cleared(db, lane)
            elif ev.type == "DISPENSE_FAILED" and lane.kind == LaneKind.ENTRANCE:
                entrance_flow.dispense_failed(db, lane, ev.detail or "")
            elif ev.type == "CARD_RETURNED" and lane.kind == LaneKind.EXIT:
                if not uid:
                    raise HTTPException(422, "uid is required")
                exit_flow.card_returned(db, lane, uid)
            db.commit()
            return {"ok": True, "lane_state": lane.state}

        space = db.scalar(select(Space).where(Space.lot_id == gw.lot_id, Space.code == (ev.space_code or "")))
        if space is None or space.gateway_id != gw.id:
            raise HTTPException(404, f"Space {ev.space_code} is not connected to {gw.id}")
        if ev.type == "SPACE_OCCUPANCY":
            if ev.occupied is None:
                raise HTTPException(422, "occupied is required")
            space_flow.occupancy_changed(db, space, ev.occupied)
        elif ev.type == "SPACE_RFID_READ":
            if not uid:
                raise HTTPException(422, "uid is required")
            space_flow.rfid_seen(db, space, uid)
        elif ev.type == "SPACE_SENSOR_FAULT":
            space_flow.sensor_fault(db, space, True, ev.detail or "")
        elif ev.type == "SPACE_SENSOR_OK":
            space_flow.sensor_fault(db, space, False)
        db.commit()
        return {"ok": True, "space_status": space.status}


@router.get("/{gateway_id}/commands", dependencies=[Depends(require_device_key)])
def poll_commands(gateway_id: str, db: DB = Depends(get_db)):
    with state_lock:
        gw = _gateway(db, gateway_id)
        gateway_seen(db, gw)
        cmds = db.scalars(select(Command).where(Command.gateway_id == gw.id, Command.delivered_at.is_(None))
                          .order_by(Command.id)).all()
        out = []
        for c in cmds:
            c.delivered_at = now()
            out.append({"id": c.id, "type": c.type, **json.loads(c.payload or "{}")})
        db.commit()
        return {"commands": out}

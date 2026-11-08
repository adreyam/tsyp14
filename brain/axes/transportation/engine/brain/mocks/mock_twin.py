"""Mock Twin layer. Sends sensor readings to the Brain (and to the Guardian), receives actuator commands, and can inject faults.

    python -m mocks.mock_twin          (port 9001)
Environment: BRAIN_URL, GUARDIAN_URL, TWIN_PORT, TWIN_TO_BRAIN_KEY, TWIN_TO_GUARDIAN_KEY, BRAIN_TO_TWIN_KEY, LOGS_DIR
"""
import os
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Header
from pydantic import BaseModel

from .common import Log, iso_now, need_key, post

BRAIN = os.environ.get("BRAIN_URL", "http://127.0.0.1:8000").rstrip("/")
GUARDIAN = os.environ.get("GUARDIAN_URL", "http://127.0.0.1:9002").rstrip("/")
K_BRAIN = os.environ.get("TWIN_TO_BRAIN_KEY", "twin-dev-key")
K_GUARD = os.environ.get("TWIN_TO_GUARDIAN_KEY", "twin-to-guardian-dev-key")
K_IN = os.environ.get("BRAIN_TO_TWIN_KEY", "brain-to-twin-dev-key")

app = FastAPI(title="Mock Twin")
log = Log("mock-twin")
truth = {}          # (lot, space) -> bool : the TRUE state of the city, kept here even when a sensor lies
watch = set()       # lots whose independent gate counter is streamed
actuators = []      # commands received from the Brain
seq = [0]
lock = threading.Lock()


def reading(device, rtype, data):
    seq[0] += 1
    return {"device_id": device, "type": rtype, "ts": iso_now(), "seq": seq[0], "data": data}


def publish(items, to_guardian=True):
    """Same data to the Brain and to the Guardian (both watch the same stream)."""
    if to_guardian:
        threading.Thread(target=post, args=(GUARDIAN + "/api/readings", items, K_GUARD), daemon=True).start()
    return post(BRAIN + "/api/v1/readings", items, K_BRAIN)


def lot_counter(lot):
    return reading(f"sensor:{lot}:counter", "parking.lot.count", {"lot_id": lot, "occupied_total": sum(1 for (l, _), v in truth.items() if l == lot and v)})


def heartbeat():
    while True:
        time.sleep(2)
        for lot in list(watch):
            publish([lot_counter(lot)])


threading.Thread(target=heartbeat, daemon=True).start()


class Space(BaseModel):
    lot_id: str
    space: str
    occupied: bool
    fake: bool = False            # True: the sensor reports it but the real state does not change


class Fake(BaseModel):
    lot_id: str
    space: str
    occupied: bool = True
    seconds: int = 15             # a stuck sensor repeats its lie


class Emergency(BaseModel):
    call_id: str
    kind: str = "ambulance"
    lat: float
    lon: float


class Flood(BaseModel):
    lot_id: str
    space: str
    per_second: int = 120
    seconds: int = 5


@app.post("/api/actuators/{actuator_id}/commands", status_code=202)
def command(actuator_id: str, body: dict, x_api_key: str = Header(default="")):
    need_key(x_api_key, K_IN)
    with lock:
        actuators.append({"ts": iso_now(), "actuator_id": actuator_id, **body})
        if len(actuators) > 1000:
            del actuators[:500]
    log.add("command", f"{actuator_id} <- {body.get('action')} {body.get('params')} ({body.get('reason')})")
    return {"accepted": True, "command_id": body.get("command_id")}


@app.get("/api/actuators/log")
def actuator_log(actuator: str = "", action: str = "", since: str = ""):
    with lock:
        return [a for a in actuators if actuator in a["actuator_id"] and action in a.get("action", "") and a["ts"] > since]


@app.post("/sim/space")
def sim_space(s: Space):
    """A real change in the city (a car parks / leaves), or with fake=true a sensor that lies."""
    if not s.fake:
        truth[(s.lot_id, s.space)] = s.occupied
    watch.add(s.lot_id)
    items = [reading(f"sensor:{s.lot_id}:{s.space}", "parking.space.occupancy", {"lot_id": s.lot_id, "space": s.space, "occupied": s.occupied}), lot_counter(s.lot_id)]
    code, body = publish(items)
    log.add("reading", f"{s.lot_id}-{s.space} occupied={s.occupied}{' (FAKE: true state kept internally)' if s.fake else ''} -> brain HTTP {code}")
    return {"brain_status": code, "brain": body}


@app.post("/sim/fake_sensor")
def fake_sensor(f: Fake):
    """A stuck sensor: keeps reporting a state that is not true, while the independent gate counter says otherwise."""
    watch.add(f.lot_id)

    def run():
        end = time.time() + f.seconds
        while time.time() < end:
            publish([reading(f"sensor:{f.lot_id}:{f.space}", "parking.space.occupancy", {"lot_id": f.lot_id, "space": f.space, "occupied": f.occupied})])
            time.sleep(1.0)
        publish([reading(f"sensor:{f.lot_id}:{f.space}", "parking.space.occupancy", {"lot_id": f.lot_id, "space": f.space, "occupied": truth.get((f.lot_id, f.space), False)}), lot_counter(f.lot_id)])
        log.add("fault_end", f"fake sensor {f.lot_id}-{f.space} recovers and reports the true state again")
    log.add("fault", f"sensor {f.lot_id}-{f.space} reports occupied={f.occupied} but the true state is {truth.get((f.lot_id, f.space), False)} for {f.seconds}s")
    threading.Thread(target=run, daemon=True).start()
    first = publish([reading(f"sensor:{f.lot_id}:{f.space}", "parking.space.occupancy", {"lot_id": f.lot_id, "space": f.space, "occupied": f.occupied}), lot_counter(f.lot_id)])
    return {"started": True, "first_brain_status": first[0]}


@app.post("/sim/flood")
def flood(f: Flood):
    """A compromised device floods the network."""
    def run():
        end = time.time() + f.seconds
        while time.time() < end:
            batch = [reading(f"sensor:{f.lot_id}:{f.space}", "parking.space.occupancy", {"lot_id": f.lot_id, "space": f.space, "occupied": False}) for _ in range(max(1, f.per_second // 5))]
            publish(batch)
            time.sleep(0.2)
        log.add("fault_end", f"flood from {f.lot_id}-{f.space} stops")
    log.add("fault", f"device sensor:{f.lot_id}:{f.space} floods {f.per_second} msg/s for {f.seconds}s")
    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


@app.post("/sim/emergency")
def emergency(e: Emergency):
    items = [reading("sensor:city:emergency-calls", "emergency.call", {"call_id": e.call_id, "kind": e.kind, "location": {"lat": e.lat, "lon": e.lon}, "severity": 3})]
    code, body = publish(items)
    log.add("emergency_call", f"call {e.call_id} ({e.kind}) at {e.lat:.5f},{e.lon:.5f} -> brain HTTP {code}")
    return {"brain_status": code, "brain": body}


@app.post("/sim/raw")
def raw(body: dict):
    """Send any JSON to the Brain exactly as given (used to test input validation)."""
    key = body.pop("_key", K_BRAIN)
    payload = body.get("payload", body)
    code, resp = post(BRAIN + "/api/v1/readings", payload, key)
    return {"brain_status": code, "brain": resp}


@app.post("/sim/reset")
def reset():
    truth.clear()
    watch.clear()
    with lock:
        actuators.clear()
    return {"ok": True}


@app.get("/api/log")
def get_log():
    return list(log.items)


@app.get("/health")
def health():
    return {"status": "ok", "layer": "mock-twin", "truth_occupied": sorted(f"{l}-{s}" for (l, s), v in truth.items() if v)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("TWIN_PORT", "9001")), log_level="warning")

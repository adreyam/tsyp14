"""Mock Guardian layer: watches the Twin's readings, detects flooding / flapping / a sensor that disagrees with the independent
gate counter, and sends trust verdicts to the Brain. It also receives the Brain's decisions and reports.

    python -m mocks.mock_guardian      (port 9002)
Environment: BRAIN_URL, GUARDIAN_PORT, TWIN_TO_GUARDIAN_KEY, BRAIN_TO_GUARDIAN_KEY, GUARDIAN_TO_BRAIN_KEY, LOGS_DIR
"""
import os
import threading
import time
from collections import deque

import uvicorn
from fastapi import FastAPI, Header
from pydantic import BaseModel

from .common import Log, need_key, post

BRAIN = os.environ.get("BRAIN_URL", "http://127.0.0.1:8000").rstrip("/")
K_TWIN = os.environ.get("TWIN_TO_GUARDIAN_KEY", "twin-to-guardian-dev-key")
K_BRAIN = os.environ.get("BRAIN_TO_GUARDIAN_KEY", "brain-to-guardian-dev-key")
K_OUT = os.environ.get("GUARDIAN_TO_BRAIN_KEY", "guardian-dev-key")
SUSPECT_AFTER, QUARANTINE_AFTER, RELEASE_AFTER = 3.0, 5.0, 8.0     # seconds

app = FastAPI(title="Mock Guardian")
log = Log("mock-guardian")
lock = threading.RLock()
dev = {}            # device_id -> {"occ", "changed", "msgs": deque, "changes": deque}
counters = {}       # lot -> (occupied_total, t)
verdicts = {}       # device_id -> {"verdict", "since", "clean_since"}
mismatch_since = {}
decisions, reports = deque(maxlen=500), deque(maxlen=500)


def send(device, verdict, reason):
    cur = verdicts.get(device, {}).get("verdict")
    if cur == verdict:
        return
    verdicts[device] = {"verdict": verdict, "since": time.time(), "clean_since": None}
    if verdict in ("released", "trusted"):
        verdicts.pop(device, None)
    code, body = post(BRAIN + "/api/v1/verdicts", {"device_id": device, "verdict": verdict, "reason": reason}, K_OUT)
    log.add("verdict", f"{device} -> {verdict}: {reason} (brain HTTP {code})")


@app.post("/api/readings", status_code=202)
def readings(items: list[dict], x_api_key: str = Header(default="")):
    need_key(x_api_key, K_TWIN)
    t = time.time()
    with lock:
        for r in items:
            d = r.get("device_id", "?")
            e = dev.setdefault(d, {"occ": None, "changed": 0.0, "msgs": deque(maxlen=1000), "changes": deque(maxlen=50), "lot": None, "space": None})
            e["msgs"].append(t)
            data = r.get("data", {})
            if r.get("type") == "parking.space.occupancy":
                e["lot"], e["space"] = data.get("lot_id"), data.get("space")
                if e["occ"] is not None and e["occ"] != data.get("occupied"):
                    e["changes"].append(t)
                if e["occ"] != data.get("occupied"):
                    e["changed"] = t
                e["occ"] = data.get("occupied")
            elif r.get("type") == "parking.lot.count":
                counters[data.get("lot_id")] = (data.get("occupied_total"), t)
    return {"accepted": len(items)}


@app.post("/api/decisions", status_code=202)
def got_decision(body: dict, x_api_key: str = Header(default="")):
    need_key(x_api_key, K_BRAIN)
    decisions.append(body)
    log.add("brain_decision", f"{body.get('action')} {body.get('target')}: {body.get('reason')}")
    return {"accepted": True}


@app.post("/api/reports", status_code=202)
def got_report(body: dict, x_api_key: str = Header(default="")):
    need_key(x_api_key, K_BRAIN)
    reports.append(body)
    log.add("brain_report", f"{body.get('device_id')}: {body.get('issue')} {body.get('detail', '')}")
    if body.get("issue") == "flood":
        with lock:
            send(body["device_id"], "quarantined", "flood reported by the Brain rate limiter")
    return {"accepted": True}


class Q(BaseModel):
    device_id: str
    reason: str = "manual quarantine"
    verdict: str = "quarantined"


@app.post("/api/quarantine")
def quarantine(q: Q):
    with lock:
        send(q.device_id, q.verdict, q.reason)
    return {"ok": True}


@app.post("/api/release")
def release(q: Q):
    with lock:
        send(q.device_id, "released", q.reason)
    return {"ok": True}


@app.post("/api/reset")
def reset():
    with lock:
        dev.clear(); counters.clear(); verdicts.clear(); mismatch_since.clear(); decisions.clear(); reports.clear()
    return {"ok": True}


@app.get("/api/log")
def get_log():
    return {"log": list(log.items), "decisions": list(decisions)[-50:], "reports": list(reports)[-50:], "verdicts": verdicts}


@app.get("/health")
def health():
    return {"status": "ok", "layer": "mock-guardian", "devices": len(dev)}


def detector():
    while True:
        time.sleep(0.5)
        t = time.time()
        with lock:
            for d, e in list(dev.items()):
                rate = sum(1 for x in e["msgs"] if t - x <= 1.0)
                if rate > 20 and verdicts.get(d, {}).get("verdict") != "quarantined":
                    send(d, "quarantined", f"message flood: {rate} readings/s")
                elif len([x for x in e["changes"] if t - x <= 20]) >= 6 and verdicts.get(d, {}).get("verdict") not in ("untrusted", "quarantined"):
                    send(d, "untrusted", "flapping: 6 state changes in 20 s")
            for lot, (total, tc) in list(counters.items()):
                if t - tc > 10:
                    continue
                sensors = [(d, e) for d, e in dev.items() if e.get("lot") == lot and e["occ"] is not None]
                n = sum(1 for _, e in sensors if e["occ"])
                if n != total:
                    first = mismatch_since.setdefault(lot, t)
                    excess = n > total
                    bad = [d for d, e in sorted(sensors, key=lambda s: -s[1]["changed"]) if e["occ"] == excess]
                    if bad:
                        d = bad[0]
                        age = t - first
                        if age >= QUARANTINE_AFTER:
                            send(d, "quarantined", f"disagrees with the independent gate counter of {lot} for {int(age)} s ({n} occupied by sensors, {total} by counter)")
                        elif age >= SUSPECT_AFTER:
                            send(d, "untrusted", f"sensor says {'occupied' if excess else 'vacant'} but the gate counter of {lot} says {total} occupied (sensors: {n})")
                else:
                    mismatch_since.pop(lot, None)
                    for d, v in list(verdicts.items()):
                        e = dev.get(d)
                        if e and e.get("lot") == lot and v["verdict"] in ("untrusted", "quarantined") and e["msgs"] and t - e["msgs"][-1] >= 2:
                            v["clean_since"] = v["clean_since"] or t
                            if t - v["clean_since"] >= RELEASE_AFTER:
                                send(d, "released", "readings agree with the gate counter again")


threading.Thread(target=detector, daemon=True).start()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("GUARDIAN_PORT", "9002")), log_level="warning")

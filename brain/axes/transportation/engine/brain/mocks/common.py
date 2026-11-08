import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Log:
    def __init__(self, name):
        d = os.environ.get("LOGS_DIR", "../logs")
        os.makedirs(d, exist_ok=True)
        self.fh = open(os.path.join(d, f"{name}.log"), "a", encoding="utf-8", buffering=1)
        self.items = deque(maxlen=500)
        self.lock = threading.Lock()

    def add(self, kind, text, **extra):
        e = {"ts": iso_now(), "kind": kind, "text": text, **extra}
        with self.lock:
            self.items.append(e)
            self.fh.write(f'{e["ts"]} {kind}: {text}\n')
        return e


def post(url, body, key, timeout=3.0):
    try:
        r = requests.post(url, json=body, headers={"X-API-Key": key}, timeout=timeout)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"raw": r.text[:200]}
    except requests.RequestException as e:
        return 0, {"error": type(e).__name__}


def need_key(header_value, expected):
    from fastapi import HTTPException
    if header_value != expected:
        raise HTTPException(401, "Invalid or missing X-API-Key")

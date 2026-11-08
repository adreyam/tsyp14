"""Messages the Brain sends to its partners (actuator commands to the Twin, decisions/reports to the Guardian).
A slow or dead partner never blocks the simulation: messages go through a bounded queue and a worker thread."""
import itertools
import queue
import threading

import requests

from .timeline import iso_now


class Outbound:
    def __init__(self, cfg, timeline):
        self.cfg, self.tl = cfg, timeline
        self.q = queue.Queue(maxsize=cfg["limits"]["queue_size"])
        self.ids = itertools.count(1)
        self.stats = {"sent": 0, "failed": 0, "dropped": 0}
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True, name="bridge-outbound")
        self.t.start()

    def _put(self, item):
        try:
            self.q.put_nowait(item)
        except queue.Full:
            try:
                self.q.get_nowait()          # drop the oldest
            except queue.Empty:
                pass
            self.stats["dropped"] += 1
            self.q.put_nowait(item)

    def next_id(self, prefix):
        return f"{prefix}-{next(self.ids):05d}"

    def twin_command(self, actuator_id, body, ref=None, quiet=False):
        t = self.cfg["twin"]
        if not t["enabled"]:
            return False
        url = t["url"].rstrip("/") + t["command_path"].format(actuator_id=actuator_id)
        self._put(("twin", url, body, self.cfg["auth"]["outbound"]["twin_key"], t["timeout_s"], f"{actuator_id} {body['action']}", ref, quiet))
        return True

    def guardian(self, kind, body, ref=None):
        g = self.cfg["guardian"]
        if not g["enabled"]:
            return False
        path = g["decision_path"] if kind == "decision" else g["report_path"]
        self._put(("guardian", g["url"].rstrip("/") + path, body, self.cfg["auth"]["outbound"]["guardian_key"], g["timeout_s"], kind, ref, False))
        return True

    def _run(self):
        while not self.stop.is_set():
            try:
                who, url, body, key, timeout, label, ref, *rest = self.q.get(timeout=0.5)
                quiet = bool(rest and rest[0])
            except queue.Empty:
                continue
            try:
                r = requests.post(url, json=body, headers={"X-API-Key": key}, timeout=timeout)
                ok = r.status_code < 300
                detail = f"HTTP {r.status_code}" if ok else f"HTTP {r.status_code}: {r.text[:120]}"
            except requests.RequestException as e:
                ok, detail = False, type(e).__name__
            self.stats["sent" if ok else "failed"] += 1
            if who == "twin" and not (quiet and ok):
                self.tl.add("actuator", "command_ack" if ok else "command_failed", f"{label} -> Twin: {detail}", ref=ref)
            elif not ok:
                self.tl.add("guardian", "report_failed", f"{label} not delivered to the Guardian ({detail})", ref=ref)

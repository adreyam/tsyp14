"""What the Guardian says about every device. Decisions of the Brain depend on it."""
import threading
import time

BAD = ("untrusted", "quarantined")


class TrustRegistry:
    def __init__(self, policy="trust", ttl_s=900):
        self.policy, self.ttl = policy, ttl_s
        self.v = {}                       # device_id -> dict
        self.lock = threading.RLock()

    def set(self, device_id, verdict, reason="", source="guardian", ttl_s=None):
        with self.lock:
            prev = self.v.get(device_id, {}).get("verdict", "unknown")
            if verdict in ("trusted", "released"):
                self.v.pop(device_id, None)
                return prev, "trusted"
            self.v[device_id] = {"device_id": device_id, "verdict": verdict, "reason": reason, "source": source,
                                 "since": time.time(), "expires": time.time() + (ttl_s or self.ttl)}
            return prev, verdict

    def status(self, device_id):
        with self.lock:
            e = self.v.get(device_id)
            if e and e["expires"] < time.time():
                del self.v[device_id]
                e = None
            return e["verdict"] if e else "unknown"

    def usable(self, device_id):
        """May the Brain base a decision on / send a command to this device?"""
        s = self.status(device_id)
        if s in BAD:
            return False
        if s == "unknown" and self.policy == "reject":
            return False
        return True

    def reason(self, device_id):
        with self.lock:
            return self.v.get(device_id, {}).get("reason", "")

    def snapshot(self):
        with self.lock:
            now = time.time()
            return [dict(e, age_s=round(now - e["since"])) for e in self.v.values() if e["expires"] >= now]

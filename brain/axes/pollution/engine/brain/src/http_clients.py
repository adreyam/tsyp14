"""
Real partner clients over HTTP + JSON (standard library only: nothing extra to
install on the demo laptop). They implement the interfaces of clients.py, so
CityBrain does not know whether it talks to a mock or to a real partner.

Every request carries   Authorization: Bearer <brain token>.
Every answer is validated before the Brain uses it. Any problem (refused
connection, timeout, HTTP error, invalid JSON, invalid verdict) raises, and
CityBrain already treats a raising partner as unavailable.
"""
import json
import time
import urllib.error
import urllib.request
from typing import Optional

from pydantic import ValidationError

from .models import BinReading, TrustVerdict


class PartnerError(Exception):
    """A partner could not be reached or sent something unusable."""


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def request_json(method: str, url: str, token: str, body=None,
                 timeout_s: float = 2.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace")
        raise PartnerError(f"{method} {url}: HTTP {e.code} {detail}".strip())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise PartnerError(f"{method} {url}: unreachable ({reason})")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise PartnerError(f"{method} {url}: answer is not JSON")


class HttpGuardianClient:
    """Asks the Guardian for a verdict on one reading.

    After a failure, the client does not call again for `retry_after_s`: a
    Guardian that hangs must not slow every reading of every cycle down to the
    timeout. Readings during that pause get no verdict, so they are ignored."""

    def __init__(self, base_url: str, token: str, verdict_path: str = "/v1/trust-verdict",
                 timeout_s: float = 2.0, retry_after_s: float = 5.0, clock=time.monotonic):
        self.url = base_url.rstrip("/") + verdict_path
        self.token = token
        self.timeout_s = timeout_s
        self.retry_after_s = retry_after_s
        self._clock = clock
        self._paused_until = 0.0
        self._last_error: Optional[str] = None

    def get_trust_verdict(self, reading: BinReading) -> TrustVerdict:
        now = self._clock()
        if now < self._paused_until:
            raise PartnerError(f"guardian unavailable ({self._last_error}); "
                               f"next try in {self._paused_until - now:.0f} s")
        body = {"tool": "get_trust_verdict", "sensor_id": reading.sensor_id,
                "timestamp": _iso(reading.timestamp)}
        try:
            answer = request_json("POST", self.url, self.token, body, self.timeout_s)
            if not isinstance(answer, dict):
                raise PartnerError("verdict is not a JSON object")
            try:
                return TrustVerdict(**answer)
            except ValidationError as e:
                bad = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}"
                                for x in e.errors()[:3])
                raise PartnerError(f"invalid verdict: {bad}")
        except PartnerError as e:
            self._last_error = str(e)
            self._paused_until = self._clock() + self.retry_after_s
            raise


class HttpTwinClient:
    """Sends dispatch orders to the Twin and fetches the city map."""

    def __init__(self, base_url: str, token: str, dispatch_path: str = "/v1/dispatch",
                 map_path: str = "/v1/map", timeout_s: float = 2.0,
                 lots_path: str = "/v1/parking-lots", barrier_path: str = "/v1/parking-barrier"):
        self.base = base_url.rstrip("/")
        self.lots_url = self.base + lots_path
        self.barrier_url = self.base + barrier_path
        self.dispatch_url = self.base + dispatch_path
        self.map_url = self.base + map_path
        self.token = token
        self.timeout_s = timeout_s

    def send_dispatch(self, message: dict) -> None:
        request_json("POST", self.dispatch_url, self.token, message, self.timeout_s)

    def get_map(self) -> dict:
        data = request_json("GET", self.map_url, self.token, None, self.timeout_s)
        if not isinstance(data, dict) or "nodes" not in data or "roads" not in data:
            raise PartnerError("map must be a JSON object with 'nodes' and 'roads'")
        return data

    # ---- air-quality axis ---------------------------------------------------
    def list_parking_lots(self) -> dict:
        """{lot id: zone}, from {"lots": [{"id": "LOT-01", "zone": "B", ...}]}."""
        data = request_json("GET", self.lots_url, self.token, None, self.timeout_s)
        lots = data.get("lots") if isinstance(data, dict) else None
        if not isinstance(lots, list):
            raise PartnerError("parking lots must be {\"lots\": [...]}")
        return {str(l["id"]): str(l["zone"]) for l in lots
                if isinstance(l, dict) and "id" in l and "zone" in l}

    def set_parking_barrier(self, lot_id: str, mode: str) -> None:
        request_json("POST", self.barrier_url, self.token,
                     {"tool": "set_parking_barrier", "id": lot_id, "mode": mode}, self.timeout_s)

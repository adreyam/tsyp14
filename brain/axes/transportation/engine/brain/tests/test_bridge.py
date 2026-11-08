"""Tests of the Brain's interface layer (no SUMO needed): validation, authentication, trust handling, flood protection."""
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("PARKING_DB_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db"))

from citybrain.bridge import config  # noqa: E402
from citybrain.bridge.service import Bridge, BridgeError  # noqa: E402
from citybrain.bridge.trust import TrustRegistry  # noqa: E402


class FakeParking:
    cars = {}


class FakeEngine:
    def __init__(self):
        import threading
        self.ready = threading.Event()
        self.ready.set()
        self.parking = FakeParking()
        self.bridge = None

    def clock(self):
        return 1, 600, "Tue 10:00"

    def call(self, fn):
        return fn()

    def dispatch_emergency(self, kind, lat, lon, call_id):
        return f"amb_{call_id}"


@pytest.fixture()
def bridge():
    cfg = config.load("does-not-exist.yaml")
    cfg["brain"]["logs_dir"] = tempfile.mkdtemp()
    cfg["twin"]["enabled"] = False
    cfg["guardian"]["enabled"] = False
    return Bridge(cfg, FakeEngine())


def now():
    return datetime.now(timezone.utc).isoformat()


def reading(**over):
    r = {"device_id": "sensor:P01:A3", "type": "parking.lot.count", "ts": now(), "data": {"lot_id": "P01", "occupied_total": 2}}
    r.update(over)
    return r


def test_valid_reading_is_accepted(bridge):
    res = bridge.ingest(reading())
    assert res["results"][0]["accepted"]


@pytest.mark.parametrize("bad,error", [
    ({"type": "water.level"}, "invalid_reading"),
    ({"device_id": "x"}, "invalid_reading"),
    ({"ts": "yesterday"}, "invalid_reading"),
    ({"data": {"lot_id": "ZZ", "occupied_total": 1}}, "invalid_data"),
    ({"data": {"lot_id": "P01", "occupied_total": -4}}, "invalid_data"),
])
def test_bad_reading_gives_clear_error(bridge, bad, error):
    with pytest.raises(BridgeError) as e:
        bridge.ingest(reading(**bad))
    assert e.value.error == error and e.value.status == 422 and e.value.message
    assert e.value.body()["hint"]


def test_not_an_object(bridge):
    with pytest.raises(BridgeError):
        bridge.ingest("hello")


def test_empty_and_too_big_batches(bridge):
    with pytest.raises(BridgeError):
        bridge.ingest([])
    with pytest.raises(BridgeError) as e:
        bridge.ingest([reading() for _ in range(201)])
    assert e.value.status == 413


def test_stale_timestamp(bridge):
    with pytest.raises(BridgeError) as e:
        bridge.ingest(reading(ts="2020-01-01T00:00:00Z"))
    assert e.value.error == "stale_timestamp"


def test_flood_is_rate_limited(bridge):
    results = [bridge.ingest([reading()])["results"][0] if True else None for _ in range(1)]
    limited = 0
    for _ in range(60):
        try:
            bridge.ingest(reading())
        except BridgeError as e:
            assert e.error == "rate_limited" and e.status == 429
            limited += 1
    assert limited > 0 and bridge.counters["rate_limited"] > 0


def test_untrusted_device_reading_is_ignored(bridge):
    bridge.verdict({"device_id": "sensor:P01:counter", "verdict": "quarantined", "reason": "test"})
    res = bridge.ingest(reading(device_id="sensor:P01:counter"))
    r = res["results"][0] if "results" in res else res
    assert not r["accepted"] and r["error"] == "device_untrusted"


def test_verdict_validation(bridge):
    with pytest.raises(BridgeError) as e:
        bridge.verdict({"device_id": "sensor:P01:A3", "verdict": "maybe"})
    assert e.value.error == "invalid_verdict"


def test_quarantined_actuator_command_is_withheld_and_manual_override_allowed(bridge):
    bridge.verdict({"device_id": "barrier:P01-EXIT1", "verdict": "quarantined", "reason": "tampering"})
    assert bridge.actuator_command("barrier:P01-EXIT1", "open") is False
    assert bridge.counters["withheld"] == 1
    assert bridge.actuator_command("barrier:P01-EXIT1", "open", manual=True) is True
    assert any(e["kind"] == "manual_override" for e in bridge.tl.items)
    bridge.verdict({"device_id": "barrier:P01-EXIT1", "verdict": "released"})
    assert bridge.actuator_command("barrier:P01-EXIT1", "open") is True


def test_emergency_call_dispatches(bridge):
    res = bridge.ingest({"device_id": "sensor:city:emergency-calls", "type": "emergency.call", "ts": now(),
                         "data": {"call_id": "C-1", "kind": "ambulance", "location": {"lat": 36.8, "lon": 10.18}}})
    assert res["results"][0]["vehicle_id"] == "amb_C-1"


def test_emergency_call_bad_location(bridge):
    with pytest.raises(BridgeError):
        bridge.ingest({"device_id": "sensor:city:emergency-calls", "type": "emergency.call", "ts": now(),
                       "data": {"call_id": "C-2", "kind": "ambulance", "location": {"lat": 99, "lon": 10}}})


def test_unknown_device_policy_reject():
    t = TrustRegistry(policy="reject")
    assert not t.usable("sensor:x:y")
    t.set("sensor:x:y", "trusted")
    t2 = TrustRegistry(policy="trust")
    assert t2.usable("sensor:x:y")


def test_config_environment_override(monkeypatch):
    monkeypatch.setenv("BRAIN_TWIN_URL", "http://twin.example:1234")
    assert config.load("does-not-exist.yaml")["twin"]["url"] == "http://twin.example:1234"

"""Phase 2 readiness: chaos on the links, Twin restart, flapping Guardian, foreign message
shapes (field mapping), HMAC signing, and the partner conformance tool."""
import json
import random
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from energy_brain.adapters import PartnerError
from energy_brain.adapters.base import ReadingsBatch
from energy_brain.adapters.rest import RestGuardian, RestTwin
from energy_brain.adapters.signing import sign, verify
from energy_brain.config import PartnerConfig
from energy_brain.schemas import SwitchCommand
from mocks import guardian as gmock
from mocks import twin as tmock
from scenarios.runner import Step, run_scenario
from scenarios.testbook import TESTBOOK, commands_exactly, event_matches, no_overload


# ------------------------------------------------------------------ chaos on the Twin link

class ChaosTwin:
    """Drops calls, replays the previous batch (duplicates / out-of-order) -- deterministic."""

    def __init__(self, inner, drop=0.1, replay=0.15, seed=3):
        self.inner, self.drop, self.replay, self.rng = inner, drop, replay, random.Random(seed)
        self.prev: ReadingsBatch | None = None
        self.dropped = self.replayed = 0

    def get_readings(self):
        if self.rng.random() < self.drop:
            self.dropped += 1
            raise PartnerError("twin", "get_readings", "timed out (chaos)")
        if self.prev is not None and self.rng.random() < self.replay:
            self.replayed += 1
            return self.prev
        self.prev = self.inner.get_readings()
        return self.prev

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_brain_still_prevents_overload_through_a_lossy_link(tmp_path):
    chaos = {}

    def wrap(t):
        chaos["c"] = ChaosTwin(t)
        return chaos["c"]
    res = run_scenario(TESTBOOK["E2"], log_dir=tmp_path, twin_wrapper=wrap)
    assert chaos["c"].dropped > 5 and chaos["c"].replayed > 5
    assert res["passed"], res["checks"]


def test_twin_restart_does_not_blind_the_brain(tmp_path):
    sc = replace(TESTBOOK["E2"], id="E2R", steps=[Step(10, "twin_restart", {})] + TESTBOOK["E2"].steps,
                 checks=[event_matches("sequence_reset", "restart"), commands_exactly("close S12"), no_overload()])
    res = run_scenario(sc, log_dir=tmp_path)
    assert res["passed"], res["checks"]


def test_flapping_guardian_blocks_action_safely(tmp_path):
    sc = replace(TESTBOOK["E2"], id="E2F", steps=[Step(5, "guardian_flap", {"subject_id": "PL-Z1"})] + TESTBOOK["E2"].steps,
                 checks=[event_matches("verdict_flapping", "PL-Z1"), commands_exactly()])
    res = run_scenario(sc, log_dir=tmp_path)
    assert res["passed"], res["checks"]


# ------------------------------------------------------------------ field mapping (foreign Twin)

ALIEN_CITY = {"site": {"name": "alien-city",
                       "areas": [{"id": "A", "max_kw": 500}, {"id": "B", "max_kw": 600}],
                       "breakers": [{"id": "T1", "type": "tie", "src": "A", "dst": "B", "share": 0.3}]}}
FMAP = {
    "city": {"path": "site", "city_id": "name",
             "zones": {"path": "areas", "fields": {"zone_id": "id", "capacity_kw": "max_kw"}},
             "switches": {"path": "breakers", "fields": {"switch_id": "id", "kind": "type", "from_zone": "src",
                                                           "to_zone": "dst", "transfer_fraction": "share"}}},
    "readings": {"path": "data.meters", "ts_format": "epoch_s",
                 "fields": {"sensor_id": "meter", "zone_id": "loc.area", "value": "p", "unit": "=kW",
                            "ts": "time", "seq": "n"}},
    "switches": {"path": "data", "fields": {"switch_id": "id", "state": "pos", "ts": "time"},
                 "values": {"state": {"ON": "closed", "OFF": "open"}}, "ts_format": "epoch_s"},
}


def _alien(request: httpx.Request) -> httpx.Response:
    now = datetime.now(timezone.utc).timestamp()
    if request.url.path == "/energy/city":
        return httpx.Response(200, json=ALIEN_CITY)
    if request.url.path == "/energy/readings":
        return httpx.Response(200, json={"data": {"meters": [
            {"meter": "M-A", "loc": {"area": "A"}, "p": 321.5, "time": now, "n": 7, "extra": "ignored"},
            {"meter": "M-B", "loc": {"area": "B"}, "p": 280.0, "time": now, "n": 7}]}})
    if request.url.path == "/energy/switches":
        return httpx.Response(200, json={"data": [{"id": "T1", "pos": "OFF", "time": now}]})
    return httpx.Response(404)


def test_foreign_twin_is_understood_through_config_only():
    client = httpx.Client(transport=httpx.MockTransport(_alien), base_url="http://alien")
    t = RestTwin(PartnerConfig(base_url="http://alien", field_map=FMAP), client)
    city = t.get_city()
    assert city.city_id == "alien-city" and city.zones[0].capacity_kw == 500
    assert city.switches[0].effect_known and city.switches[0].transfer_fraction == 0.3
    b = t.get_readings()
    assert not b.rejected and b.valid[0].kw == 321.5 and b.valid[0].zone_id == "A"
    assert t.get_switches()[0].state.value == "open"


def test_unmapped_foreign_twin_fails_loudly():
    client = httpx.Client(transport=httpx.MockTransport(_alien), base_url="http://alien")
    t = RestTwin(PartnerConfig(base_url="http://alien"), client)
    with pytest.raises(PartnerError, match="invalid city descriptor"):
        t.get_city()


# ------------------------------------------------------------------ signing

def test_sign_verify_roundtrip_and_replay():
    h = sign("k", b"body", ts=1000)
    assert verify("k", b"body", h["X-Timestamp"], h["X-Signature"], now=1005) is None
    assert verify("k", b"bodY", h["X-Timestamp"], h["X-Signature"], now=1005) == "signature mismatch"
    assert "replay" in verify("k", b"body", h["X-Timestamp"], h["X-Signature"], now=2000)
    assert verify("k", b"body", None, None) == "unsigned message"


def test_signed_partners(city_a_spec):
    tapp = tmock.create_app(city_a_spec, signing_secret="tw")
    gapp = gmock.create_app(signing_secret="gd")
    twin = RestTwin(PartnerConfig(base_url="http://t", signing_secret="tw"), TestClient(tapp))
    assert twin.send_command(SwitchCommand(switch_id="S12", operation="close", decision_id="d",
                                           reason="r")).status.value == "accepted"
    g = RestGuardian(PartnerConfig(base_url="http://g", signing_secret="gd"), TestClient(gapp))
    assert g.get_verdicts(["PL-Z1"])[0].verdict.value == "trusted"
    # wrong secret: the Guardian's answers are refused, the Twin refuses our commands
    with pytest.raises(PartnerError, match="signature mismatch"):
        RestGuardian(PartnerConfig(base_url="http://g", signing_secret="WRONG"), TestClient(gapp)).get_verdicts(["PL-Z1"])
    unsigned = RestTwin(PartnerConfig(base_url="http://t"), TestClient(tapp))
    with pytest.raises(PartnerError, match="401"):
        unsigned.send_command(SwitchCommand(switch_id="S12", operation="open", decision_id="d", reason="r"))


# ------------------------------------------------------------------ conformance tool

def test_conformance_against_mocks(city_a_spec, city_b_spec):
    from energy_brain.conformance import check_twin, check_guardian
    good = check_twin(RestTwin(PartnerConfig(base_url="http://t"), TestClient(tmock.create_app(city_a_spec))),
                      test_switch="S12", sleep=lambda s: None)
    assert all(r.status != "FAIL" for r in good), [r for r in good if r.status == "FAIL"]
    assert any(r.check.startswith("command round-trip") and r.status == "PASS" for r in good)
    blind = check_twin(RestTwin(PartnerConfig(base_url="http://t"), TestClient(tmock.create_app(city_b_spec))),
                       sleep=lambda s: None)
    assert any(r.status == "WARN" and "capacit" in r.check for r in blind)
    g = check_guardian(RestGuardian(PartnerConfig(base_url="http://g"), TestClient(gmock.create_app())),
                       ["PL-Z1", "S12"])
    assert all(r.status != "FAIL" for r in g)

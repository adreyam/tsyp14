"""Tests for the v0.5 improvements: probability scoring, shedding policy, restoration back-off,
hint hardening, verdict flapping, Twin restarts, future timestamps, J-1 plan."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from energy_brain.config import BrainConfig, PartnerConfig
from energy_brain.dayahead import DayAhead
from energy_brain.decision import expected_exposure, p_overload
from energy_brain.schemas import PowerReading, PriorityHint, TrustVerdict
from energy_brain.state import EnergyState
from scenarios.runner import Step, run_scenario
from scenarios.testbook import TESTBOOK, commands_exactly, event_matches

from test_core import CITY, NOW, make_state

CFG = BrainConfig(twin=PartnerConfig(base_url="http://t"), guardian=PartnerConfig(base_url="http://g"))


# ------------------------------------------------------------------ probability maths

def test_probability_and_expected_exposure():
    assert p_overload(500, 20, 500) == pytest.approx(0.5)
    assert p_overload(460, 20, 500) == pytest.approx(0.0228, abs=1e-3)
    assert expected_exposure(400, 10, 500) == pytest.approx(0, abs=1e-6)
    # far above capacity -> exposure tends to the deterministic overload
    assert expected_exposure(600, 1, 500) == pytest.approx(0.2, abs=1e-3)
    # uncertainty alone creates some exposure at capacity
    assert expected_exposure(500, 20, 500) == pytest.approx(20 * 0.39894 / 500, rel=1e-3)


# ------------------------------------------------------------------ shedding policy

def test_never_shed_on_a_forecast_alone():
    from energy_brain.decision import evaluate
    effects = {s.switch_id: s for s in CITY.switches}
    st = make_state({"A": 400, "B": 590, "H": 300}, slope_a=4.0)   # A measured 476 < 500, forecast far over
    r = evaluate(st, effects, {"FA"})
    why = {c.label: c.why_not for c in r.rejected}
    assert "MEASURED" in why["open FA"] and not r.ranked


def test_shed_is_last_resort_when_measured_over():
    from energy_brain.decision import evaluate
    effects = {s.switch_id: s for s in CITY.switches}
    st = make_state({"A": 600, "B": 590, "H": 300}, slope_a=1.0)
    r = evaluate(st, effects, {"FA", "SAB"})                       # SAB would overload B -> infeasible
    assert r.ranked and r.ranked[0].label == "open FA" and r.ranked[0].kind == "shed"


def test_restore_backoff_when_overload_persists(tmp_path):
    long = replace(TESTBOOK["E11"], id="E11X", duration_s=330,
                   steps=[Step(5, "stuck", {"switch_id": "S42", "stuck": True}),
                          Step(20, "ramp", {"zone_id": "Z4", "add_kw": 300, "rise_s": 60, "hold_s": 400})],
                   checks=[commands_exactly("close S42", "open F4", "close F4", "open F4"),
                           event_matches("restoration", "did not hold")])
    res = run_scenario(long, log_dir=tmp_path)
    assert res["passed"], res["checks"]


# ------------------------------------------------------------------ inbound hardening

class _Log:
    def __init__(self):
        self.events = []

    def emit(self, kind, summary, correlation_id=None, **p):
        self.events.append((kind, summary))


def _brain_with_state():
    from energy_brain.brain import EnergyBrain
    b = EnergyBrain(CFG, None, None, _Log())
    b.state = EnergyState(CFG, CITY)
    return b


def test_priority_hint_expiry_is_capped_and_bad_hints_rejected():
    b = _brain_with_state()
    now = datetime.now(timezone.utc)
    assert b.add_priority_hint(PriorityHint(zone_id="A", source="x", reason="r", expires_at=now + timedelta(days=30)))
    _, exp = b.state.priority_hints["A"]
    assert exp <= now + timedelta(seconds=CFG.inbound.max_hint_ttl_s + 5)
    assert not b.add_priority_hint(PriorityHint(zone_id="NOPE", source="x", reason="r", expires_at=now + timedelta(minutes=1)))
    assert not b.add_priority_hint(PriorityHint(zone_id="A", source="x", reason="r", expires_at=now - timedelta(minutes=1)))
    assert sum(k == "security" for k, _ in b.log.events) == 2


def test_inbound_api_requires_key_and_rate_limits():
    from fastapi.testclient import TestClient
    from energy_brain.run import inbound_app
    b = _brain_with_state()
    b.knowledge = type("K", (), {"level": "L2"})()
    cfg = CFG.model_copy(update={"inbound": CFG.inbound.model_copy(update={"api_key": "s3", "rate_per_min": 3})})
    c = TestClient(inbound_app(b, cfg))
    body = {"zone_id": "A", "source": "flood", "reason": "pump",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    assert c.post("/priority-hints", json=body).status_code == 401
    codes = [c.post("/priority-hints", json=body, headers={"X-API-Key": "s3"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


# ------------------------------------------------------------------ ingestion robustness

def _st():
    st = EnergyState(CFG, CITY)
    st.update_verdicts([TrustVerdict(subject_id="PL-A", verdict="trusted", ts=NOW)], 0.0, NOW)
    return st


def test_twin_restart_resets_sequence_instead_of_blinding_the_brain():
    st = _st()
    st.ingest(PowerReading(sensor_id="PL-A", zone_id="A", ts=NOW, value=300, seq=900), 0.0, False, NOW)
    later = NOW + timedelta(seconds=1)
    out, detail = st.ingest(PowerReading(sensor_id="PL-A", zone_id="A", ts=later, value=301, seq=1), 1.0, False, later)
    assert out == "reset" and "restart" in detail
    # a true replay (same old timestamp) is still refused
    assert st.ingest(PowerReading(sensor_id="PL-A", zone_id="A", ts=NOW, value=300, seq=1), 2.0, False, later)[0] == "duplicate"


def test_future_timestamps_rejected():
    st = _st()
    fut = PowerReading(sensor_id="PL-A", zone_id="A", ts=NOW + timedelta(minutes=5), value=300, seq=1)
    out, detail = st.ingest(fut, 0.0, False, NOW)
    assert out == "rejected" and "future" in detail


def test_flapping_verdict_is_untrusted_until_stable():
    st = EnergyState(CFG, CITY)
    flap_seen = False
    for i in range(8):
        v = "trusted" if i % 2 == 0 else "untrusted"
        st.t = float(i)
        _, flaps = st.update_verdicts([TrustVerdict(subject_id="PL-A", verdict=v, ts=NOW)], float(i), NOW)
        flap_seen |= any(on for _, on in flaps)
    assert flap_seen and st.verdict_status("PL-A", NOW) == "untrusted"
    for i in range(8, 40):                       # stays trusted: flapping clears after the window
        st.t = float(i)
        st.update_verdicts([TrustVerdict(subject_id="PL-A", verdict="trusted", ts=NOW)], float(i), NOW)
    assert st.verdict_status("PL-A", NOW) == "trusted"


# ------------------------------------------------------------------ J-1 plan

def test_day_ahead_smooths_and_wraps(tmp_path):
    da = DayAhead.from_measurements({"A": [100.0] * 50 + [200.0] * 50}, smooth=5)
    assert da.period_s == 100
    assert da.expected("A", 10) == pytest.approx(100) and da.expected("A", 75) == pytest.approx(200)
    assert da.expected("A", 110) == pytest.approx(da.expected("A", 10))       # wraps
    p = tmp_path / "j1.json"
    da.save(p)
    assert DayAhead.load(p).expected("A", 75) == pytest.approx(200)


def test_plan_deviation_reported(tmp_path):
    res = run_scenario(TESTBOOK["E2"], log_dir=tmp_path)
    import json
    lines = [json.loads(l) for l in open(res["log"])]
    assert any(l.get("kind") == "plan_deviation" and "Z1" in l["summary"] for l in lines)

import pytest
from fastapi.testclient import TestClient

from energy_brain.adapters import PartnerError
from energy_brain.adapters.rest import RestGuardian, RestTwin
from energy_brain.config import PartnerConfig
from energy_brain.schemas import SwitchCommand, SwitchOperation
from mocks import guardian as gmock
from mocks import twin as tmock


def quiet(spec):
    """Remove noise and oscillation so physics assertions are exact."""
    for z in spec["zones"]:
        z.update(noise_kw=0, amplitude_kw=0)
    return spec


@pytest.fixture
def twin(city_a_spec, clock):
    app = tmock.create_app(quiet(city_a_spec), api_key="k", clock=clock)
    client = TestClient(app)
    return RestTwin(PartnerConfig(base_url="http://t", api_key="k"), client), app.state.sim, client


@pytest.fixture
def guardian():
    app = gmock.create_app(api_key="g")
    client = TestClient(app)
    return RestGuardian(PartnerConfig(base_url="http://g", api_key="g"), client), client


def cmd(sw, op):
    return SwitchCommand(switch_id=sw, operation=op, decision_id="d1", reason="test")


# ---------------------------------------------------------------- twin physics through the adapter

def loads(t):
    return {r.zone_id: r.kw for r in t.get_readings().valid}


def test_tie_switch_transfers_load(twin):
    t, sim, _ = twin
    before = loads(t)
    assert t.send_command(cmd("S12", SwitchOperation.close)).status.value == "accepted"
    after = loads(t)
    assert after["Z1"] == pytest.approx(before["Z1"] * 0.65, rel=1e-3)
    assert after["Z2"] == pytest.approx(before["Z2"] + before["Z1"] * 0.35, rel=1e-3)
    assert {s.switch_id: s.state.value for s in t.get_switches()}["S12"] == "closed"


def test_feeder_open_deenergizes_zone(twin):
    t, _, _ = twin
    t.send_command(cmd("F3", SwitchOperation.open))
    assert loads(t)["Z3"] == 0


def test_stuck_switch_accepts_but_does_not_move(twin):
    t, sim, _ = twin
    sim.set_stuck("S12", True)
    assert t.send_command(cmd("S12", SwitchOperation.close)).status.value == "accepted"
    assert {s.switch_id: s.state.value for s in t.get_switches()}["S12"] == "open"


def test_duplicate_command_not_reexecuted(twin):
    t, sim, _ = twin
    c = cmd("S12", SwitchOperation.close)
    t.send_command(c)
    sim.set_switch("S12", "open")
    assert t.send_command(c).status.value == "duplicate"
    assert sim.switches["S12"].state == "open"


def test_ramp_and_spoof(twin, clock):
    t, sim, client = twin
    base = loads(t)["Z1"]
    client.post("/_scenario/ramp", json={"zone_id": "Z1", "add_kw": 200, "rise_s": 20, "hold_s": 60},
                headers={"X-API-Key": "k"})
    clock.t += 10
    assert loads(t)["Z1"] == pytest.approx(base + 100, rel=1e-3)
    client.post("/_scenario/spoof", json={"sensor_id": "PL-Z2", "value_kw": 999, "duration_s": 5},
                headers={"X-API-Key": "k"})
    assert loads(t)["Z2"] == 999
    assert sim.true_loads()["Z2"] != 999      # the Twin keeps the truth internally
    clock.t += 6
    assert loads(t)["Z2"] != 999              # spoof expired


# ---------------------------------------------------------------- adapter robustness

def test_bad_reading_rejected_rest_kept(twin, monkeypatch):
    t, sim, _ = twin
    real = sim.readings
    monkeypatch.setattr(sim, "readings", lambda: real()[:-1] + [{"sensor_id": "x", "value": "lots"}])
    batch = t.get_readings()
    n = len(real())                                  # 4 zone sensors + 2 substation meters
    assert len(batch.valid) == n - 1 and len(batch.rejected) == 1
    assert "zone_id" in batch.rejected[0][1]


def test_wrong_api_key_is_clear_and_not_retryable(city_a_spec, clock):
    client = TestClient(tmock.create_app(city_a_spec, api_key="right", clock=clock))
    t = RestTwin(PartnerConfig(base_url="http://t", api_key="wrong"), client)
    with pytest.raises(PartnerError, match="authentication refused") as e:
        t.get_city()
    assert e.value.retryable is False


def test_unknown_switch_rejected(twin):
    t, _, _ = twin
    assert t.send_command(cmd("NOPE", SwitchOperation.close)).status.value == "rejected"


def test_guardian_verdicts_and_override(guardian):
    g, client = guardian
    client.post("/_control/override", json={"subject_id": "PL-Z2", "verdict": "untrusted", "reason": "spoof"},
                headers={"X-API-Key": "g"})
    v = {x.subject_id: x.verdict.value for x in g.get_verdicts(["PL-Z1", "PL-Z2"])}
    assert v == {"PL-Z1": "trusted", "PL-Z2": "untrusted"}


def test_guardian_down_raises_retryable_error(guardian):
    g, client = guardian
    client.post("/_control/mode", json={"mode": "silent"}, headers={"X-API-Key": "g"})
    with pytest.raises(PartnerError, match="503") as e:
        g.get_verdicts(["PL-Z1"])
    assert e.value.retryable is True

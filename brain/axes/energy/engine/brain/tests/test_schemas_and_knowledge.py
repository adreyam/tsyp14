from datetime import datetime

import pytest
from pydantic import ValidationError

from energy_brain.config import load_city, load_config
from energy_brain.knowledge import assess, merge
from energy_brain.schemas import CityDescriptor, PowerReading

from conftest import ROOT

TS = "2026-11-04T12:00:00Z"


def test_reading_converts_units():
    assert PowerReading(sensor_id="s", zone_id="z", ts=TS, value=1.5, unit="MW").kw == 1500


@pytest.mark.parametrize("bad, msg", [
    ({"ts": "2026-11-04T12:00:00"}, "timezone"),
    ({"value": -3}, "greater than or equal"),
    ({"unit": "A"}, "unit"),
    ({"surprise": 1}, "Extra inputs"),
])
def test_reading_rejects_malformed(bad, msg):
    with pytest.raises(ValidationError, match=msg):
        PowerReading.model_validate({"sensor_id": "s", "zone_id": "z", "ts": TS, "value": 1, **bad})


def test_city_rejects_dangling_switch_reference():
    with pytest.raises(ValidationError, match="unknown zone"):
        CityDescriptor(zones=[{"zone_id": "A"}], switches=[{"switch_id": "S", "from_zone": "B"}])


def _twin_view(spec):
    from mocks.twin import CitySim
    return CityDescriptor.model_validate(CitySim(spec).descriptor())


def test_fully_described_city_is_L2(city_a_spec):
    k = assess(_twin_view(city_a_spec))
    assert k.level == "L2"
    assert set(k.actionable_switches) == {"S12", "S43", "S42", "S32", "F3", "F4"}


def test_undescribed_city_is_blind_L0(city_b_spec):
    k = assess(_twin_view(city_b_spec))
    assert k.level == "L0" and k.actionable_switches == []


def test_local_knowledge_lifts_city_b_to_L1_but_not_L2(city_b_spec):
    local = load_city(ROOT / "config/knowledge/city_b.yaml")
    k = assess(merge(_twin_view(city_b_spec), local))
    assert k.level == "L1"
    assert "effect unknown" in k.unusable_switches["T-NS"]


def test_config_reads_secrets_from_env(monkeypatch):
    monkeypatch.setenv("TWIN_URL", "http://twin.example:9000")
    monkeypatch.setenv("TWIN_API_KEY", "s3cret")
    cfg = load_config(ROOT / "config/config.yaml")
    assert cfg.twin.base_url == "http://twin.example:9000" and cfg.twin.api_key == "s3cret"

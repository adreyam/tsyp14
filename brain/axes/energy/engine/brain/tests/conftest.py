import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


@pytest.fixture
def city_a_spec():
    return yaml.safe_load((ROOT / "mocks/cities/city_a.yaml").read_text())


@pytest.fixture
def city_b_spec():
    return yaml.safe_load((ROOT / "mocks/cities/city_b.yaml").read_text())


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()

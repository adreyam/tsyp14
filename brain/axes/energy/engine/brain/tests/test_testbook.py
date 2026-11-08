"""The whole energy Test Book must pass in fast mode, on several noise seeds."""
import pytest

from scenarios.runner import run_scenario
from scenarios.testbook import TESTBOOK


@pytest.mark.parametrize("sid", list(TESTBOOK))
@pytest.mark.parametrize("seed", [7, 11])
def test_scenario(sid, seed, tmp_path):
    res = run_scenario(TESTBOOK[sid], log_dir=tmp_path, seed=seed)
    failed = [c for c in res["checks"] if not c["ok"]]
    assert res["passed"], failed

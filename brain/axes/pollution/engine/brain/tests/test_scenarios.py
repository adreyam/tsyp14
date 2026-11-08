import pytest

from demo.garbage import SCENARIOS


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_scenario_passes_all_its_checks(scenario_id):
    result = SCENARIOS[scenario_id](write_log=False)
    failed = [text for text, ok in result.checks if not ok]
    assert not failed, f"{scenario_id} failed: {failed}"

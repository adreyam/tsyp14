"""Training tests: checkpoint/resume continues the history instead of restarting. Needs SUMO (like the other tests)."""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)


@pytest.fixture()
def tiny(tmp_path):
    old = os.getcwd()
    os.chdir(ROOT)
    out = "data/_test_mock"
    subprocess.check_call([sys.executable, "training/mock_data.py", "--scenarios", "6", "--duration", "300", "--out", out])
    yield out, str(tmp_path / "run")
    os.chdir(old)
    shutil.rmtree(os.path.join(ROOT, out), ignore_errors=True)


def test_resume_continues(tiny):
    from training.core import Trainer

    data, run = tiny
    cfg = dict(data_dir=data, episode_seconds=300, n_eval=1)
    t = Trainer(run, cfg)
    t.train(2, eval_every=0, save_every=1)
    states, eps = t.q_states, t.epsilon(t.episode)
    t.close()
    t2 = Trainer(run, cfg)
    assert t2.episode == 2 and t2.resumed_from == 2
    assert t2.q_states == states and len(t2.history) == 2
    t2.train(1, eval_every=0, save_every=1)
    assert t2.episode == 3 and len(t2.history) == 3 and t2.history[-1]["epsilon"] < eps + 1e-9
    t2.close()
    t3 = Trainer(run, cfg, fresh=True)
    assert t3.episode == 0 and t3.q_states == 0
    t3.close()

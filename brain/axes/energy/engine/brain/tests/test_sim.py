"""The simulation must run the real Brain and agree with the Test Book."""
import json

import pytest
from fastapi.testclient import TestClient

from scenarios.runner import Step
from scenarios.testbook import TESTBOOK
from sim.server import create_app
from sim.session import Session


@pytest.mark.parametrize("sid", ["E2", "E6", "E7B"])
def test_session_reproduces_testbook(sid):
    s = Session(TESTBOOK[sid])
    frames = []
    while not s.finished:
        frames.append(s.step())
    assert s.result["passed"], s.result
    f = frames[-1]
    assert set(f["zones"]) == {z["id"] for z in s.city()["zones"]}
    assert json.dumps(frames)            # every frame is JSON-serializable


def test_manual_injection_is_validated_and_flagged():
    s = Session()
    with pytest.raises(ValueError, match="unknown zone_id"):
        s.apply(Step(0, "ramp", {"zone_id": "NOPE", "add_kw": 10}))
    s.apply(Step(0, "ramp", {"zone_id": "Z1", "add_kw": 300, "rise_s": 30, "hold_s": 100}))
    s.step()
    assert any(e["k"] == "scenario_marker" and "Z1 demand rises" in e["s"] for e in s.snapshot(s.sim.true_loads())["events"]) \
        or any("Z1 demand rises" in e.summary for e in s.log.events)


def test_server_endpoints():
    app = create_app(speed=20)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        assert c.post("/api/scenario/e3").json()["ok"]
        assert c.post("/api/scenario/XX").status_code == 404
        r = c.post("/api/inject", json={"action": "spoof", "args": {"sensor_id": "PL-Z9", "value_kw": 1}})
        assert r.status_code == 422 and "unknown zone_id" in r.json()["detail"]
        assert c.post("/api/control", json={"paused": True, "speed": 100}).json() == {"paused": True, "speed": 20}
        html = c.get("/").text
        assert "const LIVE = true" in html

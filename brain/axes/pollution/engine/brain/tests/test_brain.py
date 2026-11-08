from datetime import datetime, timedelta, timezone

from mocks.mock_guardian import ScriptedGuardian
from demo.city import DEPOT, build_graph
from src.audit_log import AuditLog
from src.brain import BrainConfig, CityBrain
from src.models import TrustStatus, TrustVerdict
from src.planner import PlannerConfig

T0 = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
GRAPH = build_graph()


class FakeTwin:
    def __init__(self):
        self.sent, self.fail = [], False

    def send_dispatch(self, message):
        if self.fail:
            raise ConnectionError("twin down")
        self.sent.append(message)


def make(guardian=None):
    twin, log = FakeTwin(), AuditLog()
    config = BrainConfig(planner=PlannerConfig(truck_capacity_kg=200.0, depot_node=DEPOT),
                         reading_interval_s=300.0)
    brain = CityBrain(GRAPH, guardian or ScriptedGuardian(), twin, log, config)
    return brain, twin, log


def when(minutes):
    return T0 + timedelta(minutes=minutes)


def bin_msg(sensor_id="b1", kg=46.0, node="r1c1", minutes=0, **override):
    n = GRAPH.nodes[node]
    msg = {"sensor_id": sensor_id, "weight_kg": kg, "capacity_kg": 50.0,
           "location": {"lat": n.lat, "lon": n.lon}, "zone": "zoneA",
           "zone_type": "crowded", "timestamp": when(minutes).isoformat()}
    msg.update(override)
    return msg


def truck_msg(truck_id="t1", node=DEPOT, minutes=0):
    n = GRAPH.nodes[node]
    return {"truck_id": truck_id, "location": {"lat": n.lat, "lon": n.lon},
            "status": "idle", "timestamp": when(minutes).isoformat()}


def test_a_red_bin_gets_a_truck():
    brain, twin, _ = make()
    brain.step([bin_msg()], [truck_msg()], when(0))
    assert [m["next_stop"]["sensor_id"] for m in twin.sent] == ["b1"]
    assert twin.sent[0]["truck_id"] == "t1"


def test_malformed_bin_message_is_rejected_and_names_the_bad_field():
    brain, twin, log = make()
    brain.step([bin_msg(weight_kg=-5)], [truck_msg()], when(0))
    (e,) = log.of("rejected_message")
    assert "weight_kg" in e["error"] and e["id"] == "b1"
    assert twin.sent == []


def test_message_that_is_not_an_object_is_rejected_without_crashing():
    brain, _, log = make()
    brain.step(["garbage", 42, None], ["also garbage"], when(0))
    assert len(log.of("rejected_message")) == 4
    assert all("not a JSON object" in e["error"] for e in log.of("rejected_message"))


def test_guardian_outage_blocks_dispatch_and_is_logged_once():
    guardian = ScriptedGuardian()
    brain, twin, log = make(guardian)
    guardian.down = True
    brain.step([bin_msg(minutes=0)], [truck_msg()], when(0))
    brain.step([bin_msg(minutes=5)], [truck_msg()], when(5))
    assert twin.sent == []
    assert len(log.of("guardian_unavailable")) == 1
    guardian.down = False
    brain.step([bin_msg(minutes=10)], [truck_msg()], when(10))
    assert len(twin.sent) == 1
    assert len(log.of("guardian_recovered")) == 1


def test_suspicious_is_not_enough_to_act():
    guardian = ScriptedGuardian()
    guardian.distrust("b1", "borderline", TrustStatus.SUSPICIOUS)
    brain, twin, log = make(guardian)
    brain.step([bin_msg()], [truck_msg()], when(0))
    assert twin.sent == []
    assert log.of("reading_blocked")[0]["reason"] == "borderline"


def test_verdict_about_another_sensor_is_ignored():
    class WrongSensorGuardian:
        def get_trust_verdict(self, reading):
            return TrustVerdict(sensor_id="someone-else", status=TrustStatus.TRUSTED,
                                confidence=1.0, checked_at=reading.timestamp)

    brain, twin, log = make(WrongSensorGuardian())
    brain.step([bin_msg()], [truck_msg()], when(0))
    assert twin.sent == [] and len(log.of("invalid_verdict")) == 1


def test_silent_sensor_is_left_out_of_the_plan_until_it_speaks_again():
    brain, twin, log = make()
    brain.step([bin_msg(minutes=0)], [], when(0))            # red, but no truck yet
    for m in (5, 10, 15):
        brain.step([], [], when(m))
    assert log.of("sensor_silent") == []                     # 15 min: not silent yet
    brain.step([], [truck_msg(minutes=20)], when(20))        # more than 3 intervals
    assert len(log.of("sensor_silent")) == 1
    assert twin.sent == []                                   # not planned while silent
    brain.step([bin_msg(minutes=25)], [truck_msg(minutes=25)], when(25))
    assert [m["next_stop"]["sensor_id"] for m in twin.sent] == ["b1"]


def test_dispatch_lost_to_a_twin_outage_is_retried_next_cycle():
    brain, twin, log = make()
    twin.fail = True
    brain.step([bin_msg(minutes=0)], [truck_msg()], when(0))
    assert twin.sent == [] and len(log.of("twin_unavailable")) == 1
    twin.fail = False
    brain.step([bin_msg(minutes=5)], [truck_msg()], when(5))
    assert len(twin.sent) == 1


def test_every_event_says_which_layer_it_comes_from():
    brain, _, log = make()
    brain.step([bin_msg()], [truck_msg()], when(0))
    layers = {e["event"]: e["layer"] for e in log.events}
    assert layers["sensor_reading"] == "twin"
    assert layers["trust_verdict"] == "guardian"
    assert layers["dispatch"] == "city_brain"

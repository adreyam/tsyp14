"""Tests of the emergency-vehicle priority chain (transmitter -> receiver -> controller). Needs SUMO_HOME."""

import os
import xml.etree.ElementTree as ET

import pytest

import sumo_rl
from sumo_rl import PreemptionConfig
from sumo_rl.preemption import OnboardTransmitter, PriorityMessage, RoadsideReceiver


NET = "sumo_rl/nets/single-intersection/single-intersection.net.xml"
BASE = "sumo_rl/nets/single-intersection/single-intersection.rou.xml"
AMB = "sumo_rl/nets/emergency/single_ambulances.rou.xml"
VTYPE = """<vType id="ambulance" vClass="emergency" guiShape="emergency" length="6.5" accel="3.5" decel="5.0"
           maxSpeed="22" speedFactor="1.2"/>"""


def run_single(tmp_path, preempt=True, cfg=None, routes=AMB, horizon=220, fixed=True, actions=None, seed=1):
    """Run the single intersection; return (states per second, events, tripinfo of ambulances, env info)."""
    trip = str(tmp_path / "trip.xml")
    if preempt and cfg is None:
        cfg = PreemptionConfig()
    env = sumo_rl.SumoEnvironment(
        net_file=NET, route_file=f"{BASE},{routes}", fixed_ts=fixed, num_seconds=horizon, delta_time=5, sumo_seed=seed,
        emergency_preemption=cfg if preempt else None, sumo_warnings=False,
        additional_sumo_cmd=f"--tripinfo-output {trip} --no-step-log",
    )
    env.reset()
    tl = env.sumo.trafficlight
    states, flags = [], []
    if fixed:
        for _ in range(horizon):
            env._sumo_step()
            states.append(tl.getRedYellowGreenState("t"))
            flags.append(env.traffic_signals["t"].preempted)
    else:
        original = env._sumo_step

        def sampled_step():  # record the signal every simulated second, not only every delta_time
            original()
            states.append(tl.getRedYellowGreenState("t"))
            flags.append(env.traffic_signals["t"].preempted)

        env._sumo_step = sampled_step
        done = False
        while not done:
            _, _, d, _ = env.step(actions(env) if actions else {"t": 0})
            done = d["__all__"]
    events = list(env.preemption.events) if env.preemption else []
    ctrl = env.preemption.controllers.get("t") if env.preemption else None
    mode = ctrl.mode if ctrl else None
    sent = env.preemption.messages_sent if env.preemption else 0
    env.close()
    amb = {
        t.get("id"): {k: float(t.get(k)) for k in ("duration", "waitingTime", "waitingCount")}
        for t in ET.parse(trip).getroot().iter("tripinfo")
        if t.get("id").startswith("amb")
    }
    return states, events, amb, {"mode": mode, "flags": flags, "sent": sent}


def test_off_by_default():
    env = sumo_rl.SumoEnvironment(net_file=NET, route_file=BASE, num_seconds=20, sumo_warnings=False)
    env.reset()
    assert env.preemption is None
    assert env.traffic_signals["t"].preempted is False
    env.close()


def test_receiver_builds_and_closes_requests():
    cfg = PreemptionConfig()
    rx = RoadsideReceiver("A", (0.0, 0.0), cfg)
    msg = lambda t, up: PriorityMessage("amb", int(t), t, 10.0, 0.0, 10.0, 1, up)  # noqa: E731
    rx.receive(msg(1, (("A", 3, 100.0), ("B", 1, 400.0))), 1.0)
    (req,) = rx.active_requests(1.0)
    assert (req.link_index, req.distance, req.eta) == (3, 100.0, 10.0)
    rx.receive(msg(2, (("A", 3, 80.0),)), 2.0)
    assert rx.requests["amb"].eta == 8.0
    rx.receive(msg(3, (("B", 1, 380.0),)), 3.0)  # A is no longer ahead: the vehicle crossed it
    assert rx.active_requests(3.0) == []
    rx.receive(msg(10, (("A", 3, 100.0),)), 10.0)  # a later approach starts a new request
    assert len(rx.active_requests(10.0)) == 1
    assert rx.active_requests(14.0) == []  # silent for longer than request_timeout


def test_receiver_ignores_other_signals():
    rx = RoadsideReceiver("A", (0.0, 0.0), PreemptionConfig())
    rx.receive(PriorityMessage("amb", 1, 1.0, 0, 0, 5.0, 1, (("B", 0, 50.0),)), 1.0)
    assert rx.active_requests(1.0) == []


def test_transmitter_period():
    class V:
        def getPosition(self, v):
            return (1.0, 2.0)

        def getSpeed(self, v):
            return 12.0

        def getNextTLS(self, v):
            return (("t", 2, 90.0, "G"), ("u", 0, 400.0, "r"))

    class S:
        vehicle = V()

    tx = OnboardTransmitter("amb", S(), PreemptionConfig(tx_period=2.0))
    m = tx.poll(10.0)
    assert m.upcoming == (("t", 2, 90.0), ("u", 0, 400.0)) and m.seq == 1
    assert tx.poll(11.0) is None  # not time yet
    assert tx.poll(12.0).seq == 2


def test_ambulance_does_not_stop_with_priority(tmp_path):
    _, _, base, _ = run_single(tmp_path, preempt=False)
    _, events, prio, _ = run_single(tmp_path, preempt=True)
    assert base["amb_we.0"]["waitingCount"] >= 1  # it stops at the red without priority
    assert all(a["waitingCount"] == 0 for a in prio.values())
    assert prio["amb_we.0"]["duration"] < base["amb_we.0"]["duration"] - 10
    assert {e["event"] for e in events} >= {"PREEMPT_START", "YELLOW_CLEARING", "GREEN_HELD", "PREEMPT_END"}


def test_safety_green_never_drops_straight_to_red_and_no_conflicts(tmp_path):
    states, _, _, _ = run_single(tmp_path, preempt=True)
    for prev, cur in zip(states, states[1:]):
        for a, b in zip(prev, cur):
            assert not (a in "Gg" and b in "rs"), f"green went straight to red: {prev} -> {cur}"
    for s in states:  # links 0-1 are one road, 2-3 the other: never green together
        assert not (any(c in "Gg" for c in s[:2]) and any(c in "Gg" for c in s[2:])), s


def test_signal_rejoins_its_program_exactly(tmp_path):
    base_states, _, _, _ = run_single(tmp_path, preempt=False)
    states, _, _, info = run_single(tmp_path, preempt=True)
    assert info["mode"] == "IDLE"
    # after each preemption has ended the signal shows what the unmodified program shows
    t_end = max(i for i, f in enumerate(info["flags"]) if f) + 1
    assert states[t_end + 1 :] == base_states[t_end + 1 :]


def test_no_receiver_or_lost_radio_means_no_priority(tmp_path):
    _, events, amb, _ = run_single(tmp_path, cfg=PreemptionConfig(equipped=[]))
    assert events == [] and amb["amb_we.0"]["waitingCount"] >= 1
    _, events, amb, _ = run_single(tmp_path, cfg=PreemptionConfig(packet_loss=1.0))
    assert events == [] and amb["amb_we.0"]["waitingCount"] >= 1


def test_survives_partial_packet_loss(tmp_path):
    _, _, amb, info = run_single(tmp_path, cfg=PreemptionConfig(packet_loss=0.5, seed=3))
    assert info["sent"] > 0
    assert amb["amb_we.0"]["waitingTime"] <= 8  # a lost packet only delays the request by one period


def test_out_of_range_is_not_heard(tmp_path):
    _, events, _, _ = run_single(tmp_path, cfg=PreemptionConfig(radio_range=20.0))
    assert all(e["event"] != "PREEMPT_START" or e["distance"] <= 40 for e in events)


def test_two_conflicting_ambulances_both_served(tmp_path):
    routes = tmp_path / "two.rou.xml"
    routes.write_text(
        f"""<routes>{VTYPE}
        <flow id="amb_a" type="ambulance" from="w_t" to="t_e" begin="20" number="1" departSpeed="max"/>
        <flow id="amb_b" type="ambulance" from="n_t" to="t_s" begin="20" number="1" departSpeed="max"/>
        </routes>"""
    )
    states, events, amb, info = run_single(tmp_path, routes=str(routes), horizon=150)
    assert set(amb) == {"amb_a.0", "amb_b.0"}
    assert all(a["waitingTime"] <= 25 for a in amb.values())
    vehicles = {e.get("vehicle") for e in events if e["event"] == "PREEMPT_START"}
    assert vehicles  # at least the first one started a preemption; the other is either covered or queued
    for prev, cur in zip(states, states[1:]):
        assert not any(a in "Gg" and b in "rs" for a, b in zip(prev, cur))
    assert info["mode"] == "IDLE"


def test_rl_mode_ignores_actions_while_preempted_and_hands_back(tmp_path):
    seen = {"calls": 0}

    def alternate(env):
        seen["calls"] += 1
        return {"t": seen["calls"] % 2}  # the agent keeps asking for a phase change

    states, events, amb, info = run_single(tmp_path, fixed=False, actions=alternate, horizon=160)
    assert any(e["event"] == "PREEMPT_START" for e in events)
    assert any(info["flags"]) and info["flags"][-1] is False and info["mode"] == "IDLE"
    assert all(a["waitingCount"] == 0 for a in amb.values())  # even against a hostile agent
    for prev, cur in zip(states, states[1:]):
        assert not any(a in "Gg" and b in "rs" for a, b in zip(prev, cur))


@pytest.mark.skipif("LIBSUMO_AS_TRACI" in os.environ, reason="uses the traci multi-client API")
def test_two_by_two_grid_runs(tmp_path):
    env = sumo_rl.SumoEnvironment(
        net_file="sumo_rl/nets/2x2grid/2x2.net.xml",
        route_file="sumo_rl/nets/2x2grid/2x2.rou.xml,sumo_rl/nets/emergency/ambulances.rou.xml",
        fixed_ts=True, num_seconds=260, delta_time=5, emergency_preemption=True, sumo_warnings=False,
    )
    env.reset()
    done = False
    while not done:
        _, _, d, _ = env.step(None)
        done = d["__all__"]
    ev = env.preemption.events
    env.close()
    assert {e["signal"] for e in ev if e["event"] == "PREEMPT_START"} >= {"1", "2"}
    assert sum(e["event"] == "PREEMPT_START" for e in ev) == sum(e["event"] == "PREEMPT_END" for e in ev)

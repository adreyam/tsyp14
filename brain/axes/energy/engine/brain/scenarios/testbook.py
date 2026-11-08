"""Energy Test Book: scenarios with concrete, automatically checked expected outputs."""
from __future__ import annotations

from .runner import Run, Scenario, Step


# ------------------------------------------------------------------ check builders

def no_commands(r: Run):
    return "Brain sends no switch command", not r.commands, f"commands={[c.switch_id for c in r.commands]}"


def commands_exactly(*labels: str):
    def chk(r: Run):
        got = [f"{c.operation.value} {c.switch_id}" for c in r.commands]
        return f"commands == {list(labels)}", got == list(labels), f"commands={got}"
    return chk


def no_overload(*zones: str):
    def chk(r: Run):
        ov = r.overload_seconds(r.brain.cfg.poll_interval_s)
        bad = {z: s for z, s in ov.items() if (not zones or z in zones) and s > 0}
        return f"no true overload in {list(zones) or 'any zone'}", not bad, f"overload_s={ov} peak%={r.peak_pct()}"
    return chk


def some_overload(zone: str):
    def chk(r: Run):
        s = r.overload_seconds(r.brain.cfg.poll_interval_s)[zone]
        return f"{zone} is overloaded for some time (baseline)", s > 0, f"{zone} overload_s={s}"
    return chk


def event_matches(kind: str, *needles: str, absent: bool = False):
    def chk(r: Run):
        hits = [e.summary for e in r.events(kind) if all(n in e.summary for n in needles)]
        name = f"{'no ' if absent else ''}'{kind}' event containing {list(needles)}"
        ok = (not hits) if absent else bool(hits)
        return name, ok, (hits[0] if hits else "none")
    return chk


def rejected_option(label: str, why: str):
    def chk(r: Run):
        for e in r.events("candidates_evaluated"):
            for c in e.payload.get("rejected", []):
                if c["action"] == label and why in (c["why_not"] or ""):
                    return f"option '{label}' rejected because {why}", True, c["why_not"]
        return f"option '{label}' rejected because {why}", False, "not found"
    return chk


def verification(outcome: str):
    def chk(r: Run):
        got = [v.outcome for v in r.brain.verifications]
        return f"verification {outcome}", outcome in got, f"verifications={got}"
    return chk


def acted_before_overload(zone: str, cap: float):
    """The command went out while the zone's TRUE load was still below capacity."""
    def chk(r: Run):
        name = f"acted while {zone} still below capacity"
        if r.first_command_cycle is None:
            return name, False, "no command"
        i = r.first_command_cycle
        peak = max(l.get(zone, 0) for _, l in r.truth[: i + 1])
        return name, peak <= cap, (f"command at t={r.truth[i][0]:.0f}s; max true {zone} load until then "
                                   f"{peak:.0f} kW ({100 * peak / cap:.0f}%)")
    return chk


def guardian_got_anomaly(r: Run):
    kinds = [e.get("kind") for e in r.guardian_received]
    return "actuator anomaly reported to Guardian", "actuator_anomaly" in kinds, f"received={kinds}"


def learned(switch_id: str, frm: str, to: str):
    def chk(r: Run):
        sw = r.brain.effects.get(switch_id)
        ok = bool(sw and sw.from_zone == frm and sw.to_zone == to)
        return (f"learned {switch_id}: {frm}->{to}", ok,
                f"effect={sw.model_dump() if sw else None}, level={r.brain.knowledge.level}")
    return chk


def level_is(level: str):
    def chk(r: Run):
        return f"knowledge level {level}", r.brain.knowledge.level == level, f"level={r.brain.knowledge.level}"
    return chk


def no_shedding(r: Run):
    sheds = [f"{c.operation.value} {c.switch_id}" for c in r.commands if c.switch_id.startswith("F")]
    return "no load shedding", not sheds, f"commands={[f'{c.operation.value} {c.switch_id}' for c in r.commands]}"


def customers_interrupted(r: Run):
    cmi = r.customer_minutes_interrupted(r.brain.cfg.poll_interval_s)
    return "customers interrupted (baseline)", cmi > 0, f"customer-minutes interrupted={cmi}"


E13_STEPS = [Step(15, "ramp", {"zone_id": "D", "add_kw": 200, "rise_s": 40, "hold_s": 240}, "surge in D"),
             Step(80, "ramp", {"zone_id": "A", "add_kw": 240, "rise_s": 50, "hold_s": 240}, "surge in A")]


# ------------------------------------------------------------------ scenarios

Z1_RAMP = Step(20, "ramp", {"zone_id": "Z1", "add_kw": 280, "rise_s": 60, "hold_s": 200}, "real demand surge in Z1")

TESTBOOK: dict[str, Scenario] = {s.id: s for s in [
    Scenario(
        "E1", "Calm day",
        "Normal daily variation in all zones. The Brain must not overreact.",
        duration_s=300,
        checks=[no_commands, event_matches("risk_detected", absent=True), event_matches("operator_alert", absent=True),
                no_overload()],
    ),
    Scenario(
        "E2", "Predicted overload, preventive switching",
        "Z1 demand climbs to ~120% of capacity over 60 s. The Brain forecasts it and closes tie S12 "
        "before Z1 is overloaded.",
        steps=[Z1_RAMP],
        checks=[event_matches("risk_detected", "Z1", "predicted"), commands_exactly("close S12"),
                acted_before_overload("Z1", 500), no_overload(), verification("SUCCESS")],
    ),
    Scenario(
        "E2B", "Same surge, reactive baseline (comparison only)",
        "Identical to E2 but the Brain only reacts to measured overload. Quantifies the benefit of prediction.",
        steps=[Z1_RAMP], overrides={"forecast": {"mode": "reactive"}},
        checks=[commands_exactly("close S12"), some_overload("Z1")],
    ),
    Scenario(
        "E3", "Spoofed load spike",
        "The Twin streams a fake 640 kW reading for Z1 (real ~330 kW). The Guardian flags it 3 s later. "
        "The Brain must not switch and must record why.",
        steps=[Step(30, "spoof", {"sensor_id": "PL-Z1", "value_kw": 640, "duration_s": 40}, "fake reading starts"),
               Step(33, "guardian_override", {"subject_id": "PL-Z1", "verdict": "untrusted", "reason": "spoofed"},
                    "Guardian verdict (3 s latency)"),
               Step(80, "guardian_override", {"subject_id": "PL-Z1", "verdict": "trusted"}, "sensor cleared")],
        checks=[no_commands, event_matches("reading_suspect", "PL-Z1"), event_matches("verdict_change", "PL-Z1", "untrusted"),
                event_matches("risk_detected", "Z1", absent=True),
                event_matches("reading_suspect", "confirmed at once", absent=True)],
    ),
    Scenario(
        "E4", "Protect the hospital",
        "Z4 (industrial) surges. Moving load to Z3 (hospital) would overload it; the Brain must pick S42 (to Z2) "
        "instead and never stress Z3.",
        steps=[Step(20, "ramp", {"zone_id": "Z4", "add_kw": 260, "rise_s": 60, "hold_s": 200}, "surge in Z4")],
        checks=[commands_exactly("close S42"), rejected_option("close S43", "priority zone Z3"), no_overload()],
    ),
    Scenario(
        "E5", "Stuck switch",
        "S12 is mechanically stuck. The Brain commands it during a Z1 surge, detects that it never moved, "
        "reports the actuator anomaly to the Guardian and alerts the operator.",
        steps=[Step(5, "stuck", {"switch_id": "S12", "stuck": True}, "S12 jammed"), Z1_RAMP],
        checks=[commands_exactly("close S12"), verification("FAILED"), guardian_got_anomaly,
                event_matches("operator_alert", "Z1")],
    ),
    Scenario(
        "E6", "Guardian down during real overloads",
        "The Guardian goes silent. Z3 (hospital) and Z1 (commercial) both surge. Degraded mode allows only the "
        "reversible action that relieves the hospital (S32); the Z1 action is blocked and the operator alerted.",
        steps=[Step(10, "guardian_mode", {"mode": "silent"}, "Guardian crashes"),
               Step(25, "ramp", {"zone_id": "Z3", "add_kw": 220, "rise_s": 50, "hold_s": 200}, "hospital surge"),
               Step(25, "ramp", {"zone_id": "Z1", "add_kw": 280, "rise_s": 60, "hold_s": 200}, "commercial surge")],
        checks=[commands_exactly("close S32"), event_matches("gate_blocked", "S12", "trust_unavailable"),
                event_matches("degraded_mode", "S32"), no_overload("Z3")],
    ),
    Scenario(
        "E7A", "New city, blind",
        "An unknown Twin advertises only IDs. The Brain detects the surge but, without capacities or switch "
        "effects, only alerts (L0).",
        city="city_b.yaml", duration_s=120,
        steps=[Step(40, "ramp", {"zone_id": "north", "add_kw": 450, "rise_s": 50, "hold_s": 200}, "surge in north")],
        checks=[level_is("L0"), no_commands, event_matches("operator_alert", "north", "L0")],
    ),
    Scenario(
        "E7B", "New city, learns its grid by watching",
        "Same unknown Twin plus a capacity file (L1). An operator toggles T-NS twice; the Brain learns its effect "
        "from the load shifts, reaches L2, then prevents the north overload with T-NS. No code change.",
        city="city_b.yaml", knowledge="city_b.yaml", duration_s=160,
        steps=[Step(15, "switch", {"switch_id": "T-NS", "state": "closed"}, "operator switching"),
               Step(25, "switch", {"switch_id": "T-NS", "state": "open"}),
               Step(35, "switch", {"switch_id": "T-NS", "state": "closed"}),
               Step(45, "switch", {"switch_id": "T-NS", "state": "open"}),
               Step(60, "ramp", {"zone_id": "north", "add_kw": 450, "rise_s": 50, "hold_s": 200}, "surge in north")],
        checks=[learned("T-NS", "north", "south"), level_is("L2"), commands_exactly("close T-NS"), no_overload()],
    ),
    Scenario(
        "E8", "Guardian quarantines the switch the Brain wants",
        "During a real Z1 surge the Guardian quarantines S12 (suspected compromised). The gate blocks it "
        "and the operator is alerted.",
        steps=[Step(15, "guardian_override", {"subject_id": "S12", "verdict": "untrusted", "quarantined": True,
                                               "reason": "firmware tampering suspected"}, "S12 quarantined"), Z1_RAMP],
        checks=[no_commands, event_matches("gate_blocked", "S12"), event_matches("operator_alert", "Z1")],
    ),
    Scenario(
        "E9", "Cross-domain: never cut a running flood pump",
        "Flood logic reports a pump running in Z4. Z4 surges and S42 is stuck. Shedding Z4 (F4) would normally be "
        "the last resort, but the priority hint forbids it: the Brain alerts instead.",
        steps=[Step(5, "stuck", {"switch_id": "S42", "stuck": True}),
               Step(10, "hint", {"zone_id": "Z4", "reason": "flood pump P-4 running"}, "flood domain hint"),
               Step(20, "ramp", {"zone_id": "Z4", "add_kw": 260, "rise_s": 60, "hold_s": 200}, "surge in Z4")],
        checks=[commands_exactly("close S42"), verification("FAILED"), rejected_option("open F4", "priority zone Z4"),
                event_matches("operator_alert", "Z4")],
    ),
    Scenario(
        "E10", "Switch back when the surge is over",
        "Z1 surges for about 100 s then returns to normal. The Brain closes S12 to prevent the overload, then "
        "reopens it once the forecast stays safe, so the grid returns to its normal configuration.",
        duration_s=260,
        steps=[Step(20, "ramp", {"zone_id": "Z1", "add_kw": 280, "rise_s": 60, "hold_s": 40}, "surge, ends at t=120 s")],
        checks=[commands_exactly("close S12", "open S12"), event_matches("restoration", "S12"), no_overload(),
                verification("SUCCESS")],
    ),
    Scenario(
        "E11", "Last-resort load shedding, then restore",
        "S42 is jammed and Z4 surges to ~130%. With no switching option left and Z4 measured over capacity, the "
        "Brain sheds Z4 (opens F4) as an explicit last resort, alerts the operator, and re-energizes it when the "
        "J-1 plan shows it is safe.",
        duration_s=280,
        steps=[Step(5, "stuck", {"switch_id": "S42", "stuck": True}),
               Step(20, "ramp", {"zone_id": "Z4", "add_kw": 300, "rise_s": 60, "hold_s": 60}, "surge, ends at t=140 s")],
        checks=[commands_exactly("close S42", "open F4", "close F4"), verification("FAILED"),
                event_matches("operator_alert", "last-resort"), event_matches("restoration", "F4")],
    ),
    Scenario(
        "E12", "Learning cannot be poisoned",
        "Same unknown city as E7B, but the Guardian distrusts the south sensor while the operator's first switchings "
        "happen. The Brain throws those observations away and learns T-NS only from trusted evidence.",
        city="city_b.yaml", knowledge="city_b.yaml", duration_s=200,
        steps=[Step(15, "switch", {"switch_id": "T-NS", "state": "closed"}, "operator switching"),
               Step(17, "guardian_override", {"subject_id": "PL-south", "verdict": "untrusted",
                                              "reason": "integrity check failed"}),
               Step(25, "switch", {"switch_id": "T-NS", "state": "open"}),
               Step(35, "guardian_override", {"subject_id": "PL-south", "verdict": "trusted"}),
               Step(55, "switch", {"switch_id": "T-NS", "state": "closed"}),
               Step(65, "switch", {"switch_id": "T-NS", "state": "open"}),
               Step(75, "switch", {"switch_id": "T-NS", "state": "closed"}),
               Step(85, "switch", {"switch_id": "T-NS", "state": "open"}),
               Step(100, "ramp", {"zone_id": "north", "add_kw": 450, "rise_s": 50, "hold_s": 200}, "surge in north")],
        checks=[event_matches("learned_effect", "T-NS", "discarded"), learned("T-NS", "north", "south"),
                commands_exactly("close T-NS"), no_overload()],
    ),
    Scenario(
        "E14", "Stealthy drift hides a real overload (physics catches it)",
        "Z1 really surges to ~120%. An attacker makes PL-Z1 under-read by a slowly growing offset that cancels the "
        "surge: every sample looks normal, no jump, and the Guardian keeps trusting the sensor. The substation meter "
        "SUB-N still measures the real power. The Brain sees the power balance break, reports it to the Guardian, "
        "blocks actions on the contradicted sensors and warns the operator of a possible hidden overload in Z1.",
        steps=[Z1_RAMP, Step(20, "drift", {"sensor_id": "PL-Z1", "rate_kw_s": -4.6, "duration_s": 60},
                             "attacker masks the surge")],
        checks=[event_matches("consistency", "SUB-N", "does not tell the truth"),
                event_matches("operator_alert", "HIDDEN OVERLOAD", "Z1"), no_commands],
    ),
    Scenario(
        "E14B", "Same attack, no power-balance check (comparison only)",
        "Identical to E14 with the consistency check off: the Brain sees a calm Z1 and stays silent while it is "
        "really overloaded.",
        steps=[Z1_RAMP, Step(20, "drift", {"sensor_id": "PL-Z1", "rate_kw_s": -4.6, "duration_s": 60})],
        overrides={"consistency": {"enabled": False}},
        checks=[event_matches("risk_detected", "Z1", absent=True), event_matches("operator_alert", absent=True),
                some_overload("Z1")],
    ),
    Scenario(
        "E15", "Stealthy drift fakes a surge (physics stops needless switching)",
        "No real incident. PL-Z1 is made to over-read by +3 kW/s for 70 s: a plausible-looking ramp that would "
        "lead the Brain to move load for nothing. SUB-N disagrees; the gate refuses to act on contradicted "
        "evidence and the Guardian is told.",
        steps=[Step(20, "drift", {"sensor_id": "PL-Z1", "rate_kw_s": 3.0, "duration_s": 70}, "fake ramp")],
        checks=[event_matches("consistency", "SUB-N"), no_commands,
                lambda r: ("evidence sent to the Guardian", any(e.get("kind") == "consistency"
                                                                 for e in r.guardian_received),
                           f"received={[e.get('kind') for e in r.guardian_received]}")],
    ),
    Scenario(
        "E15B", "Same fake ramp, no power-balance check (comparison only)",
        "Identical to E15 with the consistency check off: the manipulated sensor makes the Brain switch for nothing.",
        steps=[Step(20, "drift", {"sensor_id": "PL-Z1", "rate_kw_s": 3.0, "duration_s": 70})],
        overrides={"consistency": {"enabled": False}},
        checks=[lambda r: ("Brain is manipulated into switching", bool(r.commands),
                           f"commands={[f'{c.operation.value} {c.switch_id}' for c in r.commands]}")],
    ),
    Scenario(
        "E16", "Phantom acknowledgement: the switch says it moved, the power says no",
        "S12's status is spoofed: after the Brain's command it reports 'closed' but the breaker never operated. "
        "Verification compares the predicted load shift with the sensors, finds no power moved, declares the "
        "command FAILED, stops believing the status, reports the anomaly to the Guardian and alerts the operator.",
        steps=[Step(5, "stuck", {"switch_id": "S12", "stuck": False, "phantom": True}, "S12 status spoofed"), Z1_RAMP],
        checks=[commands_exactly("close S12"), verification("FAILED"),
                event_matches("verification", "no load moved", "status not believed"), guardian_got_anomaly,
                event_matches("operator_alert", "Z1"),
                lambda r: ("Brain models S12 in its real (open) position",
                           r.brain.state.switches["S12"].state.value == "open",
                           f"S12 modelled as {r.brain.state.switches['S12'].state.value}")],
    ),
    Scenario(
        "E17", "Sudden real jump, confirmed by physics at once",
        "Z1 demand jumps by 250 kW in one second (to ~115%). A jump that large is normally held for 5 samples in case "
        "it is a spoof (E3). Here substation meter SUB-N moves by the same amount, so the jump is real: the Brain "
        "accepts it immediately, restarts the zone's trend at the new level and closes S12 within seconds.",
        steps=[Step(30, "ramp", {"zone_id": "Z1", "add_kw": 250, "rise_s": 1, "hold_s": 120}, "sudden real jump")],
        checks=[event_matches("reading_suspect", "PL-Z1", "confirmed at once"), commands_exactly("close S12"),
                lambda r: ("true overload of Z1 at most 3 s",
                           r.overload_seconds(r.brain.cfg.poll_interval_s)["Z1"] <= 3,
                           f"Z1 overload_s={r.overload_seconds(r.brain.cfg.poll_interval_s)['Z1']}")],
    ),
    Scenario(
        "E17B", "Same jump, no physics (5-sample hold, comparison only)",
        "Identical to E17 with the consistency check off: the jump waits 5 samples before it is believed.",
        steps=[Step(30, "ramp", {"zone_id": "Z1", "add_kw": 250, "rise_s": 1, "hold_s": 120})],
        overrides={"consistency": {"enabled": False}},
        checks=[event_matches("reading_suspect", "PL-Z1", "persisted"), some_overload("Z1")],
    ),
    Scenario(
        "E18", "Spoof that outlasts the Guardian's reaction time",
        "PL-Z4 is spoofed to 650 kW (144% of capacity) and the Guardian only flags it after 9 s. A held jump is "
        "normally accepted once it has persisted 5 samples with the sensor still trusted, so the fake overload "
        "would be believed and could even trigger last-resort shedding. Substation meter SUB-S contradicts it: "
        "the Brain never accepts the reading, however long it lasts.",
        steps=[Step(30, "spoof", {"sensor_id": "PL-Z4", "value_kw": 650, "duration_s": 40}, "fake reading starts"),
               Step(39, "guardian_override", {"subject_id": "PL-Z4", "verdict": "untrusted", "reason": "spoofed"},
                    "Guardian verdict (9 s latency)"),
               Step(80, "guardian_override", {"subject_id": "PL-Z4", "verdict": "trusted"}, "sensor cleared")],
        checks=[no_commands, event_matches("reading_suspect", "PL-Z4", "contradicted by an aggregate meter"),
                event_matches("risk_detected", "Z4", absent=True)],
    ),
    Scenario(
        "E18B", "Same slow-flagged spoof, no physics (v0.6 behaviour, comparison only)",
        "Identical to E18 with the consistency check off: after 5 samples the fake reading is believed and the Brain "
        "acts on a fake overload.",
        steps=[Step(30, "spoof", {"sensor_id": "PL-Z4", "value_kw": 650, "duration_s": 40}),
               Step(39, "guardian_override", {"subject_id": "PL-Z4", "verdict": "untrusted", "reason": "spoofed"}),
               Step(80, "guardian_override", {"subject_id": "PL-Z4", "verdict": "trusted"})],
        overrides={"consistency": {"enabled": False}},
        checks=[event_matches("reading_suspect", "PL-Z4", "persisted"),
                lambda r: ("Brain acts on the fake overload", bool(r.commands),
                           f"commands={[f'{c.operation.value} {c.switch_id}' for c in r.commands]}")],
    ),
    Scenario(
        "E13", "Multi-zone coordination: re-dispatch, make before break",
        "Meshed city C. D surges first and is relieved into B (the best single move then). A then surges; its only "
        "tie also goes to B, which no longer has room, so every single move is infeasible. The planner finds a "
        "3-step plan: relieve D into C instead (close T-DC), then free B (open T-DB), then close T-AB. Each step "
        "is gated and verified; no zone is overloaded in between and nobody is cut off.",
        city="city_c.yaml", duration_s=260, steps=E13_STEPS,
        checks=[event_matches("plan", "close T-DC -> open T-DB -> close T-AB"),
                commands_exactly("close T-DB", "close T-DC", "open T-DB", "close T-AB"), no_overload(), no_shedding,
                verification("SUCCESS")],
    ),
    Scenario(
        "E13B", "Same surges, single-move decisions (v0.6 behaviour, comparison only)",
        "Identical to E13 with the planner off. No single move helps A, so the Brain falls back to last-resort "
        "shedding: A's customers are cut off.",
        city="city_c.yaml", duration_s=260, steps=E13_STEPS, overrides={"action": {"planner": False}},
        checks=[event_matches("decision", "open F-A", "last-resort"), customers_interrupted],
    ),
]}

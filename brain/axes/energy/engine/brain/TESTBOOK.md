# Energy Test Book (City Brain, energy domain)

Every scenario: one trigger, one log in `logs/`, automatic pass/fail. `python -m scenarios.run <ID>` (simulated clock), `--live` (real HTTP, real time) or `--twin mcp`. The whole book also runs in `pytest` on two noise seeds, over MCP, and in the simulator; the simulation page replays every run.

Observed values are from the committed run logs (seed 7). *Forecast error* = mean absolute error of the 45 s forecast against the true load, leaving out windows that contain a switching action.

| ID | Name | Result | Commands | Overload s | Peak load % | Forecast error |
|---|---|---|---|---|---|---|
| E1 | Calm day | PASS | — | 0 | {'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 8.8 kW |
| E2 | Predicted overload, preventive switching | PASS | close S12 | 0 | {'Z1': 80, 'Z2': 90, 'Z3': 65, 'Z4': 69} | 36.6 kW |
| E2B | Same surge, reactive baseline (comparison only) | PASS | close S12 | {'Z1': 3.0} | {'Z1': 103, 'Z2': 90, 'Z3': 65, 'Z4': 69} | 38.3 kW |
| E3 | Spoofed load spike | PASS | — | 0 | {'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 15.4 kW |
| E4 | Protect the hospital | PASS | close S42 | 0 | {'Z1': 72, 'Z2': 78, 'Z3': 65, 'Z4': 93} | 35.1 kW |
| E5 | Stuck switch | PASS | close S12 | {'Z1': 97.0} | {'Z1': 123, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 37.5 kW |
| E6 | Guardian down during real overloads | PASS | close S32 (degraded) | {'Z1': 92.0} | {'Z1': 122, 'Z2': 78, 'Z3': 84, 'Z4': 69} | 57.6 kW |
| E7A | New city, blind | PASS | — | {'north': 38.0} | {'north': 110, 'south': 47, 'port': 62} | 93.0 kW |
| E7B | New city, learns its grid by watching | PASS | close T-NS | 0 | {'north': 75, 'south': 86, 'port': 62} | 90.5 kW |
| E8 | Guardian quarantines the switch the Brain wants | PASS | — | {'Z1': 97.0} | {'Z1': 123, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 37.5 kW |
| E9 | Cross-domain: never cut a running flood pump | PASS | close S42 | {'Z4': 94.0} | {'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 124} | 38.2 kW |
| E10 | Switch back when the surge is over | PASS | close S12, open S12 | 0 | {'Z1': 80, 'Z2': 90, 'Z3': 65, 'Z4': 69} | 31.1 kW |
| E11 | Last-resort load shedding, then restore | PASS | close S42, open F4, close F4 | 0 | {'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 99} | 9.5 kW |
| E12 | Learning cannot be poisoned | PASS | close T-NS | 0 | {'north': 76, 'south': 83, 'port': 62} | 80.6 kW |
| E14 | Stealthy drift hides a real overload (physics catches it) | PASS | — | {'Z1': 97.0} | {'Z1': 123, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 67.6 kW |
| E14B | Same attack, no power-balance check (comparison only) | PASS | — | {'Z1': 97.0} | {'Z1': 123, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 67.6 kW |
| E15 | Stealthy drift fakes a surge (physics stops needless switching) | PASS | — | 0 | {'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 65.3 kW |
| E15B | Same fake ramp, no power-balance check (comparison only) | PASS | close S12 | 0 | {'Z1': 70, 'Z2': 76, 'Z3': 65, 'Z4': 69} | 79.9 kW |
| E16 | Phantom acknowledgement: the switch says it moved, the power says no | PASS | close S12 | {'Z1': 97.0} | {'Z1': 123, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 39.0 kW |
| E17 | Sudden real jump, confirmed by physics at once | PASS | close S12 | {'Z1': 2.0} | {'Z1': 119, 'Z2': 90, 'Z3': 65, 'Z4': 69} | 16.8 kW |
| E17B | Same jump, no physics (5-sample hold, comparison only) | PASS | close S12 | {'Z1': 6.0} | {'Z1': 120, 'Z2': 90, 'Z3': 65, 'Z4': 69} | 15.6 kW |
| E18 | Spoof that outlasts the Guardian's reaction time | PASS | — | 0 | {'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 69} | 15.9 kW |
| E18B | Same slow-flagged spoof, no physics (v0.6 behaviour, comparison only) | PASS | close S42, open F4, close F4, open S42 | 0 | {'Z1': 72, 'Z2': 68, 'Z3': 65, 'Z4': 67} | 17.6 kW |
| E13 | Multi-zone coordination: re-dispatch, make before break | PASS | close T-DB, close T-DC, open T-DB, close T-AB | 0 | {'A': 89, 'B': 94, 'C': 91, 'D': 78, 'E': 67} | 17.6 kW |
| E13B | Same surges, single-move decisions (v0.6 behaviour, comparison only) | PASS | close T-DB, open F-A, close F-A, open F-A | {'A': 3.0} | {'A': 118, 'B': 86, 'C': 63, 'D': 78, 'E': 67} | 29.1 kW |

**Prediction vs reaction (E2 vs E2B):** same surge. Predictive Brain: 0 s of overload (command at t=29s; max true Z1 load until then 388 kW (78%)). Reactive baseline: 3.0 s of overload, peak 103%. Calm day (E1): 0 false alarms.

**Plans vs single moves (E13 vs E13B):** same two surges in the meshed city. With the planner: close T-DB, close T-DC, open T-DB, close T-AB; nobody cut off. Single moves only: close T-DB, open F-A, close F-A, open F-A; 2085 customer-minutes interrupted.

**Physics vs stealthy manipulation (E14/E15 vs E14B/E15B):** a slow drift that no per-sensor check catches. With the power-balance check the Brain flags it within seconds, warns of the hidden overload (E14) and refuses to be manipulated into switching (E15). Without it the Brain is blind (E14B) or switches for nothing (E15B: close S12).

## E1 — Calm day

| Field | Value |
|---|---|
| ID | E1 |
| Name | Calm day |
| Layers involved | Twin, City Brain |
| Description | Normal daily variation in all zones. The Brain must not overreact. |
| Trigger | `python -m scenarios.run e1` |
| Expected: Twin | Normal daily load variation |
| Expected: Guardian | All trusted |
| Expected: Brain | Brain sends no switch command<br>no 'risk_detected' event containing []<br>no 'operator_alert' event containing []<br>no true overload in any zone |
| Observed | ✅ commands=[]<br>✅ none<br>✅ none<br>✅ overload_s={'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0} peak%={'Z1': 72, 'Z2': 56, 'Z3': 65, 'Z4': 69} |
| How to verify | Simulation page `#E1`; log `logs/e1-20261106T123653223Z.log` (last line = `scenario_result`) |

## E2 — Predicted overload, preventive switching

| Field | Value |
|---|---|
| ID | E2 |
| Name | Predicted overload, preventive switching |
| Layers involved | Twin, City Brain |
| Description | Z1 demand climbs to ~120% of capacity over 60 s. The Brain forecasts it and closes tie S12 before Z1 is overloaded. |
| Trigger | `python -m scenarios.run e2` |
| Expected: Twin | Z1 demand +280 kW over 60 s |
| Expected: Guardian | All trusted |
| Expected: Brain | 'risk_detected' event containing ['Z1', 'predicted']<br>commands == ['close S12']<br>acted while Z1 still below capacity<br>no true overload in any zone<br>verification SUCCESS |
| Observed | ✅ Z1: predicted overload within 45 s (541 kW vs capacity 500 kW, P(overload) 76%)<br>✅ commands=['close S12']<br>✅ command at t=29s; max true Z1 load until then 388 kW (78%)<br>✅ overload_s={'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0} peak%={'Z1': 80, 'Z2': 90, 'Z3': 65, 'Z4': 69}<br>✅ verifications=['SUCCESS'] |
| How to verify | Simulation page `#E2`; log `logs/e2-20261106T123656915Z.log` (last line = `scenario_result`) |

## E2B — Same surge, reactive baseline (comparison only)

| Field | Value |
|---|---|
| ID | E2B |
| Name | Same surge, reactive baseline (comparison only) |
| Layers involved | Twin, City Brain |
| Description | Identical to E2 but the Brain only reacts to measured overload. Quantifies the benefit of prediction. |
| Trigger | `python -m scenarios.run e2b` |
| Expected: Twin | Same as E2 |
| Expected: Guardian | All trusted |
| Expected: Brain | commands == ['close S12']<br>Z1 is overloaded for some time (baseline) |
| Observed | ✅ commands=['close S12']<br>✅ Z1 overload_s=3.0 |
| How to verify | Simulation page `#E2B`; log `logs/e2b-20261106T123658717Z.log` (last line = `scenario_result`) |

## E3 — Spoofed load spike

| Field | Value |
|---|---|
| ID | E3 |
| Name | Spoofed load spike |
| Layers involved | Twin, City Brain, Guardian |
| Description | The Twin streams a fake 640 kW reading for Z1 (real ~330 kW). The Guardian flags it 3 s later. The Brain must not switch and must record why. |
| Trigger | `python -m scenarios.run e3` |
| Expected: Twin | Streams fake 640 kW for PL-Z1 for 40 s, keeps true load internally |
| Expected: Guardian | Marks PL-Z1 untrusted 3 s after the spoof starts |
| Expected: Brain | Brain sends no switch command<br>'reading_suspect' event containing ['PL-Z1']<br>'verdict_change' event containing ['PL-Z1', 'untrusted']<br>no 'risk_detected' event containing ['Z1']<br>no 'reading_suspect' event containing ['confirmed at once'] |
| Observed | ✅ commands=[]<br>✅ PL-Z1: jump 338 -> 640 kW contradicted by an aggregate meter (power balance would not close); not accepted however long it lasts -- held out of forecast, awaiting persistence + Guardian<br>✅ PL-Z1: trusted -> untrusted<br>✅ none<br>✅ none |
| How to verify | Simulation page `#E3`; log `logs/e3-20261106T123700578Z.log` (last line = `scenario_result`) |

## E4 — Protect the hospital

| Field | Value |
|---|---|
| ID | E4 |
| Name | Protect the hospital |
| Layers involved | Twin, City Brain |
| Description | Z4 (industrial) surges. Moving load to Z3 (hospital) would overload it; the Brain must pick S42 (to Z2) instead and never stress Z3. |
| Trigger | `python -m scenarios.run e4` |
| Expected: Twin | Z4 demand +260 kW over 60 s |
| Expected: Guardian | All trusted |
| Expected: Brain | commands == ['close S42']<br>option 'close S43' rejected because priority zone Z3<br>no true overload in any zone |
| Observed | ✅ commands=['close S42']<br>✅ would push priority zone Z3 over capacity (103%)<br>✅ overload_s={'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0} peak%={'Z1': 72, 'Z2': 78, 'Z3': 65, 'Z4': 93} |
| How to verify | Simulation page `#E4`; log `logs/e4-20261106T123702447Z.log` (last line = `scenario_result`) |

## E5 — Stuck switch

| Field | Value |
|---|---|
| ID | E5 |
| Name | Stuck switch |
| Layers involved | Twin, City Brain, Guardian |
| Description | S12 is mechanically stuck. The Brain commands it during a Z1 surge, detects that it never moved, reports the actuator anomaly to the Guardian and alerts the operator. |
| Trigger | `python -m scenarios.run e5` |
| Expected: Twin | S12 jammed (accepts commands, never moves); Z1 surge |
| Expected: Guardian | Receives the Brain's actuator_anomaly report |
| Expected: Brain | commands == ['close S12']<br>verification FAILED<br>actuator anomaly reported to Guardian<br>'operator_alert' event containing ['Z1'] |
| Observed | ✅ commands=['close S12']<br>✅ verifications=['FAILED']<br>✅ received=['actuator_anomaly']<br>✅ Z1: overload risk and no safe switching option improves on doing nothing -- operator action needed |
| How to verify | Simulation page `#E5`; log `logs/e5-20261106T123704582Z.log` (last line = `scenario_result`) |

## E6 — Guardian down during real overloads

| Field | Value |
|---|---|
| ID | E6 |
| Name | Guardian down during real overloads |
| Layers involved | Twin, City Brain, Guardian |
| Description | The Guardian goes silent. Z3 (hospital) and Z1 (commercial) both surge. Degraded mode allows only the reversible action that relieves the hospital (S32); the Z1 action is blocked and the operator alerted. |
| Trigger | `python -m scenarios.run e6` |
| Expected: Twin | Z3 (hospital) +220 kW and Z1 +280 kW |
| Expected: Guardian | Silent from t=10 s (HTTP 503) |
| Expected: Brain | commands == ['close S32']<br>'gate_blocked' event containing ['S12', 'trust_unavailable']<br>'degraded_mode' event containing ['S32']<br>no true overload in ['Z3'] |
| Observed | ✅ commands=['close S32']<br>✅ close S12 blocked (trust_unavailable): verdicts missing for PL-Z1, PL-Z2, S12 and action does not relieve a persistent priority-zone overload<br>✅ acting without Guardian verdicts: verdicts missing for PL-Z3, PL-Z2, S32; reversible action relieving priority zone Z3 after >= 3 consistent samples<br>✅ overload_s={'Z1': 92.0, 'Z2': 0, 'Z3': 0, 'Z4': 0} peak%={'Z1': 122, 'Z2': 78, 'Z3': 84, 'Z4': 69} |
| How to verify | Simulation page `#E6`; log `logs/e6-20261106T123706727Z.log` (last line = `scenario_result`) |

## E7A — New city, blind

| Field | Value |
|---|---|
| ID | E7A |
| Name | New city, blind |
| Layers involved | Twin, City Brain |
| Description | An unknown Twin advertises only IDs. The Brain detects the surge but, without capacities or switch effects, only alerts (L0). |
| Trigger | `python -m scenarios.run e7a` |
| Expected: Twin | Unknown city: advertises IDs only; north +450 kW |
| Expected: Guardian | All trusted |
| Expected: Brain | knowledge level L0<br>Brain sends no switch command<br>'operator_alert' event containing ['north', 'L0'] |
| Observed | ✅ level=L0<br>✅ commands=[]<br>✅ north: unusual load rise but capacity unknown -- alert only (L0), no autonomous switching |
| How to verify | Simulation page `#E7A`; log `logs/e7a-20261106T123708810Z.log` (last line = `scenario_result`) |

## E7B — New city, learns its grid by watching

| Field | Value |
|---|---|
| ID | E7B |
| Name | New city, learns its grid by watching |
| Layers involved | Twin, City Brain |
| Description | Same unknown Twin plus a capacity file (L1). An operator toggles T-NS twice; the Brain learns its effect from the load shifts, reaches L2, then prevents the north overload with T-NS. No code change. |
| Trigger | `python -m scenarios.run e7b` |
| Expected: Twin | Operator toggles T-NS 4x, then north +450 kW |
| Expected: Guardian | All trusted |
| Expected: Brain | learned T-NS: north->south<br>knowledge level L2<br>commands == ['close T-NS']<br>no true overload in any zone |
| Observed | ✅ effect={'switch_id': 'T-NS', 'kind': <SwitchKind.tie: 'tie'>, 'from_zone': 'north', 'to_zone': 'south', 'transfer_fraction': 0.292}, level=L2<br>✅ level=L2<br>✅ commands=['close T-NS']<br>✅ overload_s={'north': 0, 'south': 0, 'port': 0} peak%={'north': 75, 'south': 86, 'port': 62} |
| How to verify | Simulation page `#E7B`; log `logs/e7b-20261106T123710245Z.log` (last line = `scenario_result`) |

## E8 — Guardian quarantines the switch the Brain wants

| Field | Value |
|---|---|
| ID | E8 |
| Name | Guardian quarantines the switch the Brain wants |
| Layers involved | Twin, City Brain, Guardian |
| Description | During a real Z1 surge the Guardian quarantines S12 (suspected compromised). The gate blocks it and the operator is alerted. |
| Trigger | `python -m scenarios.run e8` |
| Expected: Twin | Z1 surge |
| Expected: Guardian | Marks S12 untrusted + quarantined |
| Expected: Brain | Brain sends no switch command<br>'gate_blocked' event containing ['S12']<br>'operator_alert' event containing ['Z1'] |
| Observed | ✅ commands=[]<br>✅ close S12 blocked (safety): S12 is quarantined by the Guardian<br>✅ Z1: overload risk and every useful option is blocked by the safety/trust gate -- operator action needed |
| How to verify | Simulation page `#E8`; log `logs/e8-20261106T123712149Z.log` (last line = `scenario_result`) |

## E9 — Cross-domain: never cut a running flood pump

| Field | Value |
|---|---|
| ID | E9 |
| Name | Cross-domain: never cut a running flood pump |
| Layers involved | Twin, City Brain, Guardian, other Brain domain |
| Description | Flood logic reports a pump running in Z4. Z4 surges and S42 is stuck. Shedding Z4 (F4) would normally be the last resort, but the priority hint forbids it: the Brain alerts instead. |
| Trigger | `python -m scenarios.run e9` |
| Expected: Twin | S42 jammed; Z4 surge |
| Expected: Guardian | All trusted (flood domain sends a priority hint for Z4) |
| Expected: Brain | commands == ['close S42']<br>verification FAILED<br>option 'open F4' rejected because priority zone Z4<br>'operator_alert' event containing ['Z4'] |
| Observed | ✅ commands=['close S42']<br>✅ verifications=['FAILED']<br>✅ would de-energize priority zone Z4<br>✅ Z4: overload risk and no safe switching option improves on doing nothing -- operator action needed |
| How to verify | Simulation page `#E9`; log `logs/e9-20261106T123714403Z.log` (last line = `scenario_result`) |

## E10 — Switch back when the surge is over

| Field | Value |
|---|---|
| ID | E10 |
| Name | Switch back when the surge is over |
| Layers involved | Twin, City Brain |
| Description | Z1 surges for about 100 s then returns to normal. The Brain closes S12 to prevent the overload, then reopens it once the forecast stays safe, so the grid returns to its normal configuration. |
| Trigger | `python -m scenarios.run e10` |
| Expected: Twin | Z1 +280 kW for ~100 s, then back to normal |
| Expected: Guardian | All trusted |
| Expected: Brain | commands == ['close S12', 'open S12']<br>'restoration' event containing ['S12']<br>no true overload in any zone<br>verification SUCCESS |
| Observed | ✅ commands=['close S12', 'open S12']<br>✅ S12 back to normal: forecast stays safe for 10 samples (Z1 61%, Z2 63% after open S12)<br>✅ overload_s={'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0} peak%={'Z1': 80, 'Z2': 90, 'Z3': 65, 'Z4': 69}<br>✅ verifications=['SUCCESS', 'SUCCESS'] |
| How to verify | Simulation page `#E10`; log `logs/e10-20261106T123716231Z.log` (last line = `scenario_result`) |

## E11 — Last-resort load shedding, then restore

| Field | Value |
|---|---|
| ID | E11 |
| Name | Last-resort load shedding, then restore |
| Layers involved | Twin, City Brain, Guardian |
| Description | S42 is jammed and Z4 surges to ~130%. With no switching option left and Z4 measured over capacity, the Brain sheds Z4 (opens F4) as an explicit last resort, alerts the operator, and re-energizes it when the J-1 plan shows it is safe. |
| Trigger | `python -m scenarios.run e11` |
| Expected: Twin | S42 jammed; Z4 +300 kW (~130%) until t=140 s |
| Expected: Guardian | Receives the actuator_anomaly report for S42 |
| Expected: Brain | commands == ['close S42', 'open F4', 'close F4']<br>verification FAILED<br>'operator_alert' event containing ['last-resort']<br>'restoration' event containing ['F4'] |
| Observed | ✅ commands=['close S42', 'open F4', 'close F4']<br>✅ verifications=['FAILED', 'SUCCESS', 'SUCCESS']<br>✅ last-resort load shedding: open F4 de-energizes Z4; it will be restored automatically when safe<br>✅ F4 back to normal: forecast stays safe for 10 samples (Z4 61% after close F4) |
| How to verify | Simulation page `#E11`; log `logs/e11-20261106T123719239Z.log` (last line = `scenario_result`) |

## E12 — Learning cannot be poisoned

| Field | Value |
|---|---|
| ID | E12 |
| Name | Learning cannot be poisoned |
| Layers involved | Twin, City Brain, Guardian |
| Description | Same unknown city as E7B, but the Guardian distrusts the south sensor while the operator's first switchings happen. The Brain throws those observations away and learns T-NS only from trusted evidence. |
| Trigger | `python -m scenarios.run e12` |
| Expected: Twin | Operator toggles T-NS 6x; north +450 kW at t=100 s |
| Expected: Guardian | Distrusts PL-south from t=17 to 35 s |
| Expected: Brain | 'learned_effect' event containing ['T-NS', 'discarded']<br>learned T-NS: north->south<br>commands == ['close T-NS']<br>no true overload in any zone |
| Observed | ✅ T-NS: observation discarded -- PL-south turned untrusted during the confirmation window<br>✅ effect={'switch_id': 'T-NS', 'kind': <SwitchKind.tie: 'tie'>, 'from_zone': 'north', 'to_zone': 'south', 'transfer_fraction': 0.288}, level=L2<br>✅ commands=['close T-NS']<br>✅ overload_s={'north': 0, 'south': 0, 'port': 0} peak%={'north': 76, 'south': 83, 'port': 62} |
| How to verify | Simulation page `#E12`; log `logs/e12-20261106T123722743Z.log` (last line = `scenario_result`) |

## E14 — Stealthy drift hides a real overload (physics catches it)

| Field | Value |
|---|---|
| ID | E14 |
| Name | Stealthy drift hides a real overload (physics catches it) |
| Layers involved | Twin, City Brain, Guardian |
| Description | Z1 really surges to ~120%. An attacker makes PL-Z1 under-read by a slowly growing offset that cancels the surge: every sample looks normal, no jump, and the Guardian keeps trusting the sensor. The substation meter SUB-N still measures the real power. The Brain sees the power balance break, reports it to the Guardian, blocks actions on the contradicted sensors and warns the operator of a possible hidden overload in Z1. |
| Trigger | `python -m scenarios.run e14` |
| Expected: Twin | Z1 +280 kW; PL-Z1 under-reads by a growing offset that cancels it; SUB-N reports the real total |
| Expected: Guardian | Keeps trusting PL-Z1 (the drift is too slow for per-sensor checks); receives the Brain's consistency report |
| Expected: Brain | 'consistency' event containing ['SUB-N', 'does not tell the truth']<br>'operator_alert' event containing ['HIDDEN OVERLOAD', 'Z1']<br>Brain sends no switch command |
| Observed | ✅ SUB-N measures 678 kW but PL-Z1 + PL-Z2 add up to 661 kW (residual +12 kW, tolerance 20, CUSUM rule); one of these sensors does not tell the truth. If it is a zone sensor, the real loads would be: Z1 361 kW (reads 344), Z2 335 kW (reads 317). Actions relying on them are blocked; evidence sent to the Guardian<br>✅ POSSIBLE HIDDEN OVERLOAD in Z1 (~504 kW vs capacity 500, sensor reads 356): SUB-N power balance says the zone sensor under-reads -- please verify on site<br>✅ commands=[] |
| How to verify | Simulation page `#E14`; log `logs/e14-20261106T123725309Z.log` (last line = `scenario_result`) |

## E14B — Same attack, no power-balance check (comparison only)

| Field | Value |
|---|---|
| ID | E14B |
| Name | Same attack, no power-balance check (comparison only) |
| Layers involved | Twin, City Brain, Guardian |
| Description | Identical to E14 with the consistency check off: the Brain sees a calm Z1 and stays silent while it is really overloaded. |
| Trigger | `python -m scenarios.run e14b` |
| Expected: Twin | Same as E14 |
| Expected: Guardian | Keeps trusting PL-Z1 |
| Expected: Brain | no 'risk_detected' event containing ['Z1']<br>no 'operator_alert' event containing []<br>Z1 is overloaded for some time (baseline) |
| Observed | ✅ none<br>✅ none<br>✅ Z1 overload_s=97.0 |
| How to verify | Simulation page `#E14B`; log `logs/e14b-20261106T123727249Z.log` (last line = `scenario_result`) |

## E15 — Stealthy drift fakes a surge (physics stops needless switching)

| Field | Value |
|---|---|
| ID | E15 |
| Name | Stealthy drift fakes a surge (physics stops needless switching) |
| Layers involved | Twin, City Brain, Guardian |
| Description | No real incident. PL-Z1 is made to over-read by +3 kW/s for 70 s: a plausible-looking ramp that would lead the Brain to move load for nothing. SUB-N disagrees; the gate refuses to act on contradicted evidence and the Guardian is told. |
| Trigger | `python -m scenarios.run e15` |
| Expected: Twin | No incident; PL-Z1 over-reads by +3 kW/s for 70 s; SUB-N reports the real total |
| Expected: Guardian | Keeps trusting PL-Z1; receives the Brain's consistency report |
| Expected: Brain | 'consistency' event containing ['SUB-N']<br>Brain sends no switch command<br>evidence sent to the Guardian |
| Observed | ✅ SUB-N measures 668 kW but PL-Z1 + PL-Z2 add up to 681 kW (residual -10 kW, tolerance 20, CUSUM rule); one of these sensors does not tell the truth. If it is a zone sensor, the real loads would be: Z1 350 kW (reads 363), Z2 305 kW (reads 318). Actions relying on them are blocked; evidence sent to the Guardian<br>✅ commands=[]<br>✅ received=['consistency'] |
| How to verify | Simulation page `#E15`; log `logs/e15-20261106T123729134Z.log` (last line = `scenario_result`) |

## E15B — Same fake ramp, no power-balance check (comparison only)

| Field | Value |
|---|---|
| ID | E15B |
| Name | Same fake ramp, no power-balance check (comparison only) |
| Layers involved | Twin, City Brain, Guardian |
| Description | Identical to E15 with the consistency check off: the manipulated sensor makes the Brain switch for nothing. |
| Trigger | `python -m scenarios.run e15b` |
| Expected: Twin | Same as E15 |
| Expected: Guardian | Keeps trusting PL-Z1 |
| Expected: Brain | Brain is manipulated into switching |
| Observed | ✅ commands=['close S12'] |
| How to verify | Simulation page `#E15B`; log `logs/e15b-20261106T123731301Z.log` (last line = `scenario_result`) |

## E16 — Phantom acknowledgement: the switch says it moved, the power says no

| Field | Value |
|---|---|
| ID | E16 |
| Name | Phantom acknowledgement: the switch says it moved, the power says no |
| Layers involved | Twin, City Brain, Guardian |
| Description | S12's status is spoofed: after the Brain's command it reports 'closed' but the breaker never operated. Verification compares the predicted load shift with the sensors, finds no power moved, declares the command FAILED, stops believing the status, reports the anomaly to the Guardian and alerts the operator. |
| Trigger | `python -m scenarios.run e16` |
| Expected: Twin | S12 status spoofed: reports 'closed' after the command, breaker never moves; Z1 surge |
| Expected: Guardian | Receives the Brain's actuator_anomaly report for S12 |
| Expected: Brain | commands == ['close S12']<br>verification FAILED<br>'verification' event containing ['no load moved', 'status not believed']<br>actuator anomaly reported to Guardian<br>'operator_alert' event containing ['Z1']<br>Brain models S12 in its real (open) position |
| Observed | ✅ commands=['close S12']<br>✅ verifications=['FAILED']<br>✅ close S12: FAILED -- S12 reports closed but no load moved (shift {'Z1': 9.9, 'Z2': 1.9} vs predicted {'Z1': -133.0, 'Z2': 133.0}): status not believed, switch treated as still open<br>✅ received=['actuator_anomaly']<br>✅ Z1: overload risk and no safe switching option improves on doing nothing -- operator action needed<br>✅ S12 modelled as open |
| How to verify | Simulation page `#E16`; log `logs/e16-20261106T123733370Z.log` (last line = `scenario_result`) |

## E17 — Sudden real jump, confirmed by physics at once

| Field | Value |
|---|---|
| ID | E17 |
| Name | Sudden real jump, confirmed by physics at once |
| Layers involved | Twin, City Brain |
| Description | Z1 demand jumps by 250 kW in one second (to ~115%). A jump that large is normally held for 5 samples in case it is a spoof (E3). Here substation meter SUB-N moves by the same amount, so the jump is real: the Brain accepts it immediately, restarts the zone's trend at the new level and closes S12 within seconds. |
| Trigger | `python -m scenarios.run e17` |
| Expected: Twin | Z1 demand +250 kW in 1 s at t=30 s; SUB-N measures it too |
| Expected: Guardian | All trusted |
| Expected: Brain | 'reading_suspect' event containing ['PL-Z1', 'confirmed at once']<br>commands == ['close S12']<br>true overload of Z1 at most 3 s |
| Observed | ✅ PL-Z1: jump to 589 kW confirmed at once by an aggregate meter (power balance closes, meter moved alike); forecaster restarted at the new level<br>✅ commands=['close S12']<br>✅ Z1 overload_s=2.0 |
| How to verify | Simulation page `#E17`; log `logs/e17-20261106T123735415Z.log` (last line = `scenario_result`) |

## E17B — Same jump, no physics (5-sample hold, comparison only)

| Field | Value |
|---|---|
| ID | E17B |
| Name | Same jump, no physics (5-sample hold, comparison only) |
| Layers involved | Twin, City Brain |
| Description | Identical to E17 with the consistency check off: the jump waits 5 samples before it is believed. |
| Trigger | `python -m scenarios.run e17b` |
| Expected: Twin | Same as E17 |
| Expected: Guardian | All trusted |
| Expected: Brain | 'reading_suspect' event containing ['PL-Z1', 'persisted']<br>Z1 is overloaded for some time (baseline) |
| Observed | ✅ PL-Z1: jump to 593 kW persisted and sensor trusted; forecaster rebased<br>✅ Z1 overload_s=6.0 |
| How to verify | Simulation page `#E17B`; log `logs/e17b-20261106T123737279Z.log` (last line = `scenario_result`) |

## E18 — Spoof that outlasts the Guardian's reaction time

| Field | Value |
|---|---|
| ID | E18 |
| Name | Spoof that outlasts the Guardian's reaction time |
| Layers involved | Twin, City Brain, Guardian |
| Description | PL-Z4 is spoofed to 650 kW (144% of capacity) and the Guardian only flags it after 9 s. A held jump is normally accepted once it has persisted 5 samples with the sensor still trusted, so the fake overload would be believed and could even trigger last-resort shedding. Substation meter SUB-S contradicts it: the Brain never accepts the reading, however long it lasts. |
| Trigger | `python -m scenarios.run e18` |
| Expected: Twin | Streams fake 650 kW for PL-Z4 for 40 s; SUB-S reports the real total |
| Expected: Guardian | Marks PL-Z4 untrusted only 9 s after the spoof starts |
| Expected: Brain | Brain sends no switch command<br>'reading_suspect' event containing ['PL-Z4', 'contradicted by an aggregate meter']<br>no 'risk_detected' event containing ['Z4'] |
| Observed | ✅ commands=[]<br>✅ PL-Z4: jump 293 -> 650 kW contradicted by an aggregate meter (power balance would not close); not accepted however long it lasts -- held out of forecast, awaiting persistence + Guardian<br>✅ none |
| How to verify | Simulation page `#E18`; log `logs/e18-20261106T123739278Z.log` (last line = `scenario_result`) |

## E18B — Same slow-flagged spoof, no physics (v0.6 behaviour, comparison only)

| Field | Value |
|---|---|
| ID | E18B |
| Name | Same slow-flagged spoof, no physics (v0.6 behaviour, comparison only) |
| Layers involved | Twin, City Brain, Guardian |
| Description | Identical to E18 with the consistency check off: after 5 samples the fake reading is believed and the Brain acts on a fake overload. |
| Trigger | `python -m scenarios.run e18b` |
| Expected: Twin | Same as E18 |
| Expected: Guardian | Same as E18 |
| Expected: Brain | 'reading_suspect' event containing ['PL-Z4', 'persisted']<br>Brain acts on the fake overload |
| Observed | ✅ PL-Z4: jump to 650 kW persisted and sensor trusted; forecaster rebased<br>✅ commands=['close S42', 'open F4', 'close F4', 'open S42'] |
| How to verify | Simulation page `#E18B`; log `logs/e18b-20261106T123741114Z.log` (last line = `scenario_result`) |

## E13 — Multi-zone coordination: re-dispatch, make before break

| Field | Value |
|---|---|
| ID | E13 |
| Name | Multi-zone coordination: re-dispatch, make before break |
| Layers involved | Twin, City Brain |
| Description | Meshed city C. D surges first and is relieved into B (the best single move then). A then surges; its only tie also goes to B, which no longer has room, so every single move is infeasible. The planner finds a 3-step plan: relieve D into C instead (close T-DC), then free B (open T-DB), then close T-AB. Each step is gated and verified; no zone is overloaded in between and nobody is cut off. |
| Trigger | `python -m scenarios.run e13` |
| Expected: Twin | City C (meshed): D +200 kW at t=15 s, A +240 kW at t=80 s |
| Expected: Guardian | All trusted |
| Expected: Brain | 'plan' event containing ['close T-DC -> open T-DB -> close T-AB']<br>commands == ['close T-DB', 'close T-DC', 'open T-DB', 'close T-AB']<br>no true overload in any zone<br>no load shedding<br>verification SUCCESS |
| Observed | ✅ risk in A: 3-step plan close T-DC -> open T-DB -> close T-AB (expected after: A 69%, B 89%, C 92%, D 75%, E 65%); single moves: none feasible; executing step 1, re-planning after each verified step<br>✅ commands=['close T-DB', 'close T-DC', 'open T-DB', 'close T-AB']<br>✅ overload_s={'A': 0, 'B': 0, 'C': 0, 'D': 0, 'E': 0} peak%={'A': 89, 'B': 94, 'C': 91, 'D': 78, 'E': 67}<br>✅ commands=['close T-DB', 'close T-DC', 'open T-DB', 'close T-AB']<br>✅ verifications=['SUCCESS', 'SUCCESS', 'SUCCESS', 'SUCCESS'] |
| How to verify | Simulation page `#E13`; log `logs/e13-20261106T123743014Z.log` (last line = `scenario_result`) |

## E13B — Same surges, single-move decisions (v0.6 behaviour, comparison only)

| Field | Value |
|---|---|
| ID | E13B |
| Name | Same surges, single-move decisions (v0.6 behaviour, comparison only) |
| Layers involved | Twin, City Brain |
| Description | Identical to E13 with the planner off. No single move helps A, so the Brain falls back to last-resort shedding: A's customers are cut off. |
| Trigger | `python -m scenarios.run e13b` |
| Expected: Twin | Same as E13 |
| Expected: Guardian | All trusted |
| Expected: Brain | 'decision' event containing ['open F-A', 'last-resort']<br>customers interrupted (baseline) |
| Observed | ✅ A forecast 148% (P(overload) 100%) within 45 s; open F-A (last-resort shedding) -> A 19% (P 0%)<br>✅ customer-minutes interrupted=2085.0 |
| How to verify | Simulation page `#E13B`; log `logs/e13b-20261106T123746576Z.log` (last line = `scenario_result`) |

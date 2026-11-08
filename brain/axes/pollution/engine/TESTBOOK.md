# Test Book — garbage-bin and air-quality axes

DRAFT by the City Brain team. The "Expected: Twin" and "Expected: Guardian" lines are what
the City Brain needs from its partners. They must be confirmed by the Twin and Guardian teams.
Until then, the scenarios run with the mock Twin and mock Guardian in `brain/mocks/`.

Run any scenario with one command from the triplet root, for example
`python scenarios/g3_spoofed_reading.py`. Each run writes `logs/<ID>.jsonl` and prints its checks.

## What the scenarios cover

| Interaction | What it shows | Scenarios |
|---|---|---|
| Twin → Brain | The city runs normally and the Brain reacts correctly to real events | G1, G2, G4, A1 |
| Twin → Guardian | The Guardian recognizes healthy and unhealthy data | G3, G6, A2, A3 |
| Brain → Guardian | Trust verdicts change decisions, and decisions stay safe | G3, G5, G6, A2, A3, A4 |
| All three | The whole city works as one system, including when something goes wrong | G5, G6, L1, L2, A4, A5 |

## Colors used in the scenarios
A bin is **red** at 90 % full or more. It is **orange** when its projected fill, at its current rate
and until the next truck pass, reaches 90 %. Orange bins must be collected now. Pass intervals:
4 h in crowded zones, 8 h in open zones. **Yellow** can wait, **green** is under 10 %.

---

## G1 — Normal day

| Field | Value |
|---|---|
| ID | G1 |
| Name | Normal day |
| Layers involved | Twin, City Brain (Guardian trusts everything) |
| Description | 24 hours of the city: 10 bins fill at different rates, 2 trucks collect them |
| Trigger | `python scenarios/g1_normal_day.py` |
| Expected: Twin | Sends every bin and truck every 5 minutes, moves trucks along the given route, empties the bin on arrival |
| Expected: Guardian | Marks every reading trusted |
| Expected: City Brain | Sends a truck to every bin that turns orange or red; never exceeds a truck's capacity; no reading is blocked |
| How to verify | The 4 checks print PASS; no bin overflows; `logs/G1.jsonl` |

Reference run: 29 collections and 2 unloads at the depot, no overflow.

## G2 — A red bin takes over an orange stop

| Field | Value |
|---|---|
| ID | G2 |
| Name | Red bin takes over an orange stop |
| Layers involved | Twin, City Brain |
| Description | One truck is on its way to a far orange bin when a nearby bin suddenly reaches 94 % |
| Trigger | `python scenarios/g2_red_takes_over_orange.py` |
| Expected: Twin | Accepts a new dispatch for a truck that is already moving and follows the new route |
| Expected: Guardian | Trusts both bins |
| Expected: City Brain | Redirects the truck to the red bin, keeps the orange bin as the next stop, and collects red first |
| How to verify | Timeline: `t1 sent to far (orange)`, then `t1 sent to near (red), then far`; `near` is emptied before `far`; `logs/G2.jsonl` |

## G3 — Fake overflow on one sensor

| Field | Value |
|---|---|
| ID | G3 |
| Name | Fake overflow on b06 |
| Layers involved | Twin, Guardian, City Brain |
| Description | Sensor b06 reports 98 % full while the bin is really about 20 % full |
| Trigger | `python scenarios/g3_spoofed_reading.py` |
| Expected: Twin | Streams the fake reading and keeps the true level internally |
| Expected: Guardian | Marks b06 `not_trusted` the first time the fake value appears (within one reading interval), with a reason |
| Expected: City Brain | Ignores the reading, never sends a truck to b06, logs the Guardian's reason; the rest of the city keeps being served |
| How to verify | Timeline shows reading, then verdict, then `b06 ignored`, all at the same time; no dispatch to b06; `logs/G3.jsonl` |

A control run without the Guardian's protection sends a truck to b06. This shows the attack works
and that the Guardian is what stops it.

## G4 — Full truck goes to the depot

| Field | Value |
|---|---|
| ID | G4 |
| Name | Full truck goes to the depot |
| Layers involved | Twin, City Brain |
| Description | One truck of capacity 120 kg faces five red bins of 46 kg each |
| Trigger | `python scenarios/g4_full_truck_depot.py` |
| Expected: Twin | Moves the truck to the depot when told to, and resets its load to zero on arrival |
| Expected: Guardian | Trusts all readings |
| Expected: City Brain | Takes two bins, sends the truck to unload when the next bin no longer fits, then continues; never exceeds capacity |
| How to verify | Timeline shows `sent to depot` between pickups; all 5 bins collected; `logs/G4.jsonl` |

## G5 — Partners misbehave

| Field | Value |
|---|---|
| ID | G5 |
| Name | Partners misbehave |
| Layers involved | Twin, Guardian, City Brain |
| Description | Three invalid messages arrive, then the Guardian is unreachable for 20 minutes, then the Twin refuses dispatches for 20 minutes |
| Trigger | `python scenarios/g5_partners_misbehave.py` |
| Expected: Twin | Sends the invalid messages (negative weight, missing fields, not a JSON object); later refuses orders, then accepts again |
| Expected: Guardian | Is unreachable, then comes back |
| Expected: City Brain | Rejects bad messages with the faulty field named; sends no truck while the Guardian is down; retries lost dispatches; no crash, no overflow |
| How to verify | Timeline: `rejected_message`, `guardian_unavailable` then `guardian_recovered`, `twin_unavailable`, then the same truck dispatched again; `logs/G5.jsonl` |

## G6 — Real incident during an attack

| Field | Value |
|---|---|
| ID | G6 |
| Name | Real incident during an attack |
| Layers involved | Twin, Guardian, City Brain |
| Description | At the same moment, b06 reports a fake overflow and the real bin b04 really overflows |
| Trigger | `python scenarios/g6_real_incident_during_attack.py` |
| Expected: Twin | Streams both readings, the fake one and the real one |
| Expected: Guardian | Blocks b06, trusts b04 |
| Expected: City Brain | Sends a truck to b04 in the same cycle and never to b06 |
| How to verify | Timeline: `b04 -> red` and `t? sent to b04` at the same time; b06 ignored; `logs/G6.jsonl` |

---

## L1 — Live run over HTTP (stage demo)

| Field | Value |
|---|---|
| ID | L1 |
| Name | The garbage-bin axis live, over the network, with tokens |
| Layers involved | Twin, Guardian, City Brain (mocks of Twin and Guardian until the joint run) |
| Description | The three layers run as HTTP services. The Twin pushes readings every tick (1 s = 5 city minutes); the Brain asks the Guardian for each one and dispatches trucks. During the run, from the control page: fake reading on b06, real fast fill on another bin, malformed message, Guardian down then back, Twin refusing dispatches then back |
| Trigger | `python scenarios/live_demo.py`, then the buttons on `http://127.0.0.1:8200/control` |
| Expected: Twin | Pushes readings with its token; accepts dispatches with the Brain's token, refuses any other; moves the trucks |
| Expected: Guardian | Answers each verdict request with the Brain's token; marks the fake jump `not_trusted`; trusts the real fast fill |
| Expected: City Brain | Never sends a truck on the fake reading; sends one to the really full bin; rejects the malformed message with its fields named; logs a Guardian outage once and its return once; retries a refused dispatch; refuses a wrong token (`401`) |
| How to verify | Live on `http://127.0.0.1:8100/timeline`, with city time and real time on every row; `logs/LIVE.jsonl`. The same checks run automatically in `brain/tests/test_service.py` |

---

## L2 — City Ground Zero over MCP (rehearsal, then joint run)

| Field | Value |
|---|---|
| ID | L2 |
| Name | The Brain reads the Twin's bins over MCP and orders the full ones emptied |
| Layers involved | Twin (City Ground Zero MCP server), Guardian, City Brain |
| Description | Bins fill. Every cycle the Brain calls `waste_bin_report`, asks the Guardian about each bin, and calls `empty_waste_bin` for trusted red or orange bins, red first |
| Trigger | Rehearsal: `python scenarios/live_mcp_demo.py`. Joint run: the city running, then `python3 -m src.service --config config/brain.ground-zero.json` |
| Expected: Twin | Answers `waste_bin_report`; empties the bin on `empty_waste_bin`; the next report shows it empty |
| Expected: Guardian | A verdict for each bin reading; `not_trusted` for a faked bin |
| Expected: City Brain | Orders only trusted red/orange bins; never orders an untrusted bin; does not order the same bin twice before it is seen emptied; logs a Twin outage once and keeps running |
| How to verify | Timeline: `bin -> orange/red`, `sends crew to WGT-…`, `WGT-… emptied`; `logs/LIVE-MCP.jsonl` (rehearsal) or `logs/GROUND-ZERO.jsonl` (joint run); automated: `brain/tests/test_mcp.py` |

---

# Air-quality axis

## Risk score used by the City Brain
For each trusted air sensor, with PM2.5 in ug/m3, O3 and NO2 in ppb, and t-1 the reading one hour earlier:

| Step | Formula |
|---|---|
| Oxidant | Ox = (2.075 x O3 + 1.07 x NO2) / 3.145 |
| Smoothing | Z_pm = 0.9 x PM(t) + 0.1 x PM(t-1);  Z_ox = 0.5 x Ox(t) + 0.5 x Ox(t-1) |
| Normalisation | R_pm = Z_pm / T_pm x 100;  R_ox = Z_ox / T_ox x 100. Warm season (April-September): T_pm = 31, T_ox = 43. Cold season: T_pm = 25, T_ox = 26 |
| Risk | Risk = max(R_pm, R_ox) + 0.10 x min(R_pm, R_ox) |
| Level | normal < 70 <= warning < 100 <= high <= 130 < critical |

A zone takes the highest risk among its trusted sensors of the last 15 minutes. Actions: **warning**:
alert to sensitive sites (schools, clinics); **high and critical**: public alert and the zone's car parks
closed (`set_parking_barrier closed`, fewer cars driving in). A level goes up at once; it goes down
only after it has held 1 h, the risk has stayed 5 points under the level's lower bound for 2 readings,
and then one level at a time. A zone with no trusted recent sensor is **unknown**: its level and
actions are kept, clean air is never assumed.

Run all the air scenarios: `python -m demo.air all` (from `brain/`). Readings every 10 city minutes,
cold season. Each scenario also runs a **witness** without the protection it tests.

## A1 — Rush-hour peak downtown

| Field | Value |
|---|---|
| ID | A1 |
| Name | Rush-hour peak downtown: alert and car park closed, then lifted without flapping |
| Layers involved | Twin, Guardian, City Brain |
| Description | A normal weekday 06:00-20:00. Traffic raises PM2.5 and NO2 downtown (zone B) at 08:00 and 18:00; midday ozone gives a mild Ox-driven warning elsewhere |
| Trigger | `python scenarios/a1_rush_hour_peak.py` |
| Expected: Twin | Streams air readings every 10 min; applies `set_parking_barrier`; less traffic when a zone's car parks are closed |
| Expected: Guardian | Trusts all sensors |
| Expected: City Brain | Zone B goes high at each rush hour; public alert and LOT-02 closed; both lifted afterwards; no zone comes back to a level within 1 h of leaving it. Witness without hysteresis: LOT-02 closed 6 times and zone B flapping 12 times |
| How to verify | `air_level` and `air_action` events, each with the formula's numbers as reason; `logs/A1.jsonl` |

## A2 — Industrial sensor caps its values

| Field | Value |
|---|---|
| ID | A2 |
| Name | Industrial sensor caps its values: no false all-clear |
| Layers involved | Twin, Guardian, City Brain |
| Description | 08:10 a plant releases particles in zone D (one sensor, AIR-07): critical. 09:10 the plant's sensor starts reporting a flat low value. The Guardian notices only at 09:40 |
| Trigger | `python scenarios/a2_capped_industrial_sensor.py` |
| Expected: Twin | Reports the capped value, keeps the true air internally |
| Expected: Guardian | Marks AIR-07 `not_trusted` (flat while neighbours vary by more than 15 %) |
| Expected: City Brain | Before detection, goes down one step only (critical to high: LOT-04 stays closed); after it, zone D is unknown and keeps high; never announces normal air. Witness without the Guardian: zone D back to normal, LOT-04 reopened while the air is bad |
| How to verify | `air_zone_unknown` with `kept_level`; `logs/A2.jsonl` |

## A3 — Fake PM2.5 spike near the schools

| Field | Value |
|---|---|
| ID | A3 |
| Name | Fake PM2.5 spike: no unjustified restriction |
| Layers involved | Twin, Guardian, City Brain |
| Description | 10:00-10:30, AIR-05 (zone C, schools) reports 180 ug/m3; AIR-06 next door is unchanged |
| Trigger | `python scenarios/a3_fake_spike.py` |
| Expected: Twin | Streams the fake value for AIR-05 |
| Expected: Guardian | `not_trusted`: jump impossible in 10 min, neighbour unchanged |
| Expected: City Brain | Ignores AIR-05; zone C stays on AIR-06, never above warning; LOT-03 never closed. Witness without the Guardian: zone C critical, LOT-03 closed for nothing |
| How to verify | `reading_blocked` for AIR-05; no `close_parking` in zone C; `logs/A3.jsonl` |

## A4 — Guardian down during the morning peak

| Field | Value |
|---|---|
| ID | A4 |
| Name | Guardian down during the morning peak |
| Layers involved | Guardian, City Brain |
| Description | The Guardian is unreachable from 07:50 to 09:20 |
| Trigger | `python scenarios/a4_guardian_down.py` |
| Expected: Guardian | Down, then back |
| Expected: City Brain | Outage logged once; every zone unknown; no alert lifted, no car park reopened while blind; zones known again after recovery |
| How to verify | `guardian_unavailable`, `air_zone_unknown`, `guardian_recovered`, `air_zone_known`; `logs/A4.jsonl` |

## A5 — Real peak during an attack

| Field | Value |
|---|---|
| ID | A5 |
| Name | Real peak downtown during a fake peak in a residential zone |
| Layers involved | Twin, Guardian, City Brain |
| Description | Morning rush hour in zone B (real) while AIR-01 in zone A reports impossible values (attack) |
| Trigger | `python scenarios/a5_real_peak_during_attack.py` |
| Expected: Guardian | Trusts zone B's sensors, `not_trusted` for AIR-01 |
| Expected: City Brain | Zone B restricted (LOT-02 closed); zone A not restricted and still known through AIR-02 |
| How to verify | `logs/A5.jsonl` |

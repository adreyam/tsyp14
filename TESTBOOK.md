# Test Book (City Brain)

Every scenario has one trigger and leaves one log file (spec R2). The scenario files are in `brain/mocks/scenarios/<ID>.json` and `index.json` lists them in order: **three scenarios per axis** (emergency EM, transportation TR, pollution PO, energy EN), then the system checks (SY). The Guardian scenarios were removed from the Test Book (the Guardian behaviour stays in the code). **All 17 run on both sources.** Each scenario happens in one zone (its incidents are placed there, the check `only_zone` fails if an action lands elsewhere; the green waves that follow the units from their stations are the exception).

| How to run | Command |
|---|---|
| Brain alone with its mocks (Twin and Guardian stand-ins), in one process | `python scenarios/run.py TR01` or `python scenarios/tr01_*.py` |
| Every scenario, PASS/FAIL table | `python scenarios/run.py --all` |
| On the running Brain, shown live on the control center | `python scenarios/run.py EM01 --server`, or the **Run** button on the City map page |
| On the real Twin | `./start_city.sh` (Brain on `source: live`), then the Run button (or `--server`) |

**Seeing it run (spotlight)**: while a scenario runs, the control center's City map highlights its zones (the rest of the city dimmed), frames them, and follows its moving vehicle (the first unit on the road: fire engine, ambulance, police car, tow truck, garbage truck; for the traffic jam, a car driving into the queue); a banner says what is followed. You can pan or zoom freely; **Follow again** comes back to it. In the Twin itself (live, or the stand-in) the same happens with the Twin's own commands: `ZONES show <letters>`, `CAMERA follow <vehicle>`, `FOCUS device <id>` when there is nothing to follow; at the end (or on Stop) `ZONES hide ALL` and `CAMERA view overview`. These view commands need the Twin's control room (`run_control_room.sh`); without it the Twin answers "unavailable" and only the dashboard is driven. Setting: `scenario.twin_camera` in `brain/config/brain.yaml`. Code: `brain/src/citybrain/spotlight.py`.

**How a scenario reaches the live Twin**: through the Twin's own commands (`INCIDENTS trigger` with an optional place, `WEATHER set`, `EARTHQUAKE trigger`, `CLOCK set_time / set_speed`, `UTILITY auto_recharge_off / recharge_all`), through the network like any partner (`POST /v1/events`: malformed messages), and through fault injection on the Brain's side of the link (SY03 link cut, SY04 actuator channel). The Twin cannot be told to fake a sensor value or fill a bin, so the few mock-only steps are skipped on live and the live checks cover what the real city produces. On live the scenario runs in real seconds (`live_duration_s`), on the mock it runs faster (`speed`).

Log: `logs/<ID>-<UTC time>.jsonl`, one JSON line per step of every decision (event, verdict, decision, A2A, conflict, action, Twin answer), ending with `Scenario <ID> finished: PASS|FAIL` and the result of every check.

Report: `logs/<ID>-<UTC time>.txt`, the same run in plain words: the result and every check ([ok] / [FAIL] with what was observed), then what each axis did in order (time from the start, why, each command and the Twin's answer, requests between axes), alerts, and a count per axis. Device codes are written as zone letters and signal names. Read this one first; the .jsonl keeps every detail.

## The scenarios

| Axis | Scenarios |
|---|---|
| Emergency | EM01 Building fire · EM02 Flash flood · EM03 Earthquake M6.5 under zone F |
| Transportation | TR01 Traffic jam at rush hour · TR02 Crash at a junction · TR03 Signal failure at a junction |
| Pollution | PO01 Air pollution peak at evening rush · PO02 A bin overflows · PO03 Garbage truck round |
| Energy | EN01 Protect the hospital · EN02 Never cut a running flood pump · EN03 Spoofed grid reading |
| System (partners and links) | SY01 Calm day: the Brain must not overreact · SY02 A partner sends malformed messages · SY03 The link to the Twin drops and comes back · SY04 An actuator channel fails · SY05 Industrial fire with a toxic smoke plume (all axes) |

Last runs: mock source `python scenarios/run.py --all` **17/17 PASS** (logs in `logs/`). Live code path (Brain `source: live` over the Twin's TCP protocol, served by the Twin stand-in): TR02 with the spotlight (zones tinted, camera following each unit, put back on Stop). To do before the demo: one run of each on the real Godot Twin.

# Emergency

## EM01 · Building fire

| Field | |
|---|---|
| ID | EM01 |
| Name | Building fire |
| Axis | Emergency |
| Layers involved | Twin, City Brain (Twin → Brain (three axes on one event)) |
| Description | A building catches fire in zone C (INCIDENTS trigger building_fire, zone C). The Twin opens the incident and sends its first units; the Brain sends every kind of emergency vehicle. |
| Trigger | `python scenarios/run.py EM01` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (300 s) | t=2s `INCIDENTS trigger {"kind": "building_fire", "zone": "C", "x": 110, "z": -300}` |
| Steps on the mock (80 city s at ×4) | t=2s `INCIDENTS trigger {"kind": "building_fire", "zone": "C", "x": 110, "z": -300}` |
| Spotlight | follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Units leave their stations and drive to the fire along the roads (no unit appears at the scene); the Twin resolves the incident once the crews are done. |
| Expected: Guardian | Incident trusted. |
| Expected: Brain | Emergency: one more fire engine, an ambulance and a police car (every kind of emergency vehicle); asks transportation (A2A green_wave) for every unit on its way, and energy (A2A isolate_feeder) for the building. Transportation: the junctions ahead of each unit turn green and go back to their cycle once it is past. Energy: isolates zone C's feeder and re-energises it when the fire is out. Everything happens in zone C (the green waves follow the units from their stations). |
| How to verify | Map: the engines drive from the fire station along the roads, the junctions ahead of them turn green; timeline: A2A lines emergency → transportation / energy and their answers; logs/EM01-*.jsonl. On the live Twin the incident's place is chosen by the Twin, so zones and actuators can differ from the mock run; the live checks only test what does not depend on the place. |
| Checks on live | {"expect": "action", "agent": "emergency", "action_type": "dispatch_unit"}; {"expect": "record", "kind": "action", "contains": "fire unit"}; {"expect": "record", "kind": "action", "contains": "ambulance unit"}; {"expect": "record", "kind": "action", "contains": "police unit"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: green_wave"}; {"expect": "action", "agent": "transportation", "action_type": "green_wave"}; {"expect": "record", "kind": "a2a", "contains": "green_wave completed"}; {"expect": "record", "kind": "a2a", "contains": "emergency → energy: isolate_feeder"} |
| Checks on the mock | {"expect": "action", "agent": "emergency", "action_type": "dispatch_unit"}; {"expect": "record", "kind": "action", "contains": "fire unit"}; {"expect": "record", "kind": "action", "contains": "ambulance unit"}; {"expect": "record", "kind": "action", "contains": "police unit"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: green_wave"}; {"expect": "action", "agent": "transportation", "action_type": "green_wave"}; {"expect": "record", "kind": "a2a", "contains": "green_wave completed"}; {"expect": "record", "kind": "a2a", "contains": "emergency → energy: isolate_feeder"}; {"expect": "action", "agent": "transportation", "command": "reset_cycle", "modes": ["mock"], "action_type": "green_wave"}; {"expect": "action", "agent": "energy", "action_type": "isolate_feeder", "modes": ["mock"]}; {"expect": "only_zone", "zone": "C", "modes": ["mock"]} |

## EM02 · Flash flood

| Field | |
|---|---|
| ID | EM02 |
| Name | Flash flood |
| Axis | Emergency |
| Layers involved | Twin, City Brain (Twin → Brain) |
| Description | A storm (WEATHER storm) floods the low point of zone J: the water rises at FLOOD-02 on the street between junctions r6c4 and r6c5. The flood stays in zone J (the rest of the city only gets puddles). |
| Trigger | `python scenarios/run.py EM02` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (300 s) | t=1s `WEATHER set "storm"`; t=40s `ZONES lockdown {"zone": "J", "reason": "flood"}`; t=270s `WEATHER set "clear"`; t=285s `ZONES reopen {"zone": "J", "reason": "flood"}` |
| Steps on the mock (150 city s at ×5) | t=1s `WEATHER set "storm"`; t=95s `WEATHER set "clear"`; t=100s ramp device FLOOD-02, metric surface_water_depth, to 20, over 20 |
| Spotlight | watches FLOOD-02; follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Storm; flood sensors rise with the rain, FLOOD-02 fastest. Zone J is closed: its four entrances get barriers and police cars (a fire engine when one is free), the cars inside drive out (lights turn green for them) and no car enters until the zone reopens. |
| Expected: Guardian | Readings trusted (rain explains them). |
| Expected: Brain | Emergency: flood in zone J; asks transportation (A2A) to close zone J's car parks and to detour the traffic: transportation sends a police car to each end of the flooded street (r6c4 and r6c5) and holds those junctions north-south green so the traffic goes around the water; emergency sends an ambulance to stand by on the cross street; critical: energy cuts zone J's power. When the water goes down, the police cars and the ambulance are recalled, the junctions and car parks go back to normal, power is restored. |
| How to verify | Map: zone J highlighted, the camera follows the police cars and the ambulance from their stations to the flooded street; A2A panel: emergency → transportation detour_traffic; logs/EM02-*.jsonl. On the real Twin the surface water is one level for the whole city and stops at 42 mm, below the flood threshold (150 mm): the flood response (detour, ambulance, car parks, power) is checked on the mock; the live run checks the storm response, and closes zone J itself (ZONES lockdown at 40 s, reopen at 285 s): barriers and police cars on its four entrances, the cars inside leave, nobody enters. |
| Checks on live | {"expect": "action", "actuator": "LIGHTS", "command": "on"}; {"expect": "situation", "axis": "emergency", "kind": "storm"}; {"expect": "record", "kind": "a2a", "contains": "emergency → energy: street_lights_on"} |
| Checks on the mock | {"expect": "action", "actuator": "LIGHTS", "command": "on"}; {"expect": "situation", "axis": "emergency", "kind": "storm"}; {"expect": "action", "actuator": "LOT-06", "command": "closed", "modes": ["mock"]}; {"expect": "action", "actuator": "ZONE-Zone_SouthCentral_2", "command": "cut_power", "modes": ["mock"]}; {"expect": "action", "actuator": "LOT-06", "command": "auto", "modes": ["mock"]}; {"expect": "action", "actuator": "ZONE-Zone_SouthCentral_2", "command": "restore_power", "modes": ["mock"]}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: close_car_parks", "modes": ["mock"]}; {"expect": "record", "kind": "a2a", "contains": "emergency → energy: street_lights_on"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: detour_traffic", "modes": ["mock"]}; {"expect": "action", "agent": "transportation", "action_type": "police_detour", "command": "send_unit", "modes": ["mock"]}; {"expect": "action", "agent": "transportation", "actuator": "SIG-Junction_r6_c4", "command": "ns_green", "modes": ["mock"]}; {"expect": "action", "agent": "emergency", "action_type": "standby_unit", "modes": ["mock"]}; {"expect": "action", "command": "recall_unit", "modes": ["mock"]}; {"expect": "only_zone", "zone": "J", "modes": ["mock"]} |

## EM03 · Earthquake M6.5 under zone F

| Field | |
|---|---|
| ID | EM03 |
| Name | Earthquake M6.5 under zone F |
| Axis | Emergency |
| Layers involved | Twin, City Brain (Twin → Brain) |
| Description | An M6.5 earthquake shakes zone F (EARTHQUAKE trigger). Several seismic stations record strong shaking. |
| Trigger | `python scenarios/run.py EM03` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (120 s) | t=3s `EARTHQUAKE trigger {"zone": "F", "magnitude": 6.5}`; t=110s `EARTHQUAKE clear ` |
| Steps on the mock (60 city s at ×3) | t=3s `EARTHQUAKE trigger {"zone": "F", "magnitude": 6.5}`; t=55s `EARTHQUAKE clear ` |
| Spotlight | follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Seismic stations report strong shaking near zone F, less further away. |
| Expected: Guardian | Readings trusted. |
| Expected: Brain | Emergency: earthquake confirmed by two stations; the epicentre (zone F) is fixed once confirmed; cuts zone F's water and asks transportation (A2A) to stop the traffic in zone F only: every signal of zone F all red, back to its cycle when the shaking stops. Nothing outside zone F. |
| How to verify | Top bar: 'Earthquake, epicentre near zone F' critical; map arrows to SIGNALS and zone F; logs/EM03-*.jsonl. |
| Checks on live | {"expect": "action", "agent": "transportation", "action_type": "stop_traffic"}; {"expect": "action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "cut_water"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: stop_traffic"}; {"expect": "no_action", "actuator": "SIGNALS"}; {"expect": "max_actions", "value": 40} |
| Checks on the mock | {"expect": "action", "agent": "transportation", "action_type": "stop_traffic"}; {"expect": "action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "cut_water"}; {"expect": "action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "restore_water", "modes": ["mock"]}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: stop_traffic"}; {"expect": "no_action", "actuator": "SIGNALS"}; {"expect": "max_actions", "value": 40}; {"expect": "only_zone", "zone": "F", "modes": ["mock"]} |

# Transportation

## TR01 · Traffic jam at rush hour

| Field | |
|---|---|
| ID | TR01 |
| Name | Traffic jam at rush hour |
| Axis | Transportation |
| Layers involved | Twin, City Brain (Twin → Brain (transportation)) |
| Description | Morning rush (CLOCK 08:15) and a broken-down car just south of the junction at the city centre. It is rush hour, so the traffic is not sent round the block: about 24 cars of the surrounding streets drive into its lane and queue behind it, and the police wave them past only in short batches. The queue grows back over the traffic detector TRAF-03 (occupancy up, speed down) for several readings. |
| Trigger | `python scenarios/run.py TR01` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (300 s) | t=1s `CLOCK set_time "08:15"`; t=2s `INCIDENTS trigger {"kind": "breakdown", "x": 3, "z": 45, "near": {"x": 3, "z": 45}, "jam": 24}` |
| Steps on the mock (60 city s at ×2) | t=1s `CLOCK set_time "08:15"`; t=2s `INCIDENTS trigger {"kind": "breakdown", "x": 3, "z": 45, "near": {"x": 3, "z": 45}, "jam": 24}`; t=2s ramp device TRAF-03, metric occupancy, to 88, over 6; t=2s ramp device TRAF-03, metric speed, to 6, over 6; t=40s clear device TRAF-03 |
| Spotlight | watches TRAF-03; follows a car driving into the queue (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Rush-hour traffic; the broken-down car blocks its lane and a long queue (around 20 cars) builds behind it; the police officer lets them past in short batches; a tow truck drives there from the tow yard, loads the car and the queue drains. |
| Expected: Guardian | Detector readings trusted. |
| Expected: Brain | Transportation: a jam after 3 readings in a row (not a short peak); the detector sits on the north-south road, so the junction r5_c5 holds north-south green to drain the queue; back to its cycle once the traffic flows again. |
| How to verify | Map: zone K highlighted, the camera follows a car driving into the queue; the junction at the centre turns north-south green; logs/TR01-*.jsonl. On the live Twin the queue depends on the traffic of the moment: the live check only asks for the road incident. |
| Checks on live | {"expect": "situation", "axis": "transportation", "kind": "road_incident"} |
| Checks on the mock | {"expect": "situation", "axis": "transportation", "kind": "jam", "modes": ["mock"]}; {"expect": "action", "actuator": "SIG-Junction_r5_c5", "command": "ns_green", "modes": ["mock"]}; {"expect": "action", "actuator": "SIG-Junction_r5_c5", "command": "reset_cycle", "modes": ["mock"]}; {"expect": "situation", "axis": "transportation", "kind": "road_incident"}; {"expect": "only_zone", "zone": "K", "modes": ["mock"]} |

## TR02 · Crash at a junction

| Field | |
|---|---|
| ID | TR02 |
| Name | Crash at a junction |
| Axis | Transportation |
| Layers involved | Twin, City Brain (Twin → Brain (emergency asks transportation, A2A)) |
| Description | Two cars collide in a junction (INCIDENTS trigger red_light_tbone at x -95, z 100): police, ambulance and tow truck come from their stations. |
| Trigger | `python scenarios/run.py TR02` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (300 s) | t=2s `INCIDENTS trigger {"kind": "red_light_tbone", "junction": "Junction_r6_c4"}` |
| Steps on the mock (170 city s at ×4) | t=2s `INCIDENTS trigger {"kind": "red_light_tbone", "junction": "Junction_r6_c4"}` |
| Spotlight | follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | The crash blocks the junction; police, ambulance and tow truck drive from their stations along the roads; the Twin resolves it once the scene is cleared. |
| Expected: Guardian | Incident trusted. |
| Expected: Brain | Emergency: an extra ambulance and police car; asks transportation for a green wave for each unit on its way. Transportation: green ahead of every responder; once the first responder is on scene, the junction is held all red to protect the scene; back to its cycle when the crash is cleared. The ambulance gets a second green wave to hospital with the patient. |
| How to verify | Map: zone J highlighted, the camera follows the first responder from its station to the crash; green rings ahead of it, then the junction all red; logs/TR02-*.jsonl. |
| Checks on live | {"expect": "situation", "axis": "transportation", "kind": "road_incident"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: green_wave"}; {"expect": "action", "agent": "transportation", "action_type": "green_wave"} |
| Checks on the mock | {"expect": "situation", "axis": "transportation", "kind": "road_incident"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: green_wave"}; {"expect": "action", "agent": "transportation", "action_type": "green_wave"}; {"expect": "action", "agent": "transportation", "command": "all_red", "modes": ["mock"]}; {"expect": "action", "agent": "transportation", "command": "reset_cycle", "action_type": "signal_phase", "modes": ["mock"]}; {"expect": "only_zone", "zone": "J", "modes": ["mock"]} |

## TR03 · Signal failure at a junction

| Field | |
|---|---|
| ID | TR03 |
| Name | Signal failure at a junction |
| Axis | Transportation |
| Layers involved | Twin, City Brain (Twin → Brain (transportation)) |
| Description | A junction's signal controller fails in zone K (INCIDENTS trigger signal_failure at Junction_r6_c6); the Twin sends a police car to direct the traffic. |
| Trigger | `python scenarios/run.py TR03` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (200 s) | t=2s `INCIDENTS trigger {"kind": "signal_failure", "junction": "Junction_r6_c6"}` |
| Steps on the mock (60 city s at ×4) | t=2s `INCIDENTS trigger {"kind": "signal_failure", "junction": "Junction_r6_c6"}` |
| Spotlight | follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | The signal goes dark; a police car drives there from its patrol or the police HQ. |
| Expected: Guardian | Incident trusted. |
| Expected: Brain | Transportation: restarts the junction's normal cycle (reset_cycle); green wave for the police car on its way. |
| How to verify | Map: the junction's zone highlighted, the camera follows the police car; the junction's reset_cycle in the timeline; logs/TR03-*.jsonl. |
| Checks on live | {"expect": "situation", "axis": "transportation", "kind": "road_incident"}; {"expect": "action", "agent": "transportation", "command": "reset_cycle"} |
| Checks on the mock | {"expect": "situation", "axis": "transportation", "kind": "road_incident"}; {"expect": "action", "agent": "transportation", "command": "reset_cycle"}; {"expect": "only_zone", "zone": "K", "modes": ["mock"]} |

# Pollution

## PO01 · Air pollution peak at evening rush

| Field | |
|---|---|
| ID | PO01 |
| Name | Air pollution peak at evening rush |
| Axis | Pollution |
| Layers involved | Twin, City Brain (Twin → Brain) |
| Description | The Twin's clock jumps to 17:30: traffic emissions raise PM2.5 across the city. On the mock, AIR-06 (zone J) also climbs to 30 µg/m³ (an industrial release) and falls back. |
| Trigger | `python scenarios/run.py PO01` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (240 s) | t=1s `CLOCK set_time "17:30"`; t=3s `AIR release {"zone": "J", "ug": 24, "seconds": 150}` |
| Steps on the mock (100 city s at ×4) | t=1s `CLOCK set_time "17:30"`; t=2s ramp device AIR-06, metric pm2_5, to 30, over 24; t=55s ramp device AIR-06, metric pm2_5, to 6, over 12; t=75s clear device AIR-06 |
| Spotlight | watches AIR-06; follows a car driving into the queue (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Evening traffic; PM2.5 follows the emissions. |
| Expected: Guardian | Trusted (gradual rise). |
| Expected: Brain | Computes the risk score per zone; above 100 closes the zone's car parks, under 70 for 3 readings reopens them. Below 100 it only watches. |
| How to verify | Top bar pollution; map arrow AIR-06 → LOT-06; logs/PO01-*.jsonl. On the live Twin the scenario adds a release on zone J's air monitor (AIR release, device API: it rises, holds and fades over 150 s), so zone J passes 100 and its car park closes, then reopens; traffic alone keeps the air under the alert level. Every closure carries its score in the reason. |
| Checks on live | {"expect": "no_action", "agent": "pollution", "command": "open"} |
| Checks on the mock | {"expect": "no_action", "agent": "pollution", "command": "open"}; {"expect": "action", "actuator": "LOT-06", "command": "closed", "modes": ["mock"]}; {"expect": "action", "actuator": "LOT-06", "command": "auto", "modes": ["mock"]}; {"expect": "only_zone", "zone": "J", "modes": ["mock"]} |

## PO02 · A bin overflows

| Field | |
|---|---|
| ID | PO02 |
| Name | A bin overflows |
| Axis | Pollution |
| Layers involved | Twin, City Brain (Twin → Brain (waste)) |
| Description | One bin (WGT-T003) fills past 90 % in zone C; the other bins are fine, so no truck round is needed, just that bin. |
| Trigger | `python scenarios/run.py PO02` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (90 s) | t=2s `WGT-T003 fill 62`; t=4s `WGT-T003 fill 76`; t=6s `WGT-T003 fill 88`; t=8s `WGT-T003 fill 96` |
| Steps on the mock (40 city s at ×2) | t=2s ramp device WGT-T003, metric fill_level, to 96, over 6 |
| Spotlight | watches WGT-T003; plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | The bin's weight rises; once emptied it returns to the liner residue. |
| Expected: Guardian | Bin readings trusted. |
| Expected: Brain | Pollution: one bin above 90 %: send a crew to empty it (WGT-T003 empty); no garbage-truck round for a single bin. |
| How to verify | Map: the bin's zone highlighted, the Twin frames WGT-T003 while it fills; the empty command in the timeline; logs/PO02-*.jsonl. On the live Twin the scenario fills WGT-T003 itself (WGT-T003 fill 62 → 96 %, device API) and the Brain empties it once it passes 90 %. |
| Checks on live | {"expect": "action", "actuator": "WGT-T003", "command": "empty", "modes": ["live"]} |
| Checks on the mock | {"expect": "action", "actuator": "WGT-T003", "command": "empty", "modes": ["mock"]}; {"expect": "no_action", "action_type": "garbage_round", "modes": ["mock"]}; {"expect": "only_zone", "zone": "G", "modes": ["mock"]} |

## PO03 · Garbage truck round

| Field | |
|---|---|
| ID | PO03 |
| Name | Garbage truck round |
| Axis | Pollution |
| Layers involved | Twin, City Brain (Twin → Brain (waste, the truck drives)) |
| Description | Three bins of zone J fill up (WGT-T005, T006 above 75 %, T009 above 90 %): the Brain orders one garbage-truck round for zone J, starting at the zone's first container (BIN-24), and empties the fullest bin at once. |
| Trigger | `python scenarios/run.py PO03` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (300 s) | t=2s `WASTE fill {"zone": "J", "levels": [58, 62, 55, 60]}`; t=5s `WASTE fill {"zone": "J", "levels": [86, 82, 79, 84]}`; t=9s `INCIDENTS trigger {"kind": "garbage_collection", "bin": "BIN-24", "zone": "J", "x": -183.3, "z": 46.5}` |
| Steps on the mock (90 city s at ×4) | t=4s ramp device WGT-T005, metric fill_level, to 82, over 6; t=4s ramp device WGT-T006, metric fill_level, to 82, over 6; t=4s ramp device WGT-T009, metric fill_level, to 96, over 6 |
| Spotlight | follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | The containers of zone J fill up; the garbage truck leaves the sanitation depot and drives a round of zone J's containers (BIN-24 first) along the roads, the camera riding with it; each container it tips reads empty again. |
| Expected: Guardian | Trusted. |
| Expected: Brain | Pollution: three bins above 75 % in one zone: order a garbage-truck round for zone J (INCIDENTS trigger garbage_collection from BIN-24); WGT-T009 above 90 %: a crew empties it now. |
| How to verify | Top bar pollution: 'Garbage truck round …' then resolved; map and Twin camera follow the truck; logs/PO03-*.jsonl. On the live Twin the scenario fills zone J's containers (WASTE fill, device API) so the Brain orders the round itself; it also starts it at BIN-24 in case the Brain's round cooldown is still running (the Twin keeps one round at a time, so it is never doubled). |
| Checks on live | {"expect": "situation", "axis": "pollution", "kind": "garbage_round"} |
| Checks on the mock | {"expect": "situation", "axis": "pollution", "kind": "garbage_round"}; {"expect": "action", "action_type": "garbage_round", "modes": ["mock"]}; {"expect": "record", "kind": "action", "contains": "zone J", "modes": ["mock"]}; {"expect": "action", "actuator": "WGT-T009", "command": "empty", "modes": ["mock"]}; {"expect": "only_zone", "zone": "J", "modes": ["mock"]} |

# Energy

## EN01 · Protect the hospital

| Field | |
|---|---|
| ID | EN01 |
| Name | Protect the hospital |
| Axis | Energy |
| Layers involved | Twin, City Brain (Twin → Brain (energy)) |
| Description | From the energy Brain's E4. The grid reserve falls (UTILITY set_reserve 22 %, then 12 %) while the hospital zones are the city's largest loads (zone F the largest, zone K next). Load must be shed, but never where the hospitals are. |
| Trigger | `python scenarios/run.py EN01` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (130 s) | t=5s `UTILITY set_reserve 22`; t=40s `UTILITY set_reserve 12`; t=100s `UTILITY recharge_all ` |
| Steps on the mock (75 city s at ×3) | t=1s state device ZONE-Zone_NorthCentral_2, key _load, value 2.6; t=1s state device ZONE-Zone_SouthCentral_3, key _load, value 2.3; t=2s `UTILITY set_reserve 22`; t=15s `UTILITY set_reserve 12`; t=55s `UTILITY recharge_all ` |
| Spotlight | watches GRID-01, ZONE-Zone_NorthCentral_2; plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | GRID-01 reserve 22 %, then 12 %; recharged at the end. |
| Expected: Guardian | Trusted. |
| Expected: Brain | 22 %: dims every street light. 12 %: sheds the largest zone that may be cut, skipping zones F and K (hospitals) even though they draw the most; the decision says why. Recharged: restores the zone and the street lights. |
| How to verify | Top bar energy 'Grid reserve low'; City map: the shed zone goes dark, F and K stay lit; the action reads 'skipped F protected (hospital), K protected (hospital)'; logs/EN01-*.jsonl. On the live Twin the zone loads are the city's own: the hospital zones are skipped whenever they are the largest. |
| Checks on live | {"expect": "action", "actuator": "LIGHTS", "command": "dim"}; {"expect": "action", "action_type": "load_shed"}; {"expect": "no_action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "cut_power"}; {"expect": "no_action", "actuator": "ZONE-Zone_SouthCentral_3", "command": "cut_power"}; {"expect": "action", "agent": "energy", "command": "restore_power"}; {"expect": "action", "actuator": "LIGHTS", "command": "auto"} |
| Checks on the mock | {"expect": "action", "actuator": "LIGHTS", "command": "dim"}; {"expect": "action", "action_type": "load_shed"}; {"expect": "no_action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "cut_power"}; {"expect": "no_action", "actuator": "ZONE-Zone_SouthCentral_3", "command": "cut_power"}; {"expect": "record", "kind": "action", "contains": "protected (hospital)", "modes": ["mock"]}; {"expect": "action", "agent": "energy", "command": "restore_power"}; {"expect": "action", "actuator": "LIGHTS", "command": "auto"} |

## EN02 · Never cut a running flood pump

| Field | |
|---|---|
| ID | EN02 |
| Name | Never cut a running flood pump |
| Axis | Energy |
| Layers involved | Twin, City Brain (Twin → Brain (energy × emergency)) |
| Description | From the energy Brain's E9 (cross-domain). It rains and the low point of zone J floods (FLOOD-02 above the 150 mm alert, below the 300 mm critical level): the drainage pumps are running. Zone J is also the city's largest load when the grid reserve falls to 12 %. |
| Trigger | `python scenarios/run.py EN02` (mock) or `--server` (running Brain on the mock), or Run on the City map |
| Steps on the live Twin | — (mock only, see How to verify) |
| Steps on the mock (75 city s at ×3) | t=1s `WEATHER set rain`; t=1s state device FLOOD-02, key _depth, value 185.0; t=1s state device ZONE-Zone_SouthCentral_2, key _load, value 2.6; t=12s `UTILITY set_reserve 12`; t=55s `UTILITY recharge_all `; t=60s `WEATHER set clear` |
| Spotlight | watches GRID-01, FLOOD-02, ZONE-Zone_SouthCentral_2; plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Rain, FLOOD-02 about 185-250 mm; GRID-01 reserve 12 %; recharged at the end. |
| Expected: Guardian | Trusted (the rain explains the water). |
| Expected: Brain | Emergency: flood in zone J (zone closed, detour, ambulance on standby, car parks closed; no safety power cut below the critical level). Energy: dims the street lights, then sheds the largest zone that may be cut, skipping zone J ('flood pumps running') and the hospital zones; zone J is never cut. Recharged: restores the zone. |
| How to verify | City map: zone J stays lit while another zone is shed; the action reads 'skipped J flood pumps running'; logs/EN02-*.jsonl. On the real Twin the surface water stops at 42 mm (below the flood alert), so this cross-domain case runs on the mock, like the flood response of EM02. |
| Checks on live | — |
| Checks on the mock | {"expect": "situation", "axis": "emergency", "kind": "flood"}; {"expect": "action", "actuator": "LIGHTS", "command": "dim"}; {"expect": "action", "action_type": "load_shed"}; {"expect": "no_action", "actuator": "ZONE-Zone_SouthCentral_2", "command": "cut_power"}; {"expect": "record", "kind": "action", "contains": "flood pumps running"}; {"expect": "action", "agent": "energy", "command": "restore_power"} |

## EN03 · Spoofed grid reading

| Field | |
|---|---|
| ID | EN03 |
| Name | Spoofed grid reading |
| Axis | Energy |
| Layers involved | Twin, Guardian, City Brain (Twin → Guardian → Brain (energy)) |
| Description | From the energy Brain's E3. An attacker pushes fake GRID-01 readings for 30 s: reserve 6 % and a demand spike, while the real grid storage is healthy. Acting on them would dim the city and cut a neighbourhood's power for nothing. |
| Trigger | `python scenarios/run.py EN03` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (45 s) | t=5s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=8s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=11s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=14s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=17s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=20s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=23s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=26s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=29s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=32s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=35s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake) |
| Steps on the mock (45 city s at ×2) | t=5s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=8s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=11s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=14s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=17s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=20s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=23s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=26s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=29s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=32s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake); t=35s push_event GRID-01 (energy_level 6 %, grid_demand 2400 kW, fake) |
| Spotlight | watches GRID-01; plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | GRID-01 itself keeps reading its real reserve. |
| Expected: Guardian | Each pushed reading disagrees with what the Twin itself reports: untrusted (injected data). |
| Expected: Brain | Ignores every fake reading and says why; no street-light dimming, no load shedding, no power cut. |
| How to verify | Timeline: 'Guardian: GRID-01 untrusted ... injected data' and 'Ignored GRID-01 (untrusted)'; no energy action; logs/EN03-*.jsonl. |
| Checks on live | {"expect": "record", "kind": "verdict", "contains": "injected data"}; {"expect": "record", "kind": "ignored", "contains": "GRID-01"}; {"expect": "no_action", "action_type": "load_shed"}; {"expect": "no_action", "actuator": "LIGHTS", "command": "dim"}; {"expect": "no_action", "agent": "energy"} |
| Checks on the mock | {"expect": "record", "kind": "verdict", "contains": "injected data"}; {"expect": "record", "kind": "ignored", "contains": "GRID-01"}; {"expect": "no_action", "action_type": "load_shed"}; {"expect": "no_action", "actuator": "LIGHTS", "command": "dim"}; {"expect": "no_action", "agent": "energy"} |

# System (partners and links)

## SY01 · Calm day: the Brain must not overreact

| Field | |
|---|---|
| ID | SY01 |
| Name | Calm day: the Brain must not overreact |
| Axis | System (partners and links) |
| Layers involved | Twin, City Brain (Twin → Brain) |
| Description | A normal weekday morning: traffic, air, noise, water, power and seismic readings all vary with their usual noise. Nothing is wrong. |
| Trigger | `python scenarios/run.py SY01` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (120 s) | — |
| Steps on the mock (120 city s at ×4) | — |
| Spotlight | plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Streams normal readings for every device. |
| Expected: Guardian | Every reading trusted. |
| Expected: Brain | Reads everything, opens no alert, sends no emergency, traffic, energy or car-park command (routine bin service is allowed). |
| How to verify | Timeline: readings and no decision; logs/SY01-*.jsonl ends with PASS. |
| Checks on live | {"expect": "no_action", "agent": "emergency"}; {"expect": "no_action", "agent": "transportation"}; {"expect": "no_action", "agent": "energy"}; {"expect": "no_action", "actuator_prefix": "LOT-"} |
| Checks on the mock | {"expect": "no_action", "agent": "emergency"}; {"expect": "no_action", "agent": "transportation"}; {"expect": "no_action", "agent": "energy"}; {"expect": "no_action", "actuator_prefix": "LOT-"} |

## SY02 · A partner sends malformed messages

| Field | |
|---|---|
| ID | SY02 |
| Name | A partner sends malformed messages |
| Axis | System (partners and links) |
| Layers involved | Twin, City Brain (Twin → Brain (input validation)) |
| Description | A badly configured partner pushes: a body that is not JSON, a reading without readings, a device id with spaces, an unknown field and a non-numeric value. |
| Trigger | `python scenarios/run.py SY02` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (15 s) | t=1s push_raw body {"type": "reading", oops; t=2s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'FLOOD-01', 'family': 'flood', 'kind': 'sensor'}, 'readings': {}}; t=3s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'flood sensor 2', 'family': 'flood', 'kind': 'sensor'}, 'location': {'zone': 'J', 'x': -24, 'z': 109}, 'readings': {'surface_water_depth': {'value': 50, 'unit': 'mm'}}}; t=4s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'FLOOD-01', 'family': 'flood', 'kind': 'sensor'}, 'location': {'zone': 'F', 'x': -119, 'z': -176}, 'readings': {'surface_water_depth': {'value': 5, 'unit': 'mm'}}, 'priority': 'urgent'}; t=5s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'FLOOD-01', 'family': 'flood', 'kind': 'sensor'}, 'location': {'zone': 'F', 'x': -119, 'z': -176}, 'readings': {'surface_water_depth': {'value': 'deep', 'unit': 'mm'}}} |
| Steps on the mock (15 city s at ×2) | t=1s push_raw body {"type": "reading", oops; t=2s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'FLOOD-01', 'family': 'flood', 'kind': 'sensor'}, 'readings': {}}; t=3s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'flood sensor 2', 'family': 'flood', 'kind': 'sensor'}, 'location': {'zone': 'J', 'x': -24, 'z': 109}, 'readings': {'surface_water_depth': {'value': 50, 'unit': 'mm'}}}; t=4s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'FLOOD-01', 'family': 'flood', 'kind': 'sensor'}, 'location': {'zone': 'F', 'x': -119, 'z': -176}, 'readings': {'surface_water_depth': {'value': 5, 'unit': 'mm'}}, 'priority': 'urgent'}; t=5s push_event event {'type': 'reading', 'origin': {'layer': 'twin', 'device_id': 'FLOOD-01', 'family': 'flood', 'kind': 'sensor'}, 'location': {'zone': 'F', 'x': -119, 'z': -176}, 'readings': {'surface_water_depth': {'value': 'deep', 'unit': 'mm'}}} |
| Spotlight | plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | (partner) sends 5 bad messages. |
| Expected: Guardian | - |
| Expected: Brain | Refuses each with a readable reason (HTTP 400 on /v1/events), keeps running, takes no decision. |
| How to verify | Timeline: 5 'Malformed input refused' lines with the exact field; logs/SY02-*.jsonl. |
| Checks on live | {"expect": "error"}; {"expect": "max_actions", "value": 0}; {"expect": "record", "kind": "error", "contains": "device_id"} |
| Checks on the mock | {"expect": "error"}; {"expect": "max_actions", "value": 0}; {"expect": "record", "kind": "error", "contains": "device_id"} |

## SY03 · The link to the Twin drops and comes back

| Field | |
|---|---|
| ID | SY03 |
| Name | The link to the Twin drops and comes back |
| Axis | System (partners and links) |
| Layers involved | Twin, City Brain (Twin → Brain (partner failure)) |
| Description | The Brain's connection to the Twin is cut for 15 s (fault injected on the Brain's side of the link), then restored. |
| Trigger | `python scenarios/run.py SY03` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (40 s) | t=4s cut_link seconds 15 |
| Steps on the mock (40 city s at ×2) | t=4s cut_link seconds 15 |
| Spotlight | plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Keeps running; unreachable for the Brain for 15 s. |
| Expected: Guardian | - |
| Expected: Brain | Reports 'Twin unreachable' at once (status, timeline, red badge), keeps its state, resumes when the link is back. With fallback.auto_switch_to_mock it would switch to mock. |
| How to verify | Dashboard badge turns red then green; timeline system lines; logs/SY03-*.jsonl. Also try it for real: close the Godot window and reopen it. |
| Checks on live | {"expect": "system", "contains": "unreachable"}; {"expect": "system", "contains": "connected"} |
| Checks on the mock | {"expect": "system", "contains": "unreachable"}; {"expect": "system", "contains": "connected"} |

## SY04 · An actuator channel fails

| Field | |
|---|---|
| ID | SY04 |
| Name | An actuator channel fails |
| Axis | System (partners and links) |
| Layers involved | Twin, City Brain (Brain → Twin (actuator failure)) |
| Description | The Brain's actuator channel stops delivering commands (fault injected on the Brain side); a traffic signal fails (INCIDENTS trigger signal_failure) and the Brain tries to restart it. |
| Trigger | `python scenarios/run.py SY04` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (60 s) | t=0s actuator_fault value True; t=2s `INCIDENTS trigger {"kind": "signal_failure", "junction": "Junction_r6_c6"}` |
| Steps on the mock (40 city s at ×4) | t=0s actuator_fault value True; t=2s `INCIDENTS trigger {"kind": "signal_failure", "junction": "Junction_r6_c6"}` |
| Spotlight | follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | A signal failure; it does not receive the restart. |
| Expected: Guardian | - |
| Expected: Brain | Sends the command, logs the failure on the same trace, does not hammer it (retries only after the cooldown). |
| How to verify | Timeline: action then 'Twin refused ... fault injected'; logs/SY04-*.jsonl. |
| Checks on live | {"expect": "feedback_refused"}; {"expect": "action", "agent": "transportation"} |
| Checks on the mock | {"expect": "feedback_refused"}; {"expect": "action", "agent": "transportation"} |

## SY05 · Industrial fire with a toxic smoke plume (all axes)

| Field | |
|---|---|
| ID | SY05 |
| Name | Industrial fire with a toxic smoke plume (all axes) |
| Axis | System (all four axes) |
| Layers involved | Twin, City Brain (Twin → Brain (emergency × transportation × pollution × energy)) |
| Description | At the evening rush a fire breaks out in an industrial building in zone G (INCIDENTS trigger building_fire). Its smoke plume makes zone G's air critical and drifts downwind into the hospital district (zone F). Every axis has to act on the same event, through the A2A requests between them. |
| Trigger | `python scenarios/run.py SY05` (mock) or `--server` (running Brain, live or mock), or Run on the City map |
| Steps on the live Twin (240 s) | t=1s `CLOCK set_time 17:45`; t=2s `INCIDENTS trigger {"kind": "building_fire", "zone": "G", "x": 100, "z": -110}`; t=10s `AIR release {"zone": "G", "ug": 40, "seconds": 200}`; t=20s `AIR release {"zone": "F", "ug": 12, "seconds": 160}` |
| Steps on the mock (120 city s at ×3) | t=1s `CLOCK set_time 17:45`; t=2s `INCIDENTS trigger {"kind": "building_fire", "zone": "G", "x": 100, "z": -110}`; t=5s ramp device AIR-07, metric pm2_5, to 48, over 12; t=5s ramp device AIR-08, metric pm2_5, to 44, over 12; t=9s ramp device AIR-02, metric pm2_5, to 18, over 14; t=70s ramp device AIR-07, metric pm2_5, to 6, over 12; t=70s ramp device AIR-08, metric pm2_5, to 6, over 12; t=70s ramp device AIR-02, metric pm2_5, to 6, over 12; t=100s clear |
| Spotlight | watches AIR-07, AIR-08, LOT-04; follows its units as they drive (map and Twin camera); plus the zones of the incidents it starts and of the actions it causes |
| Expected: Twin | Building fire in zone G with its first response (2 fire engines, ambulance, police). Smoke: zone G's air monitors rise far over the guideline (AIR release +40 µg/m³), zone F's less (+12 µg/m³). The fire is put out and the smoke clears. |
| Expected: Guardian | Trusted (the fire explains the smoke). |
| Expected: Brain | Emergency: one more fire engine, ambulance and police car; asks transportation (A2A green_wave) for every unit on its way and energy (A2A isolate_feeder) for the building. Transportation: the junctions ahead of each unit turn green; on pollution's request (A2A close_car_parks) zone G's car park closes so no more cars drive into the smoke. Pollution: air critical in zone G, alert in zone F (the hospital district). Energy: isolates zone G's feeder for the firefighters and re-energises it when the fire is out. When the air clears the car parks reopen. |
| How to verify | Top bar: an emergency, a transportation, a pollution and an energy situation at the same time; A2A panel: emergency → transportation green_wave, emergency → energy isolate_feeder, pollution → transportation close_car_parks; City map: zone G's feeder cut, LOT-04 closed, arrows from the fire; logs/SY05-*.jsonl. On the live Twin the Twin chooses the burning building in zone G, so the checks that depend on the exact place run on the mock. |
| Checks on live | {"expect": "situation", "axis": "emergency", "kind": "incident"}; {"expect": "action", "agent": "emergency", "action_type": "dispatch_unit"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: green_wave"}; {"expect": "action", "agent": "transportation", "action_type": "green_wave"}; {"expect": "record", "kind": "a2a", "contains": "emergency → energy: isolate_feeder"}; {"expect": "situation", "axis": "pollution"}; {"expect": "record", "kind": "a2a", "contains": "pollution → transportation: close_car_parks"}; {"expect": "no_action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "cut_power"} |
| Checks on the mock | {"expect": "situation", "axis": "emergency", "kind": "incident"}; {"expect": "action", "agent": "emergency", "action_type": "dispatch_unit"}; {"expect": "record", "kind": "a2a", "contains": "emergency → transportation: green_wave"}; {"expect": "action", "agent": "transportation", "action_type": "green_wave"}; {"expect": "record", "kind": "a2a", "contains": "emergency → energy: isolate_feeder"}; {"expect": "situation", "axis": "pollution"}; {"expect": "record", "kind": "a2a", "contains": "pollution → transportation: close_car_parks"}; {"expect": "action", "actuator": "ZONE-Zone_NorthCentral_3", "command": "cut_power", "modes": ["mock"]}; {"expect": "action", "actuator": "ZONE-Zone_NorthCentral_3", "command": "restore_power", "modes": ["mock"]}; {"expect": "action", "actuator": "LOT-04", "command": "closed", "modes": ["mock"]}; {"expect": "action", "actuator": "LOT-04", "command": "auto", "modes": ["mock"]}; {"expect": "no_action", "actuator": "ZONE-Zone_NorthCentral_2", "command": "cut_power"} |

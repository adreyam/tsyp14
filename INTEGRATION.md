# Integration: Twin · Guardian · City Brain

```
            line-delimited JSON over TCP 127.0.0.1:$CITY_SIM_API_PORT (twin/scripts/sim_api.gd)
 ┌──────────────┐  read_all / list_incidents (1 s)   ┌────────────────────────────────────────────┐
 │ Twin (Godot  │ ─────────────────────────────────▶ │ City Brain  brain/run_brain.py  :8500       │
 │ + SUMO)      │ ◀───────────────────────────────── │  TwinSource (live | mock, same interface)   │
 │ run_control_ │  command {id, command, value}      │  -> Guardian gate -> 4 agents -> arbitration│
 │ room.sh      │  -> {applied, before, after}       │  -> audit log (logs/*.jsonl)                │
 └──────────────┘                                    └───────────▲───────────────────┬────────────┘
                     Verdict (HTTP, or in-process mock)          │                   │ GET /v1/status, /v1/top,
 ┌──────────────┐ ◀──── POST <guardian>/v1/trust-verdict ────────┘                   │ /v1/map, /v1/timeline
 │ Guardian     │ ────▶ POST /v1/verdicts (push, Bearer)                             ▼
 └──────────────┘                                    ┌────────────────────────────────────────────┐
                                                     │ Control center :8765  (top bar, City map,  │
                                                     │ LIVE/MOCK badge, timeline, Test Book)      │
                                                     └────────────────────────────────────────────┘
```

**Shared decisions**
- **Names**: the Twin's device ids, families, metrics, units, zones (A–P) and coordinates (metres) are used
  unchanged by every layer.
- **One machine**: the Twin's device API listens on 127.0.0.1. The Brain finds its port as the Twin's MCP server
  does.
- **Three messages**: Event, Verdict and Action, each with `source` and `trace_id` (brain/INTERFACE.md, JSON
  Schema at `GET /v1/schemas`).

## One scenario end to end: EM02 flash flood in zone J

1. Trigger: `python scenarios/run.py EM02 --server`. The Twin receives `WEATHER set storm`.
2. Twin: rain falls and the flood sensors accumulate water. `FLOOD-02` (zone J, the low point) rises fastest.
3. Brain: each `read_all` turns the changed rows into Events (`source: live`).
4. Guardian: rain explains the rise, so each Event is `trusted`.
5. Brain, emergency agent: at 150 mm it closes `LOT-06` (zone J). At 300 mm it cuts `ZONE-Zone_SouthCentral_2`
   (zone J's power). Each action carries its reason and the reading's `trace_id`.
6. Twin: it answers `applied: power cut for Zone_SouthCentral_2`. The Brain logs this as `actuator_feedback` on
   the same trace.
7. The storm ends and the water drains. The Brain reopens `LOT-06` and restores power.
8. Control center: the top bar shows "Flood in zone J", critical, then resolved. The map draws an arrow from
   FLOOD-02 to each actuator. The timeline shows reading → Guardian → decision → action → Twin answer with
   timestamps. The log is `logs/EM02-*.jsonl`.

## Start the whole city (one command)

`./start_city.sh` starts everything live: the Twin, the City Brain orchestrator, the three axis engines (brain/axes/*/engine:
waste and air, energy, transport) and the control center, then prints UP/DOWN for each service with the log to read.
`./start_city.sh --mock` runs the same without Godot. `--no-axes` skips the axis Brains.

## Partner failure and change

- **Twin down**: the Brain shows "Twin unreachable" at once (status, badge, timeline) and retries every poll.
  For the demo you can switch to mock in one word (`source: mock`). Test Book: SY03.
- **Guardian down**: the Brain runs in degraded mode and only sends reversible actions. (Guardian behaviour stays in the code; it is no longer in the Test Book.)
- **Actuator refuses a command**: the Brain logs the Twin's answer on the trace and retries only after the
  cooldown. Test Book: SY04.
- **New partner**: point `brain/config/brain.yaml` at its address. A Twin that does not speak the Godot device
  API can push Events to `POST /v1/events`.

## Items for the Twin team

1. Publish which zone holds each hospital, so `policy.protected_zones` is no longer set by hand.
2. Optional: a token on the device API if it is ever bound beyond 127.0.0.1.

## Twin device API: additions used by the Brain (twin/scripts/sim_api.gd)

Three small additions to the `INCIDENTS` command, reusing what the Twin's incident engine already does
(`twin/scripts/incident_engine.py` `send_unit`, `recall_unit`, and the incident types' own place parameters).
Checked with Godot 4.7 (`--check-only`), not yet run inside the Twin.

| Command | Value | What it does |
|---|---|---|
| `INCIDENTS trigger` | `"kind"` or `{kind, junction?, edge?, pos?, vehicle?, bin?, zone?}` | the place is passed to the incident (before, the API dropped it and the Twin chose); a `building_fire` gets a real building with road access, in `zone` when given (before, a fire triggered through the API had no building and resolved at once) |
| `INCIDENTS send_unit` | `{kind: police/ambulance/fire/tow, edge, pos, tag}` | a unit drives to a stop in a lane without an incident and stays there; it shows `incident: <tag>` in `list_emergency_units` |
| `INCIDENTS recall_unit` | unit id | releases a unit sent with `send_unit` (it goes back on patrol or to its station) |

The Brain uses them for: one zone per scenario (incidents placed in the scenario's zone), the flood detour (police
cars at both ends of the flooded street) and the ambulance standby during a flood.

### Additions of 2026-11-07 (fixes: frozen bridge, fire, follow, bins, air)

| Command | Value | What it does |
|---|---|---|
| `WGT-xxx fill` | fill level in % (0-105) | a scenario fills one bin (PO02 on the live Twin) |
| `WASTE fill` | `{zone, levels: [...], containers?: true}` or `{bins: [ids], levels}` | fills the containers (or street cans) of a zone (PO03 on the live Twin) |
| `AIR-xx release` / `AIR release` | µg/m³, or `{zone, ug, seconds?}` | a pollution release that rises, holds and fades on a monitor / the zone's monitors (PO01 on the live Twin) |
| `INCIDENTS trigger` garbage_collection | `{..., zone?}` | the round stays in that zone (all its containers); a second request joins the round already under way |
| `CAMERA follow` | a unit id (FIRE-1, GRB-1) | also works for units (keyed in CityLife) and SUMO ids, which the old lookup missed |

- The SUMO bridge no longer dies when a signal command reaches a junction held by emergency priority (it raised
  `TraCIException: phase index 6 is not in the allowed range [0,0]` and froze the whole city on "WAITING FOR SUMO
  BRIDGE"), nor when the state packet grows past 64 kB. `twin/run_control_room.sh` restarts it if it ever stops; its
  log is `twin/logs/sumo_bridge.log`.
- Air quality: traffic raises the Twin's PM2.5 / PM10 / NO2 but saturates (a jam reads at most ~80 on the risk
  score); only a release (PO01) takes a zone past 100.


### Additions of 2026-11-07 evening (signals and responders)

- **SMART-SIG on the live Twin** (`twin/scripts/smart_signals.py`, run by the incident engine every step): the 49
  junctions keep their fixed programs (6 s green) as a base, but a green is cut short when it has nothing left to
  serve (or its exit is blocked) and stretched, up to 30 s, for a clearly longer queue (max-pressure: queue waiting
  minus queue on the exit lane). Brain commands are now real holds: `SIG-x ns_green` / `ew_green` hold that green up
  to 45 s (through the amber first), then the junction stays biased towards that direction until `reset_cycle`;
  `all_red` holds all red (up to 90 s). Junctions under incident control or emergency priority are left alone.
- **Emergency priority**: every police car, ambulance and fire engine sent somewhere (not only the first response),
  and tow trucks, get green at each light from 90 m out (was 80 m, first responders only).
- **Blocked responder**: a unit stuck behind another unit already parked at a scene (the ambulance at the stop line
  in front of a fire engine) parks where it stands after 6 s and its crew goes on foot, instead of waiting there
  until the incident ends.

### Additions of 2026-11-08 (zone lockdowns, closed accident streets, A* garbage routes)

- **Zone lockdown** (earthquake, flood): `ZONES lockdown {zone, reason}` / `ZONES reopen {zone}` (device API), or
  automatically when an earthquake hits one zone (reopened by "Clean up damage" / `EARTHQUAKE clear`). The four
  streets into the zone (each zone is 2 x 2 blocks: its inner streets meet the boundary roads at four entrances) get
  red lights into them, every car routed through them is sent round, police cars (and a fire engine when one can be
  spared) park across them, and Godot draws a barrier with flashing lamps, cones and a "ROAD CLOSED" sign at each
  (`twin/scripts/road_blocks.py`, `twin/scripts/incidents/zone_roadblocks.gd`; bridge state key `lockdowns`). The
  Brain's emergency agent closes the zone of a confirmed earthquake and of a flood, and reopens it when the flood is
  over.
- **Accident street closed until towed**: after a crash or breakdown, the light at the junction before the street
  shows red for every movement into it (a "closure" copy of the junction's program) and cars routed through it are
  sent another way, checked every 2 s, until the tow truck has taken the car. Responders still get their green
  (emergency priority keeps the closed movement red for the cars sharing their lane). The rush-hour breakdown of TR01
  (`jam`) keeps its queue on purpose.
- **A* for the garbage truck** (`twin/scripts/astar_router.py`): the truck's legs are A* searches on the live road
  graph (cost = travel time + turn costs, heuristic = straight-line distance / top speed), and the round's order is
  the cheapest of all orders of its containers. The plan is written in the round's timeline ("A* route plan: …",
  with the streets A* explored against Dijkstra).
- **Evacuation of a closed zone**: when a zone closes, every civilian car on its inner streets is routed to the nearest
  way out (out of the centre junction, then a street beyond the boundary), drives a little faster (speed factor 1.35)
  and gets a short SMART-SIG green hold at each red light ahead of it (8 s, at most 20 s per direction, taking turns;
  junctions under emergency priority or a Brain hold are left alone); its usual speed comes back once it is outside.
  Cars inside the zone are out in 30-60 s (was 60-90 s, some never). A scheduled car that starts right on a closed
  entrance (a city gate) is taken off and replaced through an open gate. Bridge state: `lockdowns[].evacuating`.
- **EM02 Flash flood on the live Twin** closes zone J itself (`ZONES lockdown` at 40 s, `reopen` at 285 s) since the
  live water never reaches the Brain's flood threshold: barriers, police cars, evacuation as for an earthquake.
- **SMART-SIG, longer green for the busier road**: the green a phase deserves grows with the cars on its approaches,
  weighted by their share of all the cars at the junction (6 s + 1.2 s x own^2 / (own + cross + 1), up to 50 s; a
  Brain bias counts that road's cars twice); it still ends early when its cars have gone, its exit is blocked, or the
  cross street holds 1.5 x its pressure. Measured on SUMO (TR01 jam + fire): 39.3 stopped cars on average vs 40.1
  with fixed programs, mean speed 3.62 vs 3.54 m/s, fire engine to the scene 74 s vs 96 s; plain traffic 47.8 vs 50.1.
- Flagger: no civilian car is waved past a scene that reaches almost to the far junction (no room in the oncoming
  lane to stop the cars coming out of it); they wait for the tow.
- Responders: a unit no longer drives into another incident's junction while a car is stranded in it (rerouted, or
  it waits at the stop line); a priority overtake stops 10 m short of the junction so SUMO never forces it back
  into its lane beside another vehicle.

### Additions of 2026-11-08 afternoon (car parks, scenario clean-up, tow trucks, signals, energy scenarios)

- **Car parks empty when their zone closes** (`twin/scripts/parking_lots.py`, `parking/parking_lot_presenter.gd`): the
  barrier closes to arrivals, cars on their way are sent to another car park, and every parked car leaves as soon as
  the driveway is free (its driver runs back: no stroll, no dwell time), then drives out of the zone with the other
  evacuees. A full 12-bay car park is empty in about 3 minutes (one car at a time down its driveway). The barrier goes
  back to its mode when the zone reopens. Bridge state: `parking.lots[].evacuating`.
- **Barriers removed when a scenario ends or is stopped** (`brain/src/citybrain/scenario.py`): a live run that closed a
  zone (a `ZONES lockdown` step, an earthquake, or a lockdown the Brain sent) sends `ZONES reopen` (all zones) when it
  finishes or is stopped; barriers, police cars and red lights go. The emergency agent closes the zone again on the
  next earthquake there.
- **Tow trucks drawn as tow trucks**: `tow_response` (a tow truck driving with lights) was missing from
  `twin/sumo/vehicle_classes.json`, so Godot drew it as a plain car.
- **SMART-SIG, queues that actually move**: every approach has a single lane, so a car waiting to turn left at its head
  blocked the whole straight green (two or three cars per green however long the queue). Now that green ends after 3 s
  and the left-turn phase comes at once; after it the same road gets its straight green again if it still has the
  longer queue (once per cycle). Rush-hour breakdown (TR01 `jam`): the queue fills the broken-down car's street; once
  that street is full, a queued car reaching the junction in front of it goes round the block instead of plugging the
  junction. Measured on SUMO: plain traffic 43.9 stopped cars and 4.11 m/s (fixed programs 49.2 / 3.70); rush-hour
  jam 44.1 / 3.95 (fixed 48.4 / 3.64; this morning's version 52.6 / 3.30).
- **Energy scenarios from the energy Brain's test book** (EN01-EN03 replaced): EN01 *Protect the hospital* (E4: load
  shed, never in a hospital zone even when it is the largest load), EN02 *Never cut a running flood pump* (E9,
  cross-domain: a zone with an active flood is skipped when shedding, its pumps keep running; mock only, as the live
  water never reaches the flood alert), EN03 *Spoofed grid reading* (E3: fake GRID-01 readings are untrusted by the
  Guardian, no action). New Twin command `UTILITY set_reserve <0-100>` (sim_api.gd, utility_resource_system.gd, and the
  mock Twin) sets the grid storage so the live runs reach the low reserve at once; a stopped run recharges it. The
  Guardian stand-in now also compares a pushed `energy_level` with what the Twin itself reports.

### Addition of 2026-11-08 night (one critical scenario for every axis)

- **SY05 Industrial fire with a toxic smoke plume** (`brain/mocks/scenarios/SY05.json`, System group): a building fire
  in zone G at the evening rush with its smoke plume (`AIR release` on zones G and F). Only existing behaviour, no
  code change: emergency dispatches and asks transportation for green waves and energy for the feeder isolation;
  pollution's air alert asks transportation to close zone G's car park; energy isolates and re-energises zone G's
  feeder; the hospital district (zone F) gets an air alert and is never cut.


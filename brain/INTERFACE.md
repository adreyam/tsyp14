# City Brain · Interface Card (R1)

The City Brain reads the city from the Twin, asks the Guardian how far it can trust each reading, decides with
four agents (emergency, transportation, pollution, energy) that also ask each other for help (A2A, §4), and
commands the Twin's actuators. Every message in and out is one of four JSON messages, validated by the same pydantic models whatever the source
(`brain/src/citybrain/contracts.py`; JSON Schema served at `GET /v1/schemas`). Timestamps are UTC ISO 8601.
Every message carries `source` (`live` or `mock`) and a `trace_id` that follows one reading to its action.

## Transports

| Who | How | Message |
|---|---|---|
| Twin → Brain (pull, default) | The Twin's own device API: line-delimited JSON over TCP, `{"id","method","params"}` → `{"id","ok","result"}` (twin/scripts/sim_api.gd). The Brain calls `read_all` every `twin.poll_s` and `list_incidents` every `twin.incidents_poll_s`. | Twin rows, turned into **Event** by `twin.Normaliser` |
| Any partner → Brain (push) | `POST http://<brain>:8500/v1/events`, `Authorization: Bearer <BRAIN_TOKEN>`, one Event or a list (≤ 500) | **Event** |
| Brain → Twin | Same TCP device API, method `command` | **Action** → `{"method":"command","params":{"id","command","value"}}` |
| Twin → Brain (answer) | The answer to that command | **Event** `type: actuator_feedback`, same `trace_id` |
| Brain → Guardian | `POST <guardian.url><guardian.verdict_path>` with `{"event": Event}` (when `guardian.mode: http`) | answer: **Verdict** |
| Guardian → Brain (push) | `POST /v1/verdicts`, Bearer token | **Verdict** (cached per device for `valid_for_s`) |
| Twin → Brain (fleet) | `list_emergency_units` and `list_vehicles` every `twin.poll_s`: where each fire engine, ambulance and police car is and where it is going | the agents' fleet view (green waves) |
| Agent → agent (A2A, inside the Brain) | `A2ABus` (`brain/src/citybrain/a2a.py`), delivered in the same pass as the event that caused it | **A2A task** |
| Partner → agent (A2A over HTTP) | `POST /v1/a2a/tasks`, Bearer token; follow it with `GET /v1/a2a/tasks/<task_id>` | **A2A task** |
| Dashboard / jury (read only) | `GET /v1/health`, `/v1/status`, `/v1/timeline?after=N`, `/v1/map`, `/v1/top`, `/v1/scenarios`, `/v1/schemas`, `/v1/a2a`, `/v1/a2a/agents`, `/v1/a2a/tasks` | JSON |

## 1. Event (in)

Real example (logs/EM02-*.jsonl, a flood reading from the Twin's FLOOD-02):

```json
{
  "schema": "citybrain.event/1",
  "event_id": "ev-mw5gvxl48c6d",
  "trace_id": "tr-mw5gvxl4d1cd",
  "ts": "2026-11-06T21:08:59.463Z",
  "source": "live",
  "scenario_id": null,
  "type": "reading",
  "origin": { "layer": "twin", "device_id": "FLOOD-02", "family": "flood", "kind": "sensor" },
  "location": { "zone": "J", "x": -24.0, "z": 109.0 },
  "readings": {
    "surface_water_depth": { "value": 304.86, "unit": "mm" },
    "rise_rate": { "value": 240.0, "unit": "mm/min" }
  },
  "state": {},
  "incident": null,
  "twin_clock": "09:01"
}
```

`type` is `reading`, `incident` (`incident: {id, kind, status, phase, title}`), `actuator_feedback` or `heartbeat`.
Device ids, families, metric names and units are the Twin's own (`TRAF-01`, `AIR-06`, `FLOOD-02`, `SEIS-06`,
`SIG-Junction_r5_c5`, `VLV-04`, `SWT-01`, `LOT-06`, `ZONE-Zone_SouthCentral_2`, `GRID-01`, `WGT-T001` ...).
`location` is in Twin metres (x east, z south, −380…380) with the zone letter A–P (4 × 4 zones of 190 m).
`lat`/`lon` are optional, for partners that use them.

Refused, with a readable reason (HTTP 400 and an `error` line in the audit log): a body that is not JSON, an
unknown field, a device id that is not a Twin id, a zone outside A–P, a `reading` without readings, a non-finite
value, more than 32 metrics. Example answer:
`{"error":{"code":"invalid","message":"event.origin.device_id: device_id 'flood sensor 2' is not a Twin device id (expected e.g. FLOOD-03, SIG-Junction_r5_c5)"}}`

## 2. Verdict (Guardian → Brain)

```json
{
  "schema": "citybrain.verdict/1",
  "verdict_id": "vd-mw5gw2k1a9f3",
  "trace_id": "tr-mw5gw2jz11c0",
  "ts": "2026-11-06T21:09:12.204Z",
  "source": "live",
  "device_id": "FLOOD-03",
  "event_id": "ev-mw5gw2jz0b7e",
  "status": "untrusted",
  "confidence": 0.94,
  "reason": "FLOOD-03 reads surface_water_depth=410 while every other flood sensor reads about 0 and no rain is falling",
  "valid_for_s": 60,
  "containment": "isolate"
}
```

| status | What the Brain does |
|---|---|
| `trusted`, `released` | uses the data |
| `suspect` | uses it, but sends only reversible actions |
| `untrusted` | ignores the event and logs why (no decision is taken on it) |
| `quarantined` | ignores the device's data **and never commands that device** (chooses another actuator) |
| no verdict (Guardian down or slow) | `guardian.on_missing`: `degraded` (default, reversible actions only), `ignore` or `trust` |

`reason` is required for `suspect`, `untrusted` and `quarantined`.

## 3. Action (out)

```json
{
  "schema": "citybrain.action/1",
  "action_id": "act-mw5gvxl862cd",
  "trace_id": "tr-mw5gvxl4d1cd",
  "caused_by": ["ev-mw5gvxl48c6d"],
  "ts": "2026-11-06T21:08:59.468Z",
  "source": "live",
  "agent": "emergency",
  "action_type": "cut_power",
  "target": { "actuator_id": "ZONE-Zone_SouthCentral_2", "command": "cut_power", "value": null },
  "location": { "zone": "J", "x": -95.0, "z": 95.0 },
  "severity": "critical",
  "priority": 0,
  "parameters": { "situation": "emergency:flood:J" },
  "reason": "water above 300 mm in zone J: cut the zone's power (electrical safety)",
  "confidence": 0.9,
  "reversible": false,
  "situation_id": "emergency:flood:J"
}
```

`target.actuator_id` and `target.command` are sent to the Twin unchanged, so any Twin command works:
`INCIDENTS dispatch {id, kind}`, `SIGNALS phase all_red`, `SIG-… ns_green|ew_green|all_red|reset_cycle`,
`VLV-… open|close`, `ZONE-… cut_power|restore_power|cut_water|restore_water`, `LOT-… auto|open|closed`,
`WGT-… empty`, `LIGHTS on|off|auto`. Sent to the Twin as:
`{"id":"act-mw5gvxl862cd","method":"command","params":{"id":"ZONE-Zone_SouthCentral_2","command":"cut_power"}}`.
The Twin's answer comes back as an `actuator_feedback` Event on the same trace (`state.ok`, `state.applied` or `state.error`).

## 4. A2A task (agent → agent)

An axis that needs another axis to continue a scenario does not touch that axis's actuators: it sends an **A2A
task** to the agent that owns them, and that agent decides how (A2A task model: agent cards with skills; a task with
a skill, an input and a reason; states `submitted → working → completed | rejected | failed | canceled`, with a
note at each change; the sender is told every change). The actions planned for a task go through the same trust
gate, arbitration and audit as any other action; an action carrying `parameters.a2a_task` completes or fails the
task with the Twin's answer. Every message is one `a2a` line in the audit log, on the trace of the event that
caused it.

| Asked of | Skill | Input | Who asks, when |
|---|---|---|---|
| transportation | `green_wave` | `unit`, `incident`, `destination {x, z}` | emergency: every fire engine, ambulance, police car on its way to a scene, and an ambulance carrying a patient to hospital |
| transportation | `stop_traffic` / `resume_traffic` | `zone`, `reason` | emergency: confirmed critical earthquake / shaking over: every signal of that zone red, then back to its cycle |
| transportation | `detour_traffic` / `end_detour` | `zone`, `x`, `z`, `tag` | emergency: flood: a police car to each end of the flooded street (`INCIDENTS send_unit`), the cross street held green; then the police cars recalled |
| transportation | `close_car_parks` / `reopen_car_parks` | `zone` | emergency (flood), pollution (bad air); reopened only when no axis still needs them closed |
| energy | `isolate_feeder` / `restore_feeder` | `incident`, `zone` | emergency: building fire (rejected in a protected hospital zone) / fire out |
| energy | `cut_zone_power` / `restore_zone_power` | `zone` | emergency: critical flood (electrical safety) / flood over |
| energy | `street_lights_on` / `street_lights_auto` | — | emergency: storm (wins over dimming) / storm over |
| emergency | `dispatch_unit` | `incident`, `kind` | any axis or partner: one more unit to an open incident |
| pollution | `air_status` | `zone` | any axis or partner: the zone's air risk score, answered at once in `result` |

Real example (logs/EM01-*.jsonl):

```json
{
  "schema": "citybrain.a2a/1",
  "task_id": "a2a-mw5t3yr3d4aa",
  "trace_id": "tr-mw5t3yr2f498",
  "from_agent": "emergency",
  "to_agent": "transportation",
  "skill": "green_wave",
  "input": {
    "unit": "FIRE-1",
    "kind": "fire",
    "incident": "INC-9001",
    "destination": {
      "x": 110.0,
      "z": -300.0
    },
    "leg": "scene"
  },
  "reason": "FIRE-1 (fire) is on its way to INC-9001 (building fire): it needs green along its route",
  "state": "completed",
  "note": "FIRE-1 arrived (on_scene); 1 junction(s) turned green on its way",
  "result": {
    "junctions": 1
  }
}
```

Over HTTP (an axis engine, an operator tool): `POST /v1/a2a/tasks` with `{from_agent, to_agent, skill, input, reason}`
(`from_agent` is your own service name, never one of the four axes) → `202` with the task; an unknown skill is
`rejected` with the list of skills; a bad field is `400` with the exact problem. `GET /v1/a2a/agents` lists every
agent's skills. `POST /v1/a2a/tasks/<id>/cancel` cancels an open task.

### Green wave (how transportation does `green_wave`)

The unit drives from its station along the roads (in the Twin, and in the stand-in: `brain/mocks/fleet.py`). On
every Brain loop the transportation agent reads the unit's position and direction of travel; every signalled
junction up to `green_wave_ahead_m` (250 m) ahead of it on its road, and not past the street of its destination,
turns green in its direction (`ew_green` / `ns_green`); each one goes back to its cycle (`reset_cycle`) once the unit
is `green_wave_release_m` (12 m) past it, when it turns off that road, or after `green_wave_max_hold_s` (45 s) at
most. Two units on crossing roads: first come, first served. Junctions under a scene hold, or while traffic is
stopped city-wide, are left alone. The Twin adds its own priority in the last 80 m before each light
(twin/scripts/emergency_priority.py), in the same direction. Settings: `agents.transportation` in `brain/config/brain.yaml`.

## Pointing the Brain at another partner (configuration, never code)

Everything partner-specific is in `brain/config/brain.yaml`:

```yaml
source: live                 # live | mock: the only switch between the Godot Twin and the stand-ins
twin:   { host: 127.0.0.1, port: auto, timeout_s: 3.0, poll_s: 1.0 }   # auto = $CITY_SIM_API_PORT, else the Twin's port file, else 8820
guardian: { mode: http, url: http://10.0.0.30:8300, verdict_path: /v1/trust-verdict, token_env: GUARDIAN_TOKEN, on_missing: degraded }
brain:  { host: 127.0.0.1, port: 8500, token_env: BRAIN_TOKEN }
fallback: { auto_switch_to_mock: false, after_s: 20 }
```

Overrides without editing the file: `CITY_BRAIN_SOURCE=mock`, `CITY_BRAIN_CONFIG=/path/other.yaml`, `python brain/run_brain.py --source live --port 8600`.
A different Twin that does not speak the Godot device API can push Events to `POST /v1/events` instead.

## Authentication

- Push endpoints (`POST /v1/events`, `/v1/verdicts`, `/v1/scenarios/<ID>/run`, `/v1/scenarios/stop`, `/v1/a2a/tasks`) need `Authorization: Bearer <token>`;
  the token is read from the variable named by `brain.token_env` (default `BRAIN_TOKEN`), compared in constant time;
  a wrong token gets `401` and an `auth_refused` line in the audit log. Never written in a file (the yaml
  `token_default` is only for the local demo; set `BRAIN_TOKEN` for anything else).
- The Brain sends its own `Authorization: Bearer $GUARDIAN_TOKEN` to an HTTP Guardian.
- The Twin's device API listens on 127.0.0.1 only (same machine), as the Twin team built it.
- Read-only endpoints are open on 127.0.0.1 for the timeline screen. Bodies are capped (`brain.max_body_bytes`).

## Errors and partner failures

| Situation | Brain behaviour |
|---|---|
| Malformed or unexpected message | refused with the exact field and reason (400 / audit `error`), nothing used, the Brain keeps running |
| Twin unreachable | `/v1/status` `twin.connected: false` with the reason and the hint; dashboard badge "LIVE · Twin unreachable"; retries every poll; optional automatic switch to mock (`fallback.auto_switch_to_mock`) |
| Twin refuses a command | `actuator_feedback` with `state.error`; the command can be retried after `policy.cooldown_s` |
| Guardian down | degraded mode (above) |
| Two axes want the same actuator | `policy.axis_priority` (emergency > energy > pollution > transportation); a power cut by another axis in a zone with an active emergency is vetoed; every conflict is logged |

### Seeing a scenario (spotlight)
While a scenario runs, `GET /v1/map` carries `spotlight`: `{scenario, name, axis, zones, devices, incidents, follow, twin}`.
`zones` are the scenario's zones (its focus, the zones of the incidents it started, found as they appear on the live
Twin, of the devices it plays with and of the actions it causes); `follow` is the vehicle to keep in view
`{kind: unit|car, unit, vehicle, type, status, x, z}` (the first of its units on the road, kept until it arrives; for
`focus.follow: traffic` a car driving towards its device). The control center highlights the zones, dims the rest,
frames them and keeps `follow` in the middle of the map. In the Twin, with `scenario.twin_camera: true`, the Brain sends
the Twin's own view commands: `ZONES show <letters>`, `CAMERA follow <vehicle>`, `FOCUS device <id>`; at the end or on
Stop `ZONES hide ALL` and `CAMERA view overview`. They are presentation only: not actions, not in the decision log,
never undone; a Twin without its control room answers "unavailable" and is then not driven.

### Stopping a scenario
`POST /v1/scenarios/stop` (Bearer token) ends the running scenario within 0.1 s. Its result is `STOPPED` (checks are not graded), and on the live Twin it undoes only what that run changed: weather back to clear, earthquake cleared, clock speed back to x1, grid auto-recharge back on (after a recharge), and the incidents it triggered resolved. A time of day set with `CLOCK set_time` is left as it is. Answers `409 idle` when nothing runs.
The control center's **Stop scenario** button (top bar) calls `POST /api/scenarios/stop`, which stops whatever is running: Brain Test Book, control-center scenarios (reset of every axis), transport Test Book (its `/api/v1/scenarios/<id>/stop`) and energy (back to free play). `GET /api/scenarios/running` lists what is running.

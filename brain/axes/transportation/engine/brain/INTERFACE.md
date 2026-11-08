# Interface Card - City Brain (transportation + parking)

Version **1.0**. Plain **HTTP + JSON** (REST). No SDK, no shared code: any Twin or Guardian that can send an HTTP POST can connect.
All timestamps are ISO-8601 UTC (`2026-11-05T22:10:01Z`). Interactive documentation of every route: `http://<brain>:8000/docs`.

```
   TWIN  ──(1) readings ─────────────►  BRAIN  ──(3) actuator commands ──►  TWIN (actuators)
     │                                    ▲  │
     │                                    │  └──(4) decisions / reports ──►  GUARDIAN
     └──(readings, same stream) ──► GUARDIAN ──(2) trust verdicts ─────────┘
```

| # | Direction | Route | Who calls whom |
|---|---|---|---|
| 1 | Twin -> Brain | `POST /api/v1/readings` | the Twin pushes sensor readings and emergency calls |
| 2 | Guardian -> Brain | `POST /api/v1/verdicts` | the Guardian pushes trust verdicts about devices |
| 3 | Brain -> Twin | `POST {twin.url}/api/actuators/{actuator_id}/commands` | the Brain acts on the city through the Twin's actuators |
| 4 | Brain -> Guardian | `POST {guardian.url}/api/decisions` and `/api/reports` | the Brain tells the Guardian what it decided and what it observed |
| - | anyone | `GET /api/v1/health`, `GET /api/v1/timeline`, `GET /api/v1/state` | health check, live cross-layer timeline, trust state |

## 1. Configuration (nothing is hard-coded)

Partner addresses, keys and limits are in **`config/brain.yaml`**. Every value has an environment override (used by `docker-compose.yml`):

| Setting | Environment variable | Default |
|---|---|---|
| `twin.url` (where the Brain sends commands) | `BRAIN_TWIN_URL` | `http://127.0.0.1:9001` |
| `twin.enabled` | `BRAIN_TWIN_ENABLED` | `true` (`0` = Brain alone, no outgoing commands) |
| `guardian.url` | `BRAIN_GUARDIAN_URL` | `http://127.0.0.1:9002` |
| `guardian.enabled` | `BRAIN_GUARDIAN_ENABLED` | `true` |
| `auth.inbound.twin_key` / `guardian_key` | `BRAIN_TWIN_KEY` / `BRAIN_GUARDIAN_KEY` | `twin-dev-key` / `guardian-dev-key` |
| `auth.outbound.twin_key` / `guardian_key` | `BRAIN_TO_TWIN_KEY` / `BRAIN_TO_GUARDIAN_KEY` | `brain-to-twin-dev-key` / `brain-to-guardian-dev-key` |
| `brain.port`, `brain.logs_dir` | `BRAIN_PORT`, `BRAIN_LOGS_DIR` | `8000`, `../logs` |
| `trust.unknown_device_policy` | - | `trust` (or `reject`: refuse devices the Guardian has not rated) |

**Connecting to a new partner = 3 steps:** (1) put its URL in `config/brain.yaml`; (2) exchange the two keys of that pair (one per direction) and write them in `auth`; (3) make the partner send/accept the JSON of sections 3 and 4 (field names are listed there; a partner with other field names only needs a thin mapping function on its side).

## 2. Authentication

Shared secret in the HTTP header **`X-API-Key`**, one key per direction. The Brain refuses a call without the right key (`401`, error `missing_api_key` / `invalid_api_key`). The Twin's key only works on `/readings`, the Guardian's key only on `/verdicts`. Keys are configuration, rotate them by editing `config/brain.yaml`. (Parking gateways keep their own `X-Device-Key`; the administrator screen uses `X-Admin-Key`.)

## 3. Messages the Brain RECEIVES

### 3.1 Reading - `POST /api/v1/readings`  (header `X-API-Key: <twin key>`)

One reading, or a list of readings (`[ {...}, {...} ]` or `{"readings": [...]}`, max 200).

```json
{
  "device_id": "sensor:P01:A3",
  "type": "parking.space.occupancy",
  "ts": "2026-11-05T22:10:01Z",
  "seq": 1042,
  "data": { "lot_id": "P01", "space": "A3", "occupied": true }
}
```

| Field | Rule |
|---|---|
| `device_id` | 3-80 chars `[A-Za-z0-9:_-./]`. Naming: `sensor:<lot>:<space>`, `sensor:<lot>:counter`, `sensor:city:emergency-calls` |
| `type` | one of the three types below, otherwise `422 invalid_reading` listing the supported types |
| `ts` | ISO-8601; refused if more than 1 h old or 5 min in the future (`stale_timestamp`: clock problem) |
| `seq` | optional, integer >= 0 |
| `data` | depends on `type` |

| `type` | `data` | What the Brain does |
|---|---|---|
| `parking.space.occupancy` | `lot_id` (`P01`..`P08`), `space` (e.g. `A3`), `occupied` (bool) | updates the magnetic-sensor state of the space in the parking backend: assignments, wrong-space alerts, availability shown to drivers |
| `parking.lot.count` | `lot_id`, `occupied_total` (0-500) | records the independent gate counter of the lot (the Guardian uses it to cross-check the sensors) |
| `emergency.call` | `call_id` (letters, digits, `-`), `kind` (`ambulance` \| `fire` \| `police`), `location` `{lat, lon}`, `severity` 1-3 | dispatches the matching vehicle to the nearest street; every signal on its route gives it priority; sends actuator commands (section 4) |

Answer `200` when everything was accepted, `207` when some readings of a batch were refused (one result per reading):

```json
{ "results": [ { "accepted": true, "applied": "space_sensor" } ] }
```
```json
{ "results": [ { "index": 0, "accepted": false, "error": "device_untrusted",
                 "message": "sensor:P01:A3 is quarantined (Guardian): reading ignored.", "device_status": "quarantined" } ] }
```

### 3.2 Trust verdict - `POST /api/v1/verdicts`  (header `X-API-Key: <guardian key>`)

```json
{ "device_id": "sensor:P01:A3", "verdict": "untrusted",
  "reason": "sensor says occupied but the gate counter of P01 says 0 occupied", "ts": "2026-11-05T22:10:04Z", "ttl_s": 600 }
```

`verdict`: `trusted` | `suspect` | `untrusted` | `quarantined` | `released`. `ttl_s` optional (default 900 s, then the verdict is forgotten).

| Verdict | Effect in the Brain |
|---|---|
| `suspect` | recorded and shown; readings still used |
| `untrusted` | the device's readings are **ignored**; a parking sensor is **rolled back** to its last trusted state (the space stays assignable); the decision and its reason are written on the timeline and reported to the Guardian |
| `quarantined` | as `untrusted`; for an actuator (`barrier:*`, `signal:*`, `dispatch:*`) the Brain **withholds every command** to it and raises an administrator notice; the administrator can still override manually (logged as `manual_override`) |
| `trusted` / `released` | the device is used again |

Answer: `{"results":[{"accepted":true,"previous":"unknown","current":"untrusted","brain_actions":["rolled_back"]}]}`.

## 4. Messages the Brain SENDS

### 4.1 Actuator command -> Twin: `POST {twin.url}/api/actuators/{actuator_id}/commands`  (header `X-API-Key: <brain-to-twin key>`)

```json
{ "command_id": "cmd-00017", "ts": "2026-11-05T22:10:08Z", "action": "open",
  "params": { "lot_id": "P01" }, "reason": "ticket / exit authorised", "issued_by": "city-brain" }
```

Expected answer: any `2xx` (the mock returns `202 {"accepted": true, "command_id": "..."}`). A failure is logged and never blocks the city.

| `actuator_id` | `action` | `params` | When |
|---|---|---|---|
| `barrier:<lane>` (e.g. `barrier:P01-EXIT1`) | `open` | `lot_id` | a ticket was issued / a session was closed / the administrator overrides |
| `signal:<id>` (e.g. `signal:J1_3`) | `priority_green` | `vehicle_id` | an emergency vehicle approaches the junction |
| `dispatch:<kind>` (`dispatch:ambulance`...) | `dispatch` | `call_id`, `vehicle_id`, `lat`, `lon` | an `emergency.call` was accepted |

The Brain never sends a command to a device the Guardian rated `untrusted` or `quarantined` (except an explicit administrator override).

### 4.2 Decision -> Guardian: `POST {guardian.url}/api/decisions`  (header `X-API-Key: <brain-to-guardian key>`)

```json
{ "decision_id": "dec-00031", "ts": "2026-11-05T22:10:08Z", "action": "ignore_sensor",
  "target": "sensor:P01:A3", "reason": "untrusted: sensor says occupied but ...", "based_on": ["sensor:P01:A3"] }
```
`action` is one of `open`, `priority_green`, `dispatch`, `ignore_sensor`, `withhold_command`.

### 4.3 Report -> Guardian: `POST {guardian.url}/api/reports`

```json
{ "report_id": "rep-00004", "ts": "2026-11-05T22:10:09Z", "device_id": "lane:P07-ENT1",
  "issue": "parking_alert:DUPLICATE_ENTRY", "detail": "Vehicle d13 (RFID_D06) refused ..." }
```
`issue` values: `flood` (the Brain's rate limiter fired), `call_completed`, `parking_alert:<TYPE>` (security events of the parking system: `DUPLICATE_ENTRY`, `UNKNOWN_TAG_AT_EXIT`, `WRONG_CARD_RETURNED`, `SENSOR_FAULT`, ...).

## 5. Errors (clear and always in the same shape)

```json
{ "error": "invalid_data", "message": "'data' of a parking.space.occupancy reading is invalid.",
  "details": [ { "field": "data.lot_id", "message": "String should match pattern '^P\\d{2}$'" },
               { "field": "data.occupied", "message": "Input should be a valid boolean, unable to interpret input" } ],
  "hint": "see INTERFACE.md" }
```

| HTTP | `error` | Meaning |
|---|---|---|
| 400 | `invalid_json` | body is not JSON |
| 401 | `missing_api_key` / `invalid_api_key` | see section 2 |
| 413 | `body_too_large` / `batch_too_large` | limits in `config/brain.yaml` |
| 422 | `invalid_reading`, `invalid_data`, `invalid_verdict`, `unknown_space`, `unreachable_location`, `stale_timestamp`, `empty_batch` | the message says which field is wrong |
| 429 | `rate_limited` | more than 30 readings/s from one device: extra readings dropped, the Guardian is told (`flood`) |

An unexpected message never crashes the Brain: it is refused, counted, and shown on the timeline.

## 6. Other routes

* `GET /api/v1/health` -> `{"status":"ok","layer":"city-brain","interface_version":"1.0",...}` (no key).
* `GET /api/v1/timeline?after=<id>&layer=twin|guardian|brain|actuator` -> the cross-layer timeline (what the dashboard's *Timeline* tab shows; also written to `logs/brain.log` and, during a test scenario, to `logs/<scenario>-*.log`).
* `GET /api/v1/state` -> counters, queue state, verdicts in force, partner links.

## 7. Running the Brain alone (rule R1)

`mocks/mock_twin.py` (port 9001) and `mocks/mock_guardian.py` (port 9002) implement the other two layers: the mock Twin sends readings and receives commands, the mock Guardian cross-checks sensors against the independent counter and sends verdicts. They use exactly the messages above, so the real layers can replace them without changing the Brain (only `config/brain.yaml`). Start everything: `docker compose up` (repo root) or `run_city.bat` + `python -m mocks.mock_twin` + `python -m mocks.mock_guardian`.

## 8. What the Brain exposes to humans (not part of the partner contract)

Dashboard `http://<brain>:8000/` (map, driver dash screens, administration, training, timeline), parking administrator API `/api/admin/...` (`X-Admin-Key`), ESP32 gateway API `/api/gateways/...` (`X-Device-Key`), see `README_smart_parking.md`.

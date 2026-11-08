# Interface Card — City Brain (garbage-bin and air-quality axes)

DRAFT. Message formats are proposals to confirm with the Twin and Guardian teams.
**Status** says what is already built and tested with the mocks, and what is still open.

**Transport (built):** HTTP + JSON, UTF-8. Every request carries `Authorization: Bearer <token>`.
Addresses, paths and token names come from `config/brain.json` (see the last sections).

| Who calls | Endpoint | Body | Answer |
|---|---|---|---|
| Twin → Brain | `POST <brain>/v1/readings` | one message (§1 or §2) or a JSON array of them | `202 {"accepted": n}` · `401` wrong token · `400` not JSON · `413` too large |
| Brain → Twin | `GET <twin>/v1/map` | — | the map (§0) |
| Brain → Guardian | `POST <guardian>/v1/trust-verdict` | verdict request (§3) | verdict (§3) |
| Brain → Twin | `POST <twin>/v1/dispatch` | `dispatch_truck` (§4) | any `2xx` = accepted; anything else = refused, retried next cycle |
| anyone | `GET <brain>/v1/health` | — | `200 {"status": "ok"}` (no token) |
| timeline screen | `GET <brain>/timeline`, `/v1/events?after=N`, `/v1/state` | — | read-only, no token |
| orchestrator → Brain | `GET <brain>/v1/agent/card`, `POST <brain>/v1/agent/call` | `{"tool", "arguments"}` | tool result; see `AGENT.md` (own token: `ORCHESTRATOR_TOKEN`) |

The paths on the partner side are defaults: if a partner uses other paths, change
`twin.dispatch_path`, `twin.map_path` or `guardian.verdict_path` in the config.

## What the City Brain receives and sends

| # | Message | Direction | Status |
|---|---|---|---|
| 0 | city map | Twin → Brain | built: fetched over HTTP at startup (retried), or read from a file; tested with the mock Twin over HTTP |
| 1 | `get_sensor_reading` | Twin → Brain | built: received over HTTP, tested with the mock Twin over HTTP |
| 2 | `get_truck_state` | Twin → Brain | built: received over HTTP, tested with the mock Twin over HTTP |
| 3 | `get_trust_verdict` | Brain → Guardian → Brain | built: HTTP client, tested with the mock Guardian over HTTP |
| 4 | `dispatch_truck` | Brain → Twin | built: HTTP client, tested with the mock Twin over HTTP |

All timestamps are UTC, ISO 8601.

### 0. City map — once at startup (PROPOSAL)
```json
{
  "nodes": [
    { "id": "N1", "lat": 36.8000, "lon": 10.1800 },
    { "id": "N2", "lat": 36.8000, "lon": 10.1810 }
  ],
  "roads": [
    { "from": "N1", "to": "N2", "speed_kmh": 40, "length_m": 90, "one_way": false }
  ]
}
```
`length_m` and `one_way` are optional (without `length_m`, the straight-line distance is used).
A road shorter than the straight line between its ends is corrected and recorded in `graph.warnings`.
Bins and trucks are attached to the nearest node. The depot is one node of the map.

### 1. Bin reading — Twin → Brain, every X minutes for every bin
```json
{
  "tool": "get_sensor_reading",
  "sensor_id": "bin-042-zoneC",
  "type": "bin_weight",
  "weight_kg": 14.2,
  "capacity_kg": 50.0,
  "location": { "lat": 36.8065, "lon": 10.1815 },
  "zone": "zoneC",
  "zone_type": "crowded",
  "timestamp": "2026-11-01T08:12:00Z"
}
```
- The Twin sends facts only. The Brain computes the fill percentage, the fill rate and the color.
- `zone_type` is `crowded` or `open` and sets the truck pass interval (4 h or 8 h). If it is missing or unknown, the shorter interval is used. TO AGREE: the Twin sends it, or the Brain has a zone-to-type table in its config.
- A sensor silent for 3 × X is treated as suspect until it sends again.
- A drop of 30 points or more between two readings is read as "bin emptied".

### 2. Truck state — Twin → Brain, every X minutes for every truck
```json
{
  "tool": "get_truck_state",
  "truck_id": "t1",
  "location": { "lat": 36.8102, "lon": 10.1790 },
  "status": "en_route",
  "current_load_kg": 64.0,
  "timestamp": "2026-11-01T08:12:00Z"
}
```
`status` is `idle`, `en_route` or `collecting`. `current_load_kg` is optional but recommended: when present the Brain
uses it. Without it, the Brain estimates the load from the bins it saw emptied, and the estimate drifts.
If a truck stops sending its state, the Brain keeps using the last state it received.

### 3. Trust verdict — Brain asks the Guardian, for each reading
Request (the Guardian already receives the same readings from the Twin):
```json
{ "tool": "get_trust_verdict", "sensor_id": "bin-042-zoneC", "timestamp": "2026-11-01T08:12:00Z" }
```
Response:
```json
{
  "tool": "get_trust_verdict",
  "sensor_id": "bin-042-zoneC",
  "status": "trusted",
  "confidence": 0.95,
  "reason": null,
  "checked_at": "2026-11-01T08:12:01Z"
}
```
`status` is `trusted`, `suspicious` or `not_trusted`. `reason` is filled when the status is not `trusted`.
**The Brain acts only on `trusted`.** `suspicious`, `not_trusted`, no answer, an error, or an answer
about another sensor all mean the reading is ignored.

### 4. Dispatch — Brain → Twin, when a truck gets a new stop
```json
{
  "tool": "dispatch_truck",
  "truck_id": "t1",
  "next_stop": { "kind": "bin", "sensor_id": "bin-042-zoneC", "lat": 36.8065, "lon": 10.1815 },
  "route": [
    { "lat": 36.8102, "lon": 10.1790 },
    { "lat": 36.8080, "lon": 10.1801 },
    { "lat": 36.8065, "lon": 10.1815 }
  ],
  "timestamp": "2026-11-01T08:12:00Z"
}
```
- Only the next stop is committed. The rest of the plan is recomputed at every cycle.
- `kind` is `bin` or `depot`. A `depot` dispatch (`sensor_id: "depot"`) sends a full truck to unload.
- A new dispatch can replace the previous one: a red bin takes over a truck that was heading to an orange bin.
- TO AGREE with the Twin team: does the truck follow `route`, or move to `next_stop` its own way?



### 5. Air reading — Twin → Brain, every X minutes for every air monitor (air-quality axis)
```json
{
  "tool": "get_sensor_reading",
  "sensor_id": "AIR-03",
  "type": "air_quality",
  "pm25": 18.4,
  "o3": 12.0,
  "no2": 31.5,
  "zone": "B",
  "location": { "lat": 36.8100, "lon": 10.1900 },
  "timestamp": "2026-11-02T08:00:00Z"
}
```
- Units: `pm25` in ug/m3, `o3` and `no2` in **ppb**. `location` is optional. All three pollutants are required.
- Sent on the same endpoint as the bin readings (`POST /v1/readings`); `type` tells the axes apart.
- The Brain computes the team's risk score per sensor (formula in `TESTBOOK.md`, code in `src/air.py`),
  takes the worst trusted sensor of each zone, and keeps a level per zone: normal, warning, high, critical,
  or **unknown** when no trusted recent sensor is left (levels and actions are then kept).
- The Guardian is asked about each air reading exactly as for bins (§3).

### 6. Car parks and barriers — air-quality actions (Brain ↔ Twin)
At high and critical, the Brain closes the car parks of the zone, so fewer cars drive in; it reopens them
(`auto`) when the zone is back to warning or lower.
```
GET  <twin>/v1/parking-lots       ->  { "lots": [ { "id": "LOT-02", "zone": "B", "mode": "auto" } ] }
POST <twin>/v1/parking-barrier    <-  { "tool": "set_parking_barrier", "id": "LOT-02", "mode": "closed" }
```
`mode` is `auto`, `open` or `closed`. A refused command is logged (`twin_unavailable`) and retried next cycle.
Alerts (to schools and clinics at warning, to the public at high) are Brain decisions shown on the timeline
and in the log (`air_action`); **TO AGREE**: a Twin tool to display them in the city.

## Our Twin: City Ground Zero over MCP — BUILT (crew mode)

The triplet's Twin (City Ground Zero, Godot + SUMO) exposes the city through an **MCP server over
stdio** (`tools/mcp/city_mcp_server.py`, JSON-RPC 2.0). The Brain is an MCP client of it
(`src/mcp_client.py`, `src/mcp_twin.py`); the decision logic is unchanged.

| Step | MCP call | What the Brain does with it |
|---|---|---|
| start | `initialize`, `notifications/initialized`, `tools/list` | handshake; restarted automatically if the server dies |
| every cycle | `waste_bin_report(once=true, limit=500)` | each row becomes a §1 `get_sensor_reading` |
| new bins | `read_all(family="waste", once=true)` | zone letter (A–P) of each bin |
| decision | `empty_waste_bin(id)` | the order for a trusted red or orange bin (logged as `dispatch`, `truck_id: "crew"`) |
| every cycle (air) | `read_all(family="air", once=true)` | each AIR- monitor becomes a §5 air reading |
| air actions | `list_parking_lots()`, `set_parking_barrier(id, mode)` | car parks of a zone closed at high/critical, back to `auto` after |

Conversion of one `waste_bin_report` row:
```json
{ "id": "WGT-T002", "weight_kg": 24.0, "fill_percent": 96.0, "position": {"x": 100.0, "y": 0.17, "z": 50.0} }
```
becomes
```json
{ "tool": "get_sensor_reading", "sensor_id": "WGT-T002", "type": "bin_weight", "weight_kg": 24.0,
  "capacity_kg": 25.0, "zone": "B", "location": { "lat": 36.7995508, "lon": 10.1811219 },
  "timestamp": "<the MCP answer's timestamp>" }
```
- `capacity_kg = weight_kg / fill_percent × 100` when the fill is 5 % or more (then remembered per bin);
  otherwise a default per kind from the config (`WGT-T`: 25 kg, `WGT-C`: 120 kg — **TO CONFIRM**).
- `position` x/z in metres becomes lat/lon around `origin_lat/origin_lon` (only distances matter).
  **TO CONFIRM** that `position` is `{x, y, z}` in metres. An unknown format is rejected and logged.
- `zone_type` comes from `twin.mcp.zone_types` in the config (zone letter → `crowded`/`open`).
- Crew mode: at most `crew_max_orders_per_cycle` orders per cycle, red before orange; a bin is not
  ordered again until it is seen emptied or `crew_reorder_after_s` has passed. A failed order is retried.
- The Twin is read every `cycle_s`; if it stops answering, `twin_unreachable` is logged once and
  `twin_recovered` when it answers again.

Air metrics are matched by name loosely (`pm2_5`, `PM2.5`, `pm25`; `o3`, `ozone`; `no2`), and O3 / NO2
given in ug/m3 are converted to ppb (x 24.45 / 48.00 and x 24.45 / 46.01, at 25 C). **TO CONFIRM**: the
metric names and units of the AIR- monitors, and the shape of `list_parking_lots` (assumed
`{"lots": [{"id", "zone", ...}]}`).

**Still TO AGREE with the Twin team:** a tool that sends a refuse truck to one bin (then the Brain's
truck planner can be used instead of crew mode); the exact `position` format and bin capacities;
network access (the MCP server is stdio, so the Brain runs on the Twin's machine, or the dashboard's
HTTP + SSE transport is opened to the network); a token on that access.

Configuration: `config/brain.ground-zero.json` (`twin.transport: "mcp_stdio"`, `twin.mcp.command`,
`twin.mcp.cwd` = the Twin repository folder). Rehearsal without Godot: `python -m demo.live_mcp`
(the Twin's real MCP server, vendored in `mocks/vendor/`, on a fake device API).

## How the City Brain handles bad input
| Input | Behavior |
|---|---|
| Not a JSON object, missing field, negative weight, zero capacity, coordinates out of range | Rejected and logged (`rejected_message`) with the faulty fields named; nothing else is affected |
| Verdict for another sensor | Ignored and logged (`invalid_verdict`) |
| Guardian unreachable | Readings ignored, outage logged once, recovery logged once |
| Twin refuses a dispatch | The stop is cancelled, logged (`twin_unavailable`), retried at the next cycle |
| Wrong or missing token on `POST /v1/readings` | `401`, logged (`auth_refused`), nothing is used |
| Body that is not JSON, or larger than `max_body_bytes` | `400` or `413`, nothing is used |
| Guardian answer that is not a valid verdict | Treated as no answer: reading ignored |
| Guardian slow or down | Each call times out after `guardian.timeout_s`; after a failure the Brain waits `guardian.retry_after_s` before calling again, so one slow partner does not stall the whole cycle |

## Pointing the Brain at a different partner — BUILT
Everything that depends on the partner is in `config/brain.json`; the code has no address in it.
```json
{
  "listen":   { "host": "0.0.0.0", "port": 8100 },
  "twin":     { "url": "http://192.168.1.20:8200", "dispatch_path": "/v1/dispatch", "map_path": "/v1/map", "timeout_s": 2.0 },
  "guardian": { "url": "http://192.168.1.30:8300", "verdict_path": "/v1/trust-verdict", "timeout_s": 2.0, "retry_after_s": 5.0 },
  "auth":     { "brain_token_env": "BRAIN_TOKEN", "twin_token_env": "TWIN_TOKEN" },
  "map":      { "source": "twin" },
  "city":     { "depot_node": "N1", "truck_capacity_kg": 2000, "horizon_stops": 3, "reading_interval_s": 300 },
  "cycle_s":  1.0,
  "clock":    "twin",
  "log_path": "../../logs/JOINT-RUN.jsonl"
}
```
Run with another file: `python -m src.service --config config/brain.partners.example.json`
(or set `BRAIN_CONFIG`). A wrong config stops the Brain at startup with the faulty field named.

- `map.source` is `twin` (fetched from the Twin) or `file` (with `map.path`).
- `clock: "twin"` uses the Twin's timestamps as city time, so a Twin that runs faster than real time
  works; city time never goes backwards. `clock: "wall"` uses the Brain's own clock.
- The Brain runs one decision cycle every `cycle_s` seconds on the messages received since the last one.

## Authentication — BUILT (proposal to confirm)
- Each layer has its own token (at least 16 characters), agreed in the triplet and **never written in
  the repository**: the config only names the environment variables that hold them.
- The Brain sends `Authorization: Bearer $BRAIN_TOKEN` to the Twin and the Guardian; they should refuse
  any other token (the mocks do).
- The Brain accepts readings only with `Authorization: Bearer $TWIN_TOKEN` (constant-time comparison).
- Windows CMD: `set BRAIN_TOKEN=...` and `set TWIN_TOKEN=...` before starting the Brain.
- Limits: plain HTTP on the local router, so tokens and data are not encrypted; the timeline
  endpoints are read-only and have no token. Using `https://` URLs works with the same clients
  if a partner offers TLS.

# Interface Card — City Brain · Energy domain

**Layer:** City Brain (energy) · **Protocol:** HTTP/JSON (REST) · **Format:** JSON, UTF-8, ISO-8601 timestamps **with timezone** · **Version:** 0.1

The Brain **reads** power telemetry and switch states from the Twin, **asks** the Guardian for trust verdicts, and **sends** switch commands to the Twin. All partner-specific details sit behind adapters (`src/energy_brain/adapters/`). The decision logic only sees the canonical schemas in `src/energy_brain/schemas.py`.

## 1. What the Brain receives

| From | Call | Message | Required |
|---|---|---|---|
| Twin | `GET /energy/city` | `CityDescriptor` | zone IDs, switch IDs. Everything else is optional and lowers autonomy if missing (see §4) |
| Twin | `GET /energy/readings` | `power_reading[]` | yes |
| Twin | `GET /energy/switches` | `switch_status[]` | yes |
| Guardian | `GET /verdicts?subject=<id>&subject=<id>` | `trust_verdict[]` | yes for autonomous action |

```json
{"type":"power_reading","sensor_id":"PL-Z1","zone_id":"Z1","ts":"2026-11-04T12:00:01Z","value":321.0,"unit":"kW","seq":42}
```
```json
{"type":"switch_status","switch_id":"S12","state":"open","available":true,"ts":"2026-11-04T12:00:01Z"}
```
```json
{"type":"trust_verdict","verdict_id":"gv-17","subject_id":"PL-Z1","verdict":"trusted","ts":"2026-11-04T12:00:01Z","quarantined":false,"reason":"consistent with neighbours"}
```
```json
{"city_id":"mock-city-a",
 "zones":[{"zone_id":"Z1","capacity_kw":500,"tags":["commercial"]},{"zone_id":"Z3","capacity_kw":400,"tags":["hospital"]}],
 "switches":[{"switch_id":"S12","kind":"tie","from_zone":"Z1","to_zone":"Z2","transfer_fraction":0.35},
             {"switch_id":"F3","kind":"feeder","from_zone":"Z3"}]}
```
**Optional since v0.7: aggregate meters.** If the Twin has meters that measure the sum of several zones (substation or
feeder-head meters), it can declare them in the city descriptor and report them like any reading, with
`zone_id` = the meter's own `sensor_id`:
```json
{"city_id":"mock-city-a", "zones":[...], "switches":[...],
 "aggregate_meters":[{"sensor_id":"SUB-N","zones":["Z1","Z2"]},{"sensor_id":"SUB-S","zones":["Z3","Z4"]}]}
```
```json
{"type":"power_reading","sensor_id":"SUB-N","zone_id":"SUB-N","ts":"2026-11-04T12:00:01Z","value":641.0,"unit":"kW","seq":43}
```
The Brain then checks power balance every cycle (meter ≈ sum of its zones). A persistent mismatch (level rule or
CUSUM) is sent to the Guardian as a `consistency` event, blocks autonomous action that relies on the contradicted
sensors, and is shown to the operator with what each zone would carry if its own sensor were the wrong one. Without
aggregate meters nothing changes.

Units accepted: `W`, `kW`, `MW` (normalized to kW internally). Switch `state`: `open | closed`. Verdict: `trusted | untrusted`. Zone tags: `hospital`, `flood_pump`, `traffic_signals` (priority loads, protected first), `residential`, `commercial`, `industrial`.

## 2. What the Brain sends

| To | Call | Message |
|---|---|---|
| Twin | `POST /energy/switches/{switch_id}/command` | `switch_command` → expects `command_ack` |
| Guardian | `POST /events` | `brain_event`: `actuator_anomaly` (switch did not act, or reported moving while no power moved), `consistency` (power balance broken: physics evidence against a sensor) |
| Timeline | `POST {timeline_url}` (configurable, optional) | every `brain_event` |

**Inbound (from other Brain domains), port 8103:** `POST /priority-hints` with a `priority_hint`. The zone is treated as priority until `expires_at`: it is never de-energized and receives load only below 85% of capacity.
```json
{"type":"priority_hint","zone_id":"Z4","source":"flood","reason":"pump P-4 running","expires_at":"2026-11-04T12:30:00Z"}
```

```json
{"type":"switch_command","command_id":"cmd-3f9a0c1d2e4b","switch_id":"S12","operation":"close",
 "issued_at":"2026-11-04T12:00:05Z","decision_id":"dec-8812ab00cc11",
 "reason":"Z1 forecast 108% of capacity in 40 s; closing S12 moves ~35% to Z2 (forecast 71%)",
 "verdict_refs":["gv-17","gv-18","gv-19"],"degraded":false}
```
```json
{"type":"command_ack","command_id":"cmd-3f9a0c1d2e4b","status":"accepted","ts":"2026-11-04T12:00:05Z","detail":null}
```
```json
{"type":"brain_event","event_id":"evt-a1b2c3d4e5f6","ts":"2026-11-04T12:00:05Z","layer":"brain","domain":"energy",
 "kind":"gate_blocked","correlation_id":"dec-8812ab00cc11",
 "summary":"close S12 blocked: PL-Z1 verdict untrusted","payload":{"rule":"trust","subject":"PL-Z1"}}
```
Ack `status`: `accepted | rejected | duplicate`. `command_id` is unique, and re-sending it must not execute twice. The Brain treats an `accepted` ack as "requested", not "done": success is confirmed only by a later `switch_status` and load change (verification).

## 2b. Same contract over MCP (`twin.adapter: mcp`)

The brief has the Twin expose its sensors and actuators as MCP tools. The Brain includes a minimal MCP client (JSON-RPC 2.0 over Streamable HTTP; JSON or SSE responses; `Mcp-Session-Id` handled; auth header sent on every request). It has been tested against MCP Python SDK **1.30 (FastMCP)** and **2.3 (MCPServer)** servers. Tools it calls (names are configurable):

| Operation | Default tool name | Arguments | Returns |
|---|---|---|---|
| get_city | `energy_get_city` | none | `CityDescriptor` |
| get_readings | `energy_get_readings` | none | `power_reading[]` |
| get_switches | `energy_get_switches` | none | `switch_status[]` |
| send_command | `energy_switch_command` | `{"command": switch_command}` | `command_ack` (any other non-error result is accepted as "received"; success is always decided by verification) |

```yaml
twin:
  adapter: mcp
  base_url: http://twin-host:8000/mcp
  api_key: ${TWIN_API_KEY}
  mcp_tools: {get_readings: read_power_meters, send_command: operate_grid_switch}   # if their tools are named differently
```
Wrong tool names give an explicit error listing the tools the server offers.

## 3. Pointing the Brain at a different partner

Nothing is hard-coded. Edit `config/config.yaml` or set environment variables:

```bash
TWIN_URL=http://10.0.0.5:9000  TWIN_API_KEY=...  GUARDIAN_URL=http://10.0.0.6:7000  GUARDIAN_API_KEY=...
```
- `twin.adapter` / `guardian.adapter`: `rest` (available). Another protocol = a new adapter class; no change to Brain logic.
- `city_file`: optional local knowledge file that fills in what a Twin does not advertise (capacities, tags, switch effects).
- Check a new partner with: `python -m energy_brain.probe --config config/config.yaml`

## 4. Degrading gracefully: what the Brain does with partial information

| Level | Brain knows | Brain may |
|---|---|---|
| L0 blind | readings only | forecast against each zone's own baseline, **alert only** |
| L1 bounded | zone capacities | forecast overload, alert, no switching |
| L2 mapped | effect of ≥1 switch, and capacity of the zones it touches | full loop, **only on those switches** |

Rule: **unknown capacity ⇒ no autonomous switching for that zone.**

## 5. Authentication, signing and input safety

- **Keys:** every request carries a pre-shared key in a configurable header (default `X-API-Key`), from env vars, never from the repo.
- **Optional HMAC signing** per partner (`signing_secret`). Requests we send carry `X-Timestamp` + `X-Signature = hex(HMAC_SHA256(secret, "<timestamp>.<raw body>"))`, and the partner's responses must carry the same, signed with the shared secret. A signature older than ±30 s is refused (replay). The mocks implement both sides.
- **Inbound API (`:8103`)** for other Brain domains:
  - needs `X-API-Key`, checked before the body is parsed
  - rate-limited per client (30/min)
  - hint expiry capped at 15 min
  - unknown zones and expired hints rejected, every refusal logged as a `security` event
- **Strict schemas** reject, with a field-level error:
  - unknown fields
  - naive or future-dated timestamps (more than 5 s ahead)
  - negative loads and unknown units

  One bad reading is dropped (`reading_rejected`); the rest of the batch is used.
- **Replays:** a reading whose `seq` does not increase is a duplicate, unless its timestamp is newer. In that case it's a Twin restart: logged as `sequence_reset` and accepted.
- **Verdicts:** one older than `trust.verdict_ttl_s` (10 s) counts as missing. A verdict that changes 4 or more times in 20 s is **flapping** and counts as untrusted until stable.
- **Errors are explicit:** `[twin] get_city: authentication refused (HTTP 401) -- check api_key`.

## 6. A partner with a different message format

If a Twin or Guardian sends the same information under other names or nesting, map it in config with no code change (`twin.field_map`, `guardian.field_map`; see `src/energy_brain/adapters/mapping.py`):

```yaml
twin:
  field_map:
    readings:
      path: data.meters
      fields: {sensor_id: meter, zone_id: loc.area, value: p, unit: "=kW", ts: time, seq: n}
      ts_format: epoch_s
    switches:
      path: data
      fields: {switch_id: id, state: pos, ts: time}
      values: {state: {ON: closed, OFF: open}}
      ts_format: epoch_s
```

## 7. Checking a partner before integration

`python -m energy_brain.conformance --config config/config.yaml [--test-switch S12]` produces a PASS / WARN / FAIL report with the fix for each failure. It checks:
- reachability and authentication
- schemas, and that every zone has a reading
- freshness and clock sync, and that sequences increase
- that switch status is complete
- capacities and switch effects (WARN, because they lower the knowledge level)
- the Guardian's coverage and freshness
- optionally, a command round-trip with duplicate protection

Send the report to the partner team.

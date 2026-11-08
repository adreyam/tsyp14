# Twin · Interface Card (R1)

> Written from the Twin's code (`scripts/sim_api.gd`, `tools/mcp/city_mcp_server.py`) so partners can connect to it.
> The Twin team owns this file and should review it.

## What the Twin sends and receives

| Direction | What | How |
|---|---|---|
| out | every sensor and actuator, with its zone (A–P), position (metres) and current readings | `read_all`, `read`, `read_many`, `summary` |
| out | incidents (fires, crashes, floods, earthquakes …), road closures, emergency units | `list_incidents`, `list_emergency_units`, `list_earthquakes` |
| out | car parks, zones, buildings, vehicles, citizens, the event log | `list_parking_lots`, `list_zones`, `list_buildings`, `list_vehicles`, `list_citizens`, `event_log` |
| in | a command to one actuator or city system, answered with what was applied | `command {id, command, value}` |

## Transport

Line-delimited JSON over TCP on `127.0.0.1:<port>`. One request per line, one answer per line.

```
request  {"id": <any>, "method": "<name>", "params": {...}}
answer   {"id": <same>, "ok": true,  "result": {...}}
error    {"id": <same>, "ok": false, "error": {"code": "<code>", "message": "<text>"}}
```

Error codes: `bad_request` (not a JSON object, or a command without `id`/`command`), `unknown_method`,
`unknown_device`, `unknown_metric`, `bad_value`, `unavailable` (the system is not loaded yet).

## Examples

Read every device:

```json
{"id": 1, "method": "read_all", "params": {}}
{"id": 1, "ok": true, "result": {"devices": [
  {"id": "AIR-07", "family": "air", "kind": "sensor", "name": "Air monitor 07", "type": "air_quality",
   "zone": "G", "location": {"x": 100.0, "y": 0.0, "z": -110.0},
   "readings": {"pm25": 41.2, "pm10": 63.0, "no2": 38.5, "o3": 22.1}}
], "unknown_ids": []}}
```

Command an actuator:

```json
{"id": 2, "method": "command", "params": {"id": "SIG-Junction_r3_c7", "command": "ew_green"}}
{"id": 2, "ok": true, "result": {"id": "SIG-Junction_r3_c7", "applied": "signal Junction_r3_c7: ew green"}}
```

Start an incident (scenarios):

```json
{"id": 3, "method": "command", "params": {"id": "INCIDENTS", "command": "trigger",
  "value": {"kind": "building_fire", "zone": "G", "x": 100, "z": -110}}}
```

Other city-wide commands used by the Test Book: `WEATHER set`, `CLOCK set_time / set_speed`,
`EARTHQUAKE trigger / clear`, `ZONES lockdown / reopen`, `AIR release`, `WASTE fill`, `INCIDENTS dispatch /
send_unit / recall_unit`, `SIGNALS phase`. `list_commands {id}` lists the commands one device accepts.
The full list, with the additions made for the Brain, is in [../INTEGRATION.md](../INTEGRATION.md).

## Device ids and names

The prefix gives the family: `TRAF` traffic, `CAM` camera, `AIR` air, `NOISE`, `WX` weather, `FLOOD`, `UTIL`,
`PWR` power meter, `WTR` water meter, `WGT` waste bin, `SIG` signal, `VLV` water valve, `SWT` power switch,
`LGT` street light, `IRR` irrigation, `RSV` reservoir, `GRID`, `ZONE` service zone (power/water per zone),
`SEIS` seismic, `LOT` car park. Zones are A (north-west) to P (south-east), 2 × 2 blocks of 95 m each.
Every layer uses these ids unchanged.

## Pointing a partner at the Twin (configuration, never code)

The port is chosen at start-up and published three ways, in this order: the `CITY_SIM_API_PORT` environment
variable, the file `/tmp/city_ground_zero_api_port`, else `8820`. The City Brain reads the same three
(`brain/config/brain.yaml`, `twin:` section). An MCP client can use `tools/mcp/city_mcp_server.py` instead of
the raw socket.

## Authentication

None: the socket only listens on 127.0.0.1, so only programs on the Twin's machine can reach it. A token is
planned if the API is ever exposed beyond the machine.

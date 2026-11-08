# Energy axis

```
brain/axes/energy/
├── agent/     the orchestrator's energy agent (EnergyAgent): live Twin data, decisions sent to the Twin
└── engine/    the energy team's Brain: own simulation, own dashboard
```

## agent/ (live Twin)

`EnergyAgent` follows the City Brain pattern detect → decide → act → explain (`brain/src/citybrain/agents/base.py`).
The orchestrator gives it every trusted Twin event it handles and sends its actions to the Twin's actuators.

- Reads: GRID-01 reserve, service zones ZONE-*, power meters PWR-*, utility gateways UTIL-*, switches SWT-*
- Acts on: LIGHTS dim at 25 % reserve, ZONE-* cut_power to shed load under 15 % (never a protected zone, vetoed where an emergency is active), restore_power afterwards
- Offers (A2A skills, asked by the emergency agent or a partner): `isolate_feeder` / `restore_feeder` (building fire; rejected in a protected hospital zone), `cut_zone_power` / `restore_zone_power` (critical flood), `street_lights_on` (wins over dimming) / `street_lights_auto`
- Thresholds: `agents.energy` in `brain/config/brain.yaml`
- Shown on: the control center's City map, the top bar and the **Live Twin** tab of the domain page

## engine/ (the axis team's Brain)

the energy team's Brain (FastAPI, grid simulation and forecasting, MCP agent). Moved here unchanged by `brain/reorganize.sh`. Its own README, INTERFACE and tests are inside.

- Run: started by `./start_city.sh` or `brain/axes/start_engines.sh` (`python -m sim.server --port 8400` in `engine/brain`)
- Shown on: the **Axis Brain (own simulation)** tab of the control center (port 8400)

# Pollution (air quality and waste) axis

```
brain/axes/pollution/
├── agent/     the orchestrator's pollution agent (PollutionAgent): live Twin data, decisions sent to the Twin
└── engine/    the waste and air-quality Brain: own simulation, own dashboard
```

## agent/ (live Twin)

`PollutionAgent` follows the City Brain pattern detect → decide → act → explain (`brain/src/citybrain/agents/base.py`).
The orchestrator gives it every trusted Twin event it handles and sends its actions to the Twin's actuators.

- Reads: air AIR-*, noise NOISE-*, bins WGT-*, garbage-truck rounds
- Acts on: WGT-* empty above 90 %, INCIDENTS trigger garbage_collection when 3 bins pass 75 %
- Asks (A2A): transportation `close_car_parks` when a zone's air risk score passes 100, `reopen_car_parks` under 70
- Offers (A2A skill): `air_status` {zone} — the zone's risk score and level, answered at once
- Thresholds: `agents.pollution` in `brain/config/brain.yaml`
- Shown on: the control center's City map, the top bar and the **Live Twin** tab of the domain page

## engine/ (the axis team's Brain)

the waste and air-quality Brain (bin fill forecasting and truck routing, air risk score), with its own mock Twin and mock Guardian. Moved here unchanged by `brain/reorganize.sh`. Its own README, INTERFACE and tests are inside.

- Run: started by `./start_city.sh` or `brain/axes/start_engines.sh` (`python -m demo.live` in `engine/brain`, port 8100)
- Shown on: the **Axis Brain (own simulation)** tab of the control center (port 8100)

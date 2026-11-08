# Transportation axis

```
brain/axes/transportation/
├── agent/     the orchestrator's transportation agent (TransportationAgent): live Twin data, decisions sent to the Twin
└── engine/    the transport team's Brain: own simulation, own dashboard
```

## agent/ (live Twin)

`TransportationAgent` follows the City Brain pattern detect → decide → act → explain (`brain/src/citybrain/agents/base.py`).
The orchestrator gives it every trusted Twin event it handles and sends its actions to the Twin's actuators.

- Reads: traffic detectors TRAF-*, car parks LOT-*, road incidents, the emergency fleet (where each unit is and where it goes)
- Acts on: SIG-* green to the congested road, all_red around a crash once the responders are on scene, reset_cycle when it clears
- Offers (A2A skills): `green_wave` (the junctions ahead of a responding unit turn green in its direction and go back to their cycle once it is past; see brain/INTERFACE.md §4), `stop_traffic` / `resume_traffic` (the signals of one zone), `detour_traffic` / `end_detour` (flood: police cars sent with INCIDENTS send_unit to both ends of the flooded street, cross street held green, then recalled), `close_car_parks` / `reopen_car_parks` (LOT-*, reopened only when no axis still needs them closed)
- Thresholds: `agents.transportation` in `brain/config/brain.yaml`
- Shown on: the control center's City map, the top bar and the **Live Twin** tab of the domain page

## engine/ (the axis team's Brain)

the transport team's Brain (SUMO, sumo-rl traffic signals, smart parking, taxi demand), with its own mock Twin and mock Guardian. Moved here unchanged by `brain/reorganize.sh`. Its own README, INTERFACE and tests are inside.

- Run: started by `./start_city.sh` or `brain/axes/start_engines.sh` (`python -m citybrain.server` in `engine/brain`, port 8000; mock Twin 9001, mock Guardian 9002)
- Shown on: the **Axis Brain (own simulation)** tab of the control center (port 8000)

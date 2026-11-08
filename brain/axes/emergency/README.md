# Emergency axis

```
brain/axes/emergency/
├── agent/     the orchestrator's emergency agent (EmergencyAgent): live Twin data, decisions sent to the Twin
└── engine/    the emergency team's Brain: own simulation, own dashboard
```

## agent/ (live Twin)

`EmergencyAgent` follows the City Brain pattern detect → decide → act → explain (`brain/src/citybrain/agents/base.py`).
The orchestrator gives it every trusted Twin event it handles and sends its actions to the Twin's actuators.

- Reads: seismic, flood, weather; Twin incidents (fires, crashes, water-main breaks)
- Acts on: INCIDENTS dispatch (extra fire / ambulance / police / tow units), ZONE-* cut/restore water after a confirmed earthquake, VLV-* close for a water-main break
- Building fire: one more fire engine, an ambulance and a police car (every kind of emergency vehicle). Flood: an ambulance on standby next to the water (INCIDENTS send_unit), recalled when it is over. Earthquake: the epicentre is fixed once confirmed, everything happens in that zone.
- Asks the other axes (A2A, `brain/src/citybrain/a2a.py`): transportation `green_wave` for every fire engine, ambulance and police car on its way to a scene (and an ambulance carrying a patient to hospital), `stop_traffic` / `resume_traffic` (zone only) for a confirmed earthquake, `close_car_parks` / `reopen_car_parks` and `detour_traffic` / `end_detour` for a flood; energy `isolate_feeder` / `restore_feeder` for a building fire, `cut_zone_power` / `restore_zone_power` for a critical flood, `street_lights_on` / `street_lights_auto` for a storm
- Offers (A2A skill): `dispatch_unit` {incident, kind} — one more unit to an open incident
- Thresholds: `agents.emergency` in `brain/config/brain.yaml`
- Shown on: the control center's City map, the top bar and the **Live Twin** tab of the domain page

## engine/ (the axis team's Brain)

the emergency team's Brain (LangGraph orchestrator with PLUM earthquake, flood, storm, tsunami and crime engines, SQLite audit, own dashboard). It works on its own data in Tunis lat/lon. Moved here unchanged by `brain/reorganize.sh`. Its own README, INTERFACE and tests are inside.

- Run: `cd brain/axes/emergency/engine && pip install -r requirements.txt && python src/city_brain_dashboard.py` (see its README.md)
- Shown on: the **Axis Brain (own simulation)** tab of the control center (port its own dashboard)

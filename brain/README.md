# City Brain

Detects incidents in the Twin city and acts through the Twin's actuators. One orchestrator routes every input to
four agents (emergency, transportation, pollution, energy). Each agent follows the same steps:
detect → decide → act → explain. Every decision is logged with its reason.

```
brain/
├── README.md               this file: how to run the Brain alone
├── INTERFACE.md            Interface Card: messages in and out, examples, configuration, authentication
├── run_brain.py            start the orchestrator (HTTP API on :8500)
├── requirements.txt        pydantic, pyyaml, pytest (already used by the axis engines)
├── reorganize.sh           one-time move from the old layout to this one
├── start_brain.bat / stop_brain.bat    Windows: orchestrator + engines + control center
├── config/brain.yaml       source: live | mock, partner addresses, policies, agent thresholds
├── src/citybrain/          the orchestrator core
│   ├── contracts.py        Event, Verdict, Action, A2A task (pydantic); the zone rule shared with the Twin
│   ├── twin.py             the Twin's TCP protocol client + the one normaliser (Twin rows -> Events)
│   ├── sources.py          TwinSource: LiveTwinSource (Godot) and MockTwinSource (stand-in)
│   ├── guardian.py         GuardianSource: mock, http or off
│   ├── orchestrator.py     classify -> trust gate -> agents -> situations -> arbitration -> act -> audit
│   ├── a2a.py              A2A: the agents ask each other (green waves, feeders, car parks ...), agent cards, task states
│   ├── agents/             base.py (detect -> decide -> act -> explain) and the registry of the four axis agents
│   ├── scenario.py         Test Book runner (R2): mocks/scenarios/index.json, three scenarios per axis
│   ├── spotlight.py        while a scenario runs: its zones, incidents and moving vehicle, on the map and in the Twin
│   ├── audit.py            JSON-lines audit log (logs/); scenario.py also writes a plain-text report per run
│   ├── brain.py            the loop, the status, the live -> mock fallback
│   └── server.py           HTTP API (standard library)
├── axes/                   one folder per axis (see axes/README.md)
│   ├── emergency/          agent/ (orchestrator, live Twin)   engine/ (emergency team's Brain)
│   ├── transportation/     agent/                              engine/ (transport team's Brain, port 8000)
│   ├── pollution/          agent/                              engine/ (waste and air Brain, port 8100)
│   ├── energy/             agent/                              engine/ (energy Brain, port 8400)
│   └── start_engines.sh    start only the engines
├── control_center/         the unified dashboard (port 8765): top bar, City map, Live Twin tabs, engines' tabs
├── mocks/                  Twin + actuator stand-in, fleet.py (units drive from their stations along the roads),
│                           Guardian stand-in, scenarios/index.json + EM/TR/PO/EN/SY *.json (3 per axis)
└── tests/test_brain.py     contracts, live adapter over TCP, orchestrator rules, A2A, green waves, fleet, HTTP API
```

## Run it alone (R1)

```bash
pip install -r brain/requirements.txt
python brain/run_brain.py                 # source from config (mock by default): http://127.0.0.1:8500/v1/status
python scenarios/run.py --all             # the 17 Test Book scenarios, one .jsonl log + one .txt report each in logs/
python -m pytest brain/tests brain/control_center/tests -q
```

With the Godot Twin (same machine): start `twin/run_control_room.sh`, set `source: live` in `brain/config/brain.yaml`
(or `CITY_BRAIN_SOURCE=live`), then `python brain/run_brain.py`. The Brain finds the Twin's port the same way the
Twin's own MCP server does (`$CITY_SIM_API_PORT`, else `/tmp/city_ground_zero_api_port`, else 8820).
Or run everything at once from the triplet root: `./start_city.sh` (live) or `./start_city.sh --mock`.

The control center (`brain/control_center`, `python server.py`, http://127.0.0.1:8765)
shows the top bar (3 situations per axis), the LIVE/MOCK badge and the **City map** page (the Twin city, every
event received and action sent, the inspector, the live timeline and the Test Book buttons).

## How the orchestrator decides

1. **Classify**: by the Twin family (`flood`, `air`, `traffic` ...) or the incident kind. One event can wake
   several agents (a building fire: emergency dispatches, transportation holds the junction). An unknown family is
   logged as unrouted.
2. **Trust gate**: the Guardian's verdict decides whether the event is used, used in degraded mode (reversible
   actions only) or ignored.
3. **Agents**: detect (with persistence and hysteresis, so a calm city produces no action), decide, act, explain.
4. **Arbitration**: quarantined actuators are never commanded. A power cut by another axis in a zone with an
   active emergency is vetoed, and the energy agent then picks the next zone. On a shared resource, axis priority
   wins (`emergency > energy > pollution > transportation`) and the resource stays locked for `resource_lock_s`.
   An actuator already in the requested state is not commanded again. The same command waits `cooldown_s`.
5. **Act and audit**: the command goes to the Twin. The Twin's answer comes back on the same trace.

**Importance rule for the top bar** (`GET /v1/top`): per axis, unresolved situations come first (active, then
contained, then blocked untrusted data), then higher severity (critical > alert > watch > info), then the most
recently updated. Resolved situations stay visible for 10 minutes.

## Known limitations

- The Twin's device API has no authentication and listens on 127.0.0.1, so the Brain must run on the Twin's machine.
- There is no partner Guardian yet. The Guardian stand-in runs inside the Brain (`guardian.mode: mock`), and
  `guardian.mode: http` is ready for a real one.
- `policy.protected_zones` (hospital zones) is set by hand. The Twin does not yet publish which zone holds a hospital.
- Every Test Book scenario runs on the live Twin. The Twin cannot force a sensor to a value, though, so the
  mock-only steps (e.g. a detector forced into a jam) are skipped there. The live checks cover what the real city
  produces (see TESTBOOK.md).

# City Brain: one control center for the four axes

This file describes the three axis engines (waste and air quality, energy, transport) and the control center that shows them together. It was the README of the former `TSYP14BRAIN` package. Each engine stays exactly as its team built it, with its own agent and its own dashboard. The control center reads them; it does not replace them.

Since the reorganisation, the engines live in `brain/axes/<axis>/engine/` and the control center in `brain/control_center/`. Old names used further down map like this:

| Old name in this file | Now |
|---|---|
| `TSYP14BRAIN/control_center/` | `brain/control_center/` |
| `Energy/` | `brain/axes/energy/engine/` (port 8400) |
| `Pollution/` | `brain/axes/pollution/engine/` (waste and air, port 8100; mock Twin 8200, mock Guardian 8300) |
| `Trasnportation/` | `brain/axes/transportation/engine/` (SUMO, port 8000; mock Twin 9001, mock Guardian 9002) |
| `start_all.sh` | `./start_city.sh` (everything) or `brain/axes/start_engines.sh` (engines only) |
| `start_all.bat` / `stop_all.bat` | `brain/start_brain.bat` / `brain/stop_brain.bat` |

## Start

On Linux, run `./start_city.sh` from the triplet root. On Windows, double-click `brain/start_brain.bat`; `brain/stop_brain.bat` closes every window it opened.

You need:

- **Python 3.10 or later** on the PATH.
- **SUMO**, for transport. Install it from eclipse.dev/sumo, or run `pip install eclipse-sumo`. If you use pip, `traci` and `sumolib` must be the same version as SUMO, for example `pip install eclipse-sumo==1.25.0 traci==1.25.0 sumolib==1.25.0`. A version mismatch makes the transport Brain crash with a TraCIException at start.
- The first start installs each Brain's requirements and needs internet. Later starts work offline. The control center page loads nothing from the internet.

The panels fill in as each Brain comes up. A Brain that is down shows as **Offline**, and its panel says how to start it. Nothing else breaks.

## City Brain orchestrator (added)

The control center also follows the Twin-driven City Brain orchestrator (`brain/run_brain.py`, port 8500,
`backends.brain` in `control_center/config.json`, token `BRAIN_TOKEN`):

- **Top bar** (every page): the 3 most important situations of each axis (emergency, transportation, pollution,
  energy). Click one to focus it on the map and the timeline.
- **LIVE / MOCK badge**: which source the Brain uses. It turns red when the live Twin is unreachable.
- **City map** page (`#/city`): the Twin city (16 zones, 95 m blocks) with every event received and action sent,
  arrows from event to action, a different mark for mock data, an inspector with the JSON, agent, reason,
  trace_id, source and time, the live timeline (reading → Guardian → decision → action → Twin answer), and one
  Run button per Test Book scenario (CB01 to CB18).

## What the control center shows

| Page | What it shows |
|---|---|
| **Overview** | One sentence on the state of the city ("Needs attention: zone B air quality is high; …"), with chips that lead to each problem. There is one panel per axis: bins as a fill matrix, air zones with their risk trend, the energy load against capacity with the 45 s forecast band, and a live transport map. Next to them, one feed of every decision from every axis. Below, the **Scenarios** panel (see next section). |
| **Waste** | Bins most urgent first, with the reason for each colour, trucks or crews, and waste decisions. |
| **Air quality** | Zone levels and actions, the risk score of every zone over time (thresholds 70, 100, 130), the latest reading of each sensor with its Guardian verdict, and air decisions. |
| **Energy** | Load against capacity for each zone, the load of one zone with its 45 s forecast band, grid switches with trust, quarantine and cooldown, and energy decisions. |
| **Transport** | Live map (vehicles, signals, emergency priority, car parks), mean wait, car-park occupancy, signal learning over training windows, and transport decisions. The **Test Book scenarios** (T1 to T8, one Run button each) with their last PASS or FAIL, the checks of the last run and the log file name. |
| **Agents** | The three agents and their 44 tools, grouped as read, what-if and act. A form is built from each tool's schema, and you can run any tool. Act tools need a second click to confirm. |
| **Decision log** | Every decision and alert, filtered by axis and severity, with search. |

Each axis page has three tabs:

- **Live**: the unified view.
- **Full console**: the team's own dashboard, unchanged.
- **Agent tools**: that axis's agent.

The theme button at the bottom of the side bar switches between dark and light.

### Scenarios (on the Overview)

One click plays an incident live. Each scenario undoes its own changes when it ends. **Reset everything** stops all scenarios at once and puts every axis back to normal.

| Axis | Scenarios |
|---|---|
| Waste | W1 Bin filling fast (real surge, a truck is sent) · W2 Spoofed full bin (fake 98%, ignored) · W3 Partners fail (Guardian down, then Twin refuses dispatches) |
| Air quality | A1 Industrial release in zone D (alerts, car parks closed, then lifted) · A2 Fake pollution spike (ignored) · A3 Real peak during an attack (one ignored, one acted on) |
| Energy | E2 Predicted overload (preventive switching) · E4 Protect the hospital · E3 Spoofed load spike (no switch moves) |
| Transport | T1 Ambulance to the hospital (green wave) · T2 Rush hour and jam · T3 Parking sensor attack (a lying sensor and a flooding sensor, both quarantined) |
| Whole city | C1 Industrial fire in zone D: smoke, an industrial load surge, fire truck and ambulance, bins filling. C2 Coordinated cyberattack: fake readings on a bin, an air sensor, a power meter and a parking sensor at once, all caught. |

The Brains do not talk to each other: in C1 and C2 the control center plays the incident on every axis at the same moment, and each Brain answers with its own rules and its own Guardian. The energy scenarios restart the energy simulation, so running one of them stops another one already running.

### Demo controls

Each axis page has a **Demo controls** drawer that drives the simulations live:

- **Waste**:
  - fake a full reading on b06;
  - a real fast fill on b04;
  - Guardian down or back;
  - Twin refuses dispatches.
- **Air quality**:
  - fake PM2.5 spike on AIR-05;
  - capped sensor AIR-07;
  - an industrial release in zone D.
- **Energy**:
  - any Test Book scenario (E1 to E18);
  - a +180 kW surge in Z4;
  - a spoofed Z1 sensor;
  - Guardian down;
  - pause and speed.
- **Transport**:
  - traffic-jam test;
  - a lying parking sensor and a sensor flooding the network (attacks the transport Guardian must catch);
  - duplicate RFID (starts only when drivers d6 and d13 are at home, then needs a click on d13 in the transport console);
  - wrong-space parking;
  - traffic level;
  - pause.

The gateway relays only these controls (a fixed whitelist). The page cannot send anything else to the Brains.

## How it works

```
 browser ──► control_center/server.py :8765 ──► waste+air Brain :8100   (HTTP poll /v1/state, /v1/events; agent /v1/agent/call)
                                          ├──► energy Brain :8400       (SSE /api/stream; agent MCP /agent/mcp)
                                          └──► transport Brain :8000    (HTTP poll /api/state, /api/v1/timeline; agent /agent/invoke)
```

The gateway does five things:

1. It watches each Brain in the background.
2. It turns their events into one feed with a common severity: info, watch, attention or critical.
3. It computes the situation for each axis.
4. It lists the three agents in one registry and relays tool calls. It speaks each agent's own protocol: HTTP with a Bearer token, MCP Streamable HTTP, or HTTP with X-Agent-Key.
5. It relays the whitelisted demo controls.

Every agent call made from the page is also written to the feed.

Ports and URLs are set in `control_center/config.json`. Tokens are read from environment variables and never written in files:

| Variable | Used for | Default in start_all |
|---|---|---|
| `ORCHESTRATOR_TOKEN` | waste and air agent (the Brain and the control center must share it) | `tsyp14-control-center-token` |
| `ENERGY_AGENT_KEY` | energy MCP agent, only if the energy team turned its key on | none |
| `AGENT_KEY` | transport agent | `dev-agent-key` |

To run the control center alone, against Brains that are already running:

```
cd control_center
python server.py                 # http://localhost:8765
python server.py --port 9090 --config my_config.json
```

To run the tests, use `cd control_center && python -m unittest discover -s tests -v`. The offline tests always run. The live tests run when the control center is up on 8765.

## Notes on the package

- The transport Test Book is read from `Trasnportation\scenarios\` (`catalog.json` and `run.py`, next to the `brain` folder), as the transport Brain's own timeline does. That folder was not in the Trasnportation.zip sent to me: copy yours there, and the 8 scenarios appear on the Transport page. Only one Test Book scenario runs at a time; the control center refuses a second one while the first is still preparing the city.

- The energy folder had a Windows `.venv` of about 100 MB. It is not included, and `start_all` installs the requirements instead. `__pycache__` folders are removed too.
- One line changed in a team's file: `Trasnportation/brain/run_city.bat` now skips opening its own browser tab when `NO_BROWSER` is set, which `start_all.bat` sets. Run on its own, it behaves as before.
- The energy Brain runs on 8400, not its default 8200, because 8200 and 8300 are the waste and air mock Twin and Guardian.

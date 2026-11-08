# City Brain — garbage-bin and air-quality axes

The City Brain layer of the triplet. It receives bin readings and truck states from the Twin, asks
the Guardian whether each reading can be trusted, works out which bins need emptying, and sends
trucks to them. It runs on its own with a mock Twin and a mock Guardian (requirement R1).

## Run this layer alone
```
pip install -r requirements.txt
python -m demo.garbage all           # all scenarios: PASS/FAIL checks and a timeline
python -m demo.garbage G3            # one scenario; its log goes to ../logs/G3.jsonl
pip install -r requirements-dev.txt
python -m demo.air all               # air-quality scenarios A1 to A5
python -m pytest -q                  # 128 tests
```

## Run it live over HTTP (mocks, tokens, timeline screen)
```
python -m demo.live                  # mock Twin + mock Guardian + City Brain, all over HTTP
```
- Timeline (R3): http://127.0.0.1:8100/timeline — the "Air graphs" tab shows the risk score per zone
  (with the 70 / 100 / 130 thresholds), PM2.5, O3 and NO2 over time, and the latest value of each sensor
- Mock Twin controls (fake reading, real incident, outages, bad message): http://127.0.0.1:8200/control
- Log of the run: `../logs/LIVE.jsonl`
- Tokens: random for each run unless `BRAIN_TOKEN` and `TWIN_TOKEN` are set.

## Call it from an orchestrator (agent interface)
The Brain is also an agent that an orchestrator can call at any time: 12 tools (read, what-if, act)
over HTTP (`POST /v1/agent/call`) or MCP (`python -m src.agent_mcp`). See `AGENT.md`.
```
python examples/orchestrator_http.py      # with the live demo running and ORCHESTRATOR_TOKEN set
python -m src.agent_mcp --offline         # MCP server with the what-if tools and scenarios
```

## Rehearse the City Ground Zero integration (the triplet's Twin, over MCP)
```
python -m demo.live_mcp              # Twin's real MCP server + fake Godot device API + City Brain
```
On the Twin's Linux machine, with the city running (`./run_control_room.sh`):
```
export BRAIN_TOKEN=<agreed token>
python3 -m src.service --config config/brain.ground-zero.json   # set twin.mcp.cwd to the Twin repo first
```

## Run it with Docker (from the triplet root)
```
docker compose run --rm -T --build scenarios   # all scenarios, logs in ../logs
docker compose up --build live              # live demo on http://localhost:8100/timeline
docker compose run --rm --build tests       # the automated tests
```

`-T` matters on Windows CMD: without it the output may not show. Checked on Docker Desktop (Windows).

## Run it with partners that speak HTTP
```
set BRAIN_TOKEN=<agreed token>       (Windows CMD; export on Linux/macOS)
set TWIN_TOKEN=<agreed token>
python -m src.service --config config/brain.partners.example.json
```
Edit the addresses in the config file first. Nothing in the code needs to change.
To run the mocks as separate processes instead: `python -m mocks.serve` then `python -m src.service`.

## How it works
```
Twin readings -> validate -> Guardian verdict -> bin colors -> plan -> dispatch
```
- `src/brain.py` — one `step()` per cycle; ignores anything the Guardian does not trust
- `src/bin_status.py` — kg to %, fill rate, projected fill, color (green, yellow, orange, red)
- `src/air.py` — air-quality axis: the team's risk score (PM2.5, O3, NO2, lags, seasonal normalisation), zone levels with hysteresis, unknown state, alerts and car park closures
- `src/graph.py`, `src/pathfinding.py` — city graph and A* (by travel time)
- `src/planner.py` — horizon replanning: red before orange, truck capacity, depot, only the first stop is committed
- `src/audit_log.py` — append-only JSON lines, each event tagged by layer
- `src/config.py` — partner addresses, ports, timeouts, token variable names (`config/*.json`)
- `src/http_clients.py` — HTTP clients for the real Twin and Guardian (Bearer token, timeouts, validation)
- `src/mcp_client.py`, `src/mcp_twin.py` — MCP client of City Ground Zero: reads `waste_bin_report`, orders `empty_waste_bin`
- `src/agent.py`, `src/agent_mcp.py` — the agent interface for orchestrators (tools, HTTP, MCP); `AGENT.md`
- `src/service.py` — the Brain as an HTTP service: receives readings, runs a cycle every `cycle_s`, serves the timeline
- `static/timeline.html` — the live timeline screen (R3); `static/control.html` — mock controls
- `mocks/` — mock Twin (a small city simulation) and mock Guardian (scripted verdicts, plus a jump rule); `mocks/serve.py` serves both over HTTP
- `demo/` — the standalone scenarios (G1 to G6), the simulated city and the timeline printer
- `INTERFACE.md` — the Interface Card

## Swapping the mocks for real partners
`CityBrain` needs a `GuardianClient` and a `TwinClient` (`src/clients.py`): any object with the same methods.
The HTTP clients in `src/http_clients.py` are used by the service; the mocks are used by the scenarios.
Another protocol (MQTT, WebSocket) would only need another client.

## Known limitations
- Not yet run with the real Twin and Guardian. The MCP client is tested against the Twin's real MCP server, but on a fake device API (no Godot here); bin capacities and the `position` format are assumptions to confirm.
- City Ground Zero has no tool to send a truck to one bin yet, so the Brain uses crew mode (`empty_waste_bin`); the truck planner is used only with the mock Twin.
- Plain HTTP: tokens and data are not encrypted on the local network. The timeline endpoints are read-only and open.
- The mock Guardian's jump rule is a stand-in; real detection is the Guardian team's work.
- The mock Twin makes a redirected truck restart from its last intersection; a real Twin may move it more smoothly.
- The truck load is estimated unless the Twin sends `current_load_kg`.
- Two axes are built: garbage bins and air quality. Energy, transport and emergencies are not.
- Air: the seasons are fixed by month (April to September = warm); the Twin's air metric names and units are assumptions to confirm; alerts are shown by the Brain, not yet inside the Twin's city.

# City Brain - Transportation agent

The Brain exposes its capabilities as **18 tools** that an orchestrator can call at any time. Nothing in the Brain changed for this:
the agent is a thin layer (`agent/`) over the Brain's existing API, so it works while the dashboard and the Twin / Guardian links run.

| Reach it by | How | Use when |
|---|---|---|
| **HTTP** (built into the Brain) | `GET /agent/manifest`, `POST /agent/invoke`, `GET /.well-known/agent.json` | any orchestrator, any language |
| **MCP** | `python -m agent.mcp_server` (stdio) or `--http --port 8765` | orchestrators that speak the Model Context Protocol |
| **Python** | `from agent.client import BrainAgent` | orchestrators written in Python |

## Start

1. Start the Brain as usual (`run_city.bat`). The agent endpoints are on the same port (8000).
2. HTTP key: header `X-Agent-Key` (default `dev-agent-key`, change with the environment variable `AGENT_KEY`).
3. For MCP: `pip install -r requirements-agent.txt`, then register the server in the orchestrator, for example
   `{"command": "python", "args": ["-m", "agent.mcp_server"], "cwd": "<path to brain>", "env": {"BRAIN_URL": "http://127.0.0.1:8000", "PARKING_ADMIN_KEY": "dev-admin-key"}}`

## Calling a tool (HTTP)

```
POST /agent/invoke        header X-Agent-Key: dev-agent-key
{"tool": "start_traffic_jam", "arguments": {"cars": 300}, "request_id": "wf-17-step-3", "caller": "my-orchestrator"}
```
Every answer has the same shape:
```
{"ok": true, "tool": "...", "summary": "one sentence for the model to read", "data": {...}, "error": null, "request_id": "wf-17-step-3"}
{"ok": false, "summary": "...", "data": null, "error": {"code": "conflict", "message": "a jam test is already running"}}
```
Error codes: `unknown_tool`, `invalid_arguments`, `write_disabled`, `brain_unreachable`, `starting`, `conflict`, `not_found`, `invalid_input`, `unauthorized`, `brain_error`.
Tools never raise: the orchestrator always gets an envelope, and `summary` is written to be handed straight to a language model.

## The tools

**Read-only** (never change the city)
| Tool | Purpose |
|---|---|
| `get_city_status` | clock, cars, standing cars, mean wait, traffic level, signal mode, jam state |
| `get_signal_status` | learning mode, episode, exploration, situations learned, emergency-priority stats, last benchmark |
| `get_benchmark_results` | fixed-time vs learned results |
| `get_traffic_jam_status` | progress / result of the jam test |
| `list_parking_lots` | lots with free spaces and open alerts |
| `recommend_parking` | ranked lots for a driver (origin, destination) |
| `get_timeline` | cross-layer event log (use `last_id` as `after` to poll) |
| `get_link_health` | Twin / Guardian links, device trust states |
| `list_scenarios` | Test Book scenarios and their last result |

**Change the city** (blocked when `AGENT_ALLOW_WRITE=0`; each call is written to the Timeline as `agent_call`)
| Tool | Purpose |
|---|---|
| `set_signal_mode` | `train` or `policy` |
| `save_training` | save the learned policy now |
| `run_signal_benchmark` | headless fixed-time vs learned, optional jam |
| `start_traffic_jam` | stress test with extra cars |
| `set_simulation_controls` | traffic level, speed, pause, clock, number of emergency vehicles |
| `dispatch_emergency` | send an ambulance / fire truck / police car to a point; signals give it priority |
| `reserve_parking`, `cancel_parking_reservation` | hold / release a place |
| `run_scenario` | run a Test Book scenario (needs the `scenarios/` folder next to `brain/`) |

## Safety

* Read-only mode: `AGENT_ALLOW_WRITE=0` lets an orchestrator observe but not act.
* Inputs are validated against the JSON schema before anything happens; ranges are clamped by the Brain.
* Every state-changing call is in the Timeline (`brain agent_call`) with the caller name and request id.
* `dispatch_emergency` goes through the same path as a Twin emergency call, so the Guardian's trust verdicts still apply to the commands sent to devices.
* The keys are development defaults. Set `AGENT_KEY` and `PARKING_ADMIN_KEY` before exposing the port beyond your computer.

## Example

`python -m agent.example_orchestrator` runs: status -> policy mode -> traffic jam -> wait -> report.
`python -m pytest tests/test_agent.py` checks the tool definitions without a running Brain.

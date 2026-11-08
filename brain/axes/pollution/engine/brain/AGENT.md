# City Brain as an agent — for orchestrators

The City Brain can be called at any time by an orchestrator: a workflow engine, a multi-agent
system, an LLM with tool calling, or any MCP host. It keeps running its own decision loop; the
agent interface lets other systems **ask it questions** and **give it work** on demand.

The decision core is unchanged: deterministic rules, gated by the Guardian. The agent interface
is a set of tools in front of it (`src/agent.py`), served two ways:

| Way | How | For |
|---|---|---|
| HTTP | `GET /v1/agent/card`, `POST /v1/agent/call` on the Brain service | any orchestrator, any language |
| MCP (stdio) | `python -m src.agent_mcp` | MCP hosts and LLM agent frameworks |

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `brain_status` | read | Overview: city time, decision mode, bins per colour, trucks, air zones per level, blocked sensors, partner health, last decisions |
| `get_bins` | read | Bins most urgent first, with fill, rate, projection and the reason for the colour (filters: `color`, `min_fill_pct`, `limit`) |
| `get_air_zones` | read | Level, risk score, reason and actions in force per zone (`zone` optional) |
| `get_trucks` | read | Status, load, committed next stop and planned stops |
| `explain` | read | Why: the latest decisions about one bin, air sensor, zone or truck (`id`), with their reasons, plus its current state |
| `get_events` | read | The audit log as a stream (`after`, `events`, `limit`) |
| `assess_air` | what-if | The team's risk formula on given values: Ox, Z, R_pm, R_ox, risk, level, actions. Nothing changes in the city |
| `assess_bin` | what-if | The colour rules on a hypothetical bin (`fill_pct` or `weight_kg` + `capacity_kg`, `rate_pct_per_h`, `zone_type`) |
| `plan_route` | what-if | A* fastest route between two map nodes or coordinates |
| `submit_readings` | act | Give the Brain readings (Twin format); validated and checked by the Guardian at the next cycle |
| `run_cycle` | act | Run one decision cycle now; returns the decisions it produced |
| `run_scenario` | act | Replay a Test Book scenario (G1-G6, A1-A5) in isolation, with its own mocks; returns its checks |

Each tool has a JSON schema (`inputSchema` in the card). Arguments are validated; unknown or
wrong arguments are refused with a stable error code: `unknown_tool`, `invalid_arguments`,
`not_found`, `no_live_brain`, `no_map`, `unreachable`, `axis_disabled`.

## Calling it over HTTP

```
GET  /v1/agent/card                      public: name, version, safety rules, tools with schemas
POST /v1/agent/call                      Authorization: Bearer <ORCHESTRATOR_TOKEN>
     {"tool": "explain", "arguments": {"id": "D"}}
  -> {"agent": "city-brain", "tool": "explain", "kind": "read", "at": "...", "result": {...}}
```

| Answer | Meaning |
|---|---|
| `200` | the result |
| `400` / `404` | invalid arguments / unknown tool or id: `{"error": {"code", "message"}}` |
| `401` | missing or wrong orchestrator token (logged as `auth_refused`) |
| `403` | an act tool while `agent.allow_act` is false |
| `503` | `ORCHESTRATOR_TOKEN` not set on the Brain side |

Optional header `X-Caller: <name>` names the caller in the audit log.

Example in Python (standard library): `examples/orchestrator_http.py`. It also shows how to hand
the card's tools to an LLM that does tool calling (`as_llm_tools`).

## Calling it over MCP

```
python -m src.agent_mcp --url http://127.0.0.1:8100     proxy to a running Brain (default)
python -m src.agent_mcp --embedded --config config/brain.json   starts the Brain itself
python -m src.agent_mcp --offline                       what-if tools and scenarios only
```

- Run it from the `brain/` folder. Proxy mode reads `ORCHESTRATOR_TOKEN` from the environment.
- In proxy mode, if the Brain is down, the what-if tools and `run_scenario` still answer, computed
  locally (`served_by: "local fallback"`); the others answer `brain_unreachable`.
- MCP host entry: `examples/mcp_host_config.json`. Results come as `structuredContent` and as
  JSON text; errors as `isError: true` with `{"error": {"code", "message"}}`.

## Configuration

```json
"agent": { "enabled": true, "token_env": "ORCHESTRATOR_TOKEN", "allow_act": true }
```

`allow_act: false` gives a read-only agent (read and what-if tools only). The live demo
(`python -m demo.live`) creates a random orchestrator token for each run and prints it.

## Safety — what no orchestrator can do

- **Bypass the Guardian.** Readings sent with `submit_readings` go through the same validation and
  Guardian check as the Twin's. A reading on a distrusted sensor is blocked (tested).
- **Force an action.** There is no tool to dispatch a truck, close a car park or lift an alert
  directly: actions come only out of the decision rules.
- **Hide a call.** Every call is logged as `agent_call` (tool, kind, caller, ok, error, duration),
  and act calls appear on the live timeline.
- **Change the rules.** Thresholds and formulas come from the config file and the code, not from calls.

## Typical orchestrator loop

1. `brain_status` on a schedule or when an event comes in.
2. For anything urgent (`need_collection`, zones at high or critical): `explain` it, and pass the
   reason on (to a dashboard, an operator, another agent).
3. Before a decision of its own (e.g. an event planner choosing a venue), `assess_air` or
   `assess_bin` on the expected values.
4. To check the Brain is healthy after a deployment: `run_scenario` for G1 and A1.

Tests: `tests/test_agent.py` (offline tools, HTTP endpoint on a live Brain with mocks, MCP server
in offline and proxy mode, local fallback).

# Energy Brain as an agent: integration guide for the City Brain orchestrator

The Energy Brain is an **autonomous agent** that the orchestrator can call any time over **MCP** (Model Context Protocol, JSON-RPC over Streamable HTTP). It keeps running its own closed loop: observe → predict → compare futures → safety & trust gate → act → verify → restore. The orchestrator **supervises** it: it asks questions, steers it, and decides how much autonomy it has.

```
                  ┌──────────── City Brain orchestrator (LLM or code) ────────────┐
                  │  ask: status, zone, forecast, options, what-if, explain, events │
                  │  steer: mode, veto, approve, priority zone                     │
                  └───────────────▲──────────────────────────┬──────────────────────┘
                     webhook (alerts)│                       │ MCP tools (X-API-Key)
                  ┌─────────────────┴──────────────────────────▼──────────────────────┐
                  │ Energy Brain agent: own loop every 1 s, Safety & Trust Gate     │
                  └──────▲────────────────────┬──────────────────────────▲──────────────┘
                 readings │ switch states        │ switch commands          │ verdicts
                     City Twin (REST/MCP) ◄──────┘                      Guardian
```

## Start it

```bash
# against the real Twin and Guardian (addresses and keys in config/env)
python -m energy_brain.agent --config config/config.yaml                    # MCP on http://<host>:8300/mcp
python -m energy_brain.agent --config config/config.yaml --transport stdio  # for an orchestrator that spawns tools

# or with the live simulation (dashboard + agent on the same Brain)
python -m sim.server --speed 4 --agent-key $ORCHESTRATOR_API_KEY            # agent at http://127.0.0.1:8200/agent/mcp
python examples/orchestrator_demo.py                                        # a scripted orchestrator supervising it
```

Every call must send the header `X-API-Key: <orchestrator.api_key>` (env `ORCHESTRATOR_API_KEY`). Wrong keys are refused (401) and logged as `security` events.

## Connect an orchestrator

Any MCP client can use it. Point it at the URL with the header. The usual client configuration shape:

```json
{
  "mcpServers": {
    "energy-brain": {
      "url": "http://<host>:8300/mcp",
      "headers": { "X-API-Key": "<orchestrator key>" }
    }
  }
}
```

For a code orchestrator in Python, `examples/orchestrator_demo.py` shows the full pattern with the dependency-free client in `energy_brain/adapters/mcp.py`. It has been tested against this server running on MCP Python SDK 1.30 and 2.3.

For an LLM orchestrator, the server sends its own **instructions** at connection time, and every tool has a description written for an LLM. Every answer carries a one-line `summary` the LLM can repeat to a human, plus structured fields for code.

## Tools

| Tool | Kind | What it returns / does |
|---|---|---|
| `energy_get_status` | ask | Whole grid: each zone's load, capacity, forecast, P(overload), sensor trust; each switch; mode; pending proposals; last alert; event cursor. **Call first.** |
| `energy_get_zone(zone_id)` | ask | One zone in detail, J-1 plan included |
| `energy_forecast(zone_id, horizon_s?)` | ask | Forecast at 15 / 30 / 45 s (+ your horizon) with upper band and J-1 plan |
| `energy_evaluate_options()` | ask | Every switching option against doing nothing, why each rejected one was rejected, the recommendation. Does not act |
| `energy_what_if(switch_id)` | ask | Dry run of one switch: predicted loads, probabilities, **would the gate pass**. Does not act |
| `energy_explain()` | ask | Plain-language account: last action, its verified result, the situation now, open issues |
| `energy_get_events(since, kinds?, limit?)` | ask | Audit events after a cursor; page with `next_cursor` |
| `energy_list_proposals()` | ask | Actions waiting for approval (advisory mode) |
| `energy_describe_agent()` | ask | Agent card: role, modes, inputs/outputs, guarantees, limits (also resource `energy://agent-card`) |
| `energy_set_mode(mode, reason)` | steer | `autonomous` (agent acts) or `advisory` (agent proposes, you approve) |
| `energy_veto_switch(switch_id, duration_s, reason)` | steer | Forbid one switch for up to 1 h (maintenance, crew on site...) |
| `energy_lift_veto(switch_id)` | steer | Remove a veto |
| `energy_approve_proposal(proposal_id)` | steer | Execute a proposal, **re-checked now** against the situation and the gate |
| `energy_set_priority_zone(zone_id, reason, ttl_s)` | steer | Protect a zone (flood pump running, hospital on backup...) for up to 15 min |

## Authority and safety

- **Autonomous (default):** the agent acts within seconds of a predicted overload. The orchestrator watches, explains, vetoes switches or switches mode.
- **Advisory:** nothing moves without `energy_approve_proposal`. Proposals expire after 30 s, so an approval never acts on a stale picture.
- **The orchestrator can restrict, never force.**
  - A veto can only stop actions.
  - An approval still goes through the Safety & Trust Gate: untrusted or flapping evidence, a quarantined switch, a priority zone at risk, cooldown, or an option that is no longer better than doing nothing all block it, and the answer says why.
  - There is **no tool that moves a switch directly**.
- **Everything is audited:** every steering call is written to the audit log as an `orchestrator` event, and shown in the dashboard timeline.

## Being told instead of polling

Set `orchestrator.notify_url` (env `ORCHESTRATOR_NOTIFY_URL`). The agent then POSTs `{"agent": "energy-brain", "event": {...}}` for these kinds:
- `risk_detected`, `decision`, `plan` (multi-step coordination), `gate_blocked`, `proposal`, `operator_alert`
- `consistency` (power balance broken: a sensor contradicted by an aggregate meter)
- `actuator_anomaly`, `degraded_mode`, `verification`, `restoration`, `security`

It sends them from a background thread, so a slow orchestrator never delays the control loop.

## Typical orchestrator flows

1. **Situation report for a human:** `energy_get_status` → `energy_explain`, and say the two `summary` lines.
2. **Cross-domain coordination:** the flood agent reports a pump running in Z4, so the orchestrator calls `energy_set_priority_zone("Z4", "flood pump P-4 running", 900)`.
3. **Maintenance:** `energy_veto_switch("S42", 3600, "crew on site")`.
4. **Cautious operation (night, incident, demo):**
   1. `energy_set_mode("advisory")`.
   2. On each `proposal` event, call `energy_what_if` and then `energy_approve_proposal`, or let it expire.

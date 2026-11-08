# City Brain: Energy domain

Trust-aware predictive control of grid switches. Every second the Brain:
1. forecasts each zone's load 45 s ahead, with an overload probability
2. compares every possible switching "future" against doing nothing
3. lets only safe, trusted actions through its gate
4. acts through the Twin, verifies the result on the sensors, and puts the grid back to normal when it is safe

**New in v0.7** (details below, measured in [bench/](bench/README.md)):
- **Adaptive, calibrated forecasting:** an online-weighted ensemble of three trend windows and the J-1 shape, whose band is re-calibrated from its own realized errors, so "97.7% upper band" means what it says
- **Multi-zone coordination:** short switching plans (make before break) when no single move works, instead of shedding
- **Physics-based consistency:** power balance against substation meters (level rule + CUSUM) catches stealthy sensor manipulation the Guardian cannot see, and confirms real sudden jumps at once (no 5-sample wait)
- **Phantom acknowledgements:** a switch that reports moving while no power moved is declared FAILED and modelled in its real position
- **Statistical benchmark, ablation study, property-based safety tests** (Hypothesis)

It works with **any city**: it measures how much it knows about the grid and limits its own autonomy to match (L0 alert only, L1 bounded, L2 mapped). It learns unknown switch effects by watching operator switching, using trusted evidence only.

## As an AI agent for the City Brain orchestrator

The Brain runs as an **MCP agent** the orchestrator can call any time:
- **14 tools to ask and steer:** status, forecast, options, what-if, explain, events, proposals, mode, veto, approve, priority zone, and more
- **An agent card** describing the agent
- **Webhook alerts**

It acts on its own by default (*autonomous*); the orchestrator can switch it to *advisory* and approve each action itself. Nothing bypasses the Safety & Trust Gate. See **[AGENT.md](AGENT.md)**.

```bash
python -m energy_brain.agent --config config/config.yaml        # MCP agent on :8300/mcp
python examples/orchestrator_demo.py                             # scripted orchestrator (with sim.server running)
```

## Run it

```bash
pip install -r requirements.txt
export PYTHONPATH=src:.
python -m sim.server --speed 4           # live simulation + demo console -> http://127.0.0.1:8200
python -m scenarios.run all              # whole energy Test Book (25 scenarios), simulated clock
python -m scenarios.run all --twin mcp   # same, Brain talks to the Twin over MCP
python -m pytest -q                      # 135 tests: unit, property-based (Hypothesis), chaos, signing, mapping, MCP, agent,
                                         #   Test Book x2 seeds, simulator
python -m bench.run --set main --seeds 20 && python -m bench.report   # statistical benchmark (see bench/README.md)
```

Against real partners:
```bash
python -m energy_brain.conformance --config config/config.yaml   # readiness report for the Twin & Guardian teams
python -m energy_brain.probe       --config config/config.yaml   # quick connectivity + knowledge level
python -m energy_brain.run         --config config/config.yaml   # the Brain (inbound API on :8103)
```

## Simulation (demo, and the R3 timeline screen)

`python -m sim.server` runs the real `EnergyBrain` against the mocks. The page has five parts:
- **Grid map:** zone load against capacity, the J-1 plan, the forecast, sensor trust and priority. Switches show their state and the kW flowing through them.
- **Main real-time chart** for the focused zone:
  - measured load, and the true load (known to the simulation only)
  - the **J-1 plan** made from yesterday's data
  - the **45 s forecast re-issued every 1 / 5 / 15 / 30 / 60 s** (you choose), so you can see how each forecast compared with reality
  - the **branching futures** the Brain compared at its last decision: do nothing, chosen, rejected
  - Brain and operator switching marked on the time axis
- **What happened, in order:** the episode in plain words: what the Brain saw, the futures with their overload probabilities, the gate verdict with each sensor's trust, the command, and the verification.
- **Grid switches:** what each switch does, its state over time (▲ Brain · ◆ operator · ↺ restored · ⚡ shed · ✕ failed · ⊘ blocked), and the last thing that happened to it.
- **Timeline** across Twin / Guardian / Brain / other domains, and an **event console** with 12 injections: surge, spoof, Guardian flag / clear / flap / quarantine / down / up, jammed switch, operator switching, Twin restart, flood-pump hint.

`python -m sim.record` re-records every scenario into `sim/recordings.json`; the same page then replays offline. Link to a moment with `#E3-34` (scenario E3 at t = 34 s).

## How a decision is made

| Stage | Module | What it does |
|---|---|---|
| Observe | `adapters/` | REST or MCP (config). Strict schemas, optional **field mapping** for a foreign partner's JSON, optional **HMAC signing**. One bad reading is dropped, not the batch |
| Understand | `state.py` | Rejects stale, future-dated and replayed readings, and readings from untrusted or flapping sensors. Detects a **Twin restart** (sequence reset). A jump over 20% of capacity is held as suspect until it persists **and** the Guardian still trusts the sensor. **v0.7:** if an aggregate meter moves the same way in the same cycle (power balance closes), the jump is real: accepted at once and the trend restarted at the new level (E17: 2 s of overload instead of 6) |
| Plan | `dayahead.py` | **J-1 plan** per zone (smoothed persistence of yesterday). Reports plan deviations, gives capacity-blind zones a "normal for this time" baseline, and estimates the load of a zone that is switched off |
| Learn | `learning.py` | Infers tie / feeder effects from load shifts when someone else operates an unknown switch. **Anti-poisoning:** only with Guardian-trusted sensors, kept only if they stay trusted for 10 s. Adopted after 2 consistent observations; the Brain never toggles a switch to learn |
| Predict | `forecast.py` | **Adaptive ensemble (v0.7):** trend fits over 10 / 20 / 40 samples + "level + J-1 change over the horizon", weighted online by inverse recent error; band **calibrated** from realized errors (conformal-style factor on the regression interval). Rebasing after a switch scales the trend with the load that remains. Risk = upper band crosses capacity within 45 s for 3 samples; reports P(overload) |
| Check physics | `consistency.py` | **v0.7.** If the Twin declares aggregate (substation) meters: meter ≈ sum of its zones. Level rule + CUSUM on the residual. A broken balance → `consistency` event to the Guardian, contradicted sensors blocked at the gate and excluded from learning, operator told what each zone would carry if its own sensor lies (possible **hidden overload**) |
| Simulate + decide | `decision.py` | Load-transfer model for do-nothing + every known toggle. Score = Σ priority weight × **expected overload** E[max(0, L − cap)] / cap + switching cost. Constraints: never de-energize a priority zone; never push a zone over capacity; a priority zone receives load only below 85%. **Load shedding only as a last resort**: no switching option helps, the zone is *measured* over capacity, and its expected peak is ≥ 110% |
| Coordinate | `planner.py` | **v0.7.** When no single move solves the risk: search plans of up to 3 tie toggles on a demand-reconstructed transfer model (mean and std per zone). Every intermediate configuration must respect the hard constraints, so break-before-make orders are rejected. Executes step 1 only, re-plans after each verified step; waits up to 15 s for a switch in cooldown rather than fall back to shedding. Disjoint risky zones are handled in parallel |
| Supervise | `agent.py` | MCP tools for the orchestrator; autonomous / advisory mode, vetoes, approvals re-checked through the gate, risk hysteresis so alerts do not flicker |
| Gate | `gate.py` | Safety (availability, cooldown, quarantine, one change per zone at a time, valid transition), then trust: any untrusted → block; all trusted and fresh → pass; Guardian unreachable → **degraded mode**, which allows only reversible relief of a persistent priority-zone overload, never shedding or restoration |
| Act + verify | `brain.py`, `actions.py` | Unique command ID, retries reuse it, cooldown. SUCCESS / MISMATCH / FAILED / UNVERIFIABLE from sensors; FAILED excludes the switch and reports to the Guardian. **v0.7:** a status that says "moved" while no power moved (< 20% of the predicted shift) is FAILED too, and the Brain models the switch in its real position |
| Restore | `brain.py` | Puts switches back once every affected zone is forecast ≤ 85% for 10 samples. A shed zone stays off ≥ 90 s; if re-energizing does not hold, the wait doubles (**recloser-style back-off**) |

## Security summary

- Every partner link uses a key from the environment. An **HMAC-SHA256 signature** (with replay window) can be required per partner.
- The inbound API (`:8103`, priority hints) needs a key, checked before the body is parsed. It is rate-limited per client, and hint expiry is capped at 15 min.
- Untrusted, expired or flapping verdicts never authorize ordinary action. Learning cannot be poisoned by data the Guardian distrusts.
- Every refusal is logged as a `security` or `gate_blocked` event.

## Layout

```
src/energy_brain/   schemas · config · adapters/ (rest, mcp, mapping, signing) · state · forecast · dayahead · learning
                    decision · planner · consistency · gate · actions · brain · agent (MCP agent) · run · probe
                    conformance · eventlog · knowledge
bench/              scenarios (randomized families) · oracle · run (policies, ablations) · report · debug
examples/           orchestrator_demo.py (an orchestrator supervising the agent over MCP)
mocks/              mock Twin: REST (twin.py) and MCP (twin_mcp.py), same simulation · mock Guardian (signing, flapping)
mocks/cities/       city_a (fully described, substation meters) · city_b (describes nothing: for "new city" tests)
                    city_c (meshed, for multi-zone coordination)
config/             config.yaml · knowledge/ (optional per-city knowledge files)
scenarios/          testbook.py (E1-E18) · runner.py · run.py (trigger)
sim/                session.py · server.py (live) · record.py · dashboard.html
ci/                 energy-brain.yml (GitHub Actions: tests + Test Book on every push)
tests/              135 tests (incl. test_properties.py: safety invariants on random states)
AGENT.md            how an orchestrator connects to and supervises the agent
INTERFACE.md        Interface Card (R1)   ·   TESTBOOK.md   energy scenarios with observed results
```

## Known limitations

- **Not a power-flow model.** Load moves along known switch effects; voltage, losses and line limits inside a zone are ignored. Verification flags MISMATCH when reality disagrees. A power-flow backend (e.g. pandapower) would need line data that our Twin does not provide.
- **Trend-based forecasting.** The adaptive ensemble halves the calm-day error and calibrates the band, but every expert is still a local trend (or the J-1 shape), so a ramp is extrapolated past its end. Right after a surge the calibrated band widens for a few minutes (the errors it just saw were large), which can raise extra early warnings, not extra switching.
- **The planner respects every gate rule, cooldown included.** If the only plan that saves a zone needs a switch restored less than 60 s ago (more than 15 s of cooldown left), the Brain falls back to its single-move logic, possibly last-resort shedding. Seen in 1 of 11 noise seeds of E13.
- **Physics evidence is used to stop and to warn, not to act.** When the power balance breaks, the Brain blocks actions that rely on the contradicted sensors and warns the operator, including of a possible hidden overload. It does not switch on the strength of the meter alone (E14 still overloads; the operator is told within seconds instead of never).
- **Sudden genuine jumps** are confirmed after 5 samples (about 5 s slower than ramps) unless an aggregate meter confirms them in the same cycle. A fast ramp that stays under the 20%-per-sample limit is accepted sample by sample and its trend is extrapolated over 45 s, which can make a relief look infeasible for a few seconds.
- **One change per zone at a time.** Plans are executed one verified step at a time; zones with an action awaiting verification are left alone (other zones can be handled in parallel).
- **A switched-off zone's demand cannot be measured.** Re-energizing relies on the J-1 plan, a minimum wait and back-off.
- **Clocks must be roughly synchronized** (verdict age, signature window, future-timestamp check).
- **Validated on mocks only** (REST and MCP, SDK 1.x and 2.x). The Docker image has still not been build-tested (Docker Hub is not reachable from the build environment used for v0.7); the requirements install and run cleanly in a fresh virtual environment.
- **Shared spare capacity can be used up early.** Acting predictively on a first, moderate surge may consume capacity that a later, more critical surge needs; the rule "never push a zone over capacity" then forbids taking it back, even to protect a hospital. The benchmark measures this cost (double-surge family); relaxing the rule is an operator-policy decision, not made here.

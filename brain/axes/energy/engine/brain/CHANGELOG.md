# Changelog

## v0.7 (2026-11-06): measured, coordinated, physics-aware

### New capabilities
- **Adaptive, calibrated forecaster** (`forecast.py: AdaptiveForecaster`, default). An online ensemble of trend fits
  over 10 / 20 / 40 samples and the J-1 shape. Each prediction is scored when its target time arrives, and the experts
  are weighted by inverse recent error. The prediction band is re-scaled so the realized 97.7% quantile matches the
  nominal 2-sigma band (split-conformal style). `forecast.model: trend` restores v0.6.
- **Multi-zone coordination** (`planner.py`). When no single move solves a risk, the Brain searches plans of up to 3
  tie toggles on a demand-reconstructed transfer model, with mean and std per zone. Every intermediate configuration
  must respect the hard constraints, which gives make-before-break by construction. Only the first step is executed;
  the Brain re-plans after each verified step. A plan waits up to 15 s for a switch in cooldown instead of falling back
  to shedding. Disjoint risky zones are handled in parallel. (E13, E13B)
- **Physics-based consistency** (`consistency.py`). Optional aggregate (substation) meters in the city descriptor give
  a power-balance check, using a level rule and a CUSUM test on the residual. A broken balance produces:
  - a `consistency` event sent to the Guardian
  - a gate block on the contradicted sensors
  - exclusion of those sensors from learning
  - an operator alert with per-zone estimates, including a possible hidden overload

  The same physics also confirms real sudden jumps at once (E17), and refuses jumps it contradicts, however long they
  persist (E18). (E14, E15)
- **Phantom acknowledgements.** A switch that reports moving while no power moved (< 20% of the predicted shift) is
  declared FAILED, and its real position is used from then on. (E16)
- **Statistical benchmark** (`bench/`): 8 randomized scenario families x 2 cities x 20 seeds, four policies
  (v0.7, v0.6 logic, reactive, do-nothing), a clairvoyant oracle, a paired bootstrap, and an ablation study.
- **Property-based safety tests** (`tests/test_properties.py`, Hypothesis). Gate, decision, planner and forecaster
  invariants are checked on hundreds of random states per run.
- Utility-grade metrics in every run: energy above capacity, energy not served, customer-minutes interrupted, band
  coverage, and decision-cycle latency.

### Bugs found by the benchmark and fixed
- **A spoof flagged late by the Guardian could cause load shedding** (present in v0.6). A held jump was accepted after
  5 samples if the sensor was still trusted. With a slow Guardian, a spoofed 144% reading was believed and led to
  `open F4` (E18B). Now a jump contradicted by an aggregate meter is never accepted.
- **Over-extrapolated forecasts could cause shedding when a tie would have worked.** A fast ramp extrapolated over 45 s
  made every tie look infeasible, so shedding won. Before shedding, the Brain now checks whether a tie relieves the
  overload as measured now, and prefers it.
- **Relieved zones kept their pre-switch trend.** After a transfer, the forecaster kept the full slope, so a relieved
  zone still looked as if it ramped at the old rate. The trend is now scaled by the share of load that remains.
- **Physics-confirmed jumps inherited a steep trend** from the tail of the step (found while adding E17). A confirmed
  jump now restarts the trend at the new level.
- **Operator-alert noise.** "No option improves" alerts are now raised only while the do-nothing future still has
  P(overload) >= 50%, not while a risk that was already handled clears through hysteresis.

### Interface
- `CityDescriptor.aggregate_meters` (optional) and meter readings with `zone_id` = the meter's id (INTERFACE.md §1).
- New event kinds: `plan`, `consistency`. Both are also pushed to the orchestrator webhook.
- The conformance tool checks declared aggregate meters.

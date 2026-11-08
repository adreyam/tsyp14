# Statistical benchmark and ablation study

The Test Book proves each behaviour once. The benchmark measures how well the Brain does **across many randomized
situations**, compares it with baselines, and removes one mechanism at a time to show what each one is worth.

```bash
export PYTHONPATH=src:.
python -m bench.run --set main --seeds 20        # Brain v0.7 vs v0.6 logic vs reactive vs do-nothing
python -m bench.run --set ablation --seeds 8     # remove one mechanism at a time
python -m bench.report                           # -> bench/results/RESULTS.md, summary.json
python -m bench.debug double 2 full              # replay one benchmark scenario with its key events
```

## Method

- **Same engine as production.** Every run is the real `EnergyBrain` in fast mode (simulated clock) against the mock
  Twin and Guardian, exactly like the Test Book. Baselines and ablations are applied from outside, through config
  overrides or a hook on the built Brain (`bench/run.py: POLICIES`). Production code has no benchmark switches.
- **Randomized, reproducible scenarios** (`bench/scenarios.py`). Each (family, seed) gives one scenario from a seeded RNG,
  so every policy faces exactly the same situations (paired comparison). Duration 220 s, two cities:
  - `city_a`: 4 zones, star-shaped around Z2, hospital Z3
  - `city_c`: 5 zones, meshed, hospital E

  | Family | What happens |
  |---|---|
  | calm | nothing (false alarms, needless switching) |
  | ramp | one zone ramps to 95–135% of capacity in 15–90 s |
  | step | one zone jumps to 105–130% in 1–4 s |
  | double | two zones surge, 0–40 s apart (shared spare capacity) |
  | stuck | a ramp while a random tie is jammed |
  | spoof | a ramp while another zone's sensor is spoofed high; the Guardian flags it 1–6 s later |
  | drift | stealthy attack the Guardian does not catch: a sensor drifts slowly, masking a real surge or faking one |
  | phantom | a ramp while a random tie's status is spoofed (reports moving, never moves) |

- **Clairvoyant oracle** (`bench/oracle.py`). It knows the true demand at every second and can re-configure all
  working ties every second, with no sensing, trust, cooldown or verification constraints and no shedding. An
  incident is **preventable** if the oracle keeps every zone within capacity at every second. *Prevented* means the
  policy had zero seconds of true overload. The oracle's remaining overload energy is a floor no policy can beat.
- **Ground truth.** Overload is measured on the Twin's true load, which the Brain never sees. Spoofed or drifting
  readings do not count as overload.
- **Metrics.** True overload seconds and energy above capacity (kWh); energy not served and customer-minutes
  interrupted (each zone has a customer count); switch operations, including those taken when doing nothing was safe
  (same scenario, do-nothing run had no overload); share of fake-surge attacks that made the Brain switch; risk
  alarms on calm days; operator alerts; 45 s forecast MAE; one-sided coverage of the upper band (nominal 97.7%); and
  wall time of a decision cycle. In fast mode that time includes in-process HTTP calls to the mocks.
- **Statistics.** Means over runs, 95th percentiles for tails, and a paired bootstrap (4000 resamples) 95% CI for
  the v0.7 − v0.6 difference on the same scenarios.

## Limits

- Mocks, not a real grid. The load-transfer model of the mock is the same family as the Brain's model, which favours
  the Brain's what-if accuracy. Results show relative merit of mechanisms, not field performance.
- Random scenarios are not weighted by real-world frequency.
- City A's Z2 is a pure sink (every tie feeds into it). Surges in Z2 cannot be relieved by switching, and the oracle
  marks them unpreventable.

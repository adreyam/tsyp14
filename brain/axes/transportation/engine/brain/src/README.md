# Source map

The Python packages stay at the folder root so that every import and run script keeps working:

| Package | Role |
|---|---|
| `citybrain/` | the Brain: `engine.py` (simulation thread), `rl.py` (online signal learning), `parkingsim.py` (drivers and gates), `api.py` (dashboard API), **`bridge/` (interface to the Twin and the Guardian: `router.py`, `service.py`, `trust.py`, `outbound.py`, `timeline.py`, `config.py`)** |
| `app/` | smart-parking backend (entrance / space / exit flows, alerts, administration API, ranking) |
| `sumo_rl/` | SUMO-RL with the emergency-vehicle priority system (`sumo_rl/preemption/`) |
| `training/` | training code of the signal agent; `runs/main` = the saved training |
| `mocks/` | stand-ins for the Twin and the Guardian |
| `config/brain.yaml` | every partner address and key |

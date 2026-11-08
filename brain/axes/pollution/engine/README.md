# triplet-XX — City Brain Convergence (TSYP14, Phase 1)

> Rename this folder to `triplet-NN` with your triplet number and fill the TO FILL parts.

A smart city built by three teams: the **Twin** simulates the city, the **City Brain** decides,
the **Guardian** checks that the data is true.

## Teams — TO FILL
| Layer | Team (SB / name) | Members | Folder |
|---|---|---|---|
| Twin | | | `twin/` |
| City Brain | | | `brain/` |
| Guardian | | | `guardian/` |

## Start
Each layer runs on its own with mocks of the other two (see the README in each folder).
City Brain alone, from this root:
```
python scenarios/g1_normal_day.py      # one command per scenario, log in logs/
python scenarios/live_demo.py          # live over HTTP with mocks: timeline on http://127.0.0.1:8100/timeline
docker compose run --rm -T --build scenarios   # every scenario (G1-G6, A1-A5) in a container, logs in logs/
docker compose up --build live              # live demo: http://localhost:8100/timeline and :8200/control
docker compose run --rm --build tests       # the automated tests
```
Starting the whole city with the three real layers: TO FILL once the layers are connected.

## What is here
| Path | Content |
|---|---|
| `INTEGRATION.md` | how the three layers connect: diagram, messages, protocol, one scenario end to end |
| `TESTBOOK.md` | the test scenarios and their expected results |
| `scenarios/` | one trigger script per scenario |
| `logs/` | one log file per scenario run |
| `brain/` | City Brain (this team's work) |
| `twin/`, `guardian/` | the partner teams' work |
| `docs/` | `report.pdf`, `pitch.pdf` and diagrams (to add) |

## Status
- City Brain: built and tested alone with mocks, offline and over HTTP with tokens (garbage-bin and air-quality axes). Live timeline screen built. Docker image built and scenarios run in it on Windows (Docker Desktop). Callable as an agent by an orchestrator (HTTP or MCP, `brain/AGENT.md`).
- Twin and Guardian: TO FILL by their teams.
- Joint run with the three real layers: not done yet.

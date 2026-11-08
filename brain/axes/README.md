# The four axes

One folder per axis. Each holds two things:

| Folder | What | Data |
|---|---|---|
| `<axis>/agent/` | the axis agent of the City Brain orchestrator (`brain/src/citybrain`) | the live Twin, through the orchestrator |
| `<axis>/engine/` | the axis team's own Brain, unchanged | its own simulation (own mock Twin, own map) |

```
brain/axes/
├── README.md            this file
├── ENGINES.md           how the three engines and the control center work together (ports, tokens, details)
├── start_engines.sh     start only the engines (Linux); the whole city: ./start_city.sh at the triplet root
├── emergency/           agent/  engine/   (emergency team: PLUM, flood, storm, tsunami, crime)
├── transportation/      agent/  engine/   (SUMO signals, smart parking, taxi demand)     port 8000
├── pollution/           agent/  engine/   (air quality and waste)                      port 8100
└── energy/              agent/  engine/   (grid, load shedding, forecasting)           port 8400
```

How the orchestrator uses the agents: `brain/src/citybrain/orchestrator.py` (classify → Guardian trust gate → agents →
arbitration → act → audit). The registry of agents is `brain/src/citybrain/agents/__init__.py`.

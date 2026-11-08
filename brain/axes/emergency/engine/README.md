# City Brain Layer

> An AI-powered smart-city orchestration platform that ingests urban events (sensor telemetry, operator reports, digital-twin feeds, external observation networks) and coordinates multi-axis response across **Emergency**, **Mobility**, **Environment**, and **Energy** domains.

---

## Architecture Overview

```
                                      ┌───────────────┐
                         CityEvent    │  Orchestrator │
       sensors ─────────────────────► │  (LangGraph)  │
       operators ───────────────────► │               │
       digital twin ────────────────► └───────┬───────┘
       external feeds (Meteo/USGS/..)         │
                                              │
                      ┌───────────────────────┼───────────────────────┐
                      ▼                       ▼                       ▼
               ┌────────────┐          ┌─────────────┐         ┌─────────────┐
               │  classify  │          │    ...      │         │    ...      │
               │  (rules +  │          └─────────────┘         └─────────────┘
               │   Gemini)  │
               └──────┬─────┘
                      │ parallel fan-out
        ┌─────────────┼──────────────┬──────────────┐
        ▼             ▼              ▼              ▼
    Emergency      Mobility     Environment      Energy
      Agent         Agent          Agent          Agent
   ┌────┴─────┐
   │ Engines: │
   │ - PLUM   │
   │ - Crime  │
   │ - Flood  │
   │ - Quake  │
   │ - Tsunami│
   │ - Storm  │
   └──────────┘
        │
        ▼
   ┌──────────┐    ┌──────────┐
   │synthesize│───►│  audit   │──► SQLite + publish
   │(conflict │    │(AuditLog)│
   │resolution)    └──────────┘
   └──────────┘
```

### Processing Pipeline

1. **Classify** — Every incoming `CityEvent` is scored by deterministic rules, by the optional **Laya** System 1 router (when the `laya` package is installed) and, for ambiguous events only, by the optional **Gemini** System 2 router (function-calling). A safety-floor mechanism ensures critical rule scores (≥ 0.9) can never be downgraded by either model.
2. **Dispatch** — Axes whose score exceeds the route threshold (0.5) receive the event in parallel via LangGraph's `Send` fan-out.
3. **Run Agents** — Domain-specific agents produce severity, confidence, action lists, and evidence chains. Only axes listed in `CITY_BRAIN_AXES` (default: `emergency`) actually run; the others are still scored and routed but reported under `unavailable_axes` with no action sent.
4. **Synthesize** — A priority-based conflict resolver (`emergency:0 > environment:1 > mobility:2 > energy:3`) deduplicates resource claims across axes.
5. **Audit & Publish** — The final decision is persisted in SQLite and dispatched to downstream axis controllers.

---

## Repository Structure

```
brain-layer/
├── README.md                          # Platform documentation
├── INTERFACE.md                       # Complete API and Event payload schemas
├── requirements.txt                   # Dependency manifest
├── pytest.ini                         # Pytest configuration
├── city_brain_audit.db                # SQLite audit log (runtime artifact)
│
├── config/
│   └── hazard_thresholds.yaml         # Configurable hazard thresholds and cutoffs
│
├── src/
│   ├── __init__.py
│   ├── orchestrator.py                # LangGraph state machine, Gemini router,
│   │                                  #   classification rules, cascade logic, audit
│   └── modules/
│       ├── __init__.py
│       ├── emergency_agent.py         # Emergency Axis Agent (dispatch, playbooks)
│       ├── hazard_common.py           # HazardAssessment model, config loader, helpers
│       ├── hazard_playbooks.py        # Deterministic playbooks, optional LLM planner
│       ├── plum_engine.py             # PLUM seismic waveform early-warning engine
│       ├── crime_hotspot.py           # Crime hotspot risk scoring engine
│       ├── hotspot_model.py           # Violent-crime hotspot ML pipeline (250 m grid)
│       ├── flood_engine.py            # Hydrological and pluvial flood hazard engine
│       ├── catalog_quake_engine.py    # Catalog earthquake engine (USGS/EMSC)
│       ├── earthquake_engine.py       # One earthquake result from PLUM + catalog
│       ├── tsunami_engine.py          # Tsunami candidate & sea-level evaluation engine
│       ├── storm_engine.py            # Storm nowcasting and severe convective engine
│       └── feeds/                     # External data feed adapters (offline-capable)
│           ├── __init__.py
│           ├── open_meteo.py          # Open-Meteo forecast and live weather adapter
│           ├── usgs.py                # USGS earthquake catalog adapter
│           ├── emsc.py                # EMSC Mediterranean seismic adapter
│           ├── glofas.py              # Copernicus GloFAS river discharge stub
│           └── weather_alert.py       # Official meteorological alert stub
│
├── mocks/
│   ├── __init__.py                    # Re-exports all mock fixtures
│   ├── mock_plum.py / .json           # PLUM mock packets (network + per-sensor) for the one PLUM algorithm
│   ├── mock_crime_hotspot.py / .json  # Crime Hotspot scenarios
│   ├── mock_flood.py / .json          # Flood hazard scenarios
│   ├── mock_catalog_quake.py / .json  # Catalog earthquake scenarios
│   ├── mock_tsunami.py / .json        # Tsunami and sea-level anomaly scenarios
│   └── mock_storm.py / .json          # Severe storm & Doppler radar scenarios
│
└── tests/
    ├── test_flood_engine.py           # Pytest unit tests for FloodEngine
    ├── test_catalog_quake_engine.py   # Pytest unit tests for CatalogQuakeEngine
    ├── test_tsunami_engine.py         # Pytest unit tests for TsunamiEngine
    ├── test_storm_engine.py           # Pytest unit tests for StormEngine
    ├── test_emergency_agent_hazards.py# Pytest integration tests for EmergencyAgent
    └── test_orchestrator_hazard_rules.py# Pytest tests for orchestrator routing
```

---

## Core Emergency Engines

| Engine | File | Method & Description |
|---|---|---|
| **PLUM Engine** | `src/modules/plum_engine.py` | The one seismic waveform detector: full 2-station network detection (Kilb et al., 2021; Worden et al., 2012). `PlumStream` buffers per-sensor packets and network packets and runs the same algorithm on them; a lone sensor can never raise an alert |
| **Earthquake Engine** | `src/modules/earthquake_engine.py` | Merges PLUM (local shaking, seconds) and catalog solutions (magnitude/epicentre, minutes) into one earthquake result; confirming PLUM stations count as independent sources |
| **Crime Hotspot Engine** | `src/modules/crime_hotspot.py` | Multi-factor risk terrain scoring (Alhammadi, 2025) |
| **Flood Engine** | `src/modules/flood_engine.py` | Weighted composite risk index combining rainfall forecast, soil saturation, river stage ratio, and drainage load |
| **Catalog Quake Engine** | `src/modules/catalog_quake_engine.py` | Magnitude/depth/distance attenuation analysis from USGS/EMSC feeds; feeds into Tsunami Engine |
| **Tsunami Engine** | `src/modules/tsunami_engine.py` | Offshore marine candidate filtering, wave propagation ETA, and tide gauge sea-level anomaly confirmation |
| **Storm Engine** | `src/modules/storm_engine.py` | High-gust detection, rapid barometric drop, convective rotation signatures, and official weather alerts |

---

## Key Invariants

1. **Deterministic Hazard Detection**: No LLM is in the detection path; all hazard evaluations are pure, explainable, and reproducible logic.
2. **Risk Index, Not Probability**: All engine outputs represent normalized composite risk scores [0.0 - 1.0], never probabilities.
3. **Critical Source Agreement (Section 7.6)**: An assessment cannot reach `critical` without at least `min_sources_for_critical` (default 2) independent sources. Otherwise, it is safely capped at `warning`.
4. **Human Approval Safeguard**: Evacuation directives and all `critical` actions automatically flag `requires_human_approval = True`.
5. **Offline Operation**: System operates fully with `CITY_BRAIN_LLM=off` or without an API key; optional LLM planners fail silently to deterministic playbooks.

---

## Quick Start & Verification

### 1. Run the Pytest Test Suite
```bash
# Run all unit, integration and regression tests offline
# (tests use a temporary audit database, never city_brain_audit.db)
pytest
```

### 2. Run Self-Contained Mock Verifications
```bash
python mocks/mock_flood.py
python mocks/mock_catalog_quake.py
python mocks/mock_tsunami.py
python mocks/mock_storm.py
python mocks/mock_plum.py
python mocks/mock_crime_hotspot.py
```

### 3. Run the Orchestrator Demo
```bash
python src/orchestrator.py
```

### 4. Dashboards
```bash
python src/city_brain_dashboard.py   # City Brain control room -> http://localhost:8085 (or start_dashboard.bat)
python src/plum_dashboard.py         # PLUM test bench -> http://localhost:8080 (streams mock sensor packets into the real PLUM)
```

The control room covers every engine.

**Scenarios are mock data; the algorithms decide.** Every scenario reading comes from `mocks/mock_city_scenarios.py` and carries only observations and sensor metadata (no severity, risk level, axis, priority or units), flagged `data.mock = true` and shown with a *MOCK DATA* badge. `src/city_model.py` only places the readings on the simulated city. The axes on each scenario button are previewed by the orchestrator's own rules, and everything in a decision (routing, severity, hazard level, actions, units, approvals, and map effects such as flooded roads or the coastal evacuation zone) is produced by the orchestrator, PLUM, the crime hotspot engine, the hazard engines and the emergency agent playbooks. Swap the mock module for Twin-layer feeds and nothing else changes.


| Group | Buttons | What runs |
|---|---|---|
| Sensor scenarios | accident, fire, load spike, garbage, tunnel flood, gunshot | one sensor event through the orchestrator |
| Catastrophic hazards | local earthquake, seismic false trigger, river flood, severe storm, regional earthquake, offshore quake → tsunami | a short sequence of readings from independent sources: earthquakes send the city's PLUM station waveforms then the catalog solution (merged by `EarthquakeEngine`); the others are correlated by `FloodEngine`, `StormEngine`, `TsunamiEngine` (each drill starts from a clean buffer) |
| Live feeds | Open-Meteo weather, USGS quakes, EMSC quakes | polls the real feed adapters (internet needed) and evaluates the relevant events (quakes M4.5+ for USGS, M4.0+ for EMSC, strongest 5) |

For hazards the decision panel shows the risk level and index, confidence, independent sources, ETA, the risk drivers, and the readings that were correlated. Actions that need an operator get an **Approve** button; approvals are stored in the `approvals` table of the audit DB. The *Catastrophic hazard monitor* card shows the latest level of each engine. On the map: flooded quarter with closed roads, storm cell, direction and distance to a distant epicentre, coastal evacuation zone with a tsunami ETA countdown.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `CITY_BRAIN_HAZARD_CONFIG` | `config/hazard_thresholds.yaml` | Path to hazard thresholds YAML file |
| `CITY_BRAIN_LLM` | `on` | Set to `off` to disable Gemini routing and LLM planning |
| `CITY_BRAIN_MODEL` | `gemini-3.5-flash-lite` | Gemini model name |
| `GEMINI_API_KEY` | None | Google GenAI API key for optional LLM router and planner |
| `CITY_BRAIN_AXES` | `emergency` | Comma-separated axes whose agents are integrated and allowed to act |
| `CITY_BRAIN_AUDIT_DB` | `<project>/city_brain_audit.db` | SQLite audit log path (the test suite points it at a temp file) |

The offshore test for catalog earthquakes (tsunami candidates) uses the `global-land-mask` package (1 km land/sea grid, offline). Without it, a coarse bounding box is used that over-warns for inland quakes.

# Diagrams

- `architecture.mmd`: the three layers and how they talk (Mermaid source; GitHub renders it below).
- The text version of the same diagram, with the protocol details, is at the top of [../../INTEGRATION.md](../../INTEGRATION.md).

```mermaid
flowchart LR
  subgraph TWIN["Twin (Godot 4 + SUMO)"]
    T1["Sensors: traffic, air, flood, power, water, waste, seismic"]
    T2["Actuators: signals, power zones, valves, car parks, emergency units"]
    T3["Device API: JSON lines over TCP 127.0.0.1"]
  end
  subgraph GUARD["Guardian"]
    G1["Trust verdicts: trusted, suspect, untrusted, quarantined"]
  end
  subgraph BRAIN["City Brain :8500"]
    B1["TwinSource: live or mock"] --> B2["Trust gate"] --> B3["4 agents: emergency, transportation, pollution, energy"]
    B3 <--> B6["A2A requests between axes"]
    B3 --> B4["Arbitration: quarantine, vetoes, priorities"] --> B5["Act + audit log (logs/)"]
  end
  CC["Control center :8765: top bar, City map, live timeline, Test Book"]
  T3 -- "read_all, list_incidents (1 s)" --> B1
  B5 -- "command {id, command, value}" --> T3
  T3 -- "applied / refused" --> B5
  B2 -- "POST /v1/trust-verdict" --> G1
  G1 -- "verdict (or push POST /v1/verdicts)" --> B2
  BRAIN -- "/v1/status, /v1/timeline, /v1/map" --> CC
```

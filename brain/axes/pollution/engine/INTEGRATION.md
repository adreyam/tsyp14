# Integration — how the three layers connect

DRAFT by the City Brain team. Everything marked **TO AGREE** needs a decision by the three teams.
The traces below come from runs with the mock Twin and mock Guardian. They must be replaced
by a real joint run (requirement R4) once the real layers are connected.

## 1. Overview

```
            city map (once) + bin readings + truck states
   TWIN ───────────────────────────────────────────────────▶ CITY BRAIN
     ▲                                                          │  │
     │  dispatch_truck (next stop + route)                      │  │ "is this reading true?"
     └──────────────────────────────────────────────────────────┘  ▼
                                                                GUARDIAN
                                              trust verdict ◀──────┘
   TWIN ───── the same readings ─────▶ GUARDIAN   (it watches the same data)
```

The City Brain acts on a reading only after the Guardian has said `trusted`.

## 2. Messages

| From → To | Message | When | Format | Status |
|---|---|---|---|---|
| Twin → Brain | city map | once, at startup | `brain/INTERFACE.md` §0 | TO AGREE |
| Twin → Brain | `get_sensor_reading` (one per bin) | every X minutes | `brain/INTERFACE.md` §1 | TO AGREE |
| Twin → Brain | `get_truck_state` (one per truck) | every X minutes | `brain/INTERFACE.md` §2 | TO AGREE |
| Brain → Guardian | `get_trust_verdict` request | for each reading | `brain/INTERFACE.md` §3 | TO AGREE |
| Guardian → Brain | trust verdict | answer to the request | `brain/INTERFACE.md` §3 | TO AGREE |
| Brain → Twin | `dispatch_truck` | when a truck gets a new stop | `brain/INTERFACE.md` §4 | TO AGREE |
| Twin → Brain | air reading (`type: air_quality`) | every X minutes per air monitor | `brain/INTERFACE.md` §5 | TO AGREE |
| Brain → Twin | car park list, `set_parking_barrier` | when a zone reaches high, and after | `brain/INTERFACE.md` §6 | TO AGREE |

## 3. Protocol

**With our Twin (City Ground Zero): MCP over stdio — built on the Brain side.** The Brain runs the
Twin's MCP server as a child process and calls `waste_bin_report` (read) and `empty_waste_bin`
(act). Details, conversions and open points: `brain/INTERFACE.md`, section "Our Twin: City Ground Zero".
Open points for the Twin team: a truck dispatch tool, the `position` format and bin capacities,
network access to MCP (HTTP + SSE of the dashboard on `0.0.0.0` with a fixed port) and a token.

**With any partner that speaks HTTP** (other triplets, Phase 2), the proposal below is also built:

The spec leaves the protocol free. The City Brain side is built and tested with **HTTP + JSON**
(endpoints, status codes and examples in `brain/INTERFACE.md`):
- the Twin pushes readings and truck states to `POST <brain>/v1/readings` at the agreed interval;
- the Brain asks the Guardian `POST <guardian>/v1/trust-verdict` for each reading;
- the Brain pushes `dispatch_truck` to `POST <twin>/v1/dispatch` and fetches the map from `GET <twin>/v1/map`;
- every request carries `Authorization: Bearer <token of the caller>`;
- addresses, paths, timeouts and token variable names are in `brain/config/brain.json`.

Swapping the protocol (MQTT, WebSocket) would mean writing one more client in `brain/src/`.

Decisions to take together:
- [ ] Protocol: HTTP + JSON as above, or another one
- [ ] Ports and addresses of each machine on the demo router (proposal: Brain 8100, Twin 8200, Guardian 8300)
- [ ] Endpoint paths on the Twin and Guardian side (the Brain's are configurable)
- [ ] Tokens: one per layer, at least 16 characters, shared out of the repository
- [ ] Reading interval X (the Brain treats a sensor silent for 3 × X as suspect)
- [ ] Time: all timestamps in UTC, ISO 8601; the Brain takes city time from the Twin's timestamps
- [ ] Sensor, bin, truck and zone IDs: the same strings on all three sides
- [ ] Who has the depot position (proposal: the Twin sends it as a node of the map; the Brain reads its id from config)

### Joint run checklist (R4)
1. All three laptops on the same router (no venue Wi-Fi). Note each IP address.
2. Each team sets the agreed tokens as environment variables.
3. City Brain: copy `brain/config/brain.partners.example.json`, put the Twin and Guardian addresses,
   then `python -m src.service --config <that file>`. Check `http://<brain-ip>:8100/v1/health`.
4. Twin points its readings at `http://<brain-ip>:8100/v1/readings`.
5. Open `http://<brain-ip>:8100/timeline` and run the agreed scenarios.
6. Commit `logs/JOINT-RUN.jsonl` and a screenshot of the timeline as proof of the joint run.

## 4. What the scenarios need from each partner

The Test Book (`TESTBOOK.md`) needs the partners to offer scenario controls, because the Brain
cannot create an attack or an outage by itself.

| Partner | Control needed | Used by |
|---|---|---|
| Twin | set the real weight of a bin | G2, G6 |
| Twin | report a false weight for one sensor, keeping the true one | G3, G6 |
| Twin | make a sensor go silent | sensor-silence checks |
| Twin | refuse dispatches for a while | G5 |
| Twin | send a malformed message | G5 |
| Guardian | be stopped and restarted | G5 |
| Guardian | mark a given sensor as untrusted on demand (while the Guardian's own detection is not ready) | G3, G6 |

## 5. One scenario end to end — G3, fake overflow (mock run)

Sensor b06 starts reporting 98 % full while the bin is really about 20 % full. Real log lines
from `logs/G3.jsonl` (`python scenarios/g3_spoofed_reading.py`):

```
07:45  TWIN      b06 reports 98.0% (49.0 kg)
07:45  GUARDIAN  b06 is not_trusted: jumped from 22% to 98% in 5 min, neighbours unchanged
07:45  BRAIN     b06 ignored (jumped from 22% to 98% in 5 min, neighbours unchanged)
```

The same three events in the raw log, one JSON object per line:

```json
{"ts": "2026-11-01T07:45:00Z", "layer": "twin", "event": "sensor_reading", "sensor_id": "b06", "weight_kg": 49.0, "capacity_kg": 50.0, "fill_pct": 98.0}
{"ts": "2026-11-01T07:45:00Z", "layer": "guardian", "event": "trust_verdict", "sensor_id": "b06", "status": "not_trusted", "confidence": 0.1, "reason": "jumped from 22% to 98% in 5 min, neighbours unchanged"}
{"ts": "2026-11-01T07:45:00Z", "layer": "city_brain", "event": "reading_blocked", "sensor_id": "b06", "reason": "jumped from 22% to 98% in 5 min, neighbours unchanged"}
```

A control run without the Guardian's protection sends a truck to b06, so the attack is real
and the Guardian is what stops it.

## 6. When a partner fails

| Failure | What the City Brain does |
|---|---|
| Malformed message | Rejects it, logs the faulty fields, keeps running |
| Guardian unreachable, wrong sensor in the answer, or verdict not `trusted` | Ignores the reading; the bin is not planned until it is trusted again |
| A sensor stops sending for 3 × X | Treats it as suspect until it sends again |
| Twin refuses a dispatch | Cancels the stop, logs it, retries at the next cycle |
| A truck sends no state | Keeps its last known position (see `brain/INTERFACE.md`) |

## 7. How the teams coordinate (R4)

TO FILL by the triplet: tools used, synchronization schedule, how a partner change or failure was
handled, proof of a successful joint run (log of a run with the three real layers).

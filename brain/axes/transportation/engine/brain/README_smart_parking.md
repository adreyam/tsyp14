# Smart Parking System — City Brain (transportation axis)

Complete parking-lot system: ESP32 gateway firmware, a Python backend that runs every
parking flow, entrance/exit terminal screens, an admin dashboard, a City Brain
availability feed, and a simulator that tests every scenario without hardware.

**Space occupancy uses magnetic vehicle sensors** (QMC5883L 3-axis magnetometers)
instead of ultrasonic sensors.

```
smart-parking/
├── backend/            FastAPI + SQLite backend (all business logic)
│   └── app/
│       ├── services/   entrance_flow, space_flow, exit_flow, watchdog
│       ├── routers/    gateway (devices), terminal, admin, city
│       └── static/     terminal.html (entrance/exit screen), admin.html
├── firmware/parking_gateway/   ESP32 Arduino sketch
└── simulator/simulate.py       end-to-end test of every scenario
```

## 1. Run it (Windows CMD)

```bat
cd smart-parking\backend
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open:
- Admin dashboard: http://localhost:8000/admin (admin key `dev-admin-key`)
- Entrance terminal: http://localhost:8000/terminal/P01-ENT1
- Exit terminal: http://localhost:8000/terminal/P01-EXIT1
- API docs: http://localhost:8000/docs
- City Brain feed: http://localhost:8000/api/city/lots

### Live animated simulation

With the backend running, open **http://localhost:8000/sim**. It is an animated
top-down view of lot P01. The page plays the role of the ESP32 gateway: it sends
the real sensor and RFID events to the backend and obeys the backend's real commands
(barriers, card dispenser, space LEDs). Nothing is scripted on the page — every
decision you see (space assignment, refusal, misplaced alert, exit authorization)
comes from the backend.

- Send a registered car (V001/V002), a visitor without tag, or a car with an
  unregistered tag. Click **Leave** to make a parked car exit.
- Options: park in the wrong space first, tag unreadable at the exit, visitor loses
  the card, press "Request a ticket" yourself (in the entrance terminal panel).
- **▶ Guided demo** runs: registered entry, visitor in the wrong space, a refused
  duplicate entry, and both exits.
- When a car is stuck in manual verification, use **Admin: manual exit** in the car
  list, or the manual-exit form on the admin dashboard.
- Open `/admin` and `/terminal/P01-ENT1` in other tabs to watch them update live.

Don't run the simulation page while a real ESP32 is connected as `GW-P01`: both
would pick up the same commands.

### Scripted test

Run the scripted simulator in a **second** CMD window (it resets the demo database):

```bat
cd smart-parking
backend\venv\Scripts\activate
python simulator\simulate.py
```

Keep the admin page and the terminal pages open while it runs to watch the flows.
To reset the database manually: delete `backend\parking.db` and restart.

## 2. Updated hardware list

| Component | Quantity | Location |
|---|---|---|
| Vehicle detection sensor (inductive loop or infrared) | 1 per entrance | Each entrance lane |
| Vehicle detection sensor (inductive loop or infrared) | 1 per exit | Each exit lane |
| RFID reader | 1 per entrance | Each entrance |
| RFID reader | 1 per exit | Each exit |
| **Magnetic vehicle sensor (QMC5883L magnetometer)** | **1 per parking space** | **On / embedded in the ground at the centre of each space** |
| TCA9548A I²C multiplexer | 1 per 8 spaces | Next to the gateway (all QMC5883L share address 0x0D) |
| Local RFID reader | 1 per parking space | At each space |
| Entrance digital terminal + RFID card dispenser | 1 per entrance | Beside the entrance barrier |
| RFID card-return unit (with its own reader) | 1 per exit | Beside the exit barrier |
| Barrier (servo for the prototype) | 1 per lane | Entrance and exit |
| Guidance LED (optional) | 1 per space | At each space |
| Permanent RFID tag | 1 per registered vehicle | Windshield |
| Temporary RFID card | Reusable stock per lot | Dispensed at entrance, returned at exit |
| IoT gateway / ESP32 | As required | Each parking lot |

The magnetometer is mounted **under** the vehicle (ground), not above the space as
with ultrasonic sensors. The car's steel body distorts the Earth's magnetic field;
the firmware compares the field to an "empty space" baseline.

## 3. How magnetic detection works (firmware)

1. **Baseline** — at first boot the sensor averages 64 samples of the empty space and
   stores it in flash (NVS). Serial command `c` recalibrates (spaces must be empty).
2. **Deviation** — every 100 ms: `d = |B − B_empty|` (3 axes).
3. **Hysteresis** — occupied when `d > 1200` counts (~100 mG) for 5 consecutive samples,
   empty when `d < 600` for 5 samples. This stops flickering and ignores passing cars.
4. **Drift compensation** — while empty and quiet, the baseline slowly follows
   temperature drift.
5. **Fault detection** — after 10 consecutive I²C errors the gateway reports
   `SPACE_SENSOR_FAULT`; the backend removes the space from assignment.

Serial command `m` prints the live deviation per space — use it to tune
`MAG_ON_THRESHOLD` / `MAG_OFF_THRESHOLD` in `config.h` for your site and cars.

## 4. Flows implemented (backend)

| Step | Where |
|---|---|
| Entrance detection, RFID read / timeout | `entrance_flow.py` |
| Scenario A: registered tag, duplicate-session refusal + admin alert | `entrance_flow._identify` |
| Scenario B: visitor prompt, "Request a ticket", card availability check, dispense, card read, temp vehicle + session, card `IN_USE` | `entrance_flow.request_ticket`, `_temporary_card_presented` |
| No card available → driver told to contact the admin, **no session created** | `request_ticket` |
| Space selection (excludes occupied, reserved, out of service, sensor fault), reservation, ticket (ticket ID ≠ RFID), barrier | `entrance_flow._assign_space` |
| Arrival validation: magnetic **and** local RFID must agree → `ASSIGNED→PARKED`, `RESERVED→OCCUPIED` | `space_flow._validate_parking` |
| Wrong space → `MISPLACED`, admin alert, red blinking LED at the wrong space, until moved or reassigned by the admin | `space_flow._misplaced`, admin `reassign` |
| Vehicle disappears from its space → session **not** closed; flagged `VERIFICATION` if it does not reach the exit | `space_flow._vacated`, `watchdog.py` |
| Exit Case A / Case B, ticket-RFID match, card return confirmed by reader | `exit_flow.py` |
| Closure: exit time, `EXITED`, space `RELEASING` until the magnetic sensor confirms empty, card `AVAILABLE` | `exit_flow.close_session` |
| Unreadable tag at exit → manual verification, nothing closed automatically | `exit_flow.rfid_timeout` |
| Lost card → admin manual exit (valid ticket ID **and** confirmed manual check), card marked `LOST` | admin `manual-exit` |

Two design decisions worth knowing:
- **Leaving a space is normal** (the car is driving to the exit). The session gets
  `left_space_at` and is flagged `VERIFICATION` only after `PARKING_LEFT_SPACE_GRACE_S`
  (default 10 min) without an exit. Set it to `0` to flag immediately.
- **RFID alone never validates a space.** Requiring the magnetic sensor stops a reader
  from validating the tag of a car parked in the neighbouring space.

All timings are in `backend/app/config.py` and can be set as environment variables.

## 5. Gateway ↔ backend protocol

`POST /api/gateways/{id}/events` with header `X-Device-Key`:

```json
{"type": "SPACE_OCCUPANCY", "space_code": "E2", "occupied": true, "magnitude": 2100}
{"type": "LANE_RFID_READ", "lane_id": "P01-ENT1", "uid": "04A1B2C3"}
```

Types: `LANE_VEHICLE_DETECTED`, `LANE_RFID_READ`, `LANE_RFID_TIMEOUT`,
`LANE_VEHICLE_CLEARED`, `DISPENSE_FAILED`, `CARD_RETURNED`, `SPACE_OCCUPANCY`,
`SPACE_RFID_READ`, `SPACE_SENSOR_FAULT`, `SPACE_SENSOR_OK`.

`GET /api/gateways/{id}/commands` returns `OPEN_BARRIER`, `DISPENSE_CARD`,
`SPACE_INDICATOR` (`ASSIGNED` / `OK` / `WRONG` / `OFF`). The ESP32 polls every 300 ms
and keeps unsent events in a queue while Wi-Fi is down.

## 6. Firmware setup

1. Arduino IDE → Boards Manager → **esp32 by Espressif**, board "ESP32 Dev Module".
2. Library Manager: **MFRC522** (GithubCommunity), **ArduinoJson** (v7), **ESP32Servo**.
3. Edit `config.h`: Wi-Fi, `BACKEND_URL` (your PC's LAN IP, e.g. `http://192.168.1.50:8000`
   — not `localhost`), pins, lanes and spaces. Lane IDs and space codes must match the backend.
4. Allow port 8000 in the Windows firewall for the ESP32 to reach your PC.
5. Upload, open the serial monitor at 115200, check every RC522 reports a version
   (not `0x00`/`0xFF`).
6. Register real tags: the UIDs printed on the serial monitor replace the demo names
   (`RFID_123`, `RFID_TEMP_025`, …). Use `POST /api/admin/vehicles` and
   `POST /api/admin/temp-cards` from http://localhost:8000/docs, or edit `app/seed.py`.

Default wiring (`config.h`): SPI 18/19/23 shared by all RC522 (SS 5, 17, 16, 4, 13, 14;
RST tied to 3.3 V), I²C 21/22 → TCA9548A → QMC5883L on channels 0–2, lane sensors on
34/35, barrier servos 25/26, dispenser relay 27, space LEDs 32/33/15.

Notes for a real installation:
- RC522 (13.56 MHz) reads only a few cm — fine for the prototype and for cards, but
  windshield tags need **UHF RFID** readers (860–960 MHz). Only `readUid()` changes.
- Many cheap "HMC5883L" boards are actually QMC5883L (address 0x0D) — this code
  targets QMC5883L.
- Add a second detection loop after each barrier for safe closing.
- For many spaces, split them over several ESP32 nodes (each with its own `GATEWAY_ID`).

## 7. Top-3 parking recommendation (added)

Ranks the best 3 lots for a driver: **normalise -> CRITIC weights -> weighted sum**, using six criteria.

| Criterion | Better when | Where the number comes from |
|---|---|---|
| `drive_min` | lower | driver position -> lot (OSRM if `PARKING_RANK_OSRM_URL` is set, otherwise straight line x 1.3 at 30 km/h) |
| `walk_m` | lower | lot -> destination, straight line |
| `free_now` | higher | this backend's live count of assignable spaces (magnetic sensors) |
| `free_30` | higher | forecast of occupancy in 30 min x spaces in service, minus drivers already heading there |
| `pressure` | lower | drivers heading to the lot (reservations) per free space |
| `reputation` | higher | driver ratings 1-5, pulled toward neutral until 10 reviews |

Before scoring, lots are removed if: full (free minus drivers heading there <= 0), farther than `max_walk_m` (default 2000 m) from the destination, no space in service, excluded by the caller (redirect), or (optional) gateway offline.
CRITIC needs at least 3 candidate lots; with fewer, fixed default weights are used (`weights_source` says which).

Endpoints (docs at `/docs`):
- `POST /api/city/recommend` `{origin:{lat,lon}, destination:{lat,lon}, top_n:3, exclude:[], max_walk_m:2000}` -> ranked lots, with marks, weights and each criterion's contribution
- `POST /api/city/reserve` `{lot_id, eta_min}` -> holds a place in the competition count until ETA + 5 min; cleared automatically when a vehicle enters that lot
- `POST /api/city/reservations/{id}/cancel`, `POST /api/city/lots/{lot_id}/feedback` `{rating}`
- `POST /api/city/snapshots` (admin key) records occupancy now; it also runs every 5 min. This history feeds the forecast.
- **Redirect:** call `recommend` again with `exclude:[the full lot]`.

Forecast ladder (`services/forecast.py`): level 1 seasonal change (needs >= 6 snapshots on the same weekday around now and around now+30 min), 2 persistence, 3 lot average, 4 default 50 %. Each result shows its level. **LightGBM is not included**: there is no history to train it on yet; `predict_occupancy_rate` is where it plugs in.

**Demo data:** the base seed has only 2 lots, so lots P03-P05 (invented places and occupancy) are added; set `PARKING_RANK_DEMO_EXTRA_LOTS=0` to turn them off. Lot locations live in table `lot_profiles` (lat, lon); a lot without a row is skipped. The new tables are created automatically in an existing `parking.db`; the extra demo lots only appear after a database reset (delete `parking.db`).

Test (from `backend`, needs `pip install httpx`): `python -m unittest -v tests.test_ranking`
Try it: `curl -X POST http://localhost:8000/api/city/recommend -H "Content-Type: application/json" -d "{\"origin\":{\"lat\":36.82,\"lon\":10.21},\"destination\":{\"lat\":36.80,\"lon\":10.175}}"`

## 8. Simulated car screen with private "frequent places" (added)

Open **http://localhost:8000/car**. It simulates the car's screen AND plays the lot's ESP32 gateway (like `/sim`), so you can watch the whole trip live:

1. **Find parking** — the car sends the City Brain only its start point and the destination (`POST /api/city/candidates`) and receives the lots' raw values, *unranked*.
2. **The car ranks locally** (`static/car_ranking.js`, same maths as the server plus a 7th criterion, *routine*). The three offers show a stacked bar of how much each criterion contributed.
3. **Choose** a lot — the car reserves it, drives there on the map (speed selectable), and runs the real entrance flow against the lot system: tag read, ticket and space, barrier, magnetic sensor + local RFID at the space, `PARKED`.
4. **Leave parking** — space released, exit tag read, exit authorised, session `EXITED`. The right-hand panels show the lot's spaces, lanes and alerts live; open `/admin` and `/terminal/P03-ENT1` in other tabs to watch them too.

**Frequent places** live only in the browser's local storage (= the car), per driver profile. The example driver "Amine (example)" is invented: workplace (weekdays 07:00-13:00), children's school (weekdays 15:30-17:30), spouse's workplace (weekdays 17:30-19:30), favourite café (weekends 09:00-13:00 and weekdays 13:00-15:00). A place counts only while its time window is open (set the simulated day/time at the top). The routine criterion is the weighted mean distance from each lot to the places active now; its weight is capped at 25 %, and with no active place the ranking is exactly the six-criteria one. Coordinates are rounded to ~100 m when saved. "Delete my places" erases them. The "Privacy" panel shows exactly what was sent.

Try: destination default, Tuesday 16:00 -> P03 first (school pickup); Tuesday 10:00 or "Guest" -> P04 first.

Not implemented: a PIN or key-fob lock per driver profile, wiping on resale, cloud-sync control. Those belong to the real head unit.

Checks: `python tests/parity_check.py` (car JS vs server Python, needs Node), `node tests/e2e_car.js` (full trip in a headless browser, needs `npm i playwright`).

## 9. Demo: a tag that is already parked in another lot (added)

Open **http://localhost:8000/duplicate** for an illustrated live view of two lots (P02 and P01) and their cars.

1. A registered car (tag `RFID_123`) enters **P02**, gets a ticket and a space, and parks (`PARKED`).
2. The same tag arrives at the gate of **P01**. The backend looks for an active session of that vehicle in **any** lot (`entrance_flow._identify`), finds the P02 one and:
   - keeps the P01 barrier **closed** and shows the refusal on the entrance screen,
   - creates **no** session at P01,
   - raises a **CRITICAL `DUPLICATE_ENTRY`** alert for the administrator, naming the P02 session,
   - leaves the P02 session untouched.
3. Choose what really happened (top-right menu):
   - **A, cloned tag:** a second car carries a copy. The administrator can block the tag (new entries are refused with `VEHICLE_DISABLED`); the legitimate car still leaves P02 normally.
   - **B, left without being scanned out:** the exit reader of P02 was out of order and a guard lifted the barrier by hand, so the session stayed open. The administrator checks, closes the P02 session by hand (manual exit, which also resolves the duplicate alert), and the car is admitted at P01.

Press **Play the whole scenario**, or run the steps one at a time and use the administrator buttons. Tests: `python -m unittest -v tests.test_duplicate`; optional browser test: `node tests/e2e_duplicate.js`.

### 9b. Administrator-approved visitor RFID card for a refused duplicate (added)

After a duplicate refusal the lane is `BLOCKED` and the driver gets **no** way to ask for a temporary visitor RFID card. The administrator can approve one:

- **API:** `POST /api/admin/lanes/{lane_id}/allow-visitor-card` `{"note": "what you checked"}` (admin key). Works only while the refused car is still waiting at that lane after a `DUPLICATE_ENTRY` refusal; otherwise `409`. The lane then offers "Request a ticket" (which dispenses a temporary RFID card), the driver presents the card, and a visitor session is created for the card.
- **Dashboard:** `/admin` -> Open alerts -> **Allow visitor RFID card** on the `DUPLICATE_ENTRY` row.
- **One-shot and logged:** the approval is written on the alert; the registered tag stays tied to its original session (untouched); a second refusal needs a new approval.
- **Demo:** `/duplicate` -> scenario **C**.
- **Known gap (unchanged):** a driver who hides the tag so that it is not read at all is offered a visitor card by the normal no-tag path. The system cannot tell that from a visitor without a tag.

Tests: `python -m unittest -v tests.test_duplicate`.


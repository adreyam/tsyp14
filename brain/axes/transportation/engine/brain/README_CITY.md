# City Brain - Transportation layer (SUMO-RL + Smart Parking, one dashboard)

One program, one browser page:

* a **big live map** of a 6x6-junction city (36 traffic signals) with normal cars, trucks, **ambulances, police cars and fire trucks**;
* the **SUMO-RL signal controller keeps learning inside the running simulation**. It starts from your saved
  `runs\main` training and continues from `runs\city` every time you restart;
* **8 smart-parking lots** (142 spaces) with the same entrance/exit barriers, RFID, magnetic space sensors and LEDs as the
  smart-parking project, running through the real backend (`app/`);
* **16 drivers d1-d16** with mock habits (work, cafes, school, spouse's work, schedules); the ranking of the lots uses 6 criteria + the
  driver's **routine** (7th criterion);
* a **car dash screen** (click a driver car), an **administration screen**, and a **training results** section.

## Run it (Windows CMD)

```
cd path\to\city-brain
run_city.bat
```

The script finds SUMO, installs the missing Python packages and opens http://localhost:8000.
Manual start: `set SUMO_HOME=C:\Program Files (x86)\Eclipse\Sumo`, `pip install -r requirements_city.txt`, `python -m citybrain.server`.
Linux / WSL: `./run_city.sh`.  Another port: `set PORT=8010` before starting.

Ctrl+C in the window stops the city; the learned signal policy is saved (also every 2 training windows, with a backup copy).

## The dashboard

| Area | What you do |
|---|---|
| **Map** | Wheel = zoom, drag = move, **Fit**, **Follow car**. Signals: green/amber/red dot, a pulsing ring = emergency priority is active. Lots show every space (green free, amber reserved, red occupied, white frame = wrong space) and the two barriers (red closed / green open). Frequent places are diamonds labelled `d3-work`, `d3-cafe`, `d3-school`, `d3-spouse` ... Click a purple car to open its dash. |
| **Car dash** | Choose a destination among the driver's places, **Start trip**, **Request parking**: the backend returns the best 3 lots with a bar showing how much every criterion (incl. daily routine) contributed. **Go to ...** to choose. At the gate a **ticket with the assigned space** appears. Park in the assigned space or in another one (the administration is notified). When the administration asks to re-park, the message appears on the dash. |
| **Admin** | Per lot: lanes (barrier state, screen message), alerts, sessions, spaces, visitor cards. **Allow visitor card** (duplicate RFID), **Send re-park notification** (car parked in a wrong space), let the car keep the space, manual exit, open barrier, message a car, notices coming from the cars. Admin key: `dev-admin-key` (field at the top of the tab). |
| **Training** | Live curves per training window (600 simulated seconds = 1 episode): waiting time, stopped vehicles, speed, emergency waiting time, reward, epsilon, known situations. Buttons: switch **training / policy only**, **Save now**, **Run benchmark** (fixed-time lights vs the learned policy on identical traffic, in a separate process). |
| **Simulation** | Speed (1x to max), pause, traffic level, virtual clock (day / time / clock speed), number of ambulances / police / fire trucks, automatic trips on/off, probability of parking in a wrong space, scenarios. |

## Test scenarios

* **Duplicate RFID**: d6 and d13 own the same tag (`RFID_D06`). Press *Duplicate RFID* in the Simulation tab. d6 parks, d13 arrives at a gate and is refused
  (lane BLOCKED, alert `DUPLICATE_ENTRY`). In **Admin** press **Allow visitor card**, then on d13's dash press **Request a ticket**: d13 receives a temporary card + ticket,
  parks, and on exit returns the card.
* **Wrong space**: press *Wrong-space parking*; the car parks in another space, the administration gets an alert + a notice; press **Send re-park notification**; the car's dash shows the
  request; the driver presses **Move to the assigned space**.
* **Visitor without a tag**: *Start trip* with "no RFID tag" ticked.
* **Lot full**: choose a lot with 0 free spaces; the backend refuses and the car is sent to another lot.

## How it is built (everything important runs in the backend)

```
browser (dashboard.html)  --HTTP-->  citybrain/api.py + the smart-parking routers (FastAPI, SQLite)
                                           |
                       citybrain/engine.py (the only thread that touches SUMO/TraCI)
                         |- sumo_rl.SumoEnvironment + emergency preemption (ambulance > fire > police)
                         |- citybrain/rl.py        online Q-learning, one shared table for the 36 signals
                         |- citybrain/parkingsim.py cars, gates, ESP32 events/commands (the real entrance/space/exit flows)
                         '- citybrain/ranking7.py   routine criterion + CRITIC weighting
```

* The ranking, reservations, tickets, barrier logic, alerts and admin actions are the unchanged smart-parking services in `app/` (database: `data\citybrain.db`, reset at every start;
  `set CITY_KEEP_DB=1` to keep it). The map only shows what the backend publishes.
* Mock data: `world\world.json` (lots, drivers, places, schedules). Regenerate with `python -c "from citybrain import worldgen; worldgen.generate()"` (delete `world\world.json` first).
* Training files: `runs\city\checkpoint.pkl` (+ `.prev.pkl` backup), `history.jsonl`, `benchmarks.jsonl`. `runs\main` is only read, never modified.
  To restart the learning from the saved 2x2 training: delete the `runs\city` folder. To start from nothing: also remove `runs\main\checkpoint.pkl` (not recommended).
* Training "episodes" are windows of 600 simulated seconds inside a city that never restarts. Epsilon (exploration) decays at every window, starting at 0.15 because the table starts from your trained one.

## Useful endpoints

`GET /api/state`, `GET /api/world`, `GET /api/cars/{d1..d16}`, `POST /api/cars/{id}/offers|choose|request-ticket|park|leave|repark`,
`GET /api/training`, `POST /api/training/mode|save|benchmark`, admin routes under `/api/admin/...` (header `X-Admin-Key`).
Smart-parking documentation: `README_smart_parking.md`. Interactive API docs: http://localhost:8000/docs

## Connecting to the Twin and the Guardian (new)

* Interface contract (messages, keys, errors): `INTERFACE.md`. Partner addresses and keys: `config/brain.yaml` (environment variables override it).
* `mocks/` contains a stand-in Twin (9001) and Guardian (9002); start them with `run_mocks.bat`.
* The dashboard's **Timeline** tab shows Twin reading -> Guardian verdict -> Brain decision -> actuator action with timestamps, the verdicts in force, and one **Run** button per Test Book scenario.
* Test Book: `..\TESTBOOK.md`; one command per scenario: `python ..\scenarios\run.py T3` (logs in `..\logs`).
* What the Guardian's verdicts change: an untrusted/quarantined parking sensor is ignored and rolled back, a quarantined barrier is never opened by the Brain (the administrator can override), floods are rate-limited.

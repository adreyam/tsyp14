# TSYP14 · City Brain Convergence · Triplet 07

One smart city built by three teams: a **Twin** that simulates the city, a **Guardian** that checks whether the
data tells the truth, and a **City Brain** that detects incidents and acts through the Twin's actuators.

| Layer | Folder | Key question | What it does |
|---|---|---|---|
| Twin | [`twin/`](twin/) | What is happening in the city? | Godot 4 + SUMO city: 16 zones (A–P), 49 signalised junctions, traffic, air, flood, power, water, waste, seismic sensors; actuators (signals, power zones, valves, car parks, emergency units); device API over TCP |
| Guardian | [`guardian/`](guardian/) | Is this data telling the truth? | Trust verdicts on every reading. Until the Guardian team connects, the Brain runs its own Guardian stand-in |
| City Brain | [`brain/`](brain/) | What should we do? | One orchestrator and four axis agents (emergency, transportation, pollution, energy): classify → trust gate → detect → decide → arbitrate → act → audit |

## One-command start

Linux x86_64 with a Vulkan GPU for the Twin (see [twin/README.md](twin/README.md) for its `runtime/` folder),
Python 3.10+ for the Brain.

```bash
./start_city.sh          # the whole city, live: Twin + City Brain + the three axis engines + control center
./start_city.sh --mock   # without Godot: the Brain uses its Twin and Guardian stand-ins
docker compose up        # the Brain and the control center in containers (mock by default)
```

- Control center (the live timeline screen, R3): http://127.0.0.1:8765/#/city
- City Brain API: http://127.0.0.1:8500/v1/status

## Run a scenario (R2)

Every Test Book scenario starts with one command, or the **Run** button on the City map page, and leaves a log.

```bash
python3 scenarios/run.py --list        # the 17 scenarios
python3 scenarios/run.py SY05          # one scenario, Brain alone with its mocks
python3 scenarios/run.py SY05 --server # the same on the running city (shown live on the control center)
python3 scenarios/run.py --all         # every scenario, PASS/FAIL table at the end
```

Each run writes two files in [`logs/`](logs/):

- `logs/<ID>-<UTC time>.jsonl`: every step of every decision (reading, Guardian verdict, decision, request between
  axes, conflict, command, Twin answer), with timestamps and a `trace_id`, ending with the result of each check.
- `logs/<ID>-<UTC time>.txt`: the same run in plain words, for a quick read: the result, every check, what each axis
  did and why, each command with the Twin's answer.

## How the three rules are met

| Rule | Where |
|---|---|
| R1 · every layer runs on its own, with mocks and an Interface Card | Brain: `brain/mocks/` (Twin and Guardian stand-ins), [brain/INTERFACE.md](brain/INTERFACE.md), [brain/README.md](brain/README.md). Twin: runs alone with its own control room, [twin/INTERFACE.md](twin/INTERFACE.md). Guardian: [guardian/INTERFACE.md](guardian/INTERFACE.md) (what the Brain expects) |
| R2 · one trigger, one log per scenario | [`scenarios/`](scenarios/) (one script per scenario + `run.py`), [`logs/`](logs/), [TESTBOOK.md](TESTBOOK.md) |
| R3 · a live timeline across the three layers | Control center, City map page: reading → Guardian verdict → Brain decision → actuator command → Twin answer, with timestamps |

Partner addresses and settings are in configuration, never in code: `brain/config/brain.yaml` (or environment
variables `CITY_BRAIN_SOURCE`, `CITY_SIM_API_PORT`, `BRAIN_TOKEN`).

## Repository structure

```
.
├── README.md             this file: overview, team members, one-command start
├── INTEGRATION.md        how the 3 layers connect: diagram, protocol, one scenario end to end
├── TESTBOOK.md           the 17 test scenarios and their expected outputs (report part 4)
├── docker-compose.yml    the Brain and the control center in containers
├── start_city.sh         starts the whole city with one command
├── .github/CODEOWNERS    each folder owned by its team
├── scenarios/            one trigger script per Test Book scenario + run.py (R2)
├── logs/                 one log file per scenario run (R2)
├── twin/                 Twin team: README.md, INTERFACE.md, scripts/ (src), scenes/, tests/
├── brain/                City Brain team: README.md, INTERFACE.md, src/, mocks/ (Twin + Guardian), tests/,
│                         axes/<axis>/{agent,engine}, control_center/, config/
├── guardian/             Guardian team: README.md, INTERFACE.md
└── docs/                 report.pdf, pitch.pdf, diagrams/
```

## Tests

```bash
python3 -m pytest brain/tests brain/control_center/tests -q   # City Brain and control center
python3 scenarios/run.py --all                                # the Test Book on the mocks (17/17 PASS)
cd twin && ./test_headless.sh                                 # Twin: traffic, Godot and MCP tests
```

## Documents

- [INTEGRATION.md](INTEGRATION.md): architecture, shared decisions, EM02 traced end to end, partner failures.
- [TESTBOOK.md](TESTBOOK.md): every scenario with ID, name, layers involved, description, trigger, expected
  Twin / Guardian / Brain output and how to verify.
- [docs/](docs/): the report (20 pages max), the pitch deck and the diagrams.

## Known limitations

- No partner Guardian is connected yet: the Brain uses its Guardian stand-in (`guardian.mode: mock`);
  `guardian.mode: http` is ready for a real one ([guardian/INTERFACE.md](guardian/INTERFACE.md)).
- The Twin's device API listens on 127.0.0.1 without authentication, so the Brain runs on the Twin's machine.


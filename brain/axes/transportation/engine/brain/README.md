# City Brain layer (transportation + parking)

What it does, how to use the dashboard and how it is built: **`README_CITY.md`**.
How it talks to the Twin and the Guardian (messages, keys, errors, configuration): **`INTERFACE.md`**.
Smart-parking details (ESP32 gateways, flows, administration API): `README_smart_parking.md`.

## Run this layer alone (R1)

```
run_city.bat            (Windows CMD)  -> dashboard on http://localhost:8000
run_mocks.bat           -> stand-in Twin (9001) and Guardian (9002): the Brain then exchanges real messages with them
```
Without `run_mocks.bat` the Brain still works on its own simulation; the messages it sends to the missing partners fail quietly (logged on the timeline) and never block the city.
Partner addresses and keys: `config/brain.yaml` (or environment variables, see `INTERFACE.md` section 1).

## Tests
```
python -m pytest tests/test_bridge.py          # interface validation, trust handling, flood protection (no SUMO needed)
python ..\scenarios\run.py all                 # the Test Book, with the city running (see ..\TESTBOOK.md)
```

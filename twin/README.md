# City Ground Zero

A live smart city you can watch and control. A Godot 4 city of roads, buildings, props and 200 citizens runs on top of a
SUMO traffic simulation. Citizens work, take breaks, throw their rubbish in bins and go home, using real motion-capture
animation. Every sensor and actuator in the city (traffic, air, noise, weather, bins, signals, power, water, lights) can be
read and commanded live from a control room window or from an MCP terminal.

## Set up

You need a Linux x86_64 PC with a Vulkan-capable GPU. Everything else lives in the `runtime/` folder next to this file.
It is not stored in git because it is large (about 650 MB), so put these in it:

```
runtime/
├── linux-x86_64/godot              Godot 4.7.2 (stable, Linux x86_64) binary
└── sumo/usr/...                    SUMO 1.25.0 with its TraCI tools, shared libraries and Python 3.14
                                    (bin/python3.14, bin/sumo, share/sumo)
```

Then import the project's assets once:

```bash
./runtime/linux-x86_64/godot --headless --editor --path . --quit
```

Check that everything is in place:

```bash
./test_headless.sh
```

## Run the simulation

```bash
./run_control_room.sh
```

The city is divided into 16 zones, A (north-west) to P (south-east), shown on the ground and used everywhere (sensors, buildings, car parks, earthquakes). This starts SUMO, the citizen planner and the city window (the viewport plus the control room: time, weather, incidents,
assets, utilities, citizens and sensors). Once the city is live, the terminal turns into an **MCP terminal**:

| Key | What it does |
|---|---|
| `↑` `↓` | browse the functions, each as a ready-to-run call |
| typing | the matching function appears faintly; `◀` `▶` switch between matching functions, `Tab` pastes it |
| `Enter` | run the line |
| `q` or `Ctrl+C` | stop a live stream |

Try these (sensor functions stream live updates until you stop them):

```
read_sensor(id="WGT-T001")                  a street bin's weight, live
waste_bin_report()                          every bin, fullest first
sensor_summary(family="air")                air quality across the city
set_power_switch(id="SWT-01", state="open") cut a feeder: the readings show before and after
set_street_light(id="LGT-001", mode="on")
set_weather(weather="storm")
trigger_earthquake(zone="F", magnitude=6.5)    shake zone F: buildings collapse, the seismic sensors record it
clear_earthquakes()                            everything stands again
list_parking_lots()                            smart car parks: "05/12" occupancy, barrier state, refused cars
```

`examples` lists more, `tools` lists every function, `exit` closes the terminal and the city.
Other MCP clients can use the same server: `CITY_SIM_API_PORT=<port> python3 tools/mcp/city_mcp_server.py`.

**Web dashboard.** Add `--dashboard` to open a live web dashboard in parallel (`./run_control_room.sh --dashboard`; `--dashboard=9000` for another port, `--no-browser` to not open it). It shows and controls what the Godot control room does: clock, weather and speed; the live city map; every sensor and actuator with detailed rolling graphs; zones, incidents, earthquakes and replay; utilities, parking, waste, fleets and citizens; cameras, following and viewport highlights. Its Payload page shows one changing raw JSON object as a tree or text. The browser is strictly an MCP client: one whole-city MCP stream updates at the 100 ms MCP tick, and every control is an MCP tool call.

Other ways to start it:

- `./run_city.sh` opens the plain city without the control room.
- `CITY_NO_CLI=1 ./run_control_room.sh` opens the control room window without the terminal.

## More

- [docs/DETAILS.md](docs/DETAILS.md): the full notes on incidents, utilities, citizens, props, sensors and the MCP interface.
- [SOCIETY_ARCHITECTURE.md](SOCIETY_ARCHITECTURE.md) and [docs/INCIDENT_SYSTEM_ARCHITECTURE.md](docs/INCIDENT_SYSTEM_ARCHITECTURE.md): how the society and incident engines work.
- Tests: `./test_headless.sh` runs the traffic, Godot and MCP tests. The MCP tests alone: `python3 -m unittest discover -s tests/mcp`.
- People and animation: Microsoft Rocketbox avatars and motion capture (MIT). Other model and asset licenses are listed in `docs/DETAILS.md`.

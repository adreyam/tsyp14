#!/usr/bin/env python3
"""Interactive MCP terminal for the City Ground Zero simulation.

Features:
- MCP client over stdio.
- Interactive terminal.
- Live sensor readings.
- Actuator control.
- Event-based JSON logging.
- Separate JSON files for each Smart City subsystem.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import select
import shlex
import subprocess
import sys
import threading
import time

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_form import FormBook, LineEditor  # noqa: E402


HERE = Path(__file__).resolve().parent
CLIENT_PROTOCOL = "2025-06-18"

BOLD, DIM, GREEN, RED, CYAN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[32m",
    "\033[31m", "\033[36m", "\033[33m", "\033[0m"
)


# ================================================================================================================
# EVENT-BASED JSON LOGGER
# ================================================================================================================

# Project root:
# TSYP14-Smart-City-Challenge-main/

PROJECT_ROOT = HERE.parents[1]

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_LOCK = threading.Lock()


# Each function is associated with a specific JSON file.
EVENT_FILES = {

    # General city information
    "city_status": "city_status.json",

    # Environmental monitoring
    "read_sensor": "environment.json",
    "read_sensors": "environment.json",
    "sensor_summary": "environment.json",
    "watch_sensor": "environment.json",

    # Waste management
    "waste_bin_report": "waste_management.json",
    "empty_waste_bin": "waste_management.json",

    # Traffic management
    "read_zone": "traffic_management.json",
    "list_vehicles": "traffic_management.json",
    "list_parking_lots": "traffic_management.json",

    # Energy management
    "set_power_switch": "energy_management.json",

    # Water management
    "set_valve": "water_management.json",

    # Actuator events
    "set_street_light": "actuator_events.json",
    "set_signal_phase": "actuator_events.json",

    # Weather and simulation time
    "set_weather": "environment.json",
    "set_time_of_day": "city_status.json",

    # Incidents
    "trigger_earthquake": "incidents.json",
    "clear_earthquakes": "incidents.json",
    "trigger_incident": "incidents.json",

}


def log_mcp_event(
    tool: str,
    arguments: dict,
    response: Any = None,
    status: str = "success",
    error: str | None = None,
    duration_ms: float = 0
) -> None:
    """
    Save an MCP execution as a timestamped JSON event.

    Each subsystem has its own JSON file.
    Existing events are preserved.
    """

    filename = EVENT_FILES.get(
        tool,
        "other_events.json"
    )

    filepath = LOG_DIR / filename

    event = {

        "timestamp": datetime.now(timezone.utc).isoformat(),

        "event": tool,

        "status": status,

        "duration_ms": round(duration_ms, 2),

        "arguments": arguments,

        "data": response,

        "error": error

    }

    with LOG_LOCK:

        events = []

        if filepath.exists():

            try:

                with open(
                    filepath,
                    "r",
                    encoding="utf-8"
                ) as file:

                    events = json.load(file)

                if not isinstance(events, list):
                    events = []

            except (json.JSONDecodeError, OSError):

                events = []

        events.append(event)

        with open(
            filepath,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                events,
                file,
                indent=4,
                ensure_ascii=False,
                default=str
            )


# ================================================================================================================
# EXAMPLES
# ================================================================================================================

EXAMPLES = [

    ("What is the city doing right now?", "city_status()"),

    ("List the waste-bin weight sensors",
     'list_sensors(family="waste", limit=5)'),

    ("Watch one bin's weight live",
     'read_sensor(id="WGT-T001")'),

    ("Read it once instead of streaming",
     'read_sensor(id="WGT-T001", once=True)'),

    ("Capture 15 s of a bin with a trend line",
     'watch_sensor(id="WGT-T001", metric="bin_weight", duration_s=15)'),

    ("The fullest bins", "waste_bin_report(limit=5)"),

    ("Air quality across the city",
     'sensor_summary(family="air")'),

    ("Two sensors in the same frame",
     'read_sensors(ids=["AIR-01", "NOISE-01"])'),

    ("List the power switches",
     'list_actuators(family="power_switch")'),

    ("What can a switch do?",
     'list_commands(id="SWT-01")'),

    ("Open a power switch cabinet",
     'set_power_switch(id="SWT-01", state="open")'),

    ("... and close it again",
     'set_power_switch(id="SWT-01", state="close")'),

    ("Switch a street light on in the daytime",
     'set_street_light(id="LGT-001", mode="on")'),

    ("Return it to the adaptive schedule",
     'set_street_light(id="LGT-001", mode="auto")'),

    ("Close a water valve",
     'set_valve(id="VLV-01", state="close")'),

    ("Make it rain",
     'set_weather(weather="rain")'),

    ("Jump to 22:30",
     'set_time_of_day(time="22:30")'),

    ("Force every signal to red",
     'set_signal_phase(phase="all_red")'),

    ("... and return to normal cycling",
     'set_signal_phase(phase="reset_cycle")'),

    ("List the service zones",
     'list_sensors(family="service_zone", limit=3)'),

    ("Empty a full bin",
     'empty_waste_bin(id="WGT-C001")'),

]


CLEAR = "\033[3J\033[2J\033[H"


FAMILY_GUIDE = [

    ("waste", "bin weight kg, fill %, items thrown in"),

    ("air", "PM2.5, PM10, NO2, vehicle smoke"),

    ("traffic", "vehicles, speed, road occupancy"),

    ("weather", "wind, rain, temperature"),

    ("power_switch", "feeder switch: open/close it"),

    ("lighting", "street light: on/off/auto/dim"),

    ("water_valve", "water valve: open/close"),

    ("signal", "traffic signal state"),

    ("reservoir", "water level and demand"),

    ("service_zone", "cut/restore power or water"),

]


def _error_text(result: dict) -> str:

    error = (
        result.get("structuredContent") or {}
    ).get("error")

    return (
        f"{error['code']}: {error['message']}"
        if isinstance(error, dict)
        else result["content"][0]["text"]
    )


class MCPError(Exception):
    pass


# ================================================================================================================
# MCP CLIENT
# ================================================================================================================

class MCPClient:

    """Minimal MCP client over a stdio subprocess."""

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        trace: Callable[[str, dict], None] | None = None
    ):

        self.process = subprocess.Popen(

            command,

            stdin=subprocess.PIPE,

            stdout=subprocess.PIPE,

            stderr=subprocess.PIPE,

            text=True,

            bufsize=1,

            env=env,

            start_new_session=True

        )

        self._next_id = 0

        self._write_lock = threading.Lock()

        self.trace = trace or (
            lambda direction, message: None
        )

        self.server_info: dict = {}

        self.protocol = ""

        self.tools: list[dict] = []

        self._stderr: list[str] = []

        threading.Thread(
            target=self._drain_stderr,
            daemon=True
        ).start()

    def _drain_stderr(self):

        assert self.process.stderr is not None

        for line in self.process.stderr:

            self._stderr.append(line.rstrip())

    def _send(self, message: dict):

        assert self.process.stdin is not None

        self.trace("->", message)

        with self._write_lock:

            self.process.stdin.write(
                json.dumps(
                    message,
                    separators=(",", ":")
                ) + "\n"
            )

            self.process.stdin.flush()

    def _read(self):

        assert self.process.stdout is not None

        line = self.process.stdout.readline()

        if not line:

            raise MCPError(
                "the MCP server stopped"
                + (
                    ": " + self._stderr[-1]
                    if self._stderr
                    else ""
                )
            )

        message = json.loads(line)

        self.trace("<-", message)

        return message

    def request(
        self,
        method: str,
        params: dict | None = None,
        on_notification: Callable[[dict], None] | None = None
    ):

        self._next_id += 1

        request_id = self._next_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method
        }

        if params is not None:
            message["params"] = params

        self._send(message)

        try:

            return self._await(
                request_id,
                on_notification
            )

        except KeyboardInterrupt:

            self.cancel(
                request_id,
                "interrupted from the terminal"
            )

            return self._await(
                request_id,
                on_notification
            )

    def cancel(
        self,
        request_id: int,
        reason: str = ""
    ):

        self.notify(
            "notifications/cancelled",
            {
                "requestId": request_id,
                "reason": reason
            }
        )

    @property
    def last_request_id(self):

        return self._next_id

    def _await(
        self,
        request_id,
        on_notification
    ):

        while True:

            message = self._read()

            if (
                "id" not in message
                or message.get("id") is None
                and "method" in message
            ):

                if on_notification:
                    on_notification(message)

                continue

            if message.get("id") != request_id:
                continue

            if "error" in message:

                raise MCPError(
                    f"{message['error'].get('message')} "
                    f"(code {message['error'].get('code')})"
                )

            return message.get("result")

    def notify(
        self,
        method: str,
        params: dict | None = None
    ):

        message = {
            "jsonrpc": "2.0",
            "method": method
        }

        if params is not None:
            message["params"] = params

        self._send(message)

    def initialize(self):

        result = self.request(

            "initialize",

            {

                "protocolVersion": CLIENT_PROTOCOL,

                "capabilities": {},

                "clientInfo": {

                    "name": "city-cli",

                    "version": "1.0.0"

                }

            }

        )

        self.server_info = result.get(
            "serverInfo",
            {}
        )

        self.protocol = result.get(
            "protocolVersion",
            ""
        )

        self.notify(
            "notifications/initialized"
        )

        self.tools = self.request(
            "tools/list"
        ).get("tools", [])

        return result

    def call_tool(
        self,
        name: str,
        arguments: dict,
        on_progress=None
    ):

        params = {
            "name": name,
            "arguments": arguments
        }

        if on_progress is not None:

            params["_meta"] = {

                "progressToken": f"cli-{self._next_id + 1}"

            }

        return self.request(
            "tools/call",
            params,
            on_notification=on_progress
        )

    def raw(self, message: dict):

        self._send(message)

        if "id" not in message:
            return []

        out = []

        while True:

            reply = self._read()

            out.append(reply)

            if reply.get("id") == message["id"]:
                return out

    def close(self):

        try:

            if self.process.stdin:
                self.process.stdin.close()

            self.process.wait(timeout=3)

        except Exception:

            self.process.kill()

        for stream in (
            self.process.stdout,
            self.process.stderr
        ):

            if stream:
                stream.close()


# ================================================================================================================
# PARSING
# ================================================================================================================

def parse_value(text: str):

    try:
        return json.loads(text)

    except json.JSONDecodeError:

        if text.startswith("[") and text.endswith("]"):

            return [
                item.strip().strip("\"'")
                for item in text[1:-1].split(",")
                if item.strip()
            ]

        return text


def parse_call(line: str):

    match = re.fullmatch(
        r"\s*([A-Za-z_]\w*)\s*\((.*)\)\s*",
        line,
        re.S
    )

    if not match:
        return None

    try:

        node = ast.parse(
            f"f({match.group(2)})",
            mode="eval"
        ).body

        assert isinstance(node, ast.Call)

        positional = [
            ast.literal_eval(a)
            for a in node.args
        ]

        keywords = {

            k.arg: ast.literal_eval(k.value)

            for k in node.keywords

            if k.arg

        }

    except (
        SyntaxError,
        ValueError,
        AssertionError
    ) as error:

        raise MCPError(
            f"cannot read the arguments of "
            f"{match.group(1)}(...): {error}"
        )

    return (
        match.group(1),
        positional,
        keywords
    )


def parse_arguments(
    tokens: list[str],
    schema: dict | None
):

    if len(tokens) == 1 and tokens[0].startswith("{"):

        return json.loads(tokens[0])

    arguments = {}

    positional = []

    for token in tokens:

        if (
            "=" in token
            and not token.startswith(("{", "[", '"'))
        ):

            key, value = token.split("=", 1)

            arguments[key] = parse_value(value)

        else:

            positional.append(token)

    required = (
        schema or {}
    ).get("required", [])

    for key, value in zip(
        [
            k for k in required
            if k not in arguments
        ],
        positional
    ):

        arguments[key] = (
            parse_value(value)
            if key != "id"
            else value
        )

    return arguments


# ================================================================================================================
# DISPLAY FUNCTIONS
# ================================================================================================================

def table(rows, header):

    widths = [

        max(
            len(str(row[i]))
            for row in [header] + rows
        )

        for i in range(len(header))

    ]

    lines = [

        "  ".join(
            str(cell).ljust(widths[i])
            for i, cell in enumerate(header)
        )

    ]

    lines.append(
        "  ".join("-" * w for w in widths)
    )

    for row in rows:

        lines.append(
            "  ".join(
                str(cell).ljust(widths[i])
                for i, cell in enumerate(row)
            )
        )

    return "\n".join(lines)


def sparkline(values):

    bars = "▁▂▃▄▅▆▇█"

    low, high = min(values), max(values)

    if high - low < 1e-9:
        return bars[0] * len(values)

    return "".join(

        bars[
            min(
                7,
                int(
                    (v - low)
                    / (high - low)
                    * 7.999
                )
            )
        ]

        for v in values

    )


def render(tool: str, data: Any):

    if not isinstance(data, dict):

        return json.dumps(
            data,
            indent=2
        )

    if tool == "city_status":

        families = ", ".join(

            f"{k} {v}"

            for k, v in sorted(
                data["families"].items()
            )

        )

        return (

            f"{BOLD}{data['name']}{RESET}  "

            f"day {data['day']}  "

            f"{data['clock']}  "

            f"({data['period']})  "

            f"weather {data['weather']}  "

            f"speed {data['speed']:g}x  "

            f"{'PAUSED' if data['paused'] else 'running'}\n"

            f"{data['device_count']} devices: {families}"

        )

    if tool in ("list_sensors", "list_actuators"):

        rows = [

            [
                d["id"],
                d["family"],
                d["name"],
                d["type"],
                ",".join(d["commands"]) or "-"
            ]

            for d in data["devices"]

        ]

        return (

            f"{data['total']} devices "
            f"(showing {data['count']} from {data['offset']})\n"

            + table(
                rows,
                ["id", "family", "name", "type", "commands"]
            )

        )

    if tool == "read_sensor":

        rows = [

            [m, f"{r['value']:g}", r["unit"]]

            for m, r in data["readings"].items()

        ]

        extra = (

            f"\nstate: {json.dumps(data['state'])}"

            if data.get("state")

            else ""

        )

        return (

            f"{BOLD}{data['id']}{RESET}  "

            f"{data['name']}  "

            f"({data['type']})  "

            f"at {data['sampled_at']['clock']}  "

            f"frame {data['sampled_at']['frame']}\n"

            + table(
                rows,
                ["metric", "value", "unit"]
            )

            + extra

        )

    if tool == "read_sensors":

        out = []

        for d in data["devices"]:

            out.append(

                f"{BOLD}{d['id']}{RESET}  "

                + "  ".join(

                    f"{m}={r['value']:g}{r['unit']}"

                    for m, r in d["readings"].items()

                )

            )

        if data["unknown_ids"]:

            out.append(

                f"{RED}unknown: "
                f"{', '.join(data['unknown_ids'])}{RESET}"

            )

        return "\n".join(out)

    if tool == "waste_bin_report":

        rows = [

            [
                b["id"],
                b["kind"],
                f"{b['weight_kg']:.2f}",
                f"{b['fill_percent']:.1f}",
                b["deposits"]
            ]

            for b in data["bins"]

        ]

        return (

            f"{data['matching']} bins, "
            f"{data['total_weight_kg']} kg in total\n"

            + table(
                rows,
                ["id", "kind", "kg", "fill %", "deposits"]
            )

        )

    if tool == "watch_sensor":

        values = [
            s["value"]
            for s in data["samples"]
        ]

        return (

            f"{data['id']} {data['metric']} "
            f"({data['unit']})  "

            f"{sparkline(values)}  "

            f"min {data['min']:g}  "

            f"max {data['max']:g}  "

            f"change {data['change']:+g}  "

            f"({len(values)} samples)"

        )

    if tool == "sensor_summary":

        rows = []

        for family, metrics in data["families"].items():

            for metric, s in metrics.items():

                rows.append([

                    family,
                    metric,
                    s["devices"],
                    s["min"],
                    s["mean"],
                    s["max"],
                    s["unit"]

                ])

        return table(

            rows,

            [
                "family",
                "metric",
                "n",
                "min",
                "mean",
                "max",
                "unit"
            ]

        )

    if "applied" in data:

        text = f"{GREEN}{data['applied']}{RESET}"

        before = data.get("before")

        after = data.get("after")

        if (

            isinstance(before, dict)

            and isinstance(after, dict)

            and before

            and "value" in next(iter(before.values()), {})

        ):

            text += "\n" + "\n".join(

                f"  {m}: {before[m]['value']:g} "
                f"-> {after[m]['value']:g} {after[m]['unit']}"

                for m in after

                if m in before

            )

        return text

    return json.dumps(
        data,
        indent=2,
        ensure_ascii=False
    )


# ================================================================================================================
# TERMINAL
# ================================================================================================================

class Terminal:

    def __init__(
        self,
        client: MCPClient,
        show_protocol=False,
        as_json=False
    ):

        self.client = client

        self.show_protocol = show_protocol

        self.as_json = as_json

        self.interactive = False

        client.trace = self._trace

    def _trace(self, direction, message):

        if self.show_protocol:

            colour = (
                CYAN if direction == "->"
                else YELLOW
            )

            text = json.dumps(
                message,
                separators=(",", ":")
            )

            print(
                f"{colour}{direction} "
                f"{text[:600]}"
                f"{'…' if len(text) > 600 else ''}{RESET}"
            )

    def tool_schema(self, name):

        for tool in self.client.tools:

            if tool["name"] == name:

                return tool.get("inputSchema")

        return None

    def run_line(self, line):

        line = line.strip()

        if not line or line.startswith("#"):
            return True

        try:

            called = parse_call(line)

        except MCPError as error:

            print(f"{RED}{error}{RESET}")

            return True

        if called is not None:

            return self.run_function(*called)

        try:

            tokens = shlex.split(line)

        except ValueError as error:

            print(f"{RED}cannot parse: {error}{RESET}")

            return True

        command, rest = tokens[0], tokens[1:]

        names = [
            t["name"]
            for t in self.client.tools
        ]

        try:

            if command in ("exit", "quit", "q"):

                return False

            elif command == "help":

                self.help()

            elif command == "tools":

                self.list_tools()

            elif command in ("describe", "desc") and rest:

                self.describe(rest[0])

            elif command == "examples":

                self.examples()

            elif command == "example" and rest and rest[0].isdigit():

                self.run_example(int(rest[0]))

            elif command == "protocol":

                self.show_protocol = (
                    not rest or rest[0] == "on"
                )

                print(
                    f"protocol trace "
                    f"{'on' if self.show_protocol else 'off'}"
                )

            elif command == "json":

                self.as_json = (
                    not rest or rest[0] == "on"
                )

                print(
                    f"raw JSON output "
                    f"{'on' if self.as_json else 'off'}"
                )

            elif command == "resources":

                print(
                    json.dumps(
                        self.client.request("resources/list"),
                        indent=2
                    )
                )

            elif command == "read" and rest:

                print(
                    json.dumps(
                        self.client.request(
                            "resources/read",
                            {"uri": rest[0]}
                        ),
                        indent=2
                    )
                )

            elif command == "raw":

                for reply in self.client.raw(
                    json.loads(line.split(None, 1)[1])
                ):

                    print(
                        json.dumps(
                            reply,
                            indent=2
                        )
                    )

            elif command == "call" and rest:

                self.call(
                    rest[0],
                    rest[1:]
                )

            elif command in names:

                self.call(
                    command,
                    rest
                )

            else:

                print(
                    f"{RED}unknown command "
                    f"'{command}'. Type help.{RESET}"
                )

        except (
            MCPError,
            json.JSONDecodeError,
            IndexError
        ) as error:

            print(f"{RED}error: {error}{RESET}")

        return True

    def run_function(
        self,
        name,
        positional,
        keywords
    ):

        schema = self.tool_schema(name)

        if schema is None:

            print(
                f"{RED}unknown function "
                f"'{name}'. Type tools to see them.{RESET}"
            )

            return True

        required = [

            k

            for k in schema.get("required", [])

            if k not in keywords

        ]

        arguments = dict(keywords)

        for key, value in zip(
            required,
            positional
        ):

            arguments[key] = value

        try:

            self.call(
                name,
                [],
                arguments
            )

        except (
            MCPError,
            json.JSONDecodeError
        ) as error:

            print(f"{RED}error: {error}{RESET}")

        return True

    # ============================================================================================================
    # MCP CALL + EVENT LOGGING
    # ============================================================================================================

    def call(
        self,
        name,
        tokens,
        preset=None
    ):

        schema = self.tool_schema(name)

        if schema is None:

            print(
                f"{RED}unknown tool '{name}'. Type tools.{RESET}"
            )

            return

        arguments = (

            dict(preset)

            if preset is not None

            else parse_arguments(tokens, schema)

        )

        tty = sys.stdout.isatty()

        state = {
            "lines": 0,
            "count": 0,
            "last": None
        }

        started = time.monotonic()

        def progress(message):

            if message.get("method") != "notifications/progress":
                return

            try:

                update = json.loads(
                    message["params"]["message"]
                )

            except (
                KeyError,
                json.JSONDecodeError
            ):

                print(
                    f"{DIM}  … "
                    f"{message['params'].get('message', '')}{RESET}"
                )

                return

            if "data" in update:

                if state["count"] == 0:

                    print(
                        f"{BOLD}● live {name}{RESET}  "
                        f"{DIM}press q or Ctrl+C to stop{RESET}"
                    )

                self.show_update(
                    update.get("tool", name),
                    update["data"],
                    state,
                    tty
                )

            else:

                print(
                    f"{DIM}  … "
                    f"{message['params'].get('message', '')}{RESET}"
                )

        watching = (
            self.interactive
            and sys.stdin.isatty()
        )

        watcher = (

            KeyWatcher(
                self.client,
                self.client.last_request_id + 1
            )

            if watching

            else None

        )

        try:

            result = self.client.call_tool(
                name,
                arguments,
                on_progress=progress if watching else None
            )

            elapsed = (
                time.monotonic() - started
            ) * 1000.0

            # Handle MCP errors
            if result.get("isError"):

                error = _error_text(result)

                log_mcp_event(
                    tool=name,
                    arguments=arguments,
                    response=result,
                    duration_ms=elapsed,
                    status="error",
                    error=error
                )

                print(f"{RED}{error}{RESET}")

                return

            # Extract response
            data = result.get("structuredContent")

            if data is not None and set(data) == {"result"}:

                data = data["result"]

            if data is None:

                data = json.loads(
                    result["content"][0]["text"]
                )

            # Save event in the appropriate JSON file
            log_mcp_event(
                tool=name,
                arguments=arguments,
                response=data,
                duration_ms=elapsed,
                status="success"
            )

            # Handle streaming calls
            if (

                isinstance(data, dict)

                and "samples" in data

                and "updates" in data

            ):

                if state["count"] == 0:

                    for sample in data["samples"]:

                        self.show_update(
                            data["tool"],
                            sample["data"],
                            state,
                            False
                        )

                print(
                    f"{DIM}[{name} · "
                    f"{data['updates']} live updates · "
                    f"{'stopped by you' if data['cancelled'] else 'finished'} · "
                    f"via MCP {self.client.protocol}]{RESET}"
                )

                return

            print(

                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                )

                if self.as_json

                else render(name, data)

            )

            print(
                f"{DIM}[{name} · {elapsed:.0f} ms · "
                f"via MCP {self.client.protocol}]{RESET}"
            )

        except Exception as error:

            elapsed = (
                time.monotonic() - started
            ) * 1000.0

            log_mcp_event(
                tool=name,
                arguments=arguments,
                duration_ms=elapsed,
                status="exception",
                error=str(error)
            )

            raise

        finally:

            if watcher:
                watcher.stop()

    # ============================================================================================================
    # LIVE UPDATE
    # ============================================================================================================

    def show_update(
        self,
        name,
        data,
        state,
        tty
    ):

        state["count"] += 1

        if self.as_json:

            print(
                json.dumps(
                    data,
                    ensure_ascii=False
                )
            )

            return

        if name == "read_sensor":

            previous = state["last"] or {}

            parts = []

            for metric, reading in data["readings"].items():

                value = reading["value"]

                old = previous.get(metric)

                arrow, colour = ("", "")

                if old is not None and value > old:

                    arrow, colour = "▲", GREEN

                elif old is not None and value < old:

                    arrow, colour = "▼", YELLOW

                parts.append(

                    f"{metric} {colour}{value:g}{arrow}{RESET} "
                    f"{reading['unit']}"

                )

            state["last"] = {

                m: r["value"]

                for m, r in data["readings"].items()

            }

            print(

                f"{DIM}{data['sampled_at']['clock']}{RESET}  "

                f"{BOLD}{data['id']}{RESET}  "

                + "  ·  ".join(parts)

            )

            return

        text = (

            render(name, data)

            + f"\n{DIM}● live · update #{state['count']} "
            f"· press q or Ctrl+C to stop{RESET}"

        )

        if tty and state["lines"]:

            sys.stdout.write(
                f"\033[{state['lines']}A\033[J"
            )

        print(text)

        state["lines"] = text.count("\n") + 1

        sys.stdout.flush()

    # ============================================================================================================
    # SENSOR DISCOVERY
    # ============================================================================================================

    def discover_samples(self):

        if getattr(self, "_samples", None):

            return self._samples

        for attempt in range(6):

            samples = self._sample_ids()

            if (

                any(
                    i.startswith("WGT-T")
                    for i in samples.get("waste", [])
                )

                or not samples.get("waste")

            ):

                break

            time.sleep(0.5)

        self._samples = samples

        return samples

    def _sample_ids(self):

        samples = {}

        for family in (

            "waste",
            "air",
            "traffic",
            "noise",
            "weather",
            "power_meter",
            "power_switch",
            "lighting",
            "water_valve",
            "service_zone"

        ):

            try:

                devices = self.client.call_tool(

                    "list_sensors",

                    {

                        "family": family,

                        "limit": (
                            200
                            if family == "waste"
                            else 6
                        )

                    }

                )["structuredContent"]["devices"]

            except (
                MCPError,
                KeyError
            ):

                continue

            ids = [
                d["id"]
                for d in devices
            ]

            if family == "waste":

                ids = (

                    [
                        i for i in ids
                        if i.startswith("WGT-T")
                    ][:4]

                    +

                    [
                        i for i in ids
                        if i.startswith("WGT-C")
                    ][:2]

                    or ids[:4]

                )

            samples[family] = ids[:6]

        return samples

    # ============================================================================================================
    # HEADER
    # ============================================================================================================

    def header(self):

        client = self.client

        lines = [

            f"{BOLD}CITY GROUND ZERO · MCP TERMINAL{RESET}"

        ]

        try:

            status = client.call_tool(
                "city_status",
                {}
            )["structuredContent"]

            lines.append(

                f"{GREEN}● live{RESET}  "

                f"day {status['day']}  "

                f"{status['clock']}  "

                f"{status['weather']}  ·  "

                f"{status['device_count']} devices"

            )

        except (
            MCPError,
            KeyError
        ):

            lines.append(
                f"{YELLOW}● city not reachable{RESET}"
            )

        dashboard = os.environ.get(
            "CITY_DASHBOARD_URL",
            ""
        )

        if dashboard:

            lines.append(

                f"{GREEN}● dashboard{RESET}  "
                f"{dashboard}   "
                f"{DIM}(an MCP client of this city){RESET}"

            )

        lines += [

            "",

            f"Press {BOLD}↑ ↓{RESET} to browse the commands, "
            f"or start typing: the rest is suggested "
            f"({BOLD}◀ ▶{RESET} other functions, "
            f"{BOLD}Tab{RESET} pastes it).",

            f"{BOLD}Enter{RESET} runs the line. "
            f"The sensor functions stream live by themselves; "
            f"stop with {BOLD}q{RESET} or "
            f"{BOLD}Ctrl+C{RESET} (or add once=True).",

            "",

            f"{BOLD}Sensors{RESET}   {DIM}(live){RESET}",

            f"  {CYAN}read_sensor{RESET}(id)               one device",

            f"  {CYAN}read_sensors{RESET}(ids)             several devices",

            f"  {CYAN}waste_bin_report{RESET}()            all bins, fullest first",

            f"  {CYAN}sensor_summary{RESET}(family)        min / mean / max",

            f"  {CYAN}city_status{RESET}()                 clock, weather, counts",

            "",

            f"{BOLD}Actuators{RESET}   {DIM}(act once, show before → after){RESET}",

            f"  {CYAN}set_power_switch{RESET}(id, state)   open / close",

            f"  {CYAN}set_valve{RESET}(id, state)          open / close",

            f"  {CYAN}set_street_light{RESET}(id, mode)    on / off / auto / dim",

            f"  {CYAN}set_weather{RESET}(weather)          clear / rain / storm / fog …",

            f"  {CYAN}set_signal_phase{RESET}(phase)       all_red / ns_green / ew_green …",

            f"  {CYAN}empty_waste_bin{RESET}(id)           empty one bin",

            "",

            f"{BOLD}City{RESET}   {DIM}(zones A to P, parking, earthquakes, incidents){RESET}",

            f"  {CYAN}read_zone{RESET}(zone)                everything in one zone      "
            f"{CYAN}list_parking_lots{RESET}()   smart car parks",

            f"  {CYAN}trigger_earthquake{RESET}(zone, magnitude)   "
            f"{CYAN}clear_earthquakes{RESET}()   "
            f"{CYAN}trigger_incident{RESET}(kind)   "
            f"{CYAN}list_vehicles{RESET}()"

        ]

        samples = self.discover_samples()

        picks = []

        for family, label in (

            ("waste", "bin"),
            ("air", "air"),
            ("power_switch", "switch"),
            ("lighting", "light"),
            ("water_valve", "valve")

        ):

            if samples.get(family):

                picks.append(

                    f"{label} {CYAN}"
                    f"{', '.join(samples[family][:2])}{RESET}"

                )

        if picks:

            lines += [

                "",

                f"{BOLD}Ids to try{RESET}   "
                + "    ".join(picks)

            ]

        lines += [

            "",

            f"{GREEN}● Event-based JSON logging enabled{RESET}",

            f"{DIM}Log directory: {LOG_DIR}{RESET}",

            "",

            f"{DIM}Also: examples · tools · help · exit{RESET}"

        ]

        return "\n".join(lines)

    # ============================================================================================================
    # HELP
    # ============================================================================================================

    def help(self):

        print(f"""{BOLD}How to use{RESET}
  ↑ ↓                               browse the commands
  read_sensor(id="WGT-T001")        type a function call
  read_sensor("WGT-T001")           positional argument
  read_sensor(id="WGT-T001", once=True)   one reading
  tools                             every function and its arguments
  describe set_power_switch         function details
  examples / example 3              ready-made calls
  protocol on|off                   raw JSON-RPC messages
  json                              toggle JSON output
  exit                              close terminal

{GREEN}Event-based JSON logging enabled:{RESET}
  {LOG_DIR}
""")

    def list_tools(self):

        rows = []

        for tool in self.client.tools:

            props = tool["inputSchema"].get(
                "properties",
                {}
            )

            required = tool["inputSchema"].get(
                "required",
                []
            )

            signature = ", ".join(

                k if k in required else f"{k}?"

                for k in props

            )

            rows.append([

                f"{tool['name']}({signature})",

                tool["description"][:80]

            ])

        print(
            table(
                rows,
                ["function", "what it does"]
            )
        )

    def describe(self, name):

        for tool in self.client.tools:

            if tool["name"] == name:

                print(

                    f"{BOLD}{name}{RESET}\n"

                    f"{tool['description']}\n"

                    f"{json.dumps(tool['inputSchema'], indent=2)}"

                )

                return

        print(
            f"{RED}unknown tool '{name}'{RESET}"
        )

    def examples(self):

        for number, (title, line) in enumerate(
            EXAMPLES,
            1
        ):

            print(

                f"{BOLD}{number:2d}{RESET}  {title}\n"

                f"    {DIM}{line}{RESET}"

            )

    def run_example(self, number):

        if not 1 <= number <= len(EXAMPLES):

            print(
                f"{RED}example {number} does not exist{RESET}"
            )

            return

        title, line = EXAMPLES[number - 1]

        print(

            f"{BOLD}{title}{RESET}\n"

            f"{DIM}city-mcp> {line}{RESET}"

        )

        self.run_line(line)


# ================================================================================================================
# KEY WATCHER
# ================================================================================================================

class KeyWatcher:

    """Cancel a streaming MCP request with q or Esc."""

    def __init__(
        self,
        client,
        request_id
    ):

        import termios
        import tty

        self._termios = termios

        self._fd = sys.stdin.fileno()

        self._saved = termios.tcgetattr(
            self._fd
        )

        tty.setcbreak(
            self._fd,
            termios.TCSANOW
        )

        self._client = client

        self._request_id = request_id

        self._running = True

        self._thread = threading.Thread(
            target=self._watch,
            daemon=True
        )

        self._thread.start()

    def _watch(self):

        while self._running:

            ready, _, _ = select.select(
                [sys.stdin],
                [],
                [],
                0.1
            )

            if ready and self._running:

                key = os.read(
                    self._fd,
                    1
                )

                if key in (
                    b"q",
                    b"Q",
                    b"\x1b"
                ):

                    self._client.cancel(

                        self._request_id,

                        "stopped from the terminal"

                    )

                    return

    def stop(self):

        self._running = False

        self._thread.join(timeout=0.5)

        self._termios.tcsetattr(

            self._fd,

            self._termios.TCSADRAIN,

            self._saved

        )


# ================================================================================================================
# CITY STATUS
# ================================================================================================================

def city_alive():

    pid = os.environ.get(
        "CITY_GODOT_PID",
        ""
    )

    if not pid.isdigit():
        return True

    try:

        os.kill(
            int(pid),
            0
        )

        return True

    except OSError:

        return False


# ================================================================================================================
# INTERACTIVE MODE
# ================================================================================================================

def interactive(terminal):

    terminal.interactive = True

    history_file = (
        Path.home() / ".city_mcp_history"
    )

    history = []

    try:

        history = (
            history_file.read_text().splitlines()[-200:]
        )

    except OSError:
        pass

    book = FormBook(

        terminal.client.tools,

        terminal.discover_samples()

    )

    commands = (

        [
            t["name"]
            for t in terminal.client.tools
        ]

        +

        [
            "tools",
            "describe",
            "examples",
            "example",
            "protocol",
            "json",
            "raw",
            "resources",
            "read",
            "help",
            "exit"
        ]

    )

    editor = LineEditor(
        book,
        history,
        commands
    )

    while True:

        if not city_alive():

            print(

                f"{YELLOW}The city window was closed "
                f"— ending the terminal.{RESET}"

            )

            break

        try:

            line = editor.read(

                f"{GREEN}city-mcp>{RESET} ",

                city_alive

            )

        except KeyboardInterrupt:

            print()

            break

        if line is None:

            print()

            break

        if not terminal.run_line(line):
            break

    try:

        history_file.write_text(
            "\n".join(history[-200:]) + "\n"
        )

    except OSError:
        pass


# ================================================================================================================
# MAIN
# ================================================================================================================

def main(argv=None):

    parser = argparse.ArgumentParser(

        description="Interactive MCP terminal for the City Ground Zero simulation"

    )

    parser.add_argument(

        "--exec",
        "-c",
        action="append",
        default=[],
        metavar="LINE",
        help="run this command and exit (repeatable)"

    )

    parser.add_argument(
        "--script",
        help="run commands in a file and exit"
    )

    parser.add_argument(

        "--show-protocol",
        action="store_true",
        help="print every JSON-RPC message"

    )

    parser.add_argument(

        "--json",
        action="store_true",
        help="print raw JSON results"

    )

    parser.add_argument(

        "--server",
        default=str(HERE / "city_mcp_server.py"),
        help="MCP server script to start"

    )

    parser.add_argument(

        "--port",
        type=int,
        help="city device-API port (default: CITY_SIM_API_PORT)"

    )

    args = parser.parse_args(argv)

    env = dict(os.environ)

    if args.port:

        env["CITY_SIM_API_PORT"] = str(args.port)

    client = MCPClient(

        [sys.executable, args.server],

        env=env

    )

    terminal = Terminal(

        client,

        show_protocol=args.show_protocol,

        as_json=args.json

    )

    try:

        client.initialize()

    except (
        MCPError,
        OSError
    ) as error:

        print(

            f"{RED}could not start the MCP server: "
            f"{error}{RESET}"

        )

        client.close()

        return 1

    batch = list(args.exec)

    if args.script:

        batch += Path(
            args.script
        ).read_text().splitlines()

    if batch:

        for line in batch:

            if not terminal.run_line(line):
                break

        client.close()

        return 0

    if sys.stdout.isatty():

        sys.stdout.write(CLEAR)

    print("\n")

    print(
        terminal.header()
    )

    print("\n")

    interactive(terminal)

    client.close()

    return 0


if __name__ == "__main__":

    sys.exit(main())


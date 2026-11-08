"""Function examples, autocomplete and the line editor of the MCP terminal.

At the prompt: ↑ ↓ browse the commands (each appears as a ready-to-run call). While typing, the rest of the best match is
shown faintly; ◀ ▶ step through the matches when there are several; Tab pastes the shown match; Enter runs the line.
EditorState is a pure model (testable without a terminal); LineEditor is the terminal front end.
"""
from __future__ import annotations

import json
import os
import re
import select
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

BOLD, DIM, GREEN, CYAN, RESET, REVERSE = "\033[1m", "\033[2m", "\033[32m", "\033[36m", "\033[0m", "\033[7m"

# Functions in the order ◀ ▶ walks through them (the header's order); anything else follows alphabetically.
ORDER = ["read_sensor", "read_sensors", "waste_bin_report", "sensor_summary", "city_status",
         "set_power_switch", "set_valve", "set_street_light", "set_weather", "set_signal_phase", "empty_waste_bin"]
HIDDEN = {"subscribe"}


@dataclass
class Field:
    key: str
    kind: str                       # string | number | list | bool
    text: str                       # what is shown between the quotes / digits
    choices: list = field(default_factory=list)   # values ↑/↓ cycle through
    selected: bool = True           # the value is replaced by the first key typed

    def value(self) -> Any:
        if self.kind == "number":
            try:
                number = float(self.text)
                return int(number) if number.is_integer() and "." not in self.text else number
            except ValueError:
                return self.text
        if self.kind == "list":
            return [item.strip().strip("\"'") for item in self.text.split(",") if item.strip()]
        if self.kind == "bool":
            return self.text.strip().lower() in ("true", "1", "yes")
        return self.text

    def literal(self) -> str:
        if self.kind == "number":
            return self.text if re.fullmatch(r"-?\d+(\.\d+)?", self.text) else json.dumps(self.text)
        if self.kind == "list":
            return json.dumps(self.value())
        if self.kind == "bool":
            return "True" if self.value() else "False"
        return json.dumps(self.text, ensure_ascii=False)

    def cycle(self, step: int) -> None:
        if self.kind == "number":
            try:
                number = float(self.text) + step
            except ValueError:
                return
            self.text = str(int(number)) if number.is_integer() else str(number)
        elif self.choices:
            shown = self.text if self.kind != "list" else self.text.split(",")[0].strip()
            options = [str(c) for c in self.choices]
            index = options.index(shown) if shown in options else -1
            chosen = options[(index + step) % len(options)]
            self.text = chosen
        self.selected = True


@dataclass
class FunctionForm:
    name: str
    description: str
    fields: list[Field]
    variants: list[list[str]] = field(default_factory=list)   # each: the field texts of one ready-to-run example
    variant: int = 0

    def compose(self) -> str:
        return f"{self.name}(" + ", ".join(f"{f.key}={f.literal()}" for f in self.fields) + ")"

    def show_variant(self, index: int) -> None:
        if not self.variants:
            return
        self.variant = index % len(self.variants)
        for f, text in zip(self.fields, self.variants[self.variant]):
            f.text = text

    def next_variant(self, step: int) -> None:
        self.show_variant(self.variant + step)

    def hint(self) -> str:
        parts = ["◀ ▶ function"]
        if len(self.variants) > 1:
            parts.append(f"↑ ↓ example {self.variant + 1}/{len(self.variants)}")
        parts.append("Enter run")
        return "   ".join(parts)


class FormBook:
    """All function forms, built from the server's tool list and live sample ids."""

    def __init__(self, tools: list[dict], samples: dict[str, list[str]]):
        self.samples = samples
        by_name = {t["name"]: t for t in tools if t["name"] not in HIDDEN}
        names = [n for n in ORDER if n in by_name] + sorted(n for n in by_name if n not in ORDER)
        self.tools = [by_name[n] for n in names]
        self.index = -1

    def names(self) -> list[str]:
        return [t["name"] for t in self.tools]

    def step(self, direction: int) -> FunctionForm:
        self.index = (0 if direction > 0 else len(self.tools) - 1) if self.index < 0 else (self.index + direction) % len(self.tools)
        return self.build(self.tools[self.index])

    def jump_to(self, name: str) -> FunctionForm:
        self.index = self.names().index(name)
        return self.build(self.tools[self.index])

    def sample(self, family: str, fallback: str) -> list[str]:
        return self.samples.get(family) or [fallback]

    def build(self, tool: dict) -> FunctionForm:
        name = tool["name"]
        schema = tool["inputSchema"]
        props = schema.get("properties", {})
        wanted = self.visible_keys(name, schema)
        fields = []
        for key in wanted:
            spec = props.get(key, {})
            fields.append(self.make_field(name, key, spec))
        form = FunctionForm(name, tool.get("description", ""), fields)
        form.variants = self.make_variants(fields)
        return form

    @staticmethod
    def make_variants(fields: list[Field]) -> list[list[str]]:
        """Ready-to-run examples: the default first, then other live ids / allowed values (at most 12)."""
        import itertools
        options = []
        for f in fields:
            if f.kind == "number" or not f.choices:
                options.append([f.text])
            else:
                choices = [str(c) for c in f.choices]
                if f.kind == "list":
                    choices = [f.text] + [c for c in choices[1:3]]
                options.append(([f.text] + [c for c in choices if c != f.text])[:4 if f.key == "id" else 6])
        combos = [list(c) for c in itertools.product(*options)] if options else []
        return combos[:12]

    @staticmethod
    def visible_keys(name: str, schema: dict) -> list[str]:
        required = list(schema.get("required", []))
        extra = {"watch_sensor": ["metric", "duration_s"], "waste_bin_report": ["limit"], "list_sensors": ["limit"], "set_street_light": []}
        return required + [k for k in extra.get(name, []) if k not in required and k in schema.get("properties", {})]

    def make_field(self, tool: str, key: str, spec: dict) -> Field:
        s = self.sample
        id_families = {
            "read_sensor": "waste", "watch_sensor": "waste", "empty_waste_bin": "waste", "set_power_switch": "power_switch",
            "set_valve": "water_valve", "set_street_light": "lighting", "set_service_zone": "service_zone", "list_commands": "power_switch",
            "command_actuator": "power_switch",
        }
        if key == "id":
            family = id_families.get(tool, "waste")
            fallback = {"waste": "WGT-T001", "power_switch": "SWT-01", "water_valve": "VLV-01", "lighting": "LGT-001", "service_zone": "ZONE-1"}[family]
            ids = s(family, fallback)
            if tool == "read_sensor":
                ids = ids + [i for fam in ("air", "traffic", "weather", "noise", "power_meter") for i in self.samples.get(fam, [])[:1]]
            return Field("id", "string", ids[0], choices=ids)
        if key == "ids":
            ids = [s("air", "AIR-01")[0], (self.samples.get("noise") or self.samples.get("traffic") or ["NOISE-01"])[0]]
            return Field("ids", "list", ", ".join(ids), choices=s("air", "AIR-01") + self.samples.get("traffic", []) + self.samples.get("noise", []))
        if key == "family":
            options = spec.get("enum", [])
            default = {"sensor_summary": "air"}.get(tool, "waste")
            return Field("family", "string", default if default in options else (options[0] if options else default), choices=list(options))
        if key == "metric":
            return Field("metric", "string", "bin_weight", choices=["bin_weight", "fill_level", "deposits"])
        if key == "command":
            return Field("command", "string", "open", choices=["open", "close", "on", "off", "auto", "empty"])
        if key == "state":
            options = spec.get("enum", ["open", "close"])
            return Field("state", "string", options[0], choices=list(options))
        if key == "service":
            return Field("service", "string", "power", choices=list(spec.get("enum", ["power", "water"])))
        if key == "time":
            return Field("time", "string", "22:30", choices=["06:30", "12:00", "18:45", "22:30", "03:15"])
        if "enum" in spec:
            return Field(key, "string", str(spec["enum"][0]), choices=[str(v) for v in spec["enum"]])
        kind = spec.get("type", "string")
        kind = "number" if kind in ("integer", "number") else "list" if kind == "array" else "bool" if kind == "boolean" else "string"
        default = spec.get("default", spec.get("minimum", 1 if kind == "number" else ""))
        if key == "duration_s":
            default = 10
        if key == "limit":
            default = 5
        if key == "speed":
            default = 2
        return Field(key, kind, str(default))


def visible_width(text: str) -> int:
    return len(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text))


class EditorState:
    """The prompt line (with a real cursor), its autocomplete matches and the keys that move through them."""

    def __init__(self, book: FormBook, words: list[str] | None = None):
        self.book = book
        self.words = [w for w in (words or []) if w not in book.names()]          # plain commands
        self.buffer = ""
        self.cursor = 0
        self.choice = 0                   # which matching function the faint text shows
        self.browsing: FunctionForm | None = None   # set while ↑ ↓ has put a function's example on the line
        self._examples: list[str] | None = None
        book.index = -1

    # ---- matches (only while the cursor is at the end of what was typed)
    def all_calls(self) -> list[str]:
        if self._examples is None:
            self._examples = [self.book.build(tool).compose() for tool in self.book.tools]   # one ready-to-run call per function
        return self._examples

    def matches(self) -> list[str]:
        if not self.buffer or self.cursor != len(self.buffer):
            return []
        pool = self.all_calls() + ([w for w in self.words] if "(" not in self.buffer else [])
        return [c for c in pool if c.startswith(self.buffer) and c != self.buffer]

    def suggestion(self) -> str:
        found = self.matches()
        return found[self.choice % len(found)] if found else ""

    def ghost(self) -> str:
        suggestion = self.suggestion()
        return suggestion[len(self.buffer):] if suggestion else ""

    def _edited(self) -> None:
        self.choice, self.browsing = 0, None

    # ---- keys; returns the line to run on Enter, else None
    def key(self, key: str) -> str | None:
        if key == "enter":
            return self.buffer
        if key in ("up", "down"):
            self.browsing = self.book.step(-1 if key == "up" else 1)
            self.buffer = self.browsing.compose()
            self.cursor, self.choice = len(self.buffer), 0
        elif key in ("left", "right"):
            count = len(self.matches())
            if count > 1:      # a faint suggestion with alternatives: step through the matching functions
                self.choice = (self.choice + (1 if key == "right" else -1)) % count
            else:              # otherwise the arrows just move the cursor
                self.cursor = max(0, min(len(self.buffer), self.cursor + (1 if key == "right" else -1)))
                self.choice = 0
        elif key == "home":
            self.cursor, self.choice = 0, 0
        elif key == "end":
            self.cursor, self.choice = len(self.buffer), 0
        elif key == "tab":
            suggestion = self.suggestion()
            if suggestion:
                self.buffer = suggestion + (" " if suggestion in ("example", "describe", "protocol", "json", "read", "raw") else "")
                self.cursor = len(self.buffer)
                self._edited()
        elif key == "backspace":
            if self.cursor > 0:
                self.buffer = self.buffer[:self.cursor - 1] + self.buffer[self.cursor:]
                self.cursor -= 1
            self._edited()
        elif key == "delete":
            self.buffer = self.buffer[:self.cursor] + self.buffer[self.cursor + 1:]
            self._edited()
        elif key == "escape":
            self.buffer, self.cursor = "", 0
            self._edited()
        elif len(key) == 1 and key.isprintable():
            self.buffer = self.buffer[:self.cursor] + key + self.buffer[self.cursor:]
            self.cursor += 1
            self._edited()
        return None

    def hint(self) -> str:
        count = len(self.matches())
        if self.browsing is not None and self.buffer == self.browsing.compose():
            return "↑ ↓ commands   Enter run"
        if not self.buffer:
            return "↑ ↓ browse the commands, or start typing"
        if count:
            return "Tab paste" + (f"   ◀ ▶ functions {self.choice % count + 1}/{count}" if count > 1 else "") + "   Enter run"
        return "Enter run"


class LineEditor:
    """Prompt driven by EditorState. Falls back to input() when stdin is not a terminal."""

    def __init__(self, book: FormBook, history: list[str] | None = None, commands: list[str] | None = None):
        self.book = book
        self.commands = commands or []

    def read(self, prompt: str, keep_waiting: Callable[[], bool] | None = None) -> str | None:
        """Read one editable line, returning ``None`` when its owner shuts down.

        The normal terminal path polls rather than blocking in ``os.read`` so a
        parent application can close the prompt immediately when the city it is
        attached to goes away.
        """
        keep_waiting = keep_waiting or (lambda: True)
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            try:
                return input(prompt)
            except EOFError:
                return None
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd, termios.TCSANOW)   # keys arrive one by one (typed-ahead input is kept); Ctrl+C still interrupts
        try:
            return self._edit(fd, prompt, keep_waiting)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def _edit(self, fd: int, prompt: str, keep_waiting: Callable[[], bool]) -> str | None:
        state = EditorState(self.book, self.commands)
        while True:
            if not keep_waiting():
                return None
            self._draw(prompt, state)
            # A short timeout is what lets the terminal notice the city window
            # closing even while no key has been pressed.
            ready, _, _ = select.select([fd], [], [], 0.12)
            if not ready:
                continue
            key = self._read_key(fd)
            if key is None or (key == "eof" and not state.buffer):
                return None
            line = state.key(key)
            if line is not None:
                sys.stdout.write("\r\033[K" + prompt + line + "\n\033[K")
                sys.stdout.flush()
                return line

    def _read_key(self, fd: int) -> str | None:
        data = os.read(fd, 1)
        if not data:
            return None
        byte = data[0]
        if byte == 0x1B:
            ready, _, _ = select.select([fd], [], [], 0.03)
            if not ready:
                return "escape"
            rest = os.read(fd, 1)
            if rest in (b"[", b"O"):
                param = b""
                final = os.read(fd, 1)
                while final and final[0] in b"0123456789;":
                    param += final
                    final = os.read(fd, 1)
                if final == b"~":
                    return {b"3": "delete", b"1": "home", b"7": "home", b"4": "end", b"8": "end"}.get(param, "other")
                return {b"A": "up", b"B": "down", b"C": "right", b"D": "left", b"H": "home", b"F": "end"}.get(final, "other")
            return "other"
        if byte in (0x0D, 0x0A):
            return "enter"
        if byte in (0x7F, 0x08):
            return "backspace"
        if byte == 0x09:
            return "tab"
        if byte == 0x04:
            return "eof"
        if byte == 0x01:
            return "home"          # Ctrl+A
        if byte == 0x05:
            return "end"           # Ctrl+E
        if byte < 0x20:
            return "other"
        if byte >= 0x80:     # a multi-byte UTF-8 character
            length = 2 if byte >> 5 == 0b110 else 3 if byte >> 4 == 0b1110 else 4
            data += os.read(fd, length - 1)
            return data.decode("utf-8", errors="replace")
        return chr(byte)

    def _draw(self, prompt: str, state: EditorState) -> None:
        line = state.buffer
        column = visible_width(prompt) + state.cursor
        back = f"\r\033[{column}C" if column else "\r"
        sys.stdout.write("\r\033[K" + prompt + line + f"{DIM}{state.ghost()}{RESET}" + "\n\033[K" + f"{DIM}{state.hint()}{RESET}" + "\033[1A" + back)
        sys.stdout.flush()

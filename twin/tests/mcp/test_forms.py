"""The ◀ ▶ function forms: pure model tests plus a pseudo-terminal session that presses the arrow keys."""
from __future__ import annotations

import os
import pty
import re
import select
import sys
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "tools" / "mcp"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import city_form  # noqa: E402
import city_mcp_server  # noqa: E402
from fake_city import FakeCity  # noqa: E402

SAMPLES = {"waste": ["WGT-T001", "WGT-T002", "WGT-C001"], "air": ["AIR-01", "AIR-02"], "traffic": ["TRAF-01"], "power_switch": ["SWT-01", "SWT-02"],
           "lighting": ["LGT-001"], "water_valve": ["VLV-01"], "service_zone": ["ZONE-A"]}


def tools():
    server = city_mcp_server.Server(city_mcp_server.SimClient(port=1))
    return [t.describe() for t in server.tools.values()]


class FormModelTest(unittest.TestCase):
    def setUp(self):
        self.book = city_form.FormBook(tools(), SAMPLES)

    def test_functions_follow_the_header_order_and_wrap(self):
        first = self.book.step(1)
        self.assertEqual(first.name, "read_sensor")
        self.assertEqual(self.book.step(1).name, "read_sensors")
        self.assertEqual(self.book.step(-1).name, "read_sensor")
        self.assertEqual(self.book.step(-1).name, self.book.names()[-1])        # wraps to the last
        self.assertEqual(self.book.step(1).name, "read_sensor")
        self.assertNotIn("subscribe", self.book.names())

    def test_fields_come_filled_with_live_values(self):
        self.assertEqual(self.book.jump_to("read_sensor").compose(), 'read_sensor(id="WGT-T001")')
        self.assertEqual(self.book.jump_to("set_power_switch").compose(), 'set_power_switch(id="SWT-01", state="open")')
        self.assertEqual(self.book.jump_to("set_valve").compose(), 'set_valve(id="VLV-01", state="open")')
        self.assertEqual(self.book.jump_to("set_street_light").compose(), 'set_street_light(id="LGT-001", mode="on")')
        self.assertEqual(self.book.jump_to("read_sensors").compose(), 'read_sensors(ids=["AIR-01", "TRAF-01"])')
        self.assertEqual(self.book.jump_to("city_status").compose(), "city_status()")
        self.assertEqual(self.book.jump_to("set_weather").compose(), 'set_weather(weather="clear")')
        self.assertEqual(self.book.jump_to("set_service_zone").compose(), 'set_service_zone(id="ZONE-A", service="power", state="cut")')
        self.assertEqual(self.book.jump_to("watch_sensor").compose(), 'watch_sensor(id="WGT-T001", metric="bin_weight", duration_s=10)')

    def test_every_form_is_a_valid_call_for_its_tool(self):
        import city_cli
        for tool in self.book.tools:
            form = self.book.build(tool)
            call = city_cli.parse_call(form.compose())
            self.assertIsNotNone(call, form.compose())
            self.assertEqual(call[0], tool["name"])
            for key in tool["inputSchema"].get("required", []):
                self.assertIn(key, call[2], f"{tool['name']} lacks {key}")
            problem = city_mcp_server._validate(call[2], tool["inputSchema"])
            self.assertEqual(problem, "", f"{form.compose()}: {problem}")

    def test_up_down_cycle_ready_to_run_examples(self):
        import city_cli
        form = self.book.jump_to("set_power_switch")
        seen = []
        for _ in range(len(form.variants)):
            seen.append(form.compose())
            form.next_variant(1)
        self.assertEqual(seen[0], 'set_power_switch(id="SWT-01", state="open")')
        self.assertIn('set_power_switch(id="SWT-01", state="close")', seen)
        self.assertIn('set_power_switch(id="SWT-02", state="open")', seen)
        self.assertEqual(len(set(seen)), len(seen))
        self.assertEqual(form.compose(), seen[0])                       # wraps around
        form.next_variant(-1)
        self.assertEqual(form.compose(), seen[-1])
        for text in seen:                                               # every example is a valid call
            self.assertIsNotNone(city_cli.parse_call(text))

    def test_functions_without_choices_have_one_example(self):
        form = self.book.jump_to("city_status")
        self.assertEqual(form.compose(), "city_status()")

    def test_unknown_samples_fall_back_to_defaults(self):
        book = city_form.FormBook(tools(), {})
        self.assertEqual(book.jump_to("read_sensor").compose(), 'read_sensor(id="WGT-T001")')


class EditorStateTest(unittest.TestCase):
    def setUp(self):
        self.book = city_form.FormBook(tools(), SAMPLES)
        self.state = city_form.EditorState(self.book, ["help", "tools", "examples", "exit", "read_sensor"])

    def type(self, text):
        for char in text:
            self.state.key(char)

    def names(self):
        return [m.split("(")[0] for m in self.state.matches()]

    def test_up_down_browse_the_commands(self):
        self.state.key("down")
        self.assertEqual(self.state.buffer, 'read_sensor(id="WGT-T001")')
        self.state.key("down")
        self.assertEqual(self.state.buffer, 'read_sensors(ids=["AIR-01", "TRAF-01"])')
        self.state.key("up")
        self.state.key("up")
        self.assertEqual(self.state.buffer, self.book.build(self.book.tools[-1]).compose())      # wraps to the last
        self.assertEqual(self.state.key("enter"), self.state.buffer)

    def test_typing_shows_a_faint_completion(self):
        self.type("set_power")
        self.assertEqual(self.state.ghost(), '_switch(id="SWT-01", state="open")')
        self.assertEqual(self.state.buffer, "set_power")          # the faint text is not part of the line yet
        self.assertEqual(self.state.key("enter"), "set_power")    # Enter runs what was typed, not the suggestion

    def test_side_arrows_step_through_matching_functions_and_tab_pastes(self):
        self.type("set_")
        names = self.names()
        self.assertGreater(len(names), 3)
        self.assertEqual(len(names), len(set(names)))                      # one option per function, never per parameter
        self.assertEqual(names[0], "set_power_switch")
        first = self.state.suggestion()
        self.state.key("right")
        self.assertEqual(self.state.suggestion().split("(")[0], names[1])
        self.state.key("left")
        self.assertEqual(self.state.suggestion(), first)
        self.state.key("left")                                              # wraps to the last function
        last = self.state.suggestion()
        self.assertEqual(last.split("(")[0], names[-1])
        self.state.key("tab")
        self.assertEqual(self.state.buffer, last)
        self.assertEqual(self.state.ghost(), "")

    def test_a_single_function_has_a_single_option(self):
        self.type("set_power_switch(")
        self.assertEqual(len(self.state.matches()), 1)
        self.state.key("right")
        self.assertEqual(self.state.suggestion(), 'set_power_switch(id="SWT-01", state="open")')

    def test_matching_narrows_while_typing(self):
        self.type("set_s")
        self.assertEqual(set(self.names()), {"set_street_light", "set_signal", "set_signal_phase", "set_service_zone", "set_simulation_speed"})
        self.type("t")
        self.assertEqual(self.names(), ["set_street_light"])

    def test_plain_commands_complete_too(self):
        self.type("exa")
        self.assertEqual(self.state.suggestion(), "examples")
        self.state.key("tab")
        self.assertEqual(self.state.buffer, "examples")

    def test_no_suggestion_for_unknown_text_and_backspace_works(self):
        self.type("zzz")
        self.assertEqual(self.state.ghost(), "")
        self.state.key("backspace")
        self.assertEqual(self.state.buffer, "zz")
        self.state.key("tab")
        self.assertEqual(self.state.buffer, "zz")

    def test_without_a_suggestion_the_side_arrows_move_the_cursor(self):
        self.type("zzz")                                  # nothing matches: no faint text
        self.assertEqual(self.state.ghost(), "")
        self.state.key("left")
        self.state.key("left")
        self.assertEqual(self.state.cursor, 1)
        self.type("X")
        self.assertEqual(self.state.buffer, "zXzz")
        self.state.key("right")
        self.state.key("right")
        self.state.key("right")                           # stops at the end
        self.assertEqual(self.state.cursor, 4)
        self.state.key("home")
        self.state.key("delete")
        self.assertEqual(self.state.buffer, "Xzz")
        self.state.key("right")
        self.state.key("backspace")
        self.assertEqual(self.state.buffer, "zz")
        self.state.key("end")
        self.assertEqual(self.state.cursor, 2)

    def test_a_single_suggestion_does_not_capture_the_arrows(self):
        self.type("set_power_switch(")
        self.assertEqual(len(self.state.matches()), 1)
        self.state.key("left")                            # only one option: the arrow moves the cursor and hides the faint text
        self.assertEqual(self.state.cursor, len("set_power_switch(") - 1)
        self.assertEqual(self.state.ghost(), "")
        self.state.key("right")
        self.assertEqual(self.state.cursor, len("set_power_switch("))

    def test_arrows_navigate_options_only_while_several_are_suggested(self):
        self.type("set_")
        self.assertGreater(len(self.state.matches()), 1)
        before = self.state.cursor
        self.state.key("right")
        self.assertEqual(self.state.cursor, before)       # the cursor stayed: the arrow stepped through the functions
        self.state.key("escape")
        self.type("set_power_switch(i")
        self.state.key("left")                            # one match left -> normal cursor movement
        self.assertEqual(self.state.cursor, len("set_power_switch(i") - 1)

    def test_hints_are_short(self):
        self.assertIn("↑ ↓", self.state.hint())
        self.type("set_p")
        self.assertIn("Tab paste", self.state.hint())
        self.type("o")
        self.type("wer_switch")
        self.state.key("escape")
        self.type("set_")
        self.assertIn("◀ ▶ functions 1/", self.state.hint())
        self.state.key("escape")
        self.assertEqual(self.state.buffer, "")
        self.state.key("down")
        self.assertEqual(self.state.hint(), "↑ ↓ commands   Enter run")


class ArrowKeySessionTest(unittest.TestCase):
    def read_until(self, fd, needle, timeout=15.0):
        buffer = b""
        end = time.monotonic() + timeout
        strip = lambda raw: re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw.decode(errors="replace"))
        while time.monotonic() < end and needle not in strip(buffer):
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
        return buffer.decode(errors="replace")

    def test_browse_type_complete_and_run(self):
        city = FakeCity()
        city.switch_closed = False
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["CITY_SIM_API_PORT"] = str(city.port)
            os.environ["HOME"] = "/tmp"
            os.execv(sys.executable, [sys.executable, str(PROJECT / "tools" / "mcp" / "city_cli.py")])
        plain = lambda text: re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        try:
            self.read_until(fd, "Ids to try")
            self.read_until(fd, "city-mcp> ")
            os.write(fd, b"\x1b[B")                                   # ↓ browse: first command with its fields filled in
            self.assertIn('read_sensor(id="WGT-T001")', plain(self.read_until(fd, 'read_sensor(id="WGT-T001")')))
            os.write(fd, b"\x1b")                                     # Esc clears the line
            self.read_until(fd, "start typing")
            os.write(fd, b"set_power_switch(")                        # typing: the rest appears faintly
            seen = self.read_until(fd, "zzz-never", 0.8)            # let the whole line arrive
            self.assertIn("\x1b[2m", seen)                            # the suggestion is drawn dim
            self.assertIn('set_power_switch(id="SWT-01", state="open")', plain(seen))
            os.write(fd, b"\x1b")                                     # Esc, then a shorter prefix with several functions
            self.read_until(fd, "zzz-never", 0.4)
            os.write(fd, b"set_")
            self.assertIn("functions 1/", plain(self.read_until(fd, "zzz-never", 0.8)))
            os.write(fd, b"\x1b[C")                                   # ▶ next matching function
            self.assertIn("functions 2/", plain(self.read_until(fd, "zzz-never", 0.6)))
            os.write(fd, b"\t")                                       # Tab pastes it
            pasted = plain(self.read_until(fd, "zzz-never", 0.6))
            self.assertIn("set_valve(id=", pasted)
            os.write(fd, b"\r")                                       # Enter runs the pasted line
            out = plain(self.read_until(fd, "via MCP"))
            self.assertIn("VLV-01", out)
            self.read_until(fd, "city-mcp> ")
            os.write(fd, b"zzz\x1b[D\x1b[DX\r")                      # no suggestion: ◀ moves the cursor normally
            self.assertIn("unknown command 'zXzz'", plain(self.read_until(fd, "unknown command")))
            self.read_until(fd, "city-mcp> ")
            os.write(fd, b"exit\r")
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.WEXITSTATUS(status), 0)
        finally:
            city.close()
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

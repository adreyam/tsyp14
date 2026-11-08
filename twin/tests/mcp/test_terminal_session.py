"""The interactive terminal run in a pseudo-terminal, the way a person uses it after ./run_control_room.sh."""
from __future__ import annotations

import os
import pty
import select
import sys
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_city import FakeCity  # noqa: E402


class TerminalSessionTest(unittest.TestCase):
    def read_until(self, fd, needle, timeout=15.0):
        buffer = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and needle.encode() not in buffer:
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

    def test_prompt_tools_examples_and_exit(self):
        city = FakeCity()
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["CITY_SIM_API_PORT"] = str(city.port)
            os.execv(sys.executable, [sys.executable, str(PROJECT / "tools" / "mcp" / "city_cli.py")])
        try:
            banner = self.read_until(fd, "exit")
            banner = (banner + self.read_until(fd, "city-mcp>")).replace("\r\n", "\n")
            self.assertIn("MCP TERMINAL", banner)
            self.assertIn("Sensors", banner)
            self.assertIn("Ids to try", banner)
            self.assertIn("WGT-T001", banner)                 # ids are fetched live through MCP
            self.assertIn("5 devices", banner)
            self.assertIn("\x1b[3J\x1b[2J", banner)          # screen and scrollback are wiped before the header
            self.assertLess(banner.index("MCP TERMINAL"), banner.index("city-mcp>"))
            above = banner.split("MCP TERMINAL")[0]
            self.assertGreaterEqual(above.count("\n"), 2)                              # blank space above the header
            self.assertIn("\n\n", banner.split("exit")[-1])                           # and between header and prompt
            # A sensor read is a subscription: updates keep arriving until q.
            os.write(fd, b'read_sensor(id="WGT-T001")\n')
            stream = self.read_until(fd, "live read_sensor")
            city.bins["WGT-T001"] = 77.0
            stream += self.read_until(fd, "77", 8.0)
            self.assertIn("77", stream)
            os.write(fd, b"q")
            done = self.read_until(fd, "live updates")
            self.assertIn("stopped by you", done)
            self.read_until(fd, "city-mcp>")
            # Ctrl+C also ends a subscription and returns to the prompt.
            os.write(fd, b'read_sensors(ids=["AIR-01"])\n')
            self.read_until(fd, "live")
            os.write(fd, b"\x03")
            self.assertIn("live updates", self.read_until(fd, "live updates"))
            self.read_until(fd, "city-mcp>")
            os.write(fd, b"examples\n")
            self.assertIn("Watch one bin's weight live", self.read_until(fd, "Send a raw JSON-RPC message"))
            os.write(fd, b'set_power_switch("SWT-01", state="open")\n')
            self.assertIn("SWT-01 open", self.read_until(fd, "via MCP"))
            self.assertFalse(city.switch_closed)
            os.write(fd, b'read_sensor(id="NOPE", once=True)\n')
            self.assertIn("unknown_device", self.read_until(fd, "unknown_device"))
            os.write(fd, b"exit\n")
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

"""
The Brain process: source + Guardian + orchestrator + audit, one loop.

    Brain(cfg).start()   runs the loop in a thread (used by the HTTP server)
    Brain(cfg).step()    one poll of the source and one pass of the orchestrator (used by tests and the CLI runner)

Fallback: if the live Twin stays unreachable, the Brain says so on /v1/status, in the audit log and on the
dashboard. With fallback.auto_switch_to_mock: true it also switches itself to the mock source; otherwise
set `source: mock` in brain/config/brain.yaml (or CITY_BRAIN_SOURCE=mock) and restart.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .audit import Audit
from .config import Config
from .contracts import iso, utc_now
from .guardian import make_guardian
from .orchestrator import Orchestrator
from .sources import TwinSource, make_source


class Brain:
    def __init__(self, cfg: Config, audit: Optional[Audit] = None, source: Optional[TwinSource] = None):
        self.cfg = cfg
        self.audit = audit or Audit(cfg.logs_dir(), cfg.logs.keep_in_memory)
        self.lock = threading.RLock()
        self.started = iso(utc_now())
        self.configured_source = cfg.source
        self.switched: Optional[str] = None
        self._build(source)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.scenario = None                     # running ScenarioRunner, if any
        self.audit.write("system", None, self.source.name,
                         f"City Brain started: source={self.source.name}, guardian={self.guardian.mode}, config={cfg.path}",
                         payload={"source": self.source.name, "guardian": self.guardian.mode})

    def _build(self, source: Optional[TwinSource] = None) -> None:
        self.source = source or make_source(self.cfg)
        self.guardian = make_guardian(self.cfg, self.source.name)
        self.orch = Orchestrator(self.cfg, self.source, self.guardian, self.audit)

    # ------------------------------------------------------------------ loop
    def step(self) -> int:
        with self.lock:
            events = self.source.poll()
            for change in getattr(self.source, "state_changes", []):
                self.audit.write("system", None, self.source.name, change["message"],
                                 severity="info" if change["connected"] else "critical", payload=change)
            if hasattr(self.source, "state_changes"):
                self.source.state_changes.clear()
            for ev in events:
                self.orch.ingest(ev)
            self.orch.tick()                   # moving units (green waves) and A2A tasks sent from outside
            self._fallback()
            return len(events)

    def _fallback(self) -> None:
        if self.source.name != "live" or not self.cfg.fallback.auto_switch_to_mock:
            return
        down = getattr(self.source, "down_for", lambda: 0.0)()
        if down >= self.cfg.fallback.after_s:
            self.audit.write("system", None, "live",
                             f"live Twin unreachable for {down:.0f} s: switching to the mock source (fallback.auto_switch_to_mock)",
                             severity="critical")
            self.switch("mock", reason="fallback")

    def switch(self, kind: str, reason: str = "operator") -> None:
        with self.lock:
            old = self.source.name
            self.source.close()
            self.cfg.source = kind
            self._build()
            self.switched = f"{old} -> {kind} ({reason}) at {iso(utc_now())}"
            self.audit.write("system", None, kind, f"source switched {old} -> {kind} ({reason})", severity="alert")

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.step()
            except Exception as e:                   # the loop must survive anything a partner sends
                self.audit.write("error", None, self.source.name, f"loop error: {type(e).__name__}: {e}", severity="alert")
            self._stop.wait(max(0.05, min(self.cfg.twin.poll_s, 0.5) - (time.monotonic() - t0)))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="brain-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.source.close()

    # ------------------------------------------------------------------ views
    def status(self) -> dict:
        src = self.source.status()
        return {"service": "city-brain", "started": self.started, "now": iso(utc_now()),
                "source": self.source.name, "configured_source": self.configured_source, "switched": self.switched,
                "twin": src, "guardian": self.guardian.status(),
                "live_ok": src.get("connected", False),
                "fallback_hint": None if self.source.name == "mock" or src.get("connected") else
                "the live Twin is unreachable: start twin/run_control_room.sh, or set source: mock in brain/config/brain.yaml "
                "(or CITY_BRAIN_SOURCE=mock) and restart the Brain",
                "scenario": self.scenario.view() if self.scenario else None,
                "stats": self.orch.stats, "a2a": dict(self.orch.a2a.stats, open=len(self.orch.a2a.open_tasks())),
                "log_file": str(self.audit.path)}

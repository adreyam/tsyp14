"""
GuardianSource: where trust verdicts come from.

    MockGuardianSource   brain/mocks/guardian_standin.MockGuardian, in-process (default: there is no partner Guardian yet)
    HttpGuardianSource   a real Guardian: POST <url><verdict_path> with the event, answer = a Verdict (brain/INTERFACE.md)
    OffGuardianSource    no Guardian: every event gets no verdict and the 'on_missing' policy applies

A real Guardian can also push verdicts to POST /v1/verdicts at any time (they are cached per device).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, Optional

from pydantic import ValidationError

from .config import Config
from .contracts import Event, Verdict, explain_validation


class GuardianSource:
    mode = "?"

    def __init__(self) -> None:
        self.pushed: Dict[str, Verdict] = {}          # device -> latest pushed verdict
        self.last_error: Optional[str] = None
        self.available = True

    def verdict(self, ev: Event, pushed: bool = False) -> Optional[Verdict]:
        cached = self.pushed.get(ev.origin.device_id)
        if cached is not None:
            age = (ev.ts - cached.ts).total_seconds()
            if age <= cached.valid_for_s:
                return cached
            self.pushed.pop(ev.origin.device_id, None)
        return self._ask(ev, pushed)

    def _ask(self, ev: Event, pushed: bool) -> Optional[Verdict]:
        return None

    def push(self, v: Verdict) -> None:
        self.pushed[v.device_id] = v

    def is_quarantined(self, device: str) -> Optional[str]:
        v = self.pushed.get(device)
        return v.reason if v is not None and v.status == "quarantined" else None

    def status(self) -> dict:
        return {"mode": self.mode, "available": self.available, "last_error": self.last_error,
                "quarantined": {d: v.reason for d, v in self.pushed.items() if v.status == "quarantined"}}


class MockGuardianSource(GuardianSource):
    mode = "mock"

    def __init__(self, source: str):
        super().__init__()
        from mocks.guardian_standin import MockGuardian
        self.g = MockGuardian(source)

    def _ask(self, ev: Event, pushed: bool) -> Optional[Verdict]:
        v = self.g.judge(ev, pushed=pushed)
        self.available = not self.g.down
        self.last_error = "mock Guardian down (scenario)" if self.g.down else None
        return v

    def is_quarantined(self, device: str) -> Optional[str]:
        return self.g.quarantined.get(device) or super().is_quarantined(device)

    def status(self) -> dict:
        s = super().status()
        s["quarantined"] = {**s["quarantined"], **self.g.quarantined}
        s["available"] = not self.g.down
        return s


class HttpGuardianSource(GuardianSource):
    mode = "http"

    def __init__(self, cfg: Config):
        super().__init__()
        self.url = cfg.guardian.url.rstrip("/") + cfg.guardian.verdict_path
        self.timeout = cfg.guardian.timeout_s
        self.token = os.environ.get(cfg.guardian.token_env, "")
        self._retry_at = 0.0

    def _ask(self, ev: Event, pushed: bool) -> Optional[Verdict]:
        if time.monotonic() < self._retry_at:
            return None
        body = json.dumps({"event": ev.dump()}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode() or "{}")
            v = Verdict(**data)
            self.available, self.last_error = True, None
            return v
        except ValidationError as e:
            self.last_error = "Guardian answer is not a valid verdict: " + explain_validation(e, "verdict")
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.available = False
            self.last_error = f"Guardian unreachable at {self.url}: {e}"
            self._retry_at = time.monotonic() + 5.0
        return None


class OffGuardianSource(GuardianSource):
    mode = "off"

    def _ask(self, ev: Event, pushed: bool) -> Optional[Verdict]:
        self.available = False
        return None


def make_guardian(cfg: Config, source_name: str) -> GuardianSource:
    if cfg.guardian.mode == "mock":
        return MockGuardianSource(source_name)
    if cfg.guardian.mode == "http":
        return HttpGuardianSource(cfg)
    return OffGuardianSource()

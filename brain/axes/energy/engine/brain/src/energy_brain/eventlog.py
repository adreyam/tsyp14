"""Append-only JSON-lines event log (R2) + optional timeline sink (R3).

One file per run: logs/<run_name>-<UTC timestamp>.log. Every line is one
BrainEvent, so the jury (and our tests) can replay the decision chain.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .schemas import BrainEvent

Sink = Callable[[BrainEvent], None]


class EventLog:
    def __init__(self, log_dir: str | Path, run_name: str, sink: Optional[Sink] = None, echo: bool = False):
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"
        self.path = Path(log_dir) / f"{run_name}-{stamp}.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._sink = sink
        self._echo = echo
        self.events: list[BrainEvent] = []

    def emit(self, kind: str, summary: str, correlation_id: str | None = None, **payload) -> BrainEvent:
        evt = BrainEvent(kind=kind, summary=summary, correlation_id=correlation_id, payload=payload)
        self.events.append(evt)
        self._fh.write(evt.model_dump_json() + "\n")
        self._fh.flush()
        if self._echo:
            print(f"[{evt.ts:%H:%M:%S}] {kind:<20} {summary}")
        if self._sink:
            try:
                self._sink(evt)
            except Exception as exc:  # the timeline must never break the Brain
                self._fh.write(json.dumps({"kind": "sink_error", "error": str(exc)}) + "\n")
        return evt

    def close(self) -> None:
        self._fh.close()

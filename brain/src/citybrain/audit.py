"""
Audit log: every step of every decision, with its reason, in one JSON-lines file and in memory.

Record kinds (one line each, all with ts, trace_id, source):
    event      a reading or incident entered the Brain
    verdict    the Guardian's verdict on it
    ignored    the Brain did not use it, and why (untrusted, quarantined, unknown family, malformed)
    decision   an agent detected something and chose a response (or chose not to act)
    conflict   two axes wanted the same resource; who won and why
    action     an actuator command sent to the Twin
    feedback   the Twin's answer to that command
    situation  a situation opened, changed or closed (feeds the dashboard top bar)
    a2a        one agent asked another to do something, or answered (see a2a.py)
    system     source connected/lost, mode switches, scenario start/end
    error      a malformed message, with the readable validation problem

The dashboard reads them with GET /v1/timeline?after=<seq>.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import iso, utc_now


class Audit:
    def __init__(self, logs_dir: Path, keep: int = 4000, name: Optional[str] = None):
        self.dir = Path(logs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = self.dir / (name or f"brain-{stamp}.jsonl")
        self._fh = open(self.path, "a", encoding="utf-8")
        self._mem: deque = deque(maxlen=keep)
        self._seq = 0
        self._lock = threading.Lock()
        self._extra: List[Any] = []            # extra sinks (scenario run logs)

    def add_sink(self, fh) -> None:
        with self._lock:
            self._extra.append(fh)

    def remove_sink(self, fh) -> None:
        with self._lock:
            if fh in self._extra:
                self._extra.remove(fh)

    def write(self, kind: str, trace_id: Optional[str], source: Optional[str], summary: str,
              axis: Optional[str] = None, severity: str = "info", payload: Optional[Dict[str, Any]] = None,
              scenario_id: Optional[str] = None) -> dict:
        with self._lock:
            self._seq += 1
            rec = {"seq": self._seq, "ts": iso(utc_now()), "kind": kind, "trace_id": trace_id, "source": source,
                   "axis": axis, "severity": severity, "summary": summary, "scenario_id": scenario_id,
                   "payload": payload or {}}
            line = json.dumps(rec, ensure_ascii=False, default=str)
            self._fh.write(line + "\n")
            self._fh.flush()
            for fh in self._extra:
                try:
                    fh.write(line + "\n")
                    fh.flush()
                except ValueError:
                    pass
            self._mem.append(rec)
            return rec

    def since(self, after: int = 0, limit: int = 500, kinds: Optional[set] = None) -> List[dict]:
        with self._lock:
            out = [r for r in self._mem if r["seq"] > after and (kinds is None or r["kind"] in kinds)]
        return out[-limit:]

    @property
    def last_seq(self) -> int:
        return self._seq

    def close(self) -> None:
        self._fh.close()

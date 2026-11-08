"""
Append-only audit log, one JSON object per line (R2: every run leaves a log).

Each event says which layer it comes from, so a timeline screen (R3) can show
reading (twin) -> verdict (guardian) -> decision and dispatch (city_brain).
Timestamps come from the caller, so a simulated run is reproducible.
"""
import json
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat().replace("+00:00", "Z")
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not serializable: {type(o).__name__}")


class AuditLog:
    def __init__(self, path: Optional[str] = None, wall_clock: bool = False):
        """wall_clock=True adds the real time of each event ("wall"), next to the
        city time ("ts"). Used by the live service, so the timeline shows real
        delays; left off in simulated scenarios so their logs stay reproducible."""
        self.path = path
        self.wall_clock = wall_clock
        self.events: List[dict] = []
        self._lock = threading.Lock()
        if path:
            open(path, "w").close()            # a new run starts a new log

    def log(self, ts: datetime, layer: str, event: str, **payload) -> dict:
        entry = {"ts": ts, "layer": layer, "event": event, **payload}
        if self.wall_clock:
            entry["wall"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = json.dumps(entry, default=_json_default)
        with self._lock:
            self.events.append(json.loads(line))
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        return entry

    def since(self, after: int) -> List[dict]:
        """Events after the first `after` ones, each with its position "seq"."""
        n = len(self.events)
        return [{"seq": i, **self.events[i]} for i in range(max(0, after), n)]

    def of(self, event: str) -> List[dict]:
        return [e for e in self.events if e["event"] == event]

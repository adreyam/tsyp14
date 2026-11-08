"""One timeline of what happens across the three layers (Twin -> Guardian verdict -> Brain decision -> actuator action).

Every entry is kept in memory (shown on the dashboard) and appended to logs/brain.log. While a scenario runs, the same
entries are also written to logs/<scenario>-<date>.log, which is the proof the jury can read after the event."""
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

LAYERS = ("twin", "guardian", "brain", "actuator", "test")


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Timeline:
    def __init__(self, logs_dir, clock_fn=None):
        self.logs_dir = logs_dir
        os.makedirs(logs_dir, exist_ok=True)
        self.clock_fn = clock_fn or (lambda: "")
        self.items = deque(maxlen=2000)
        self.n = 0
        self.lock = threading.RLock()
        self.scenario = None          # {"id", "path", "fh", "started"}
        self._main = open(os.path.join(logs_dir, "brain.log"), "a", encoding="utf-8", buffering=1)

    def add(self, layer, kind, text, ref=None, **extra):
        with self.lock:
            self.n += 1
            e = {"id": self.n, "ts": iso_now(), "clock": self.clock_fn(), "layer": layer, "kind": kind, "text": text, "ref": ref}
            if extra:
                e["extra"] = extra
            self.items.append(e)
            line = f'{e["ts"]} [{layer.upper():8s}] {kind}: {text}' + (f"  (ref {ref})" if ref else "")
            self._main.write(line + "\n")
            if self.scenario:
                self.scenario["fh"].write(line + "\n")
            return e

    def since(self, after_id=0, limit=300, layer=None):
        with self.lock:
            rows = [e for e in self.items if e["id"] > after_id and (layer is None or e["layer"] == layer)]
            return rows[-limit:]

    # ------------------------------------------------------------------ scenario logs
    def start_scenario(self, sid, title=""):
        with self.lock:
            self.stop_scenario(quiet=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = os.path.join(self.logs_dir, f"{sid}-{stamp}.log")
            fh = open(path, "w", encoding="utf-8", buffering=1)
            self.scenario = {"id": sid, "path": path, "fh": fh, "started": iso_now(), "checks": []}
            fh.write(f"# scenario {sid} {title}\n# started {self.scenario['started']}\n")
            self.add("test", "scenario_start", f"{sid} {title}".strip(), ref=sid)
            return path

    def note_check(self, text, ok):
        with self.lock:
            if self.scenario:
                self.scenario["checks"].append({"text": text, "ok": bool(ok)})
            self.add("test", "check_pass" if ok else "check_FAIL", text, ref=self.scenario["id"] if self.scenario else None)

    def stop_scenario(self, quiet=False):
        with self.lock:
            sc = self.scenario
            if not sc:
                return None
            ok = all(c["ok"] for c in sc["checks"]) and bool(sc["checks"])
            if not quiet:
                self.add("test", "scenario_end", f'{sc["id"]} {"PASS" if ok else "FAIL"} ({sum(c["ok"] for c in sc["checks"])}/{len(sc["checks"])} checks)', ref=sc["id"])
            sc["fh"].write(f'# finished {iso_now()}  RESULT {"PASS" if ok else "FAIL"}\n')
            sc["fh"].close()
            summary = {"id": sc["id"], "started": sc["started"], "finished": iso_now(), "pass": ok, "checks": sc["checks"], "log": os.path.basename(sc["path"])}
            with open(os.path.join(self.logs_dir, f'{sc["id"]}-latest.json'), "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=1)
            self.scenario = None
            return summary

    def results(self):
        out = []
        for f in sorted(os.listdir(self.logs_dir)):
            if f.endswith("-latest.json"):
                try:
                    with open(os.path.join(self.logs_dir, f), encoding="utf-8") as fh:
                        out.append(json.load(fh))
                except (OSError, ValueError):
                    pass
        return out

"""
Test Book scenario runner (spec R2: one trigger, one log file per run).

A scenario is a JSON file in brain/mocks/scenarios/<ID>.json: a timeline of steps played on the city and a
list of checks run on the Brain's audit records. brain/mocks/scenarios/index.json lists them in order, three per
axis (emergency EM, transportation TR, pollution PO, energy EN) then the system checks (SY); only the scenarios
listed there are loaded. "focus" tells the spotlight what to show while it runs (spotlight.py): "zones",
"devices", and "follow": "units" (the units of its incidents as they drive) or "traffic" (a car driving into the
scenario's first device). Steps:

    twin_command   {"id", "command", "value"}       a real Twin command (INCIDENTS trigger, WEATHER set, EARTHQUAKE trigger):
                                                    runs on the live Twin and on the mock Twin alike
    incident       {"kind", "x", "z", "id", "resolve_after"}  mock: an incident at a precise place
    resolve        {"id"}                           mock: the Twin resolves an incident
    set / ramp     {"device", "metric", "value"} / {"to", "over"}   mock: a sensor reads a value (fake or real)
    stick          {"device", "metric"}             mock: a sensor freezes
    clear          {"device"?}                      mock: back to the simulated values
    state          {"device", "key", "value"}       mock: change a device's internal state (grid drain, lot filling)
    push_event     {"event": {...}}                 an event pushed over the network (validated like any partner input)
    push_raw       {"body": "..."}                  a malformed message pushed over the network
    flood_messages {"device", "count"}              a device floods the Brain with messages
    guardian       {"device", "status", "reason"}   the (mock) Guardian forces a verdict (e.g. quarantine an actuator)
    guardian_down  {"value": true|false}            the Guardian stops / resumes answering
    twin_down      {"value": true|false}            mock: the Twin connection drops / comes back
    cut_link       {"seconds": n}                   live and mock: the Brain's link to the Twin is cut for n seconds
    actuator_fault {"value": true|false}            live and mock: every command fails on the Brain's actuator channel
    refuse_commands {"value": true|false}           mock: the actuators refuse commands

Any step and any check may carry "modes": ["mock"] or ["live"] to apply only on that source.

Checks (all must pass):
    {"expect": "action", "agent"?, "action_type"?, "actuator"?, "actuator_prefix"?, "command"?}
    {"expect": "no_action", same filters}           {"expect": "max_actions", "value": n}
    {"expect": "ignored", "device"}                 {"expect": "conflict"}      {"expect": "error"}
    {"expect": "situation", "axis", "kind"?}        {"expect": "feedback_refused"}
    {"expect": "system", "contains"}               {"expect": "record", "kind", "contains"}
    {"expect": "only_zone", "zone"}                 every placed action in that zone (green waves excepted)
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import BRAIN_DIR
from .spotlight import Spotlight

SCENARIO_DIR = BRAIN_DIR / "mocks" / "scenarios"
MOCK_ONLY = {"incident", "resolve", "set", "ramp", "stick", "clear", "state", "twin_down", "refuse_commands"}


def load_index() -> List[dict]:
    """The groups of the Test Book, in order (index.json); without it, every file in one group."""
    try:
        idx = json.loads((SCENARIO_DIR / "index.json").read_text(encoding="utf-8"))
        return [g for g in idx.get("groups", []) if isinstance(g, dict) and isinstance(g.get("scenarios"), list)]
    except (OSError, ValueError):
        return [{"axis": "all", "title": "Scenarios", "scenarios": sorted(p.stem for p in SCENARIO_DIR.glob("*.json") if p.stem != "index")}]


def load_scenarios() -> Dict[str, dict]:
    out = {}
    for g in load_index():
        for sid in g["scenarios"]:
            p = SCENARIO_DIR / f"{sid}.json"
            try:
                spec = json.loads(p.read_text(encoding="utf-8"))
                spec.setdefault("axis", g.get("axis"))
                out[spec["id"]] = spec
            except (OSError, ValueError, KeyError) as e:
                out[sid] = {"id": sid, "name": f"invalid scenario file: {e}", "steps": [], "checks": [], "invalid": True,
                            "axis": g.get("axis")}
    return out


def _match(rec: dict, c: dict) -> bool:
    a = rec.get("payload") or {}
    t = a.get("target") or {}
    if c.get("agent") and a.get("agent") != c["agent"]:
        return False
    if c.get("action_type") and a.get("action_type") != c["action_type"]:
        return False
    if c.get("actuator") and t.get("actuator_id") != c["actuator"]:
        return False
    if c.get("actuator_prefix") and not str(t.get("actuator_id", "")).startswith(c["actuator_prefix"]):
        return False
    if c.get("command") and t.get("command") != c["command"]:
        return False
    return True


ONLY_ZONE_EXEMPT = {"green_wave", "street_lights", "signals_all"}      # route support and city-wide commands


def _in_zone(loc: Optional[dict], zone: str, margin: float = 15.0) -> bool:
    if not loc or loc.get("x") is None or loc.get("z") is None:
        return True                                   # a city-wide command (no place): not a zone matter
    i = "ABCDEFGHIJKLMNOP".index(zone)
    x0, z0 = -380.0 + (i % 4) * 190.0, -380.0 + (i // 4) * 190.0
    return x0 - margin <= float(loc["x"]) <= x0 + 190.0 + margin and z0 - margin <= float(loc["z"]) <= z0 + 190.0 + margin


def evaluate(records: List[dict], checks: List[dict]) -> List[dict]:
    actions = [r for r in records if r["kind"] == "action"]
    out = []
    for c in checks:
        e = c["expect"]
        if e == "action":
            hits = [r for r in actions if _match(r, c)]
            ok, got = bool(hits), (hits[0]["summary"] if hits else "no matching action")
        elif e == "no_action":
            hits = [r for r in actions if _match(r, c)]
            ok, got = not hits, (hits[0]["summary"] if hits else "none sent")
        elif e == "max_actions":
            ok, got = len(actions) <= int(c["value"]), f"{len(actions)} action(s)"
        elif e == "ignored":
            hits = [r for r in records if r["kind"] == "ignored" and c["device"] in r["summary"]]
            ok, got = bool(hits), (hits[0]["summary"] if hits else "not ignored")
        elif e in ("conflict", "error"):
            hits = [r for r in records if r["kind"] == e]
            ok, got = bool(hits), (hits[0]["summary"] if hits else f"no {e} record")
        elif e == "situation":
            hits = [r for r in records if r["kind"] == "situation" and r.get("axis") == c["axis"]
                    and (not c.get("kind") or (r.get("payload") or {}).get("kind") == c["kind"])]
            ok, got = bool(hits), (hits[0]["summary"] if hits else "no situation opened")
        elif e == "feedback_refused":
            hits = [r for r in records if r["kind"] == "feedback" and "refused" in r["summary"]]
            ok, got = bool(hits), (hits[0]["summary"] if hits else "every command applied")
        elif e == "record":
            hits = [r for r in records if r["kind"] == c["kind"] and c.get("contains", "") in r["summary"]]
            ok, got = bool(hits), (hits[0]["summary"] if hits else f"no {c['kind']} record containing '{c.get('contains', '')}'")
        elif e == "system":
            hits = [r for r in records if r["kind"] == "system" and c["contains"] in r["summary"]]
            ok, got = bool(hits), (hits[0]["summary"] if hits else "no such system record")
        elif e == "only_zone":
            # The scenario stays in its zone: every placed action is in the zone (or on its boundary roads, 15 m),
            # except the green waves that follow the units from their stations.
            out_of = [r for r in actions if (r.get("payload") or {}).get("action_type") not in ONLY_ZONE_EXEMPT
                      and not _in_zone((r.get("payload") or {}).get("location"), c["zone"])]
            ok, got = not out_of, (out_of[0]["summary"] if out_of else f"every action in zone {c['zone']}")
        else:
            ok, got = False, f"unknown check '{e}'"
        out.append({"check": c, "pass": ok, "observed": got})
    return out


class ScenarioRunner:
    def __init__(self, brain, spec: dict, drive: bool):
        self.brain = brain
        self.spec = spec
        self.drive = drive            # True: the runner calls brain.step() itself (CLI); False: the Brain loop runs
        self.state = "starting"
        self.result: Optional[dict] = None
        self.t = 0.0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.log_path = brain.cfg.logs_dir() / f"{spec['id']}-{stamp}.jsonl"
        self.stop_event = threading.Event()   # set by stop(): the run ends at its next tick
        self.sent: List[tuple] = []           # Twin commands this run sent, so a stop can undo them
        self.incidents_before: Optional[set] = None
        self.start_seq: Optional[int] = None
        self.spot: Optional[Spotlight] = None    # what to show on the map and in the Twin while it runs

    def stop(self) -> bool:
        """Ask a running scenario to stop. The run ends within 0.1 s, undoes its Twin changes and logs STOPPED."""
        if self.state not in ("starting", "running"):
            return False
        self.stop_event.set()
        return True

    def _open_incidents(self) -> set:
        try:
            res = self.brain.source.client.request("list_incidents", {}) or {}
            return {str(i.get("id")) for i in res.get("incidents", []) if i.get("id") and str(i.get("phase") or "active") != "resolved"}
        except Exception:
            return set()

    def _undo_live(self) -> List[str]:
        """Put the live Twin back after a stopped run: only what this run changed (weather, quake, clock speed,
        grid recharge, the incidents it triggered). A time of day set with CLOCK set_time stays as it is."""
        b, done = self.brain, []
        cmds = {(i, c) for i, c, _ in self.sent}
        weather_set = [v for i, c, v in self.sent if (i, c) == ("WEATHER", "set")]
        todo = []
        if weather_set and weather_set[-1] != "clear":
            todo.append(("WEATHER", "set", "clear"))
        if ("EARTHQUAKE", "trigger") in cmds:
            todo.append(("EARTHQUAKE", "clear", None))
        if any((i, c) == ("CLOCK", "set_speed") and v not in (1, "1") for i, c, v in self.sent):
            todo.append(("CLOCK", "set_speed", 1))
        if ("UTILITY", "auto_recharge_off") in cmds or ("UTILITY", "set_reserve") in cmds:
            todo += [("UTILITY", "recharge_all", None), ("UTILITY", "auto_recharge_on", None)]
        if self._closed_zones():
            todo.append(("ZONES", "reopen", {"zone": "", "reason": ""}))
        if ("INCIDENTS", "trigger") in cmds and self.incidents_before is not None:
            todo += [("INCIDENTS", "resolve", iid) for iid in sorted(self._open_incidents() - self.incidents_before)]
        for i, c, v in todo:
            try:
                b.source.client.request("command", {"id": i, "command": c, **({"value": v} if v is not None else {})})
                done.append(f"{i} {c}{'' if v is None else ' ' + str(v)}")
            except Exception as e:
                b.audit.write("error", None, b.source.name, f"{self.spec['id']}: undo {i} {c} failed: {e}", severity="alert")
        return done

    def _closed_zones(self) -> bool:
        """Did this run close a zone (a ZONES lockdown step, an earthquake, or a lockdown the Brain sent)?"""
        if any(i == "ZONES" and c == "lockdown" for i, c, _ in self.sent) or any(i == "EARTHQUAKE" for i, _, _ in self.sent):
            return True
        if self.start_seq is None:
            return False
        for r in self.brain.audit.since(self.start_seq, limit=100000, kinds={"action"}):
            target = (r.get("payload") or {}).get("target") or {}
            if target.get("actuator_id") == "ZONES" and target.get("command") == "lockdown":
                return True
        return False

    def _reopen_zones(self) -> None:
        """At the end of a live run every zone it closed is reopened: barriers, police cars and red lights go."""
        if self._city() is not None or not self._closed_zones():
            return
        b = self.brain
        try:
            b.source.client.request("command", {"id": "ZONES", "command": "reopen", "value": {"zone": "", "reason": ""}})
            b.audit.write("system", None, b.source.name, f"{self.spec['id']}: closed zones reopened at the end of the run",
                          scenario_id=self.spec["id"])
        except Exception as e:
            b.audit.write("error", None, b.source.name, f"{self.spec['id']}: reopening the zones failed: {e}", severity="alert")

    def view(self) -> dict:
        return {"id": self.spec["id"], "name": self.spec.get("name"), "axis": self.spec.get("axis"), "state": self.state,
                "t": round(self.t, 1), "duration_s": self.spec.get("duration_s"), "log": self.log_path.name,
                "result": (self.result or {}).get("verdict"),
                "focus": self.spot.view() if self.spot and self.state in ("starting", "running") else None}

    def _city(self):
        return getattr(self.brain.source, "city", None)

    def _apply(self, s: dict) -> None:
        b, city, do = self.brain, self._city(), s["do"]
        if s.get("modes") and b.source.name not in s["modes"]:
            return
        if do in MOCK_ONLY and city is None:
            b.audit.write("system", None, b.source.name, f"{self.spec['id']}: step '{do}' needs the mock Twin; skipped on live")
            return
        g = getattr(b.guardian, "g", None)
        if do == "twin_command":
            value = s.get("value")
            if isinstance(value, dict) and isinstance(value.get("near"), dict):
                value = self._near_vehicle(value)          # a breakdown "near" a place: the nearest car there (live)
            try:
                b.source.client.request("command", {"id": s["id"], "command": s["command"], **({"value": value} if "value" in s else {})})
                self.sent.append((s["id"], s["command"], s.get("value") if not isinstance(s.get("value"), dict) else None))
                b.audit.write("system", None, b.source.name, f"{self.spec['id']}: Twin command {s['id']} {s['command']} {s.get('value', '')}")
            except Exception as e:
                b.audit.write("error", None, b.source.name, f"{self.spec['id']}: Twin command failed: {e}", severity="alert")
        elif do == "incident":
            city.new_incident(s["kind"], s["x"], s["z"], iid=s.get("id"), resolve_after=s.get("resolve_after"))
        elif do == "resolve":
            city.resolve(s["id"])
        elif do == "set":
            city.set_reading(s["device"], s["metric"], s.get("value"))
        elif do == "ramp":
            city.ramp(s["device"], s["metric"], s["to"], s["over"])
        elif do == "stick":
            city.stick(s["device"], s["metric"])
        elif do == "clear":
            city.clear(s.get("device"))
        elif do == "state":
            city.set_state(s["device"], s["key"], s["value"])
        elif do == "push_event":
            ev = dict(s["event"])
            ev.setdefault("source", b.source.name)
            ev.setdefault("scenario_id", self.spec["id"])
            ok, msg = b.orch.ingest_raw(ev, pushed=True)
        elif do == "push_raw":
            b.orch.ingest_raw(s["body"], pushed=True)
        elif do == "flood_messages":
            dev = b.orch.city.latest.get(s["device"])
            for _ in range(int(s.get("count", 60))):
                if dev is None:
                    break
                b.orch.ingest_raw(dev.model_copy(update={"event_id": f"ev-flood{_:04d}", "scenario_id": self.spec["id"]}).dump(), pushed=True)
        elif do == "guardian":
            if g is not None:
                g.force(s["device"], s["status"], s.get("reason", "forced by scenario"))
        elif do == "guardian_down":
            if g is not None:
                g.down = bool(s.get("value", True))
        elif do == "twin_down":
            city.down = bool(s.get("value", True))
        elif do == "refuse_commands":
            city.refuse_commands = bool(s.get("value", True))
        elif do == "cut_link":
            b.source.link_cut_until = time.monotonic() + float(s.get("seconds", 10)) / max(float(self.spec.get("speed", 1.0)), 1e-6) \
                if city is not None else time.monotonic() + float(s.get("seconds", 10))
        elif do == "actuator_fault":
            b.source.actuator_fault = bool(s.get("value", True))

    def _recall_sent_units(self) -> None:
        """Units sent without an incident during the run (INCIDENTS send_unit: flood detour police, an ambulance on
        standby) go home when the run ends or is stopped, whether or not the agents recalled them already."""
        b = self.brain
        try:
            units = (b.source.client.request("list_emergency_units", {}) or {}).get("units", [])
        except Exception:
            return
        for u in units:
            tag = str(u.get("incident") or "")
            if tag and not tag.startswith("INC-") and u.get("status") not in ("garaged", "returning", "entering_depot", "patrol"):
                try:
                    b.source.client.request("command", {"id": "INCIDENTS", "command": "recall_unit", "value": u["unit"]})
                    b.audit.write("system", None, b.source.name, f"{self.spec['id']}: {u['unit']} ({tag}) recalled at the end of the run",
                                  scenario_id=self.spec["id"])
                except Exception:
                    pass

    def _near_vehicle(self, value: dict) -> dict:
        """INCIDENTS trigger {kind, near: {x, z}}: on the live Twin, the car nearest to that place becomes the incident's
        vehicle (the Twin places a breakdown on a vehicle); the stand-in uses x and z."""
        v = {k: w for k, w in value.items() if k != "near"}
        if self._city() is not None:
            return v
        try:
            nx, nz = float(value["near"]["x"]), float(value["near"]["z"])
            cars = (self.brain.source.client.request("list_vehicles", {"limit": 500}) or {}).get("vehicles", [])
            best = min((c for c in cars if not c.get("unit") and c.get("location")),
                       key=lambda c: (c["location"]["x"] - nx) ** 2 + (c["location"]["z"] - nz) ** 2, default=None)
            if best is not None and ((best["location"]["x"] - nx) ** 2 + (best["location"]["z"] - nz) ** 2) ** 0.5 < 120:
                v["vehicle"] = best["id"]
        except Exception:
            pass
        return v

    def _reset(self) -> None:
        city, g = self._city(), getattr(self.brain.guardian, "g", None)
        if city is not None:
            city.clear()
            city.weather, city.down, city.refuse_commands = "clear", False, False
            city.quakes.clear()
        if g is not None:
            g.down = False
            g.forced.clear()
            g.quarantined.clear()
            g.speed = self.spec.get("speed", 1.0)
        self.brain.source.link_cut_until = 0.0
        self.brain.source.actuator_fault = False

    def run(self, timeout_s: Optional[float] = None) -> dict:
        b, spec = self.brain, self.spec
        if spec.get("invalid"):
            self.result = {"id": spec["id"], "verdict": "FAIL", "checks": [], "error": spec["name"]}
            return self.result
        if b.source.name not in spec.get("modes", ["mock"]):
            self.result = {"id": spec["id"], "verdict": "SKIPPED", "checks": [],
                           "error": f"this scenario does not run on the {b.source.name} source"}
            self.state = "skipped"
            return self.result
        fh = open(self.log_path, "w", encoding="utf-8")
        b.audit.add_sink(fh)
        self.state = "running"
        try:
            with b.lock:
                self._reset()
                b.orch.fresh_start()
                b.orch.scenario_id = spec["id"]
                city = self._city()
                if city is not None:
                    city.speed = float(spec.get("speed", 1.0))
                    city.t0 = time.monotonic() - city.t / city.speed
                    b.source.norm.scenario_id = spec["id"]
                start_seq = b.audit.last_seq
                self.start_seq = start_seq
                b.audit.write("system", None, b.source.name, f"Scenario {spec['id']} started: {spec.get('name')}",
                              payload={"scenario": {k: spec.get(k) for k in ("id", "name", "layers", "description", "trigger", "expected")}},
                              scenario_id=spec["id"])
            live = self._city() is None
            self.incidents_before = self._open_incidents()
            with b.lock:
                self.spot = Spotlight(b, spec, start_seq, self.incidents_before)
            steps = sorted(({**s, "at": s.get("live_at", s.get("at", 0))} if live else s for s in spec.get("steps", [])),
                           key=lambda s: s.get("at", 0))
            # the live Twin runs at its own pace: real seconds, and its own (longer) duration when the scenario gives one
            dur = float(spec.get("live_duration_s", spec.get("duration_s", 30))) if live else float(spec.get("duration_s", 30))
            speed = 1.0 if live else float(spec.get("speed", 1.0))
            t_start = time.monotonic()
            i = 0
            while True:
                self.t = (time.monotonic() - t_start) * speed
                while i < len(steps) and steps[i].get("at", 0) <= self.t:
                    with b.lock:
                        self._apply(steps[i])
                    i += 1
                if self.drive:
                    b.step()
                if time.monotonic() - getattr(self, "_spot_at", 0.0) >= 0.4:
                    self._spot_at = time.monotonic()
                    with b.lock:
                        try:
                            self.spot.update()
                        except Exception as e:           # presentation must never break a run
                            b.audit.write("error", None, b.source.name, f"{spec['id']}: spotlight: {type(e).__name__}: {e}")
                if self.t >= dur or (timeout_s and time.monotonic() - t_start > timeout_s) or self.stop_event.is_set():
                    break
                time.sleep(0.05 if self.drive else 0.1)
            with b.lock:
                self.spot.close()
                self._recall_sent_units()
                if not self.stop_event.is_set():
                    self._reopen_zones()             # (a stopped run reopens them in _undo_live)
            if self.stop_event.is_set():
                return self._finish_stopped(city)
            with b.lock:
                recs = [r for r in b.audit.since(start_seq, limit=100000) if r.get("scenario_id") in (spec["id"], None)]
                checks = evaluate(recs, [c for c in spec.get("checks", []) if b.source.name in c.get("modes", ["mock", "live"])])
                verdict = "PASS" if all(c["pass"] for c in checks) else "FAIL"
                self.result = {"id": spec["id"], "name": spec.get("name"), "verdict": verdict, "checks": checks,
                               "log": str(self.log_path), "records": len(recs), "source": b.source.name}
                self.result["report"] = write_report(self.log_path, spec, self.result, recs, self.t)
                b.audit.write("system", None, b.source.name, f"Scenario {spec['id']} finished: {verdict}",
                              severity="info" if verdict == "PASS" else "alert", payload=self.result, scenario_id=spec["id"])
                b.orch.scenario_id = None
                if city is not None:
                    b.source.norm.scenario_id = None
                    self._reset()
                    city.speed = 1.0
                    city.t0 = time.monotonic() - city.t
            self.state = verdict.lower()
            return self.result
        finally:
            b.audit.remove_sink(fh)
            fh.close()


    def _finish_stopped(self, city) -> dict:
        b, spec = self.brain, self.spec
        with b.lock:
            undone = self._undo_live() if city is None else []
            self.result = {"id": spec["id"], "name": spec.get("name"), "verdict": "STOPPED", "checks": [],
                           "log": str(self.log_path), "source": b.source.name, "undone": undone,
                           "stopped_at_s": round(self.t, 1)}
            if self.start_seq is not None:
                recs = [r for r in b.audit.since(self.start_seq, limit=100000) if r.get("scenario_id") in (spec["id"], None)]
                self.result["report"] = write_report(self.log_path, spec, self.result, recs, self.t)
            b.audit.write("system", None, b.source.name,
                          f"Scenario {spec['id']} stopped by the operator at t={self.t:.0f} s"
                          + (f"; undone on the Twin: {', '.join(undone)}" if undone else ""),
                          severity="watch", payload=self.result, scenario_id=spec["id"])
            b.orch.scenario_id = None
            if city is not None:
                b.source.norm.scenario_id = None
                self._reset()
                city.speed = 1.0
                city.t0 = time.monotonic() - city.t
            else:
                self.brain.source.link_cut_until = 0.0
                self.brain.source.actuator_fault = False
        self.state = "stopped"
        return self.result


# ---------------------------------------------------------------------- readable run report (logs/<ID>-<stamp>.txt)
_BANDS = ["North", "NorthCentral", "SouthCentral", "South"]


def _human(text: str) -> str:
    """Device codes in plain words for the report (the .jsonl log keeps the exact ids)."""
    import re
    t = str(text or "")
    t = re.sub(r"ZONE-Zone_(North|NorthCentral|SouthCentral|South)_([1-4])",
               lambda m: f"zone {'ABCDEFGHIJKLMNOP'[_BANDS.index(m.group(1)) * 4 + int(m.group(2)) - 1]} power", t)
    t = re.sub(r"SIG-Junction_r(\d)_c(\d)", r"signal r\1c\2", t)
    for a, b in (("ns_green", "north-south green"), ("ew_green", "east-west green"), ("reset_cycle", "back to normal cycle"),
                 ("all_red", "all red"), ("cut_power", "cut power"), ("restore_power", "restore power"),
                 ("cut_water", "cut water"), ("restore_water", "restore water")):
        t = t.replace(a, b)
    return t


def _wrap(text: str, indent: str, width: int = 110) -> str:
    import textwrap
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent + "  ")


def write_report(log_path: Path, spec: dict, result: dict, recs: List[dict], duration: float) -> Optional[str]:
    """A plain-text report of a run beside its .jsonl log: verdict and checks, then what each axis did, in order (only
    the decisions that led to a command, a request to another axis, a refusal or an alert), then a count per axis.
    Never raises: a report must not change a run."""
    try:
        from datetime import datetime as _dt
        path = Path(log_path).with_suffix(".txt")
        t0 = None
        for r in recs:
            try:
                t0 = _dt.fromisoformat(r["ts"].replace("Z", "+00:00"))
                break
            except (KeyError, ValueError):
                continue

        def rel(r: dict) -> str:
            try:
                return f"+{(_dt.fromisoformat(r['ts'].replace('Z', '+00:00')) - t0).total_seconds():>4.0f} s"
            except (KeyError, ValueError, TypeError):
                return "      "

        out = [f"SCENARIO {spec['id']} · {spec.get('name', '')}",
               f"Result: {result.get('verdict')}   Source: {result.get('source')}   Duration: {duration:.0f} s"
               + (f"   Stopped at {result.get('stopped_at_s')} s" if result.get("verdict") == "STOPPED" else ""),
               "", _wrap("What it tests: " + str(spec.get("description", "")), ""), ""]
        checks = result.get("checks") or []
        if checks:
            ok = sum(1 for c in checks if c.get("pass"))
            out.append(f"CHECKS: {ok} of {len(checks)} passed")
            for c in checks:
                what = ", ".join(f"{k} {v}" for k, v in c["check"].items() if k not in ("expect", "modes"))
                out.append(_wrap(f"[{'ok' if c['pass'] else 'FAIL'}] {c['check'].get('expect')}: {_human(what)}", "  "))
                if not c["pass"]:
                    out.append(_wrap(f"observed: {_human(c.get('observed', ''))}", "         "))
            out.append("")
        if result.get("undone"):
            out += [f"Undone on the Twin after the stop: {', '.join(result['undone'])}", ""]
        traces: Dict[str, List[dict]] = {}
        order: List[str] = []
        for r in recs:
            tid = r.get("trace_id")
            if r["kind"] in ("decision", "action", "feedback", "a2a", "conflict", "ignored") and tid:
                if tid not in traces:
                    traces[tid] = []
                    order.append(tid)
                traces[tid].append(r)
        out.append("WHAT HAPPENED (time from the start of the run)")
        counts: Dict[str, Dict[str, int]] = {}
        for tid in order:
            rs = traces[tid]
            important = any(r["kind"] in ("action", "a2a", "conflict", "ignored") or r.get("severity") in ("alert", "critical")
                            for r in rs)
            dec = next((r for r in rs if r["kind"] == "decision"), None)
            axis = (dec or rs[0]).get("axis") or "brain"
            c = counts.setdefault(axis, {"decisions": 0, "commands": 0, "refused": 0})
            c["decisions"] += sum(1 for r in rs if r["kind"] == "decision")
            if not important:
                continue
            acts = [r for r in rs if r["kind"] == "action"]
            c["commands"] += len(acts)
            c["refused"] += sum(1 for r in rs if r["kind"] in ("conflict", "ignored")
                                or (r["kind"] == "feedback" and "refused" in r["summary"]))
            text = str((dec or rs[0])["summary"])
            text = text[text.index("] ") + 2:] if text.startswith("[") and "] " in text else text
            title, _, why = text.partition(": ")
            if dec is None and all(r["kind"] in ("a2a", "feedback") for r in rs):
                title = "Requests between axes: " + title.replace("A2A ", "", 1)
            out.append("")
            out.append(f"{rel(rs[0])}  {axis.upper():<15}{_human(title)}")
            if dec and why:
                out.append(_wrap("why: " + _human(why.replace(" -> no action needed", "")), " " * 25))
            for r in rs:
                s = str(r["summary"])
                if r["kind"] == "action":
                    body = s.split(": ", 1)[1] if ": " in s else s
                    what = body.split(" because ")[0]
                    fb = next((f for f in rs if f["kind"] == "feedback" and f["seq"] > r["seq"]
                               and all(w in f["summary"] for w in what.split(" ")[:2])), None)
                    answer = "" if fb is None else (" -> done" if "refused" not in fb["summary"] else
                                                    " -> REFUSED: " + fb["summary"].split(": ", 1)[-1])
                    out.append(_wrap(f"{rel(r)}  command: {_human(what)}{_human(answer)}", " " * 19))
                elif r["kind"] == "a2a" and " working: " not in s and " submitted: " not in s:
                    label = "answer" if any(w in s for w in (" completed", " failed", " rejected")) else "asks"
                    out.append(_wrap(f"{rel(r)}  {label}: {_human(s.replace('A2A ', '', 1))}", " " * 19))
                elif r["kind"] in ("conflict", "ignored"):
                    out.append(_wrap(f"{rel(r)}  {'conflict' if r['kind'] == 'conflict' else 'not sent'}: {_human(s)}", " " * 19))
        alerts = [r for r in recs if r["kind"] in ("system", "error") and r.get("severity") in ("alert", "critical")]
        if alerts:
            out += ["", "ALERTS"] + [_wrap(f"{rel(r)}  {_human(r['summary'])}", "  ") for r in alerts]
        out += ["", "BY AXIS"] + [f"  {a:<15}{v['decisions']:>3} decision(s), {v['commands']:>3} command(s), {v['refused']:>2} refused / not sent"
                                  for a, v in sorted(counts.items())]
        out += ["", f"Full record (every reading, verdict and answer, one JSON per line): {Path(log_path).name}"]
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return str(path)
    except Exception:
        return None


def run_in_background(brain, spec: dict) -> ScenarioRunner:
    r = ScenarioRunner(brain, spec, drive=False)
    brain.scenario = r
    threading.Thread(target=r.run, name=f"scenario-{spec['id']}", daemon=True).start()
    return r

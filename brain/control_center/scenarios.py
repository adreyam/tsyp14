"""Demo scenarios for the control center: three main scenarios per axis and two city-wide ones.

A scenario is a timed list of steps. Each step is either a whitelisted demo control (the same
ones as the Demo controls drawers) or an agent tool call. Steps marked as `end` undo what the
scenario changed: they run when the scenario finishes, or at once on "Reset all".

The four Brains do not talk to each other. In a city-wide scenario the control center plays
the incident on every axis at the same moment, and each Brain answers it with its own rules
and its own Guardian.
"""
import threading
import time
from typing import Callable, Dict, List, Optional

# Car parks of the transport map (lat, lon), used as emergency destinations.
HOSPITAL = (36.80831458857348, 10.180750531673665)       # P06 Hospital Parking
HARBOR = (36.80292472152354, 10.182994448818379)         # P08 Harbor Parking, industrial side
# Placeholder resolved when the scenario starts: the bin with the lowest fill, so the fake 98%
# is a jump big enough for the Guardian to catch whatever the city looks like at that moment.
EMPTIEST = "@emptiest_bin"


def ctl(name, ident=None, **body):
    return {"type": "control", "name": name, "id": ident, "body": body}


def agent(name, tool, **args):
    return {"type": "agent", "agent": name, "tool": tool, "args": args}


def step(at, axis, label, action, retry=0):
    """`retry`: seconds during which a refused step is tried again every 5 s (e.g. a car still busy)."""
    return {"at": at, "axis": axis, "label": label, "action": action, "retry": retry}


CATALOG: List[dict] = [
    # ------------------------------------------------------------- waste
    {"id": "W1", "axis": "waste", "title": "Bin filling fast",
     "watch": "A real surge on b04: the Brain projects it full and sends a truck before it overflows.",
     "duration": 60,
     "steps": [step(0, "waste", "b04 starts filling fast (real)", ctl("waste_air.control", action="surge", sensor_id="b04"))],
     "end": []},
    {"id": "W2", "axis": "waste", "title": "Spoofed full bin",
     "watch": "The emptiest bin suddenly reports 98%: the reading is ignored and no truck leaves.",
     "duration": 60,
     "steps": [step(0, "waste", "{bin} reports a fake 98%", ctl("waste_air.control", action="spoof", sensor_id=EMPTIEST, value=98))],
     "end": [step(0, "waste", "{bin} reports its real level again", ctl("waste_air.control", action="unspoof", sensor_id=EMPTIEST))]},
    {"id": "W3", "axis": "waste", "title": "Partners fail",
     "watch": "The Guardian goes down, then the Twin refuses dispatches: the Brain holds, retries, then recovers.",
     "duration": 70,
     "steps": [step(0, "waste", "Guardian goes down", ctl("waste_air.control", action="guardian_down")),
               step(25, "waste", "Guardian is back", ctl("waste_air.control", action="guardian_up")),
               step(30, "waste", "Twin refuses dispatches", ctl("waste_air.control", action="twin_offline"))],
     "end": [step(0, "waste", "Twin accepts dispatches again", ctl("waste_air.control", action="twin_online")),
             step(0, "waste", "Guardian is up", ctl("waste_air.control", action="guardian_up"))]},
    # ------------------------------------------------------------- air quality
    {"id": "A1", "axis": "air", "title": "Industrial release",
     "watch": "A real particle release in zone D: the risk climbs, schools are alerted, car parks close, then all is lifted.",
     "duration": 100,
     "steps": [step(0, "air", "Particle release in zone D (real)", ctl("waste_air.control", action="air_release", sensor_id="D", value=40))],
     "end": [step(0, "air", "Release over", ctl("waste_air.control", action="air_release_stop"))]},
    {"id": "A2", "axis": "air", "title": "Fake pollution spike",
     "watch": "AIR-05 reports an impossible PM2.5 of 180: the Guardian flags it and no false alert goes out.",
     "duration": 60,
     "steps": [step(0, "air", "AIR-05 reports a fake PM2.5 spike", ctl("waste_air.control", action="air_spike", sensor_id="AIR-05"))],
     "end": [step(0, "air", "Air sensors report the real air again", ctl("waste_air.control", action="air_stop"))]},
    {"id": "A3", "axis": "air", "title": "Real peak during an attack",
     "watch": "A fake spike in zone C and a real release in zone B at the same time: the Brain ignores one and acts on the other.",
     "duration": 100,
     "steps": [step(0, "air", "AIR-05 (zone C) reports a fake spike", ctl("waste_air.control", action="air_spike", sensor_id="AIR-05")),
               step(5, "air", "Real particle release in zone B", ctl("waste_air.control", action="air_release", sensor_id="B", value=40))],
     "end": [step(0, "air", "Release over", ctl("waste_air.control", action="air_release_stop")),
             step(0, "air", "Air sensors report the real air again", ctl("waste_air.control", action="air_stop"))]},
    # ------------------------------------------------------------- energy (Test Book scripts of the energy Brain)
    {"id": "E2", "axis": "energy", "title": "Predicted overload",
     "watch": "A surge is forecast 45 s ahead: the Brain closes a tie switch before the zone overloads.",
     "duration": 120,
     "steps": [step(0, "energy", "Energy scenario E2 starts", ctl("energy.scenario", "E2"))], "end": []},
    {"id": "E4", "axis": "energy", "title": "Protect the hospital",
     "watch": "Load rises around Z3: the hospital zone keeps power first, other zones give way.",
     "duration": 120,
     "steps": [step(0, "energy", "Energy scenario E4 starts", ctl("energy.scenario", "E4"))], "end": []},
    {"id": "E3", "axis": "energy", "title": "Spoofed load spike",
     "watch": "A meter reports a fake jump: physics and the Guardian disagree, so no switch moves.",
     "duration": 120,
     "steps": [step(0, "energy", "Energy scenario E3 starts", ctl("energy.scenario", "E3"))], "end": []},
    # ------------------------------------------------------------- transport
    {"id": "T1", "axis": "transport", "title": "Ambulance to the hospital",
     "watch": "An ambulance crosses the city: every signal on its route turns green ahead of it.",
     "duration": 90,
     "steps": [step(0, "transport", "Ambulance sent to the hospital",
                    agent("transport", "dispatch_emergency", kind="ambulance", lat=HOSPITAL[0], lon=HOSPITAL[1], call_id="CC-T1"))],
     "end": []},
    {"id": "T2", "axis": "transport", "title": "Rush hour and jam",
     "watch": "Traffic jumps to 1.8 times normal and 300 extra cars arrive: the learned signals absorb the jam.",
     "duration": 120,
     "steps": [step(0, "transport", "Traffic level 1.8", ctl("transport.controls", traffic_level=1.8)),
               step(5, "transport", "300 extra cars in 60 s", ctl("transport.jam", size=300, seconds=60))],
     "end": [step(0, "transport", "Traffic back to normal", ctl("transport.controls", traffic_level=1.0))]},
    {"id": "T3", "axis": "transport", "title": "Parking sensor attack",
     "watch": "A parking sensor lies, then another floods the network: the Guardian distrusts both, "
              "the Brain isolates them and keeps the car park count true.",
     "duration": 60,
     "steps": [step(0, "transport", "Sensor P02-A1 claims a car that is not there",
                    ctl("transport.fake_sensor", lot_id="P02", space="A1", occupied=True, seconds=25)),
               step(30, "transport", "Sensor P05-A2 floods 120 messages per second",
                    ctl("transport.flood", lot_id="P05", space="A2", per_second=120, seconds=6))],
     "end": []},
    # ------------------------------------------------------------- whole city
    {"id": "C1", "axis": "city", "title": "Industrial fire in zone D",
     "watch": "Smoke in zone D, a load surge at the industrial feeder, fire truck and ambulance across town, "
              "bins filling around the site. Every Brain answers its part.",
     "duration": 120,
     "steps": [step(0, "air", "Smoke: particle release in zone D", ctl("waste_air.control", action="air_release", sensor_id="D", value=45)),
               step(0, "energy", "Energy free play, then +180 kW in Z4 (industrial)", ctl("energy.scenario", "FREE")),
               step(2, "energy", "Z4 load +180 kW over 20 s", ctl("energy.inject", action="ramp", args={"zone_id": "Z4", "add_kw": 180, "rise_s": 20})),
               step(3, "transport", "Fire truck sent to the site", agent("transport", "dispatch_emergency", kind="fire", lat=HARBOR[0], lon=HARBOR[1], call_id="CC-C1-FIRE")),
               step(15, "transport", "Ambulance sent to the hospital", agent("transport", "dispatch_emergency", kind="ambulance", lat=HOSPITAL[0], lon=HOSPITAL[1], call_id="CC-C1-AMB")),
               step(20, "waste", "Bins around the site fill fast", ctl("waste_air.control", action="surge", sensor_id="b09"))],
     "end": [step(0, "air", "Fire out: release over", ctl("waste_air.control", action="air_release_stop"))]},
    {"id": "C2", "axis": "city", "title": "Coordinated cyberattack",
     "watch": "Fake readings hit every axis at once: a bin, an air sensor, a power meter and a parking sensor. "
              "No truck leaves, no alert goes out, no switch moves, and the car park count stays true.",
     "duration": 80,
     "steps": [step(0, "waste", "{bin} reports a fake 98%", ctl("waste_air.control", action="spoof", sensor_id=EMPTIEST, value=98)),
               step(0, "air", "AIR-05 reports a fake PM2.5 spike", ctl("waste_air.control", action="air_spike", sensor_id="AIR-05")),
               step(0, "energy", "Energy free play", ctl("energy.scenario", "FREE")),
               step(2, "energy", "Meter PL-Z1 spoofed at 900 kW for 30 s", ctl("energy.inject", action="spoof", args={"sensor_id": "PL-Z1", "value_kw": 900, "duration_s": 30})),
               step(4, "transport", "Parking sensor P03-A1 claims a car that is not there",
                    ctl("transport.fake_sensor", lot_id="P03", space="A1", occupied=True, seconds=30))],
     "end": [step(0, "waste", "{bin} reports its real level again", ctl("waste_air.control", action="unspoof", sensor_id=EMPTIEST)),
             step(0, "air", "Air sensors report the real air again", ctl("waste_air.control", action="air_stop"))]},
]

RESET_ALL = [
    step(0, "waste", "Twin accepts dispatches", ctl("waste_air.control", action="twin_online")),
    step(0, "waste", "Guardian is up", ctl("waste_air.control", action="guardian_up")),
    step(0, "air", "Release over", ctl("waste_air.control", action="air_release_stop")),
    step(0, "air", "Air sensors report the real air", ctl("waste_air.control", action="air_stop")),
    step(0, "energy", "Energy back to free play", ctl("energy.scenario", "FREE")),
    step(0, "transport", "Traffic back to normal", ctl("transport.controls", traffic_level=1.0)),
]


class Runner:
    """Plays scenarios in background threads and reports their progress."""

    def __init__(self, control: Callable, call_agent: Callable, feed, bins: Callable = lambda: {}):
        self.control, self.call_agent, self.feed, self.bins = control, call_agent, feed, bins
        self.spoofed: set = set()                    # bins spoofed by a scenario, unspoofed on reset
        self.by_id = {s["id"]: s for s in CATALOG}
        self.runs: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def _emptiest(self) -> str:
        """The trusted bin with the lowest fill right now (b06 when the Brain is not known yet)."""
        bins = self.bins() or {}
        state = bins.get("bins") or {}
        blocked = bins.get("blocked_bins") or {}
        cands = [(b.get("fill_pct", 100), sid) for sid, b in state.items() if sid not in blocked]
        return min(cands)[1] if cands else "b06"

    # -- one action
    def _do(self, st: dict, ctx: Optional[dict] = None) -> Optional[str]:
        a = st["action"]
        try:
            if a["type"] == "control":
                body = dict(a["body"])
                if body.get("sensor_id") == EMPTIEST:
                    body["sensor_id"] = (ctx or {}).get("bin") or self._emptiest()
                if a["name"] == "waste_air.control" and body.get("action") == "spoof":
                    self.spoofed.add(body["sensor_id"])        # bins only: energy spoofs carry their own args
                elif a["name"] == "waste_air.control" and body.get("action") == "unspoof":
                    self.spoofed.discard(body["sensor_id"])
                self.control(a["name"], a["id"], body)
            else:
                res = self.call_agent(a["agent"], a["tool"], dict(a["args"]))
                if not res.get("ok"):
                    err = res.get("error") or {}
                    return err.get("message") or err.get("code") or "refused"
            return None
        except Exception as e:  # noqa: BLE001 - reported to the operator, never fatal
            return str(e)[:200]

    def _play(self, sc: dict, run: dict) -> None:
        t0 = time.monotonic()
        ctx = run["ctx"]
        for i, st in enumerate(sorted(sc["steps"], key=lambda s: s["at"])):
            while not run["cancel"].is_set() and time.monotonic() - t0 < st["at"]:
                run["cancel"].wait(0.2)
            if run["cancel"].is_set():
                break
            err = self._do(st, ctx)
            t_retry = time.monotonic()
            while err and st.get("retry") and time.monotonic() - t_retry < st["retry"] and not run["cancel"].is_set():
                run["cancel"].wait(5)
                err = self._do(st, ctx)
            label = st["label"].format(**ctx)
            run["done"].append({"label": label, "axis": st["axis"], "ok": err is None, "error": err})
            self.feed.add(st["axis"], "watch" if err else "info",
                          f"Scenario {sc['id']} · {label}" + (f": failed ({err})" if err else ""),
                          kind="scenario")
        # wait for the end of the scenario, then undo what it changed
        while not run["cancel"].is_set() and time.monotonic() - t0 < sc["duration"]:
            run["cancel"].wait(0.25)
        for st in sc["end"]:
            self._do(st, ctx)
        run["state"] = "stopped" if run["cancel"].is_set() else "done"
        run["ended"] = time.time()
        failed = sum(1 for d in run["done"] if not d["ok"])
        self.feed.add(sc["axis"], "watch" if failed else "info",
                      f"Scenario {sc['id']} {sc['title']} {'stopped' if run['state'] == 'stopped' else 'finished'}"
                      + (f", {failed} step(s) failed" if failed else ""), kind="scenario")

    # -- API
    def start(self, sid: str) -> dict:
        sc = self.by_id.get(sid)
        if not sc:
            raise KeyError(f"unknown scenario {sid}")
        with self.lock:
            cur = self.runs.get(sid)
            if cur and cur["state"] == "running":
                return self.view(sid)
            run = {"state": "running", "started": time.time(), "ended": None, "done": [],
                   "cancel": threading.Event(), "ctx": {"bin": self._emptiest()}}
            self.runs[sid] = run
        self.feed.add(sc["axis"], "info", f"Scenario {sc['id']} {sc['title']} started", kind="scenario")
        threading.Thread(target=self._play, args=(sc, run), daemon=True).start()
        return self.view(sid)

    def reset(self) -> dict:
        with self.lock:
            runs = [r for r in self.runs.values() if r["state"] == "running"]
        for r in runs:
            r["cancel"].set()
        undo = [step(0, "waste", f"{b} reports its real level", ctl("waste_air.control", action="unspoof", sensor_id=b))
                for b in sorted(self.spoofed)]
        errors = [e for e in (self._do(st) for st in RESET_ALL + undo) if e]
        self.feed.add("city", "info", "All scenarios reset: every axis is back to normal operation", kind="scenario")
        return {"ok": not errors, "stopped": len(runs), "errors": errors}

    def view(self, sid: str) -> dict:
        sc, run = self.by_id[sid], self.runs.get(sid)
        out = {"id": sid, "axis": sc["axis"], "title": sc["title"], "watch": sc["watch"],
               "duration": sc["duration"],
               "steps": [s["label"].format(bin=(run or {}).get("ctx", {}).get("bin", "a bin")) for s in sc["steps"]],
               "axes": sorted({s["axis"] for s in sc["steps"]}), "state": "idle"}
        if run:
            now = run["ended"] or time.time()
            out.update(state=run["state"], elapsed=round(now - run["started"], 1),
                       done=[{k: d[k] for k in ("label", "axis", "ok", "error")} for d in run["done"]])
        return out

    def catalog(self) -> List[dict]:
        return [self.view(s["id"]) for s in CATALOG]

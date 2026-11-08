"""
Pollution agent: air quality (risk score per zone), waste bins, noise.

Air risk score (the air-quality axis method): each pollutant is divided by its WHO 2021 24 h guideline and
multiplied by 100; the zone's score is the highest of them (PM2.5, PM10, NO2; the Twin has no O3 sensor).
    score >= level_watch (70)     watch
    score >= level_alert (100)    alert: ask transportation (A2A close_car_parks) to close the zone's car parks, so
                                  fewer cars drive in; reopen_car_parks when the air is back under the watch level
    score >= level_critical (130) critical
A level must hold for `persist_samples` readings in a row before it counts, and the car parks reopen (auto)
only once the score is back under the watch level for the same number of readings (no flapping).

Waste: a bin above `bin_full_pct` gets an empty command (the Twin sends a crew). When `round_min_bins` bins or
more are above `round_pct`, the agent orders a garbage-truck round (INCIDENTS trigger garbage_collection, the Twin's
own collection round), at most once per `round_cooldown_s`. Every truck round of the Twin (ordered by the Brain or
not) is followed as a situation until the Twin closes it.
Noise: above `noise_watch_dba` for several readings is reported (no actuator for noise in the Twin).
"""
from __future__ import annotations

from typing import Dict, List

from citybrain.contracts import Event
from citybrain.agents.base import Agent, CityView, Finding, Plan, loc_of


# First container of the Twin's garbage-round route in each zone (twin/runtime/sumo/garbage_bins.json): a round
# ordered for a zone starts there, so it happens in that zone.
ROUND_START = {"A": ("BIN-01", -238.5, -291.6), "B": ("BIN-02", -143.5, -291.6), "C": ("BIN-04", 46.5, -291.6),
               "D": ("BIN-06", 236.5, -291.6), "E": ("BIN-15", -278.4, -48.5), "F": ("BIN-09", -183.3, -135.5),
               "G": ("BIN-19", 46.5, -101.7), "H": ("BIN-13", 196.7, -135.5), "I": ("BIN-23", -278.4, 46.5),
               "J": ("BIN-24", -183.3, 46.5), "K": ("BIN-26", 6.7, 46.5), "L": ("BIN-29", 196.7, 46.5),
               "M": ("BIN-37", -278.4, 236.5), "N": ("BIN-38", -88.3, 236.5), "O": ("BIN-39", 6.7, 236.5),
               "P": ("BIN-42", 291.6, 236.5)}


class PollutionAgent(Agent):
    """Pollution: air quality per zone, waste bins and garbage-truck rounds, noise."""
    axis = "pollution"
    families = {"air", "waste", "noise"}
    incident_kinds = {"garbage_collection"}
    skills = {"air_status": "the air risk score and level of a zone, from its worst sensor (input: zone); answered at once"}

    def __init__(self, cfg):
        super().__init__(cfg)
        self.limits = dict(self.p.get("limits") or {"pm2_5": 15, "pm10": 45, "nitrogen_dioxide": 25})
        self.count: Dict[str, tuple] = {}         # zone -> (level, consecutive)
        self.level: Dict[str, str] = {}           # zone -> current level
        self.calm: Dict[str, int] = {}
        self.bins: set = set()
        self.loud: Dict[str, int] = {}
        self.sensor: Dict[str, tuple] = {}        # device -> (zone, score, metric, value)
        self.fill: Dict[str, float] = {}          # bin -> last fill level
        self.parks_asked: Dict[str, str] = {}     # zone -> close_car_parks task id (A2A to transportation)
        self.round_open = False
        self.round_at = -1e9

    def score(self, ev: Event) -> tuple:
        parts = {m: (ev.value(m) or 0.0) / lim * 100 for m, lim in self.limits.items() if ev.value(m) is not None}
        if not parts:
            return 0.0, None
        m = max(parts, key=parts.get)
        return parts[m], m

    def detect(self, ev: Event, city: CityView) -> List[Finding]:
        if ev.type == "incident":
            inc = ev.incident
            where = f", zone {ev.location.zone}" if ev.location else ""
            key = f"pollution:truck:{inc.id}"
            self.rounds_open = getattr(self, "rounds_open", set())
            if inc.status == "resolved":
                self.rounds_open.discard(inc.id)
                if not self.rounds_open:
                    self.round_open = False      # the Twin closed the round: the next full zone gets a new one
                return [Finding(key, f"Garbage truck round {inc.id}", "garbage_round", "info", 0.95,
                                f"the Twin closed collection round {inc.id}", ev.location, {"incident": inc.id}, resolved=True)]
            self.rounds_open.add(inc.id)
            return [Finding(key, f"Garbage truck round {inc.id}{where}", "garbage_round", "info", 0.95,
                            f"truck on collection round {inc.id} (phase {inc.phase}); units: "
                            f"{', '.join(u for u in ev.state.get('units') or [] if u) or 'assigning'}", ev.location, {"incident": inc.id})]
        fam = ev.origin.family
        if fam == "waste":
            return self._bin(ev) + self._round(ev)
        if fam == "noise":
            return self._noise(ev)
        return self._air(ev)

    def _air(self, ev: Event) -> List[Finding]:
        zone = ev.location.zone
        own, own_metric = self.score(ev)
        self.sensor[ev.origin.device_id] = (zone, own, own_metric, ev.value(own_metric) if own_metric else 0.0)
        # the zone's score is its worst sensor (two monitors in one zone must not cancel each other)
        dev, (_, s, metric, value) = max(((d, v) for d, v in self.sensor.items() if v[0] == zone), key=lambda kv: kv[1][1])
        lw, la, lc = self.num("level_watch", 70), self.num("level_alert", 100), self.num("level_critical", 130)
        need = int(self.num("persist_samples", 3))
        lvl = "critical" if s >= lc else "alert" if s >= la else "watch" if s >= lw else "info"
        key = f"pollution:air:{zone}"
        what = (f"{dev}: risk score {s:.0f} driven by {metric} {value:.1f} µg/m³ "
                f"(guideline {self.limits.get(metric)} µg/m³; levels {lw:g}/{la:g}/{lc:g})") if metric else ""
        prev_lvl, n = self.count.get(zone, ("info", 0))
        n = n + 1 if lvl == prev_lvl else 1
        self.count[zone] = (lvl, n)
        cur = self.level.get(zone, "info")
        if lvl == "info":
            if cur != "info":
                self.calm[zone] = self.calm.get(zone, 0) + 1
                if self.calm[zone] >= need:
                    self.level[zone], self.calm[zone] = "info", 0
                    return [Finding(key, f"Air quality in zone {zone}", "air", "info", 0.9,
                                    f"{what}; under {lw:g} for {need} readings", ev.location, {"score": s}, resolved=True)]
            return []
        self.calm[zone] = 0
        if n < need or lvl == cur:
            return []
        self.level[zone] = lvl
        return [Finding(key, f"Air quality in zone {zone}", "air", lvl, min(0.95, 0.6 + 0.1 * n),
                        f"{what}; held for {n} readings", ev.location, {"score": s, "metric": metric, "previous": cur})]

    def on_task(self, task, city: CityView):
        zone = str(task.input.get("zone", "")).upper()
        if task.skill != "air_status" or len(zone) != 1 or zone not in "ABCDEFGHIJKLMNOP":
            self.a2a.update(task, "rejected", "air_status needs a zone A..P")
            return []
        rows = [(d, v) for d, v in self.sensor.items() if v[0] == zone]
        if not rows:
            self.a2a.update(task, "completed", f"no trusted air sensor reading in zone {zone} yet", {"zone": zone, "score": None})
            return []
        dev, (_, score, metric, value) = max(rows, key=lambda kv: kv[1][1])
        level = self.level.get(zone, "info")
        self.a2a.update(task, "completed", f"zone {zone}: risk score {score:.0f} ({level}), worst sensor {dev} ({metric} {value:.1f} µg/m³)",
                        {"zone": zone, "score": round(score, 1), "level": level, "sensor": dev, "metric": metric})
        return []

    def _bin(self, ev: Event) -> List[Finding]:
        fill, dev = ev.value("fill_level", 0.0), ev.origin.device_id
        full = self.num("bin_full_pct", 90)
        if fill >= full and dev not in self.bins:
            self.bins.add(dev)
            return [Finding(f"pollution:waste:{dev}", f"Bin {dev} full, zone {ev.location.zone}", "waste", "watch", 0.9,
                            f"{dev} at {fill:.0f} % (limit {full:g} %)", ev.location, {"device": dev})]
        if fill < full * 0.5 and dev in self.bins:
            self.bins.discard(dev)
            return [Finding(f"pollution:waste:{dev}", f"Bin {dev} full, zone {ev.location.zone}", "waste", "info", 0.9,
                            f"{dev} emptied ({fill:.0f} %)", ev.location, {"device": dev}, resolved=True)]
        return []

    def _round(self, ev: Event) -> List[Finding]:
        """Several bins filling at once: order a garbage-truck round rather than one crew per bin."""
        self.fill[ev.origin.device_id] = ev.value("fill_level", 0.0) or 0.0
        self.bin_zone = getattr(self, "bin_zone", {})
        if ev.location:
            self.bin_zone[ev.origin.device_id] = ev.location.zone
        pct, need = self.num("round_pct", 75), int(self.num("round_min_bins", 3))
        high = sorted(d for d, v in self.fill.items() if v >= pct)
        by_zone: Dict[str, list] = {}
        for d in high:
            by_zone.setdefault(self.bin_zone.get(d, "?"), []).append(d)
        zone, bins = max(by_zone.items(), key=lambda kv: len(kv[1]), default=("?", []))
        truck_busy = bool(getattr(self, "rounds_open", set()))   # a round is already driving: it is not ordered twice
        if len(bins) >= need and not self.round_open and not truck_busy and self.now() - self.round_at >= self.num("round_cooldown_s", 300):
            self.round_open, self.round_at = True, self.now()
            # one round for the zone where the bins are full: it stays in that zone
            return [Finding("pollution:collection", f"{len(bins)} bins above {pct:g} %, zone {zone}", "collection", "watch", 0.9,
                            f"{', '.join(bins[:6])} in zone {zone} are above {pct:g} % (round threshold: {need} bins in one zone)",
                            ev.location, {"bins": bins, "zone": zone})]
        if self.round_open and len(high) < max(1, need - 1):
            self.round_open = False
            return [Finding("pollution:collection", "Bins filling up", "collection", "info", 0.9,
                            f"only {len(high)} bin(s) still above {pct:g} %", ev.location, resolved=True)]
        return []

    def _noise(self, ev: Event) -> List[Finding]:
        db, dev = ev.value("db_a", 0.0), ev.origin.device_id
        lim = self.num("noise_watch_dba", 75)
        n = self.loud.get(dev, 0)
        if db >= lim:
            self.loud[dev] = n + 1
            if n + 1 == 3:
                return [Finding(f"pollution:noise:{dev}", f"Noise at {dev}, zone {ev.location.zone}", "noise", "watch", 0.8,
                                f"{dev}: {db:.0f} dBA (limit {lim:g}) for 3 readings; reported, the Twin has no noise actuator", ev.location)]
        elif n >= 3:
            self.loud[dev] = 0
            return [Finding(f"pollution:noise:{dev}", f"Noise at {dev}, zone {ev.location.zone}", "noise", "info", 0.8,
                            f"{dev} back to {db:.0f} dBA", ev.location, resolved=True)]
        else:
            self.loud[dev] = 0
        return []

    def decide(self, f: Finding, city: CityView) -> List[Plan]:
        if f.kind == "collection":
            if f.resolved:
                return []
            start = ROUND_START.get(f.evidence.get("zone", ""))
            value = ({"kind": "garbage_collection", "bin": start[0], "zone": f.evidence.get("zone"), "x": start[1], "z": start[2]}
                     if start else "garbage_collection")
            return [Plan("INCIDENTS", "trigger", "garbage_round", value=value, priority=3,
                         reason=f"{f.reason}: order a garbage-truck collection round"
                                + (f", starting at {start[0]} in zone {f.evidence.get('zone')}" if start else ""),
                         parameters={"bins": f.evidence.get("bins", []), "zone": f.evidence.get("zone")})]
        if f.kind == "waste":
            return [] if f.resolved else [Plan(f.evidence["device"], "empty", "empty_bin", priority=3, location=f.location,
                                               reason=f"{f.reason}: send a crew to empty it")]
        if f.kind != "air":
            return []
        zone = f.location.zone
        if f.severity in ("alert", "critical") and not f.resolved:
            if zone not in self.parks_asked and city.in_zone("parking", zone):
                t = self.ask("transportation", "close_car_parks", {"zone": zone},
                             f"air risk {f.evidence['score']:.0f} in zone {zone}: fewer cars should drive in", context=f.key)
                self.parks_asked[zone] = t.task_id
            return []
        if (f.resolved or f.evidence.get("previous") in ("alert", "critical")) and self.parks_asked.pop(zone, None):
            self.ask("transportation", "reopen_car_parks", {"zone": zone},
                     f"air in zone {zone} {'back to normal' if f.resolved else 'down to watch'}", context=f.key)
        return []

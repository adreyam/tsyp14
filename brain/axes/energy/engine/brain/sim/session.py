"""A simulation session: mock Twin + mock Guardian + the real EnergyBrain on a
simulated clock, advanced one cycle at a time, with a full snapshot per cycle.

The Brain inside is exactly the production code; only its partners are mocks.
Used by the live server (sim/server.py) and the replay recorder (sim/record.py).
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi.testclient import TestClient

from energy_brain.adapters import make_twin
from energy_brain.adapters.rest import RestGuardian
from energy_brain.brain import EnergyBrain
from energy_brain.config import BrainConfig, load_city, load_config
from energy_brain.eventlog import EventLog
from energy_brain.schemas import PriorityHint
from mocks import guardian as gmock
from mocks import twin as tmock
from scenarios.runner import GUARDIAN_KEY, ROOT, TWIN_KEY, Run, Scenario, Step, _deep_merge, make_day_ahead

FREE_PLAY = Scenario("FREE", "Free play", "No script: inject events yourself.", duration_s=10 ** 9)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _CapturingTwin:
    """Wraps the Twin adapter to keep the raw readings the Brain received (incl. spoofed ones)."""

    def __init__(self, inner):
        self.inner, self.last_readings = inner, {}

    def get_readings(self):
        b = self.inner.get_readings()
        self.last_readings = {r.zone_id: r.kw for r in b.valid}
        return b

    def __getattr__(self, name):
        return getattr(self.inner, name)


class Session:
    def __init__(self, scenario: Scenario = FREE_PLAY, seed: int = 7, twin_protocol: str = "rest",
                 log_dir: Optional[Path] = None):
        self.scenario, self.seed = scenario, seed
        cfg_dict = _deep_merge(load_config(ROOT / "config/config.yaml").model_dump(), scenario.overrides)
        self.city_spec = yaml.safe_load((ROOT / "mocks/cities" / scenario.city).read_text())
        self.capacity = {z["zone_id"]: z["capacity_kw"] for z in self.city_spec["zones"] if z.get("capacity_kw")}
        self.clock = _Clock()
        twin_app = tmock.create_app(self.city_spec, api_key=TWIN_KEY, clock=self.clock, seed=seed)
        self.sim = twin_app.state.sim
        self.twin_http, self.g_http = TestClient(twin_app), TestClient(gmock.create_app(api_key=GUARDIAN_KEY))
        self._mcp_cm = None
        if twin_protocol == "mcp":
            from mocks.twin_mcp import create_mcp_app
            self._mcp_cm = TestClient(create_mcp_app(self.sim, api_key=TWIN_KEY))
            mcp_http = self._mcp_cm.__enter__()
            cfg_dict["twin"].update(adapter="mcp", base_url="http://testserver/mcp", api_key=TWIN_KEY)
        else:
            cfg_dict["twin"].update(base_url="http://testserver", api_key=TWIN_KEY)
        cfg_dict["guardian"].update(base_url="http://testserver", api_key=GUARDIAN_KEY)
        self.cfg = BrainConfig.model_validate(cfg_dict)
        twin = make_twin(self.cfg.twin, mcp_http if twin_protocol == "mcp" else self.twin_http)
        self.twin = _CapturingTwin(twin)
        guardian = RestGuardian(self.cfg.guardian, self.g_http)
        self.log = EventLog(log_dir or Path(tempfile.mkdtemp()), f"sim-{scenario.id.lower()}")
        local = load_city(ROOT / "config/knowledge" / scenario.knowledge) if scenario.knowledge else None
        self.day_ahead = make_day_ahead(self.city_spec, seed)
        self.brain = EnergyBrain(self.cfg, self.twin, guardian, self.log, local_city=local, clock=self.clock,
                                 day_ahead=self.day_ahead)
        self._last_eval_id = None
        self.run = Run(scenario, self.brain, self.log, self.capacity,
                       customers={z["zone_id"]: int(z.get("customers", round(2 * z["base_kw"])))
                                  for z in self.city_spec["zones"]})
        self.pending_steps = sorted(scenario.steps, key=lambda s: s.at)
        self.t0 = self.clock()
        self.cycle = 0
        self._event_cursor = 0
        self.event_t: list[float] = []      # sim time of each log event
        self.result: Optional[dict] = None
        self.modified = False               # set when events are injected by hand
        self.log.emit("scenario_marker", f"START {scenario.id} {scenario.name}", scenario=scenario.id)
        self.brain.start()

    # ----------------------------------------------------------------- control
    @property
    def t(self) -> float:
        return self.clock() - self.t0

    @property
    def finished(self) -> bool:
        return self.t >= self.scenario.duration_s

    def apply(self, step: Step) -> None:
        a, kw = step.action, step.args
        ct, cg = {"X-API-Key": TWIN_KEY}, {"X-API-Key": GUARDIAN_KEY}

        def ok(r):
            if r.status_code >= 400:
                d = r.json().get("detail", r.text)
                raise ValueError(d if isinstance(d, str) else "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in d))
        if a in ("ramp", "spoof", "stuck", "switch", "drift"):
            ok(self.twin_http.post(f"/_scenario/{a}", json=kw, headers=ct))
        elif a == "guardian_mode":
            ok(self.g_http.post("/_control/mode", json=kw, headers=cg))
        elif a == "guardian_override":
            ok(self.g_http.post("/_control/override", json=kw, headers=cg))
        elif a == "twin_restart":
            self.sim.seq = 0
        elif a == "guardian_flap":
            ok(self.g_http.post("/_control/flap", json=kw, headers=cg))
        elif a == "hint":
            self.brain.add_priority_hint(PriorityHint(
                zone_id=kw["zone_id"], source=kw.get("source", "flood-domain"), reason=kw["reason"],
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=kw.get("ttl_s", 600))))
        else:
            raise ValueError(f"unknown action '{a}'")
        text, layer = describe(a, kw)
        self.log.emit("scenario_marker", text + (f" ({step.note})" if step.note else ""),
                      scenario=self.scenario.id, action=a, args=kw, layer=layer)

    def step(self) -> dict:
        while self.pending_steps and self.pending_steps[0].at <= self.t:
            self.apply(self.pending_steps.pop(0))
        self.brain.step()
        truth = self.sim.true_loads()
        self.run.truth.append((self.t, truth))
        if self.run.first_command_cycle is None and self.brain.commands:
            self.run.first_command_cycle = self.cycle
        snap = self.snapshot(truth)
        self.cycle += 1
        self.clock.t += self.cfg.poll_interval_s
        if self.finished and self.result is None and self.scenario.checks:
            self.run.guardian_received = self.g_http.get("/_control/received",
                                                         headers={"X-API-Key": GUARDIAN_KEY}).json()
            checks = [c(self.run) for c in self.scenario.checks]
            self.result = {"passed": all(ok for _, ok, _ in checks), "modified": self.modified,
                           "checks": [{"expect": n, "ok": ok, "observed": d} for n, ok, d in checks]}
            snap["result"] = self.result
        return snap

    def close(self) -> None:
        self.log.close()
        if self._mcp_cm is not None:
            self._mcp_cm.__exit__(None, None, None)

    # ----------------------------------------------------------------- views
    def city(self) -> dict:
        st, b = self.brain.state, self.brain
        return {
            "id": st.city.city_id, "scenario": {"id": self.scenario.id, "name": self.scenario.name,
                                                 "description": self.scenario.description,
                                                 "duration": self.scenario.duration_s},
            "level": b.knowledge.level,
            "zones": [{"id": z.zone_id, "cap": z.capacity_kw, "tags": [t.value for t in z.tags],
                       "true_cap": self.capacity.get(z.zone_id)} for z in st.city.zones],
            "switches": [{"id": sid, **self._switch_geom(sid)} for sid in self.sim.switches],
            "horizon": self.cfg.forecast.horizon_s,
            "plan": {z["zone_id"]: self.day_ahead.series(z["zone_id"], 0, self._span(), 2.0)
                     for z in self.city_spec["zones"]},
            "plan_method": self.day_ahead.method,
            "policy": {"headroom": self.cfg.action.priority_headroom, "restore_margin": self.cfg.action.restore_margin,
                       "shed_above": self.cfg.action.shed_above, "verdict_ttl": self.cfg.trust.verdict_ttl_s},
        }

    def _span(self) -> float:
        return min(self.scenario.duration_s, 600)

    def _flow(self, sid: str, truth: dict) -> Optional[float]:
        return _flow_of(self.sim, sid, truth)

    def _switch_geom(self, sid: str) -> dict:
        real = self.sim.switches[sid]
        known = self.brain.effects.get(sid)
        return {"kind": real.kind, "from": real.from_zone, "to": real.to_zone,
                "known": known is not None,
                "frac": round(known.transfer_fraction, 3) if known and known.transfer_fraction else None}

    def snapshot(self, truth: dict) -> dict:
        st, b, f = self.brain.state, self.brain, self.cfg.forecast
        zones = {}
        for zid, z in st.zones.items():
            fc = z.model.snapshot()
            zones[zid] = {
                "lvl": _r(fc.at(0)) if fc.ready else None,
                "sd": round(fc.std(f.horizon_s), 1) if fc.ready else None,
                "j1": _r(self.brain.plan(zid)),
                "true": round(truth.get(zid, 0), 1),
                "read": _r(self.twin.last_readings.get(zid)),
                "acc": _r(z.last_kw),
                "fc": _r(fc.at(f.horizon_s)) if fc.ready else None,
                "up": _r(fc.peak_upper(f.horizon_s, f.band_sigma)) if fc.ready else None,
                "trend": round(fc.trend, 2) if fc.ready else None,
                "risk": z.at_risk,
                "prio": st.is_priority(zid),
                "trust": st.verdict_status(z.sensor_id) if z.sensor_id else "missing",
                "suspect": z.suspect_kw is not None,
            }
        switches = {}
        for sid, real in self.sim.switches.items():
            s = st.switches.get(sid)
            switches[sid] = {
                "state": s.state.value if s else real.state,
                "trust": st.verdict_status(sid),
                "quar": st.quarantined(sid),
                "stuck": real.stuck,
                "pending": sid in b.pending,
                "failed": sid in b.failed,
                "cool": max(0, round(b.cooldown_until.get(sid, 0) - self.clock())),
                "known": sid in b.effects,
                "flow": self._flow(sid, truth),
                "brain_changed": sid in b.changed,
            }
        new = b.log.events[self._event_cursor:]
        self._event_cursor = len(b.log.events)
        events = [{"t": round(self.t, 1), "k": e.kind, "s": e.summary, "c": e.correlation_id,
                   **({"L": e.payload["layer"]} if "layer" in e.payload else {}),
                   **({"p": _payload(e)} if e.kind in ("candidates_evaluated", "gate_passed", "gate_blocked",
                                                       "decision", "verification", "learned_effect",
                                                       "command_sent", "restoration", "risk_detected", "proposal") else {})}
                  for e in new]
        ev = None
        if b.last_eval and b.last_eval.get("decision_id") != self._last_eval_id:
            sig = tuple(c["action"] for c in b.last_eval["ranked"])
            if sig != getattr(self, "_eval_sig", None) or self.cycle % 3 == 0:
                self._eval_sig = sig
                ev = b.last_eval
            self._last_eval_id = b.last_eval["decision_id"]
        return {"t": round(self.t, 1), "cycle": self.cycle, "level": b.knowledge.level, "eval": ev, "mode": b.mode,
                "guardian_ok": b.guardian_ok, "zones": zones, "switches": switches, "events": events,
                "commands": len(b.commands)}


def describe(a: str, kw: dict) -> tuple[str, str]:
    """Human sentence + originating layer for an injected event."""
    if a == "ramp":
        return f"{kw['zone_id']} demand rises by {kw['add_kw']:g} kW over {kw.get('rise_s', 30):g} s", "Twin"
    if a == "spoof":
        return f"Sensor {kw['sensor_id']} spoofed: reports {kw['value_kw']:g} kW for {kw.get('duration_s', 30):g} s", "Twin"
    if a == "stuck":
        if kw.get("phantom"):
            return f"Switch {kw['switch_id']} status spoofed: it will report moving but never move", "Twin"
        return f"Switch {kw['switch_id']} {'jams (accepts commands, never moves)' if kw.get('stuck', True) else 'freed'}", "Twin"
    if a == "drift":
        how = "under-reads" if kw["rate_kw_s"] < 0 else "over-reads"
        return (f"Sensor {kw['sensor_id']} slowly {how} ({kw['rate_kw_s']:+g} kW/s for {kw.get('duration_s', 120):g} s): "
                f"a stealthy manipulation", "Twin")
    if a == "switch":
        return f"Operator sets {kw['switch_id']} {kw['state']}", "Twin"
    if a == "guardian_mode":
        return {"silent": "Guardian goes down", "trusted": "Guardian back online",
                "untrusted": "Guardian distrusts everything"}[kw["mode"]], "Guardian"
    if a == "guardian_override":
        q = " and quarantines it" if kw.get("quarantined") else ""
        why = f": {kw['reason']}" if kw.get("reason") else ""
        return f"Guardian marks {kw['subject_id']} {kw['verdict']}{q}{why}", "Guardian"
    if a == "twin_restart":
        return "Twin restarts (its reading counters start again from 0)", "Twin"
    if a == "guardian_flap":
        return f"Guardian verdict for {kw['subject_id']} starts flipping every call", "Guardian"
    if a == "hint":
        return f"{kw.get('source', 'flood-domain')}: {kw['reason']} in {kw['zone_id']}", "Other"
    return f"{a} {kw}", "Twin"


def _r(v):
    return None if v is None else round(v, 1)


def _flow_of(sim, sid: str, truth: dict) -> Optional[float]:
    s = sim.switches[sid]
    if s.kind != "tie" or s.state != "closed":
        return None
    away = sum(x.transfer_fraction for x in sim.switches.values()
               if x.kind == "tie" and x.state == "closed" and x.from_zone == s.from_zone)
    served = truth.get(s.from_zone, 0.0)
    return round(s.transfer_fraction * served / max(1e-6, 1 - min(away, 0.95)), 1)


def _payload(e) -> dict:
    p = e.payload
    keep = ("ranked", "rejected", "no_action", "rule", "trust", "degraded", "action", "score", "risky",
            "no_action_score", "outcome", "observed", "switch", "action_kind", "switch_id", "zone_id",
            "p_overload", "now_kw", "forecast_kw")
    return {k: p[k] for k in keep if k in p}

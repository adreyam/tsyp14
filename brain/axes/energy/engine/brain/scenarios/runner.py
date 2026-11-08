"""Scenario runner: one trigger -> one run -> one log file -> automatic verdict (R2).

  python -m scenarios.run e2            # fast: simulated clock, in-process mocks, seconds to run
  python -m scenarios.run e2 --live     # live: real HTTP servers, real time (for the demo)
  python -m scenarios.run all           # whole energy Test Book

The log is JSON lines: every Brain event, scenario markers, and a final
`scenario_result` line with metrics and pass/fail for each expected output.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import yaml

from energy_brain.adapters.rest import RestGuardian, RestTwin
from energy_brain.brain import EnergyBrain
from energy_brain.config import BrainConfig, PartnerConfig, load_city, load_config
from energy_brain.eventlog import EventLog
from energy_brain.schemas import PriorityHint
from mocks import guardian as gmock
from mocks import twin as tmock

ROOT = Path(__file__).resolve().parents[1]
TWIN_KEY, GUARDIAN_KEY = "scenario-twin-key", "scenario-guardian-key"


@dataclass
class Step:
    at: float
    action: str            # ramp | spoof | stuck | switch | guardian_mode | guardian_override | hint
    args: dict = field(default_factory=dict)
    note: str = ""


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    city: str = "city_a.yaml"
    knowledge: Optional[str] = None
    duration_s: float = 150
    steps: list[Step] = field(default_factory=list)
    overrides: dict = field(default_factory=dict)            # deep-merged into BrainConfig
    checks: list[Callable[["Run"], tuple[str, bool, str]]] = field(default_factory=list)


@dataclass
class Run:
    scenario: Scenario
    brain: EnergyBrain
    log: EventLog
    capacity: dict[str, float]
    truth: list[tuple[float, dict]] = field(default_factory=list)
    guardian_received: list[dict] = field(default_factory=list)
    first_command_cycle: Optional[int] = None
    demand: list[tuple[float, dict]] = field(default_factory=list)   # noiseless demand (dark zones still have demand)
    step_ms: list[float] = field(default_factory=list)               # wall time of each Brain cycle
    customers: dict[str, int] = field(default_factory=dict)

    # ---------- helpers for checks
    def events(self, kind: Optional[str] = None) -> list:
        return [e for e in self.log.events if kind is None or e.kind == kind]

    def overload_seconds(self, dt: float) -> dict[str, float]:
        out = {}
        for z, cap in self.capacity.items():
            out[z] = round(sum(dt for _, loads in self.truth if loads.get(z, 0) > cap), 1)
        return out

    def peak_pct(self) -> dict[str, int]:
        return {z: round(100 * max((l.get(z, 0) for _, l in self.truth), default=0) / cap)
                for z, cap in self.capacity.items()}

    def energy_over_capacity_kwh(self, dt: float) -> float:
        return round(sum(max(0.0, l.get(z, 0) - cap) for _, l in self.truth for z, cap in self.capacity.items())
                     * dt / 3600, 3)

    def energy_not_served_kwh(self, dt: float) -> float:
        """Demand of de-energized zones (load 0 while demand > 0)."""
        tot = 0.0
        for (_, l), (_, d) in zip(self.truth, self.demand):
            tot += sum(d[z] for z in d if l.get(z, 1.0) <= 0.0 and d[z] > 0)
        return round(tot * dt / 3600, 3)

    def customer_minutes_interrupted(self, dt: float) -> float:
        tot = 0.0
        for _, l in self.truth:
            tot += sum(self.customers.get(z, 0) for z, v in l.items() if v <= 0.0)
        return round(tot * dt / 60, 1)

    @property
    def commands(self):
        return self.brain.commands


def make_day_ahead(city_spec: dict, seed: int, period_s: int = 600):
    """'Yesterday' for this city: the same mock run quietly with other noise, smoothed into a J-1 plan."""
    from energy_brain.dayahead import DayAhead, record_yesterday
    clk = {"t": 0.0}
    sim = tmock.CitySim(city_spec, clock=lambda: clk["t"], seed=seed + 1000)

    def loads(t):
        clk["t"] = t
        return sim.true_loads()
    return DayAhead.from_measurements(record_yesterday(loads, period_s), made_at="J-1 (simulated)")


def noiseless_demand(sim, t: float) -> dict[str, float]:
    out = {}
    for zid, z in sim.zones.items():
        import math as _m
        d = z.base_kw + z.amplitude_kw * _m.sin(2 * _m.pi * t / z.period_s)
        d += sum(r.extra(t) for r in sim.ramps if r.zone_id == zid)
        out[zid] = max(0.0, d)
    return out


def band_coverage(uppers: list[tuple[float, dict]], truth: list[tuple[float, dict]], horizon: float,
                  changes: list[float]) -> Optional[float]:
    """Share of upper-band forecasts that the true load h seconds later stayed below (one-sided coverage)."""
    tmap = {round(t, 3): l for t, l in truth}
    hit = n = 0
    for t, fc in uppers:
        tt = round(t + horizon, 3)
        if tt not in tmap or any(t <= c <= t + horizon for c in changes):
            continue
        for z, v in fc.items():
            if z in tmap[tt]:
                n += 1
                hit += tmap[tt][z] <= v
    return round(hit / n, 4) if n else None


def forecast_mae(forecasts: list[tuple[float, dict]], truth: list[tuple[float, dict]], horizon: float,
                 changes: list[float], dt: float) -> Optional[float]:
    """Mean absolute error of the horizon-ahead forecast against the true load, skipping windows in which
    a switch moved (the forecast is not meant to know about future switching)."""
    tmap = {round(t, 3): l for t, l in truth}
    errs = []
    for t, fc in forecasts:
        tt = round(t + horizon, 3)
        if tt not in tmap or any(t <= c <= t + horizon for c in changes):
            continue
        errs += [abs(v - tmap[tt][z]) for z, v in fc.items() if z in tmap[tt]]
    return round(sum(errs) / len(errs), 1) if errs else None


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def log_events_of(log, kind):
    return [e for e in log.events if e.kind == kind]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server(threading.Thread):
    def __init__(self, app, port):
        import uvicorn
        super().__init__(daemon=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True


def run_scenario(sc: Scenario, live: bool = False, log_dir: Optional[Path] = None, echo: bool = False,
                 seed: int = 7, twin_protocol: str = "rest", twin_wrapper: Optional[Callable] = None,
                 brain_hook: Optional[Callable] = None) -> dict:
    """brain_hook(brain) runs after the Brain is built: used by the benchmark for baselines and ablations."""
    import contextlib
    with contextlib.ExitStack() as stack:
        return _run(sc, live, log_dir, echo, seed, twin_protocol, stack, twin_wrapper, brain_hook)


def _run(sc, live, log_dir, echo, seed, twin_protocol, stack, twin_wrapper=None, brain_hook=None) -> dict:
    from mocks.twin_mcp import create_mcp_app
    base_cfg = load_config(ROOT / "config/config.yaml").model_dump()
    cfg_dict = _deep_merge(base_cfg, sc.overrides)
    city_spec = yaml.safe_load((ROOT / "mocks/cities" / sc.city).read_text())
    capacity = {z["zone_id"]: z["capacity_kw"] for z in city_spec["zones"] if z.get("capacity_kw")}
    poll = cfg_dict["poll_interval_s"]

    if live:
        clock = time.monotonic
        twin_app = tmock.create_app(city_spec, api_key=TWIN_KEY, seed=seed)
        g_app = gmock.create_app(api_key=GUARDIAN_KEY)
        tp, gp, mp = _free_port(), _free_port(), _free_port()
        servers = [_Server(twin_app, tp), _Server(g_app, gp)]
        if twin_protocol == "mcp":
            servers.append(_Server(create_mcp_app(twin_app.state.sim, api_key=TWIN_KEY), mp))
        for s in servers:
            s.start()
        time.sleep(1.0)
        twin_http = httpx.Client(base_url=f"http://127.0.0.1:{tp}", timeout=3)
        g_http = httpx.Client(base_url=f"http://127.0.0.1:{gp}", timeout=2)
        mcp_url, mcp_http = f"http://127.0.0.1:{mp}/mcp", httpx.Client(timeout=3)
    else:
        from fastapi.testclient import TestClient

        class FakeClock:
            t = 1000.0
            def __call__(self):
                return self.t
        clock = FakeClock()
        twin_app = tmock.create_app(city_spec, api_key=TWIN_KEY, clock=clock, seed=seed)
        g_app = gmock.create_app(api_key=GUARDIAN_KEY)
        twin_http, g_http = TestClient(twin_app), TestClient(g_app)
        servers = []
        mcp_url, mcp_http = "http://testserver/mcp", None
        if twin_protocol == "mcp":
            mcp_http = stack.enter_context(TestClient(create_mcp_app(twin_app.state.sim, api_key=TWIN_KEY)))

    if twin_protocol == "mcp":
        cfg_dict["twin"].update(adapter="mcp", base_url=mcp_url, api_key=TWIN_KEY)
    else:
        cfg_dict["twin"].update(base_url=str(twin_http.base_url), api_key=TWIN_KEY)
    cfg_dict["guardian"].update(base_url=str(g_http.base_url), api_key=GUARDIAN_KEY)
    cfg = BrainConfig.model_validate(cfg_dict)
    from energy_brain.adapters import make_twin
    twin = make_twin(cfg.twin, mcp_http if twin_protocol == "mcp" else twin_http)
    if twin_wrapper:
        twin = twin_wrapper(twin)
    guardian = RestGuardian(cfg.guardian, g_http)
    ctl_t = {"X-API-Key": TWIN_KEY}
    ctl_g = {"X-API-Key": GUARDIAN_KEY}

    log = EventLog(log_dir or (ROOT.parent / "logs"), sc.id.lower(), echo=echo)
    local = load_city(ROOT / "config/knowledge" / sc.knowledge) if sc.knowledge else None
    brain = EnergyBrain(cfg, twin, guardian, log, local_city=local, clock=clock,
                        day_ahead=make_day_ahead(city_spec, seed))
    if brain_hook:
        brain_hook(brain)
    forecasts, sw_changes, last_states = [], [], None
    run = Run(sc, brain, log, capacity,
              customers={z["zone_id"]: int(z.get("customers", round(2 * z["base_kw"]))) for z in city_spec["zones"]})
    sim = twin_app.state.sim
    uppers = []

    log.emit("scenario_marker", f"START {sc.id} {sc.name} ({'live' if live else 'fast'} mode, Twin over {twin_protocol.upper()})",
             scenario=sc.id, description=sc.description)
    brain.start()

    def apply(step: Step):
        a, kw = step.action, step.args
        log.emit("scenario_marker", f"t={step.at:g}s {a} {kw} {step.note}".strip(), scenario=sc.id)
        if a in ("ramp", "spoof", "stuck", "switch", "drift"):
            twin_http.post(f"/_scenario/{a}", json=kw, headers=ctl_t).raise_for_status()
        elif a == "guardian_mode":
            g_http.post("/_control/mode", json=kw, headers=ctl_g).raise_for_status()
        elif a == "guardian_override":
            g_http.post("/_control/override", json=kw, headers=ctl_g).raise_for_status()
        elif a == "twin_restart":
            twin_app.state.sim.seq = 0          # a restarted Twin starts counting again
        elif a == "guardian_flap":
            g_http.post("/_control/flap", json=kw, headers=ctl_g).raise_for_status()
        elif a == "hint":
            brain.add_priority_hint(PriorityHint(
                zone_id=kw["zone_id"], source=kw.get("source", "flood-domain"), reason=kw["reason"],
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=kw.get("ttl_s", 600))))
        else:
            raise ValueError(f"unknown scenario action {a}")

    pending = sorted(sc.steps, key=lambda s: s.at)
    first_flag = first_hidden = None
    t0 = clock()
    try:
        n = int(sc.duration_s / poll)
        for i in range(n):
            el = clock() - t0
            while pending and pending[0].at <= el:
                apply(pending.pop(0))
            _t0 = time.perf_counter()
            brain.step()
            run.step_ms.append(1000 * (time.perf_counter() - _t0))
            run.demand.append((el, noiseless_demand(sim, sim.t())))
            truth = twin_http.get("/_scenario/truth", headers=ctl_t).json()
            # ground truth from the Twin (never seen by the Brain), sampled once per cycle
            run.truth.append((el, truth["loads_kw"]))
            if run.first_command_cycle is None and brain.commands:
                run.first_command_cycle = i
            if first_flag is None and any(e.kind == "consistency" and e.payload.get("flagged") for e in log.events[-40:]):
                first_flag = el
            if first_hidden is None and any(e.kind == "operator_alert" and "HIDDEN" in e.summary for e in log.events[-40:]):
                first_hidden = el
            states = {k: v["state"] for k, v in truth["switches"].items()}
            if last_states is not None and states != last_states:
                sw_changes.append(el)
            last_states = states
            h = brain.cfg.forecast.horizon_s
            forecasts.append((el, {z: zs.model.snapshot().at(h) for z, zs in brain.state.zones.items()
                                   if zs.model.snapshot().ready}))
            k = brain.cfg.forecast.band_sigma
            uppers.append((el, {z: zs.model.snapshot().upper(h, k) for z, zs in brain.state.zones.items()
                                if zs.model.snapshot().ready and zs.last_kw}))
            if live:
                time.sleep(max(0.0, t0 + (i + 1) * poll - clock()))
            else:
                clock.t += poll
        run.guardian_received = g_http.get("/_control/received", headers=ctl_g).json()
    finally:
        for s in servers:
            s.stop()

    results = [chk(run) for chk in sc.checks]
    metrics = {
        "commands": [f"{c.operation.value} {c.switch_id}" + (" (degraded)" if c.degraded else "") for c in run.commands],
        "overload_seconds": run.overload_seconds(poll),
        "peak_load_pct": run.peak_pct(),
        "verifications": [v.outcome for v in brain.verifications],
        "knowledge_level": brain.knowledge.level,
        "forecast_mae_kw": forecast_mae(forecasts, run.truth, brain.cfg.forecast.horizon_s, sw_changes, poll),
        "risk_alarms": len(log_events_of(log, "risk_detected")),
        "band_coverage": band_coverage(uppers, run.truth, brain.cfg.forecast.horizon_s, sw_changes),
        "energy_over_capacity_kwh": run.energy_over_capacity_kwh(poll),
        "energy_not_served_kwh": run.energy_not_served_kwh(poll),
        "customer_minutes_interrupted": run.customer_minutes_interrupted(poll),
        "switch_operations": len(run.commands),
        "consistency_flag_s": first_flag,          # scenario time of the first power-balance flag
        "hidden_overload_alert_s": first_hidden,
        "attack_start_s": min((st.at for st in sc.steps if st.action == "drift"), default=None),
        "operator_alerts": len(log_events_of(log, "operator_alert")),
        "step_ms_p50": round(sorted(run.step_ms)[len(run.step_ms) // 2], 2) if run.step_ms else None,
        "step_ms_p95": round(sorted(run.step_ms)[int(0.95 * (len(run.step_ms) - 1))], 2) if run.step_ms else None,
        "events": len(log.events),
    }
    passed = all(ok for _, ok, _ in results)
    log.emit("scenario_marker", f"END {sc.id}: {'PASS' if passed else 'FAIL'}", scenario=sc.id)
    summary = {"type": "scenario_result", "scenario": sc.id, "name": sc.name, "passed": passed,
               "checks": [{"expect": n, "ok": ok, "observed": d} for n, ok, d in results], "metrics": metrics,
               "mode": "live" if live else "fast", "seed": seed,
               "twin_protocol": twin_protocol}
    with log.path.open("a") as fh:
        fh.write(json.dumps(summary) + "\n")
    log.close()
    summary["log"] = str(log.path)
    return summary

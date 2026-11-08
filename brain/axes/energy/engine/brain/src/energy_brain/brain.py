"""EnergyBrain: one closed-loop cycle per poll interval.

  Observe    switches, readings, Guardian verdicts
  Understand validate readings, update state, learn switch effects (anti-poisoned),
             verify past actions, compare with the J-1 plan
  Predict    trend forecast + overload probability per zone
  Simulate   evaluate every feasible switching option (a "future") at the horizon
  Decide     rank futures by expected overload against doing nothing; last-resort shedding
  Gate       safety + trust (degraded mode if the Guardian is unreachable)
  Act        send the command, verify it in later cycles, restore when safe
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .actions import Pending, Verification, send_with_retry, verify
from .adapters import PartnerError
from .config import BrainConfig
from .dayahead import DayAhead
from .decision import Candidate, DecisionResult, evaluate, evaluate_restore, outlook, p_overload
from .eventlog import EventLog
from .gate import check
from .knowledge import KnowledgeReport, assess, merge
from .learning import EffectLearner, Observation
from .schemas import CityDescriptor, PriorityHint, Switch, SwitchCommand, SwitchKind, SwitchOperation, SwitchState, new_id
from .state import EnergyState


@dataclass
class _PendingObs:
    switch_id: str
    obs: Observation
    sensors: list[str]
    until: float


@dataclass
class Proposal:
    """An action the agent wants to take while in advisory mode, waiting for the orchestrator."""
    proposal_id: str
    candidate: Candidate
    decision_id: str
    risky: list[str]
    no_action: Optional[Candidate]
    created_t: float
    expires_t: float
    reason: str

    def as_dict(self, state, t: float) -> dict:
        c = self.candidate
        return {"proposal_id": self.proposal_id, "action": c.label, "kind": c.kind, "for_zones": self.risky,
                "reason": self.reason, "expires_in_s": round(max(0.0, self.expires_t - t), 1),
                "predicted_after": c.brief(state)}


@dataclass
class _Changed:
    """A switch the Brain moved away from its normal state (to restore later)."""
    kind: str                 # relief | shed
    pre_kw: Optional[float]   # load of a shed zone before shedding
    since: float = 0.0        # when the Brain made the change
    safe_count: int = 0


class EnergyBrain:
    def __init__(self, cfg: BrainConfig, twin, guardian, log: EventLog, local_city: Optional[CityDescriptor] = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 day_ahead: Optional[DayAhead] = None):
        self.cfg, self.twin, self.guardian, self.log = cfg, twin, guardian, log
        self.local_city, self.clock, self.wall = local_city, clock, wall
        self.day_ahead = day_ahead or (DayAhead.load(cfg.dayahead.file) if cfg.dayahead.file else None)
        self.learner = EffectLearner(cfg.learning)
        self.pending: dict[str, Pending] = {}       # switch_id -> pending command
        self.cooldown_until: dict[str, float] = {}
        self.failed: set[str] = set()
        self.changed: dict[str, _Changed] = {}      # switches to restore when safe
        self.pending_obs: list[_PendingObs] = []
        self.shed_attempts: dict[str, int] = {}     # feeder -> re-energizations that had to be undone
        self.cycle = 0
        self.t_start: Optional[float] = None
        self.guardian_ok: Optional[bool] = None
        self.twin_ok = True
        self.consistency = None
        self.status_override: dict[str, SwitchState] = {}
        self._hidden_alerted: set[tuple[str, str]] = set()
        self.verifications: list[Verification] = []
        self.commands: list[SwitchCommand] = []
        self.last_eval: Optional[dict] = None       # latest options comparison (for displays)
        self._recent_restores: list[Candidate] = []
        # ---- supervision by an orchestrator (see energy_brain.agent)
        self.mode = "autonomous"                    # autonomous: acts itself | advisory: proposes, waits for approval
        self.vetoes: dict[str, tuple[float, str]] = {}   # switch -> (until clock, reason)
        self.proposals: dict[str, Proposal] = {}
        self.proposal_ttl_s = 30.0
        self._seen_sigs: set = set()
        self._blocked_logged: set[tuple[str, str]] = set()
        self._suspect_logged: set[str] = set()
        self._alerted: set = set()

    # ================================================================== setup
    def start(self) -> KnowledgeReport:
        twin_city = self.twin.get_city()
        self.state = EnergyState(self.cfg, merge(twin_city, self.local_city))
        self.t_start = self.clock()
        from .consistency import ConsistencyMonitor
        self.consistency = ConsistencyMonitor.build(self.state.city.aggregate_meters, self.cfg.consistency)
        if self.consistency:
            self.log.emit("knowledge_level", f"power-balance check on {len(self.consistency.meters)} aggregate "
                          f"meter(s): " + "; ".join(f"{m.meter.sensor_id} = {' + '.join(m.meter.zones)}"
                                                    for m in self.consistency.meters.values()))
        if self.day_ahead:
            for zid, z in self.state.zones.items():     # adaptive forecaster: J-1 shape as one of its experts
                if hasattr(z.model, "plan_fn") and zid in self.day_ahead.zones:
                    z.model.plan_fn = (lambda t, zid=zid: self.day_ahead.expected(
                        zid, t - self.t_start + self.cfg.dayahead.offset_s))
            self.log.emit("knowledge_level", f"J-1 plan loaded: {len(self.day_ahead.zones)} zone profile(s), "
                          f"{self.day_ahead.method}", zones=sorted(self.day_ahead.zones))
        return self._assess("startup")

    def _assess(self, why: str) -> KnowledgeReport:
        self.knowledge = assess(self.state.city)
        self.effects: dict[str, Switch] = {s.switch_id: s for s in self.state.city.switches if s.effect_known}
        self.log.emit("knowledge_level", f"{why}: city '{self.state.city.city_id}' at {self.knowledge.level} "
                      f"(actionable switches: {', '.join(self.knowledge.actionable_switches) or 'none'})",
                      **self.knowledge.as_payload())
        return self.knowledge

    def tod(self) -> float:
        """Time of day used to read the J-1 plan."""
        return self.clock() - (self.t_start or self.clock()) + self.cfg.dayahead.offset_s

    def plan(self, zone_id: str) -> Optional[float]:
        return self.day_ahead.expected(zone_id, self.tod()) if self.day_ahead else None

    def add_priority_hint(self, hint: PriorityHint) -> bool:
        """Cross-domain input: e.g. flood logic says 'pump running in zone X, keep it powered'.
        Expiry is capped so a forgotten or forged hint cannot pin a zone forever."""
        now = self.wall()
        if hint.zone_id not in self.state.zones:
            self.log.emit("security", f"priority hint for unknown zone '{hint.zone_id}' rejected",
                          hint=hint.model_dump(mode="json"))
            return False
        if hint.expires_at <= now:
            self.log.emit("security", f"expired priority hint for {hint.zone_id} rejected", source=hint.source)
            return False
        cap = now + timedelta(seconds=self.cfg.inbound.max_hint_ttl_s)
        expires = min(hint.expires_at, cap)
        self.state.priority_hints[hint.zone_id] = (hint.reason, expires)
        self.log.emit("priority_hint", f"{hint.zone_id} treated as priority until {expires:%H:%M:%S} "
                      f"({hint.source}: {hint.reason})" + (" -- expiry capped" if expires < hint.expires_at else ""),
                      zone_id=hint.zone_id, source=hint.source)
        return True

    # ================================================================== cycle
    def step(self) -> None:
        self.cycle += 1
        t, now = self.clock(), self.wall()
        st = self.state
        st.t = t

        # ---------------- observe
        try:
            switches = self.twin.get_switches()
            batch = self.twin.get_readings()
            if not self.twin_ok:
                self.log.emit("partner_error", "Twin reachable again")
            self.twin_ok = True
        except PartnerError as e:
            if self.twin_ok:
                self.log.emit("partner_error", f"{e} -- holding last state, no actions while blind",
                              partner=e.partner, operation=e.operation)
            self.twin_ok = False
            return

        self._observe_verdicts(batch, switches, t, now)

        # ---------------- understand: switching changes (exempt from jump check, learn effects)
        before = {zid: z.last_kw for zid, z in st.zones.items() if z.last_kw is not None}
        if self.status_override:   # statuses disproved by the power flow (see _verify): model the real position
            switches = [sw.model_copy(update={"state": self.status_override[sw.switch_id]})
                        if sw.switch_id in self.status_override else sw for sw in switches]
        sw_changes = st.update_switches(switches)
        exempt: set[str] = set()
        unknown_changed = []
        for sid, old, new in sw_changes:
            eff = self.effects.get(sid)
            if eff:
                exempt |= {z for z in (eff.from_zone, eff.to_zone) if z}
            else:
                exempt |= set(st.zones)
                unknown_changed.append((sid, new))

        for raw, why in batch.rejected:
            self.log.emit("reading_rejected", f"malformed reading dropped: {why}", raw=raw)
        if self.consistency:   # aggregate meters first: they can corroborate a sudden jump in this same cycle
            raw = {r.zone_id: r.kw for r in batch.valid if not self.consistency.is_meter(r.sensor_id)}
            prev = {sid: m.kw for sid, m in self.consistency.meters.items()}
            for r in batch.valid:
                if self.consistency.is_meter(r.sensor_id):
                    self.consistency.observe(r.sensor_id, r.kw, t)
            st.corroborate = lambda zid, x: self.consistency.corroborates(st, zid, x, raw, prev, t)
            st.contradict = lambda zid, x: self.consistency.contradicts(st, zid, x, raw, t)
        for r in batch.valid:
            if self.consistency and self.consistency.is_meter(r.sensor_id):
                continue
            outcome, detail = st.ingest(r, t, exempt=r.zone_id in exempt, now=now)
            if outcome == "rejected":
                self.log.emit("reading_rejected", f"{r.sensor_id}: {detail}", sensor_id=r.sensor_id)
            elif outcome == "reset":
                self.log.emit("sequence_reset", f"{r.sensor_id}: {detail}", sensor_id=r.sensor_id)
            elif outcome == "suspect":
                if r.sensor_id not in self._suspect_logged:
                    self._suspect_logged.add(r.sensor_id)
                    self.log.emit("reading_suspect", f"{r.sensor_id}: {detail} -- held out of forecast, "
                                  f"awaiting persistence + Guardian", sensor_id=r.sensor_id, value_kw=r.kw)
            elif outcome == "confirmed":
                self._suspect_logged.discard(r.sensor_id)
                self.log.emit("reading_suspect", f"{r.sensor_id}: {detail}", sensor_id=r.sensor_id, value_kw=r.kw)
            elif outcome == "accepted":
                self._suspect_logged.discard(r.sensor_id)

        if self.consistency:
            self._check_consistency(t)

        if self.cfg.learning.enabled:
            self._learn(unknown_changed, before, t, now)

        self._verify(t)
        self._check_plan()

        # ---------------- predict
        self._update_risk(now)
        if self.cycle % self.cfg.forecast_log_every == 0:
            self._log_forecast()

        # ---------------- simulate, decide, gate, act
        risky = [zid for zid, z in st.zones.items() if z.at_risk and st.capacity(zid)]
        if risky:
            self._decide_and_act(risky, t, now)
        elif self.cfg.action.restore_enabled and self.changed:
            self._restore(t, now)

    # ================================================================== observe helpers
    def _observe_verdicts(self, batch, switches, t: float, now: datetime) -> None:
        st = self.state
        subjects = sorted({r.sensor_id for r in batch.valid} | {s.switch_id for s in switches})
        try:
            changes, flaps = st.update_verdicts(self.guardian.get_verdicts(subjects), t, now)
        except PartnerError as e:
            if self.guardian_ok is not False:
                self.log.emit("degraded_mode", f"{e} -- verdicts expire after {self.cfg.trust.verdict_ttl_s:g} s; "
                              f"only reversible priority-relief actions allowed until it returns",
                              partner=e.partner, operation=e.operation)
            self.guardian_ok = False
            return
        if self.guardian_ok is False:
            self.log.emit("degraded_mode", "Guardian reachable again: leaving degraded mode")
        self.guardian_ok = True
        for subj, old, new in changes:
            if subj in st.flapping:
                continue
            if old != "none" or not new.startswith("trusted"):
                self.log.emit("verdict_change", f"{subj}: {old} -> {new}", subject=subj, old=old, new=new)
            if new.startswith("untrusted"):
                self._forget_sensor(subj)
        for subj, on in flaps:
            if on:
                self.log.emit("verdict_flapping", f"{subj}: Guardian verdict keeps flipping "
                              f"({self.cfg.trust.flap_changes}+ changes in {self.cfg.trust.flap_window_s:g} s) -- "
                              f"treated as untrusted until stable", subject=subj)
                self._forget_sensor(subj)
            else:
                self.log.emit("verdict_flapping", f"{subj}: verdict stable again", subject=subj)

    def _forget_sensor(self, subj: str) -> None:
        """Do not let possibly-falsified history drive forecasts or learning."""
        for z in self.state.zones.values():
            if z.sensor_id == subj:
                z.model.reset()
                z.last_kw = z.last_t = None
                z.risk_count, z.suspect_kw, z.suspect_count = 0, None, 0
                z.at_risk = False
        kept = [p for p in self.pending_obs if subj not in p.sensors]
        for p in self.pending_obs:
            if subj in p.sensors:
                self.log.emit("learned_effect", f"{p.switch_id}: observation discarded -- {subj} turned untrusted "
                              f"during the confirmation window", switch_id=p.switch_id)
        self.pending_obs = kept

    # ================================================================== physics (power balance)
    def _check_consistency(self, t: float) -> None:
        st = self.state
        for ev in self.consistency.check(st, t):
            m, zones = ev["meter"], ev["zones"]
            sensors = [st.sensor_of(z) for z in zones]
            est = ev["estimates"]
            hidden = [z for z, v in est.items() if st.capacity(z) and v > st.capacity(z)
                      and ev["readings"][z] <= st.capacity(z)]
            if ev["flagged"] and not ev["transition"]:
                new = [z for z in hidden if (m, z) not in self._hidden_alerted]
                if new:
                    self._hidden_alerted |= {(m, z) for z in new}
                    hid = ", ".join("%s (~%.0f kW vs capacity %g, sensor reads %.0f)"
                                    % (z, est[z], st.capacity(z), ev["readings"][z]) for z in new)
                    self.log.emit("operator_alert", f"POSSIBLE HIDDEN OVERLOAD in {hid}: {m} power balance says the "
                                  f"zone sensor under-reads -- please verify on site", meter=m, hidden=new)
                continue
            if ev["flagged"]:
                self._hidden_alerted |= {(m, z) for z in hidden}
                txt = (f"{m} measures {ev['meter_kw']:.0f} kW but {' + '.join(map(str, sensors))} add up to "
                       f"{ev['total']:.0f} kW (residual {ev['residual']:+.0f} kW, tolerance {ev['tolerance']:.0f}, "
                       f"{ev['rule']} rule); "
                       f"one of these sensors does not tell the truth. If it is a zone sensor, the real loads would be: "
                       + ", ".join(f"{z} {v:.0f} kW (reads {ev['readings'][z]:.0f})" for z, v in ev["estimates"].items())
                       + ". Actions relying on them are blocked; evidence sent to the Guardian")
                evt = self.log.emit("consistency", txt, meter=m, flagged=True, **{k: ev[k] for k in
                                    ("residual", "tolerance", "meter_kw", "total", "estimates", "readings")})
                self._report(evt)
                hid = ", ".join("%s (~%.0f kW vs capacity %g)" % (z, est[z], st.capacity(z)) for z in hidden)
                self.log.emit("operator_alert", f"power balance broken on {m} ({', '.join(zones)})"
                              + (f"; POSSIBLE HIDDEN OVERLOAD in {hid}" if hidden else "")
                              + " -- please verify on site", meter=m, hidden=hidden)
            else:
                self._hidden_alerted = {h for h in self._hidden_alerted if h[0] != m}
                self.log.emit("consistency", f"{m}: power balance restored (residual {ev['residual']:+.0f} kW)",
                              meter=m, flagged=False, residual=ev["residual"])
        st.inconsistent = self.consistency.flagged_sensors(st)

    # ================================================================== learning (anti-poisoned)
    def _learn(self, unknown_changed, before: dict, t: float, now: datetime) -> None:
        st = self.state
        if unknown_changed:
            after = {zid: z.last_kw for zid, z in st.zones.items() if z.last_kw is not None}
            if len(unknown_changed) > 1:
                self.log.emit("learned_effect", f"{len(unknown_changed)} unknown switches changed together; "
                              f"cannot attribute the load change")
            else:
                sid, new = unknown_changed[0]
                obs, detail = self.learner.observe(sid, new, before, after, commit=False)
                if obs is None:
                    self.log.emit("learned_effect", f"{sid} -> {new.value}: {detail}", switch_id=sid)
                else:
                    zones = [z for z in (obs.from_zone, obs.to_zone) if z]
                    sensors = [st.sensor_of(z) for z in zones]
                    status = {s: st.verdict_status(s, now) if s else "missing" for s in sensors}
                    shaky = [s for s, v in status.items() if v != "trusted" or s in st.inconsistent]
                    shaky += [st.sensor_of(z) for z in zones if st.zones[z].suspect_kw is not None]
                    if shaky:
                        self.log.emit("learned_effect", f"{sid} -> {new.value}: observation refused, sensors not "
                                      f"trusted ({', '.join(sorted(set(map(str, shaky))))}) -- learning is "
                                      f"protected against poisoned data", switch_id=sid)
                    else:
                        self.pending_obs.append(_PendingObs(sid, obs, sensors, t + self.cfg.learning.confirm_s))
                        self.log.emit("learned_effect", f"{sid} -> {new.value}: observed {detail}; kept if "
                                      f"{', '.join(sensors)} stay trusted for {self.cfg.learning.confirm_s:g} s",
                                      switch_id=sid)
        due = [p for p in self.pending_obs if t >= p.until]
        self.pending_obs = [p for p in self.pending_obs if t < p.until]
        for p in due:
            if any(st.verdict_status(s, now) != "trusted" or s in st.inconsistent for s in p.sensors):
                self.log.emit("learned_effect", f"{p.switch_id}: observation discarded -- a sensor lost trust",
                              switch_id=p.switch_id)
                continue
            self.learner.commit(p.switch_id, p.obs)
            learned = self.learner.learned(p.switch_id)
            if learned and p.switch_id not in self.effects:
                self.log.emit("learned_effect", f"{p.switch_id} effect adopted: {learned.kind.value} "
                              f"{learned.from_zone}->{learned.to_zone or '(off)'} "
                              f"fraction {learned.transfer_fraction}", switch=learned.model_dump(mode="json"))
                st.city = CityDescriptor(city_id=st.city.city_id, zones=st.city.zones,
                                         aggregate_meters=st.city.aggregate_meters,
                                         switches=[learned if s.switch_id == p.switch_id else s
                                                   for s in st.city.switches])
                self._assess("learned switch effect")

    # ================================================================== verify
    def _verify(self, t: float) -> None:
        st = self.state
        for sid, p in list(self.pending.items()):
            v = verify(p, st, t, self.cfg.action.verify_tolerance)
            if v is None:
                continue
            del self.pending[sid]
            self.verifications.append(v)
            self.log.emit("verification", f"{p.candidate.label}: {v.outcome} -- {v.detail}",
                          correlation_id=p.cmd.decision_id, outcome=v.outcome, observed=v.observed,
                          action=p.candidate.label, action_kind=p.candidate.kind)
            if v.outcome == "FAILED":
                self.failed.add(sid)
                st_now = st.switches.get(sid)
                target = SwitchState.closed if p.cmd.operation == SwitchOperation.close else SwitchState.open
                if st_now is not None and st_now.state == target:   # reported moved, power says it did not
                    self.status_override[sid] = SwitchState.open if target == SwitchState.closed else SwitchState.closed
                self.changed.pop(sid, None)
                evt = self.log.emit("actuator_anomaly", f"{sid} did not reach the commanded state; excluded from "
                                    f"autonomous use, re-planning", correlation_id=p.cmd.decision_id,
                                    switch_id=sid, command_id=p.cmd.command_id)
                self._report(evt)
                self._seen_sigs = set()
            elif v.outcome == "MISMATCH":
                evt = self.log.emit("actuator_anomaly", f"{sid} moved but the grid did not respond as modelled; "
                                    f"possible sensor or actuator inconsistency", correlation_id=p.cmd.decision_id,
                                    switch_id=sid, command_id=p.cmd.command_id)
                self._report(evt)
            if v.outcome != "FAILED" and p.candidate.kind == "restore":
                self.changed.pop(sid, None)

    # ================================================================== J-1 plan
    def _check_plan(self) -> None:
        if not self.day_ahead:
            return
        ing = self.cfg.ingest
        for zid, z in self.state.zones.items():
            exp = self.plan(zid)
            if exp is None or z.last_kw is None or exp <= 0:
                continue
            dev = (z.last_kw - exp) / exp
            z.deviation_count = z.deviation_count + 1 if abs(dev) > ing.plan_deviation_frac else 0
            if z.deviation_count >= ing.plan_deviation_samples and not z.deviating:
                z.deviating = True
                self.log.emit("plan_deviation", f"{zid}: {z.last_kw:.0f} kW vs J-1 plan {exp:.0f} kW "
                              f"({100 * dev:+.0f}%) for {ing.plan_deviation_samples} s", zone_id=zid,
                              actual_kw=round(z.last_kw, 1), plan_kw=round(exp, 1))
            elif z.deviating and z.deviation_count == 0:
                z.deviating = False
                self.log.emit("plan_deviation", f"{zid}: back on the J-1 plan ({z.last_kw:.0f} vs {exp:.0f} kW)",
                              zone_id=zid)

    # ================================================================== predict
    def _update_risk(self, now: datetime) -> None:
        f = self.cfg.forecast
        ol = outlook(self.state)
        for zid, z in self.state.zones.items():
            if z.last_kw is None:
                continue
            cap = self.state.capacity(zid)
            fc = z.model.snapshot()
            prob = None
            if cap:
                if f.mode == "reactive" or not fc.ready:
                    risky, ref = z.last_kw > cap, z.last_kw
                else:
                    ref = max(z.last_kw, fc.peak_upper(f.horizon_s, f.band_sigma))
                    risky = ref > cap
                o = ol.get(zid)
                prob = p_overload(o.mu, o.s, cap) if o else None
                basis = f"{ref:.0f} kW vs capacity {cap:g} kW" + (f", P(overload) {100 * prob:.0f}%" if prob else "")
            else:
                plan = self.plan(zid)
                base = plan if plan else z.model.mean
                ref = fc.at(f.horizon_s)
                risky = fc.ready and ref > f.l0_alert_ratio * base
                basis = (f"forecast {ref:.0f} kW vs {'J-1 plan' if plan else 'own baseline'} {base:.0f} kW "
                         f"(capacity unknown)")
            z.risk_count = z.risk_count + 1 if risky else 0
            was = z.at_risk
            if not was:
                z.at_risk = z.risk_count >= f.risk_persistence
                z.clear_count = 0
            else:   # hysteresis: stay at risk until clearly below the limit for a while (no alert flicker)
                calm = (ref <= f.clear_ratio * cap) if cap else not risky
                z.clear_count = z.clear_count + 1 if calm else 0
                z.at_risk = z.clear_count < f.risk_persistence
            if z.at_risk and not was:
                self.log.emit("risk_detected", f"{zid}: {'predicted' if f.mode == 'predictive' else 'actual'} "
                              f"overload within {f.horizon_s:g} s ({basis})", zone_id=zid, now_kw=round(z.last_kw, 1),
                              forecast_kw=round(ref, 1), capacity_kw=cap, priority=self.state.is_priority(zid, now),
                              trend_kw_per_s=round(fc.trend, 2), p_overload=prob)
                if not cap:
                    self.log.emit("operator_alert", f"{zid}: unusual load rise but capacity unknown -- "
                                  f"alert only (L0), no autonomous switching", zone_id=zid)
            elif was and not z.at_risk:
                self.log.emit("risk_cleared", f"{zid}: back within limits ({basis})", zone_id=zid)
                self._alerted = {a for a in self._alerted if a[0] != zid}
                self._blocked_logged = {b for b in self._blocked_logged if b[0] != zid}
                self._seen_sigs = {s for s in self._seen_sigs if zid not in s[0]}

    def _log_forecast(self) -> None:
        h, k = self.cfg.forecast.horizon_s, self.cfg.forecast.band_sigma
        zones = {}
        for zid, z in self.state.zones.items():
            if z.last_kw is None:
                continue
            fc = z.model.snapshot()
            cap = self.state.capacity(zid)
            zones[zid] = {"now_kw": round(z.last_kw, 1), "forecast_kw": round(fc.at(h), 1),
                          "upper_kw": round(fc.upper(h, k), 1), "trend_kw_s": round(fc.trend, 2),
                          "capacity_kw": cap, "ready": fc.ready, "plan_kw": _r(self.plan(zid)),
                          **({"model": fc.info} if fc.info else {})}
        txt = ", ".join(f"{z} {v['now_kw']:.0f}->{v['upper_kw']:.0f}" + (f"/{v['capacity_kw']:g}" if v['capacity_kw'] else "")
                        for z, v in zones.items())
        self.log.emit("forecast", f"+{h:g}s upper band: {txt}", zones=zones)

    # ================================================================== decide + act
    def _decide_and_act(self, risky: list[str], t: float, now: datetime) -> None:
        st = self.state
        busy = {z for p in self.pending.values() for z in p.candidate.shift}
        if not [z for z in risky if z not in busy]:
            return  # every risky zone has an action in flight; wait for its verification
        usable = set(self.knowledge.actionable_switches) - self.failed
        result = evaluate(st, self.effects, usable)
        # zones with an action in flight are left alone; other risky zones can be handled in parallel
        result.ranked = [c for c in result.ranked if not (set(c.shift) & busy)]
        decision_id = new_id("dec")
        if self.cfg.action.planner and self.cfg.forecast.mode == "predictive":
            plan_c = self._plan(usable, risky, result, decision_id, t)
            if plan_c == "wait":
                return
            if plan_c is not None:
                result.ranked.insert(0, plan_c)
        self.last_eval = {"t": round(t - (self.t_start or t), 1), "risky": risky, "decision_id": decision_id,
                          **result.summary(st)}
        sig = (tuple(risky), tuple(c.label for c in result.ranked), tuple(sorted(self.failed)))
        if sig not in self._seen_sigs:          # log each distinct outcome once per risk episode
            self._seen_sigs.add(sig)
            self.log.emit("candidates_evaluated", f"risk in {', '.join(risky)}: {len(result.ranked)} option(s) better "
                          f"than doing nothing, {len(result.rejected)} rejected", correlation_id=decision_id,
                          risky=risky, **result.summary(st))

        blocked_any = False
        for c in result.ranked:
            g = self._gate(c, t, now)
            if not g.passed:
                blocked_any = True
                key = (risky[0], f"{c.switch_id}:{g.rule}")
                if key not in self._blocked_logged:
                    self._blocked_logged.add(key)
                    self.log.emit("gate_blocked", f"{c.label} blocked ({g.rule}): {g.detail}",
                                  correlation_id=decision_id, rule=g.rule, trust=g.trust, action=c.label)
                continue
            if self.mode == "advisory":
                self._propose(c, decision_id, risky, result.no_action, t)
            else:
                self._execute(c, g, decision_id, risky, result.no_action, t)
            return

        why = "every useful option is blocked by the safety/trust gate" if blocked_any else \
              "no safe switching option improves on doing nothing"
        for zid in risky:
            # a zone already brought back to a low overload probability (e.g. just relieved, risk clearing through
            # hysteresis) needs no operator: alert only while the do-nothing future is still genuinely at risk
            if result.no_action.p_over.get(zid, 1.0) < self.cfg.action.alert_min_p:
                continue
            if (zid, why) not in self._alerted:
                self._alerted.add((zid, why))
                self.log.emit("operator_alert", f"{zid}: overload risk and {why} -- operator action needed",
                              correlation_id=decision_id, zone_id=zid)

    def _plan(self, usable: set[str], risky: list[str], result, decision_id: str, t: float):
        """Multi-zone coordination (planner.py): a plan of several moves when it beats every single move."""
        from .planner import first_step_candidate, search
        st = self.state
        ol = outlook(st)
        not_now = frozenset(sid for sid in usable if sid in self.pending or t < self.cooldown_until.get(sid, -1e18)
                            or (sid in self.vetoes and t < self.vetoes[sid][0]))
        plan, base = search(st, self.effects, usable, ol, self.cfg.action.max_plan_steps, not_now)
        # a plan in progress may need a switch that is still cooling down for a few seconds: wait for it rather
        # than let a worse fallback (last-resort shedding) cut customers in the middle of the plan
        blocked = frozenset(sid for sid in not_now if sid in self.pending or (sid in self.vetoes and t < self.vetoes[sid][0])
                            or self.cooldown_until.get(sid, -1e18) - t > self.cfg.action.plan_wait_max_s)
        later, _ = search(st, self.effects, usable, ol, self.cfg.action.max_plan_steps, blocked)
        if later is not None and later.steps[0][0] in not_now and later.score < base and \
                (plan is None or later.score < plan.score - self.cfg.action.switching_cost):
            sid = later.steps[0][0]
            key = ("plan_wait", tuple(later.labels))
            if key not in self._seen_sigs:
                self._seen_sigs.add(key)
                self.log.emit("plan", f"risk in {', '.join(risky)}: best plan {' -> '.join(later.labels)} starts "
                              f"with {sid}, in cooldown for {self.cooldown_until.get(sid, t) - t:.0f} s more; "
                              f"waiting for it instead of a worse fallback", correlation_id=decision_id,
                              steps=later.labels, waiting_for=sid)
            return "wait"
        if plan is None or len(plan.steps) < 2 or plan.score >= base:
            return None        # a single move (decision.py) is as good: nothing to coordinate
        if self.pending:
            return "wait"      # a plan step is awaiting verification: re-plan from what is observed next
        c = first_step_candidate(st, self.effects, usable, ol, plan)
        sig = ("plan", tuple(plan.labels))
        if sig not in self._seen_sigs:
            self._seen_sigs.add(sig)
            singles = ", ".join(c2.label for c2 in result.ranked if c2.kind == "relief") or "none feasible"
            after = ", ".join(f"{z} {100 * plan.mu[z] / st.capacity(z):.0f}%" for z in sorted(plan.mu) if st.capacity(z))
            self.log.emit("plan", f"risk in {', '.join(risky)}: {len(plan.steps)}-step plan "
                          f"{' -> '.join(plan.labels)} (expected after: {after}); single moves: {singles}; "
                          f"executing step 1, re-planning after each verified step", correlation_id=decision_id,
                          steps=plan.labels, score=round(plan.score, 4), no_action_score=round(base, 4),
                          expected_kw=plan.mu, p_overload={z: round(p, 3) for z, p in plan.p_over.items() if p >= 0.005},
                          intermediate_upper_kw=plan.intermediate)
        return c

    def _gate(self, c: Candidate, t: float, now: datetime):
        v = self.vetoes.get(c.switch_id)
        if v and t < v[0]:
            from .gate import GateResult
            return GateResult(False, "orchestrator", f"vetoed by the orchestrator for {v[0] - t:.0f} s more: {v[1]}")
        return check(self.state, c, self.effects[c.switch_id], t=t, cooldown_until=self.cooldown_until,
                     busy=set(self.pending), failed=self.failed, now=now,
                     busy_zones=frozenset(z for p in self.pending.values() for z in p.candidate.shift))

    def _restore(self, t: float, now: datetime) -> None:
        st = self.state
        dark = {s.from_zone for sid, s in self.effects.items()          # zones whose feeder is open
                if s.kind == SwitchKind.feeder and st.switches.get(sid)
                and st.switches[sid].state == SwitchState.open}
        # re-energize shed zones first; a tie is only restored when both its zones are energized
        order = sorted(self.changed.items(), key=lambda kv: kv[1].kind != "shed")
        for sid, ch in order:
            if sid in self.pending or sid in self.failed or t < self.cooldown_until.get(sid, 0):
                continue
            sw = self.effects.get(sid)
            if sw and sw.kind == SwitchKind.tie and {sw.from_zone, sw.to_zone} & dark:
                continue
            restore_kw = None
            if sw and sw.kind == SwitchKind.feeder:
                # a dark zone's demand cannot be measured: wait, then try, backing off after each failure
                wait = self.cfg.action.restore_min_shed_s * 2 ** self.shed_attempts.get(sid, 0)
                if t - ch.since < wait:
                    continue
                restore_kw = self.plan(sw.from_zone) or ch.pre_kw
            c, why = evaluate_restore(st, self.effects, sid, restore_kw)
            if c is None:
                continue
            ch.safe_count = ch.safe_count + 1 if why == "safe" else 0
            if ch.safe_count < self.cfg.action.restore_persistence:
                continue
            g = self._gate(c, t, now)
            decision_id = new_id("dec")
            if not g.passed:
                key = ("restore", f"{sid}:{g.rule}")
                if key not in self._blocked_logged:
                    self._blocked_logged.add(key)
                    self.log.emit("gate_blocked", f"{c.label} (restoration) blocked ({g.rule}): {g.detail}",
                                  correlation_id=decision_id, rule=g.rule, trust=g.trust, action=c.label)
                continue
            if self.mode == "advisory":
                self._propose(c, decision_id, [], None, t)
                return
            pct = ", ".join(f"{z} {100 * c.loads[z] / st.capacity(z):.0f}%" for z in c.shift if st.capacity(z))
            self.log.emit("restoration", f"{sid} back to normal: forecast stays safe for "
                          f"{self.cfg.action.restore_persistence} samples ({pct} after {c.label})",
                          correlation_id=decision_id, switch_id=sid)
            self._execute(c, g, decision_id, [], None, t)
            return

    def _execute(self, c: Candidate, g, decision_id: str, risky: list[str], no_action: Optional[Candidate],
                 t: float) -> None:
        st = self.state
        pct = lambda loads, z: f"{100 * loads[z] / st.capacity(z):.0f}%" if st.capacity(z) else "?"  # noqa: E731
        if c.kind == "restore":
            reason = f"restore {c.switch_id}: " + ", ".join(f"{z} {pct(c.loads, z)}" for z in c.shift)
        else:
            def risk_txt(z):
                p = no_action.p_over.get(z, 0)
                return f"{z} forecast {pct(no_action.loads, z)} (P(overload) {100 * p:.0f}%)"
            after = ", ".join(f"{z} {pct(c.loads, z)} (P {100 * c.p_over.get(z, 0):.0f}%)" for z in c.shift)
            reason = (f"{', '.join(risk_txt(z) for z in risky)} within {self.cfg.forecast.horizon_s:g} s; "
                      f"{c.label}{' (last-resort shedding)' if c.kind == 'shed' else ''} -> {after}"
                      + (f"; step 1 of plan {' -> '.join(c.plan)}" if c.plan else ""))
        self.log.emit("decision", reason, correlation_id=decision_id, action=c.label, action_kind=c.kind,
                      score=round(c.score, 4), no_action_score=round(no_action.score, 4) if no_action else None)
        if g.degraded:
            self.log.emit("degraded_mode", f"acting without Guardian verdicts: {g.detail}", correlation_id=decision_id)
            self.log.emit("operator_alert", f"degraded-mode action {c.label} taken; please review",
                          correlation_id=decision_id)
        if c.kind == "shed":
            self.log.emit("operator_alert", f"last-resort load shedding: {c.label} de-energizes "
                          f"{next(iter(c.shift))}; it will be restored automatically when safe",
                          correlation_id=decision_id)
        self.log.emit("gate_passed", f"{c.label}: {g.detail}", correlation_id=decision_id, rule=g.rule,
                      trust=g.trust, degraded=g.degraded)
        cmd = SwitchCommand(switch_id=c.switch_id, operation=c.operation, decision_id=decision_id,
                            reason=reason, verdict_refs=g.verdict_refs, degraded=g.degraded)
        before = {z: (st.zones[z].last_kw or 0.0) for z in c.shift_now}
        self.log.emit("command_sent", f"{cmd.command_id}: {c.label}", correlation_id=decision_id,
                      command=cmd.model_dump(mode="json"), action_kind=c.kind)
        ack, err = send_with_retry(self.twin, cmd, self.cfg.action.max_send_attempts)
        if err:
            self.log.emit("command_ack", f"{cmd.command_id} NOT executed: {err}", correlation_id=decision_id,
                          command_id=cmd.command_id)
            self.failed.add(c.switch_id)
            return
        self.log.emit("command_ack", f"{cmd.command_id}: {ack.status.value}", correlation_id=decision_id,
                      command_id=cmd.command_id, status=ack.status.value)
        self.commands.append(cmd)
        sw = self.effects.get(c.switch_id)
        reenergize = c.kind == "restore" and sw is not None and sw.kind == SwitchKind.feeder
        if c.kind == "shed" and any(p.kind == "restore" and p.switch_id == c.switch_id for p in self._recent_restores):
            self.shed_attempts[c.switch_id] = self.shed_attempts.get(c.switch_id, 0) + 1
            self.log.emit("restoration", f"{c.switch_id}: re-energizing did not hold; next attempt waits "
                          f"{self.cfg.action.restore_min_shed_s * 2 ** self.shed_attempts[c.switch_id]:.0f} s",
                          correlation_id=decision_id, switch_id=c.switch_id)
        if reenergize:
            # no cooldown: if the overload is still there, the zone must be shed again at once (back-off applies)
            self._recent_restores = [c] + self._recent_restores[:3]
        else:
            self.cooldown_until[c.switch_id] = t + self.cfg.action.switch_cooldown_s
        self.pending[c.switch_id] = Pending(cmd, c, t, t + self.cfg.action.ack_timeout_s, before)
        if c.kind == "relief" and c.switch_id in self.changed:
            self.changed.pop(c.switch_id)          # a plan moved it back to its normal state
        elif c.kind in ("relief", "shed") and not g.degraded:
            pre = st.zones[next(iter(c.shift))].last_kw if c.kind == "shed" else None
            self.changed[c.switch_id] = _Changed(c.kind, pre, since=t)

    # ================================================================== supervision API (agent / orchestrator)
    def _propose(self, c: Candidate, decision_id: str, risky: list[str], no_action, t: float) -> None:
        for pid, p in list(self.proposals.items()):
            if t >= p.expires_t:
                del self.proposals[pid]
        if any(p.candidate.label == c.label for p in self.proposals.values()):
            return                                     # already waiting for this one
        pid = new_id("prop")
        if c.kind == "restore":
            reason = f"back to normal: {c.label}"
        else:
            reason = ", ".join(f"{z} P(overload) {100 * no_action.p_over.get(z, 0):.0f}%" for z in risky) if no_action else ""
        self.proposals[pid] = Proposal(pid, c, decision_id, risky, no_action, t, t + self.proposal_ttl_s, reason)
        self.log.emit("proposal", f"{pid}: {c.label} proposed ({reason}); waiting for orchestrator approval "
                      f"(advisory mode, expires in {self.proposal_ttl_s:g} s)", correlation_id=decision_id,
                      proposal_id=pid, action=c.label, action_kind=c.kind)

    def set_mode(self, mode: str, reason: str = "", who: str = "orchestrator") -> dict:
        if mode not in ("autonomous", "advisory"):
            raise ValueError("mode must be 'autonomous' or 'advisory'")
        old, self.mode = self.mode, mode
        if mode == "autonomous":
            self.proposals.clear()
        self.log.emit("orchestrator", f"{who}: mode {old} -> {mode}" + (f" ({reason})" if reason else ""),
                      mode=mode, who=who)
        return {"mode": mode, "previous": old}

    def veto(self, switch_id: str, duration_s: float, reason: str, who: str = "orchestrator") -> dict:
        if switch_id not in self.state.switches and switch_id not in {s.switch_id for s in self.state.city.switches}:
            raise ValueError(f"unknown switch_id '{switch_id}'")
        duration_s = max(1.0, min(float(duration_s), 3600.0))
        until = self.clock() + duration_s
        self.vetoes[switch_id] = (until, reason or "no reason given")
        for pid, p in list(self.proposals.items()):
            if p.candidate.switch_id == switch_id:
                del self.proposals[pid]
        self.log.emit("orchestrator", f"{who}: veto on {switch_id} for {duration_s:.0f} s ({reason})",
                      switch_id=switch_id, duration_s=duration_s, who=who)
        return {"switch_id": switch_id, "vetoed_for_s": duration_s}

    def lift_veto(self, switch_id: str, who: str = "orchestrator") -> dict:
        existed = self.vetoes.pop(switch_id, None) is not None
        if existed:
            self.log.emit("orchestrator", f"{who}: veto on {switch_id} lifted", switch_id=switch_id, who=who)
        return {"switch_id": switch_id, "was_vetoed": existed}

    def approve(self, proposal_id: str, who: str = "orchestrator") -> dict:
        """Execute a proposal -- re-checked NOW: still a ranked option and still through the full gate."""
        t, now = self.clock(), self.wall()
        p = self.proposals.pop(proposal_id, None)
        if p is None:
            return {"executed": False, "why": f"no pending proposal '{proposal_id}' (expired, replaced or unknown)"}
        if t >= p.expires_t:
            return {"executed": False, "why": "proposal expired; ask for current options"}
        self.log.emit("orchestrator", f"{who}: approved {proposal_id} ({p.candidate.label})",
                      correlation_id=p.decision_id, proposal_id=proposal_id, who=who)
        if p.candidate.kind == "restore":
            sw = self.effects.get(p.candidate.switch_id)
            kw = p.candidate.shift.get(sw.from_zone) if sw is not None and sw.kind == SwitchKind.feeder else None
            c, why = evaluate_restore(self.state, self.effects, p.candidate.switch_id, kw)
            if c is None or why != "safe":
                return {"executed": False, "why": f"no longer safe to restore: {why}"}
        else:
            usable = set(self.knowledge.actionable_switches) - self.failed
            res = evaluate(self.state, self.effects, usable)
            c = next((x for x in res.ranked if x.label == p.candidate.label), None)
            if c is None:
                return {"executed": False, "why": "the situation changed: this option is no longer better than "
                                                  "doing nothing"}
            p.no_action = res.no_action
        g = self._gate(c, t, now)
        if not g.passed:
            self.log.emit("gate_blocked", f"{c.label} (approved by {who}) blocked ({g.rule}): {g.detail}",
                          correlation_id=p.decision_id, rule=g.rule, trust=g.trust, action=c.label)
            return {"executed": False, "why": f"safety & trust gate: {g.detail}", "rule": g.rule}
        if c.kind == "restore":
            self.log.emit("restoration", f"{c.switch_id} back to normal (approved by {who})",
                          correlation_id=p.decision_id, switch_id=c.switch_id)
        self._execute(c, g, p.decision_id, p.risky, p.no_action, t)
        return {"executed": True, "action": c.label, "decision_id": p.decision_id,
                "note": "command sent; verification follows within a few seconds (see events)"}

    def what_if(self, switch_id: str) -> dict:
        """Dry run: what toggling this switch would do now, and whether the gate would let it through."""
        t, now = self.clock(), self.wall()
        if switch_id not in self.effects:
            return {"switch_id": switch_id, "known": False,
                    "why": "effect unknown to the Brain (not advertised, not configured, not learned)"}
        res = evaluate(self.state, self.effects, {switch_id})
        c = next(iter(res.ranked), None) or next((x for x in res.rejected if x.switch_id == switch_id), None)
        if c is None:
            return {"switch_id": switch_id, "known": True, "why": "nothing to evaluate (no data or not applicable)"}
        g = self._gate(c, t, now) if c.operation is not None and c.feasible else None
        return {"switch_id": switch_id, "action": c.label, "kind": c.kind,
                "better_than_doing_nothing": c in res.ranked, "why_not": c.why_not,
                "do_nothing": res.no_action.brief(self.state), "after": c.brief(self.state),
                "gate": None if g is None else {"would_pass": g.passed, "rule": g.rule, "detail": g.detail,
                                                "degraded": g.degraded, "trust": g.trust}}

    def options(self) -> dict:
        usable = set(self.knowledge.actionable_switches) - self.failed
        res = evaluate(self.state, self.effects, usable)
        risky = [z for z, s in self.state.zones.items() if s.at_risk]
        return {"zones_at_risk": risky, **res.summary(self.state),
                "recommendation": res.ranked[0].label if res.ranked else "do nothing"}

    def _report(self, evt) -> None:
        try:
            self.guardian.report(evt)
        except PartnerError as e:
            self.log.emit("partner_error", f"could not report to Guardian: {e}")


def _r(v):
    return None if v is None else round(v, 1)

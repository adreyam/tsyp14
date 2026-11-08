"""Partner conformance check: run it against a Twin and a Guardian before
integrating, and send the report to their team. Every FAIL comes with the fix.

  python -m energy_brain.conformance --config config/config.yaml
  python -m energy_brain.conformance --config config/config.yaml --test-switch S12   # also round-trips a command

--test-switch toggles that switch and puts it back: only use it on a test grid.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .adapters import PartnerError, make_guardian, make_twin
from .config import load_config
from .knowledge import assess
from .schemas import SwitchCommand, SwitchOperation, SwitchState


@dataclass
class Result:
    partner: str
    check: str
    status: str          # PASS | WARN | FAIL
    detail: str
    fix: str = ""


def _r(rs, partner, check, ok, detail, fix="", warn=False):
    rs.append(Result(partner, check, "PASS" if ok else ("WARN" if warn else "FAIL"), detail, "" if ok else fix))
    return ok


def check_twin(twin, test_switch: Optional[str] = None, stale_s: float = 10.0,
               sleep: Callable[[float], None] = time.sleep) -> list[Result]:
    rs: list[Result] = []
    P = "twin"
    try:
        city = twin.get_city()
    except PartnerError as e:
        _r(rs, P, "reachable, authenticated, city descriptor valid", False, str(e),
           "check base_url / api_key; the descriptor must match CityDescriptor (see INTERFACE.md section 1)")
        return rs
    _r(rs, P, "reachable, authenticated, city descriptor valid", True, f"{len(city.zones)} zones, {len(city.switches)} switches")
    k = assess(city)
    _r(rs, P, "zone capacities advertised", bool(k.zones_with_capacity),
       f"{len(k.zones_with_capacity)}/{len(city.zones)} zones", "add capacity_kw per zone (or give us a knowledge file): "
       "without it the Brain only alerts (L0)", warn=True)
    _r(rs, P, "switch effects advertised", bool(k.actionable_switches), f"actionable: {k.actionable_switches or 'none'}",
       "add kind/from_zone/to_zone/transfer_fraction per switch; otherwise the Brain must learn them by watching",
       warn=True)
    try:
        b1 = twin.get_readings()
    except PartnerError as e:
        _r(rs, P, "readings endpoint", False, str(e), "see INTERFACE.md: power_reading[]")
        return rs
    _r(rs, P, "readings valid", not b1.rejected, f"{len(b1.valid)} valid, {len(b1.rejected)} rejected"
       + (f" (first: {b1.rejected[0][1]})" if b1.rejected else ""), "fix the fields named in the error")
    zones = {z.zone_id for z in city.zones}
    meters = {m.sensor_id for m in city.aggregate_meters}     # aggregate meters report under their own id
    seen = {r.zone_id for r in b1.valid if r.sensor_id not in meters}
    if meters:
        got = {r.sensor_id for r in b1.valid} & meters
        _r(rs, P, "aggregate meters report", got == meters, f"missing: {sorted(meters - got) or 'none'}",
           "one power_reading per declared aggregate meter (zone_id = the meter's sensor_id)")
    _r(rs, P, "every zone has a reading", zones <= seen, f"missing: {sorted(zones - seen) or 'none'}",
       "one power_reading per zone per call")
    _r(rs, P, "readings reference known zones", seen <= zones, f"unknown: {sorted(seen - zones) or 'none'}",
       "zone_id in readings must match the city descriptor")
    now = datetime.now(timezone.utc)
    ages = [(now - r.ts).total_seconds() for r in b1.valid]
    _r(rs, P, "readings fresh and clock in sync", bool(ages) and max(map(abs, ages)) <= stale_s,
       f"age {min(ages, default=0):.1f}..{max(ages, default=0):.1f} s", f"timestamps must be within {stale_s:g} s of UTC now "
       "(NTP-synchronized clock, ISO-8601 with timezone)")
    _r(rs, P, "sequence numbers present", all(r.seq is not None for r in b1.valid), "seq on every reading",
       "add an increasing seq per sensor: it protects against replays", warn=True)
    sleep(1.1)
    b2 = twin.get_readings()
    s1 = {r.sensor_id: r.seq for r in b1.valid}
    inc = [r.seq > s1[r.sensor_id] for r in b2.valid if r.seq is not None and s1.get(r.sensor_id) is not None]
    _r(rs, P, "sequence increases between calls", all(inc) if inc else True, f"{sum(inc)}/{len(inc)} increased",
       "seq must strictly increase per sensor")
    try:
        sws = twin.get_switches()
        ids = {s.switch_id for s in sws}
        _r(rs, P, "switch status valid and complete", ids == {s.switch_id for s in city.switches},
           f"{len(sws)} switches", "one switch_status per switch in the descriptor")
    except PartnerError as e:
        _r(rs, P, "switch status valid and complete", False, str(e), "see INTERFACE.md: switch_status[]")
        return rs
    if test_switch:
        st = {s.switch_id: s.state for s in sws}.get(test_switch)
        if st is None:
            _r(rs, P, f"command round-trip on {test_switch}", False, "unknown switch", "pick a switch id from the descriptor")
            return rs
        op = SwitchOperation.close if st == SwitchState.open else SwitchOperation.open
        back = SwitchOperation.open if op == SwitchOperation.close else SwitchOperation.close
        cmd = SwitchCommand(switch_id=test_switch, operation=op, decision_id="conformance", reason="conformance test")
        try:
            ack = twin.send_command(cmd)
            dup = twin.send_command(cmd)
            sleep(1.0)
            moved = {s.switch_id: s.state for s in twin.get_switches()}[test_switch] != st
            twin.send_command(SwitchCommand(switch_id=test_switch, operation=back, decision_id="conformance",
                                            reason="conformance test: put back"))
            _r(rs, P, f"command round-trip on {test_switch}", ack.status.value == "accepted" and moved,
               f"ack {ack.status.value}, state changed: {moved}", "accept switch_command and report the new state")
            _r(rs, P, "duplicate command not re-executed", dup.status.value in ("duplicate", "accepted"),
               f"second send -> {dup.status.value}", "remember command_id and answer 'duplicate'", warn=True)
        except PartnerError as e:
            _r(rs, P, f"command round-trip on {test_switch}", False, str(e), "see INTERFACE.md section 2")
    return rs


def check_guardian(guardian, subjects: list[str], ttl_s: float = 10.0) -> list[Result]:
    rs: list[Result] = []
    P = "guardian"
    try:
        vs = guardian.get_verdicts(subjects)
    except PartnerError as e:
        _r(rs, P, "reachable, authenticated, verdicts valid", False, str(e),
           "check base_url / api_key; answer trust_verdict[] (see INTERFACE.md)")
        return rs
    _r(rs, P, "reachable, authenticated, verdicts valid", True, f"{len(vs)} verdicts")
    got = {v.subject_id for v in vs}
    _r(rs, P, "a verdict for every subject asked", set(subjects) <= got, f"missing: {sorted(set(subjects) - got) or 'none'}",
       "answer every ?subject= (sensor ids and switch ids)")
    now = datetime.now(timezone.utc)
    ages = [(now - v.ts).total_seconds() for v in vs]
    _r(rs, P, f"verdicts fresher than {ttl_s:g} s", bool(ages) and max(ages) <= ttl_s,
       f"oldest {max(ages, default=0):.1f} s", "stamp verdicts when issued; stale ones count as missing (degraded mode)")
    return rs


def report(results: list[Result]) -> str:
    w = max(len(r.check) for r in results) if results else 10
    lines = []
    for r in results:
        lines.append(f"{r.status:4}  {r.partner:8}  {r.check:<{w}}  {r.detail}")
        if r.fix:
            lines.append(f"{'':4}  {'':8}  {'':<{w}}  -> {r.fix}")
    fails = sum(r.status == "FAIL" for r in results)
    warns = sum(r.status == "WARN" for r in results)
    lines.append(f"\n{len(results)} checks: {fails} FAIL, {warns} WARN")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--test-switch", default=None)
    a = ap.parse_args()
    cfg = load_config(a.config)
    twin, guardian = make_twin(cfg.twin), make_guardian(cfg.guardian)
    rs = check_twin(twin, a.test_switch, cfg.ingest.stale_s)
    subjects = []
    try:
        subjects = [r.sensor_id for r in twin.get_readings().valid] + [s.switch_id for s in twin.get_switches()]
    except PartnerError:
        pass
    rs += check_guardian(guardian, subjects or ["unknown"], cfg.trust.verdict_ttl_s)
    print(report(rs))
    sys.exit(1 if any(r.status == "FAIL" for r in rs) else 0)


if __name__ == "__main__":
    main()

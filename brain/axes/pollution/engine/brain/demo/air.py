"""
Test Book scenarios for the air-quality axis (R2).

One command launches a scenario and leaves a log in logs/<ID>.jsonl.
From the triplet root:    python scenarios/a1_rush_hour_peak.py
From the brain/ folder:   python -m demo.air A1      (or: all)

Each scenario prints its checks as PASS/FAIL, then its key events. Where it
matters, a WITNESS run shows what would happen without the protection being
tested (no hysteresis, no Guardian), so the jury sees the protection is real.
"""
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from mocks.mock_air import MockAirCity
from mocks.mock_guardian import ScriptedGuardian
from src.air import AirBrain, AirConfig
from src.audit_log import AuditLog

from .garbage import default_log_dir

HOURS_14 = 84                 # 06:00 -> 20:00, one reading every 10 min


@dataclass
class Result:
    scenario_id: str
    name: str
    log: AuditLog
    city: MockAirCity
    brain: AirBrain
    checks: List[Tuple[str, bool]] = field(default_factory=list)

    def check(self, text: str, ok: bool) -> None:
        self.checks.append((text, bool(ok)))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.checks)

    def levels(self, zone: str) -> List[str]:
        return [e["level"] for e in self.log.of("air_level") if e["zone"] == zone]

    def closures(self, lot: str) -> int:
        return sum(1 for _, l, m in self.city.barrier_log if l == lot and m == "closed")


def run(sid: str, name: str, ticks: int, city: Optional[MockAirCity] = None,
        guardian=None, hooks: Optional[Dict[int, Callable]] = None,
        config: AirConfig = AirConfig(), write_log: bool = True) -> Result:
    """hooks[k](city, guardian) runs before cycle k."""
    city = city or MockAirCity()
    guardian = guardian if guardian is not None else ScriptedGuardian()
    path = None
    if write_log:
        os.makedirs(default_log_dir(), exist_ok=True)
        path = os.path.join(default_log_dir(), f"{sid}.jsonl")
    log = AuditLog(path)
    brain = AirBrain(guardian, city, log, config)
    for k in range(ticks):
        if hooks and k in hooks:
            hooks[k](city, guardian)
        msgs, now = city.tick()
        brain.step(msgs, now)
    return Result(sid, name, log, city, brain)


class NoGuardian:
    """Witness runs only: trusts everything, like a Brain without a Guardian."""

    def get_trust_verdict(self, reading):
        return ScriptedGuardian().get_trust_verdict(reading)


def flips(r: "Result", zone: str, within_min: float = 60.0) -> int:
    """Flapping: a zone comes back to a level less than `within_min` after leaving it
    (high -> warning -> high in 20 min). Slow changes over hours are normal."""
    ev = [e for e in r.log.of("air_level") if e["zone"] == zone]
    out = 0
    for a, b, c in zip(ev, ev[1:], ev[2:]):
        if c["level"] == a["level"]:
            t_b = datetime.fromisoformat(b["ts"].replace("Z", "+00:00"))
            t_c = datetime.fromisoformat(c["ts"].replace("Z", "+00:00"))
            if (t_c - t_b).total_seconds() < within_min * 60:
                out += 1
    return out


# ---------------------------------------------------------------------------
# A1: rush-hour peak downtown -> alert and car park closed, then lifted, no flapping.
# ---------------------------------------------------------------------------
def a1_rush_hour_peak(write_log: bool = True) -> Result:
    r = run("A1", "Rush-hour peak downtown, alert then lift without flapping",
            HOURS_14, write_log=write_log)
    witness = run("A1-witness", "same day without hysteresis", HOURS_14, write_log=False,
                  config=AirConfig(exit_margin=0, exit_readings=1, min_level_s=0))
    r.check("zone B reaches 'high' at the morning and evening rush hours",
            r.levels("B").count("high") == 2)
    r.check("a public alert is issued in zone B and later lifted",
            any(e["action"] == "public_alert" and e["zone"] == "B" for e in r.log.of("air_action"))
            and any(e["action"] == "lift_public_alert" and e["zone"] == "B"
                    for e in r.log.of("air_action")))
    r.check("car park LOT-02 is closed once per rush hour, and open again at the end",
            r.closures("LOT-02") == 2 and r.city.barriers["LOT-02"] == "auto")
    r.check("no zone flaps (comes back to a level within 1 h of leaving it)",
            all(flips(r, z) == 0 for z in "ABCD"))
    r.check(f"witness without hysteresis: LOT-02 closed {witness.closures('LOT-02')} times "
            f"and zone B flaps {flips(witness, 'B')} times; with it: {r.closures('LOT-02')} and 0",
            witness.closures("LOT-02") > r.closures("LOT-02") and flips(witness, "B") > 0)
    r.check("zone A (residential) never needs a restriction",
            "high" not in r.levels("A") and r.closures("LOT-01") == 0)
    return r


# ---------------------------------------------------------------------------
# A2: the industrial sensor caps its values (cheating) while the real air is bad.
# ---------------------------------------------------------------------------
def a2_capped_industrial_sensor(write_log: bool = True) -> Result:
    def release(city, g):            # 08:00 the plant releases particles
        city.event["D"] = 24.0

    def cap(city, g):                # 09:00 the plant's own sensor starts reporting a flat low value
        city.reported["AIR-07"] = {"pm25": 9.0, "o3": 9.0, "no2": 12.0}

    def detect(city, g):             # the Guardian spots the flat line after 30 min
        g.distrust("AIR-07", "flat for 30 min while PM2.5 nearby varies by more than 15 %")

    hooks = {12: release, 18: cap, 21: detect}     # release 08:10, cap 09:10, detected 09:40
    r = run("A2", "Industrial sensor caps its values: no false all-clear", 48, hooks=hooks,
            write_log=write_log)
    witness = run("A2-witness", "same, without the Guardian", 48, guardian=NoGuardian(),
                  hooks={12: release, 18: cap}, write_log=False)
    severe = ("high", "critical")
    r.check("the real release puts zone D at high or critical and closes LOT-04",
            any(l in severe for l in r.levels("D")) and r.closures("LOT-04") >= 1)
    unknown = [e for e in r.log.of("air_zone_unknown") if e["zone"] == "D"]
    r.check("once the sensor is distrusted, zone D is 'unknown' and keeps its severe level",
            bool(unknown) and unknown[0]["kept_level"] in severe)
    r.check("between the cap and the Guardian's detection, D goes down one step at most",
            all(e["level"] != "normal" for e in r.log.of("air_level")
                if e["zone"] == "D" and e["ts"][11:16] > "09:00"))
    r.check("the Brain never announces normal air in zone D after the cap",
            r.brain.zones["D"].level in severe and r.city.barriers["LOT-04"] == "closed")
    r.check("witness without the Guardian: zone D falls back to normal and LOT-04 reopens",
            witness.brain.zones["D"].level == "normal" and witness.city.barriers["LOT-04"] == "auto")
    return r


# ---------------------------------------------------------------------------
# A3: a fake PM2.5 spike is injected on one school-area sensor.
# ---------------------------------------------------------------------------
def a3_fake_spike(write_log: bool = True) -> Result:
    def spoof(city, g):
        city.reported["AIR-05"] = {"pm25": 180.0}
        g.distrust("AIR-05", "PM2.5 jumped from 9 to 180 ug/m3 in 10 min, AIR-06 next door unchanged")

    def stop(city, g):
        city.reported.pop("AIR-05", None)
        g.trust("AIR-05")

    hooks = {24: spoof, 27: stop}            # 10:00 -> 10:30
    r = run("A3", "Fake PM2.5 spike near the schools: no unjustified restriction", 36,
            hooks=hooks, write_log=write_log)
    witness = run("A3-witness", "same, without the Guardian", 36, guardian=NoGuardian(),
                  hooks={24: lambda c, g: c.reported.__setitem__("AIR-05", {"pm25": 180.0}),
                         27: lambda c, g: c.reported.pop("AIR-05", None)}, write_log=False)
    blocked = [e for e in r.log.of("reading_blocked") if e["sensor_id"] == "AIR-05"]
    r.check("the Guardian's verdict blocks the fake readings", len(blocked) >= 3)
    r.check("zone C is never raised above warning, LOT-03 never closed",
            "high" not in r.levels("C") and "critical" not in r.levels("C")
            and r.closures("LOT-03") == 0)
    r.check("zone C stays known: AIR-06 (trusted) keeps reporting",
            not [e for e in r.log.of("air_zone_unknown") if e["zone"] == "C"])
    r.check("witness without the Guardian: zone C goes critical and LOT-03 is closed",
            "critical" in witness.levels("C") and witness.closures("LOT-03") >= 1)
    return r


# ---------------------------------------------------------------------------
# A4: the Guardian goes down during the morning peak.
# ---------------------------------------------------------------------------
def a4_guardian_down(write_log: bool = True) -> Result:
    def down(city, g):
        g.down = True

    def up(city, g):
        g.down = False

    hooks = {11: down, 20: up}               # 07:50 -> 09:20
    r = run("A4", "Guardian down during the morning peak", 30, hooks=hooks, write_log=write_log)
    r.check("the outage is logged once, the recovery once",
            len(r.log.of("guardian_unavailable")) == 1 and len(r.log.of("guardian_recovered")) == 1)
    r.check("every zone becomes 'unknown' during the outage",
            {e["zone"] for e in r.log.of("air_zone_unknown")} == set("ABCD"))
    lifts = [e for e in r.log.of("air_action") if e["action"].startswith(("lift", "reopen"))
             and "07:50" <= e["ts"][11:16] < "09:20"]
    r.check("no alert is lifted and no car park reopened while blind", not lifts)
    r.check("after recovery the zones are known again and follow the real air",
            len(r.log.of("air_zone_known")) == 4 and r.brain.zones["B"].level != "unknown")
    return r


# ---------------------------------------------------------------------------
# A5: a real peak downtown while an attacker fakes a peak elsewhere.
# ---------------------------------------------------------------------------
def a5_real_peak_during_attack(write_log: bool = True) -> Result:
    def attack(city, g):
        city.reported["AIR-01"] = {"pm25": 150.0, "no2": 90.0}
        g.distrust("AIR-01", "values impossible next to AIR-02, signature mismatch")

    r = run("A5", "Real peak downtown during a fake peak in a residential zone", 24,
            hooks={8: attack}, write_log=write_log)
    r.check("zone B (real rush-hour peak) is restricted: LOT-02 closed",
            "high" in r.levels("B") and r.closures("LOT-02") >= 1)
    r.check("zone A (fake peak) is not restricted: LOT-01 never closed",
            r.closures("LOT-01") == 0 and "high" not in r.levels("A"))
    r.check("zone A keeps a trusted sensor (AIR-02), so it stays known",
            not [e for e in r.log.of("air_zone_unknown") if e["zone"] == "A"])
    return r


SCENARIOS = {"A1": a1_rush_hour_peak, "A2": a2_capped_industrial_sensor,
             "A3": a3_fake_spike, "A4": a4_guardian_down, "A5": a5_real_peak_during_attack}

SHOW = ("air_level", "air_action", "air_zone_unknown", "air_zone_known", "reading_blocked",
        "guardian_unavailable", "guardian_recovered")


def describe(e: dict) -> Optional[str]:
    t = e["ts"][11:16]
    ev = e["event"]
    if ev == "air_level":
        return f"{t}  BRAIN     zone {e['zone']} -> {e['level']} (risk {e['risk']}): {e['reason']}"
    if ev == "air_action":
        tgt = f" {e['target']}" if e.get("target") else ""
        return f"{t}  BRAIN     zone {e['zone']}: {e['action']}{tgt}"
    if ev == "air_zone_unknown":
        return f"{t}  BRAIN     zone {e['zone']} UNKNOWN: {e['reason']}"
    if ev == "air_zone_known":
        return f"{t}  BRAIN     zone {e['zone']} known again"
    if ev == "reading_blocked":
        return f"{t}  BRAIN     {e['sensor_id']} ignored ({e['reason']})"
    if ev in ("guardian_unavailable", "guardian_recovered"):
        return f"{t}  BRAIN     {ev}"
    return None


def main(argv: List[str]) -> int:
    wanted = argv[1] if len(argv) > 1 else "all"
    ids = list(SCENARIOS) if wanted == "all" else [wanted]
    failed = 0
    for sid in ids:
        if sid not in SCENARIOS:
            print(f"Unknown scenario '{sid}'. Options: {list(SCENARIOS)} or 'all'")
            return 2
        r = SCENARIOS[sid]()
        print(f"\n=== {r.scenario_id}: {r.name} ===")
        for text, ok in r.checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
        print("  key events:")
        seen = set()
        for e in r.log.events:
            if e["event"] in SHOW:
                line = describe(e)
                key = (e["event"], e.get("sensor_id"), e.get("reason"))
                if e["event"] == "reading_blocked" and key in seen:
                    continue
                seen.add(key)
                print("   ", line)
        print(f"  log: logs/{r.scenario_id}.jsonl")
        failed += 0 if r.passed else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

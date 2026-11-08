"""
Test Book scenarios for the garbage-bin axis (R2).

One command launches a scenario and leaves a log in logs/<ID>.jsonl.
From the triplet root:    python scenarios/g3_spoofed_reading.py
From the brain/ folder:   python -m demo.garbage G3      (or: all)

Every scenario prints its checks as PASS/FAIL, then its timeline.
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from mocks.mock_guardian import ScriptedGuardian
from src.audit_log import AuditLog
from src.bin_status import BinConfig
from src.brain import BrainConfig, CityBrain
from src.planner import PlannerConfig

from .city import DEPOT, build_graph, build_twin
from .timeline import render



def default_log_dir() -> str:
    """logs/ at the triplet root when run inside the triplet repo, else brain/logs."""
    env = os.environ.get("CITY_LOG_DIR")
    if env:
        return env
    brain_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    root = os.path.dirname(brain_dir)
    if os.path.isdir(os.path.join(root, "scenarios")):
        return os.path.join(root, "logs")
    return os.path.join(brain_dir, "logs")


TICK_S = 300.0                      # the Twin sends every bin every 5 minutes


@dataclass
class Result:
    scenario_id: str
    name: str
    log: AuditLog
    twin: object
    brain: CityBrain
    guardian: ScriptedGuardian
    checks: List[Tuple[str, bool]] = field(default_factory=list)

    def check(self, text: str, ok: bool) -> None:
        self.checks.append((text, bool(ok)))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.checks)

    def dispatches(self, truck: Optional[str] = None) -> List[dict]:
        return [e for e in self.log.of("dispatch")
                if truck is None or e["truck_id"] == truck]


def run(scenario_id: str, name: str, ticks: int, twin, guardian=None,
        truck_capacity_kg: float = 200.0, hooks: Optional[Dict[int, Callable]] = None,
        write_log: bool = True) -> Result:
    """Run `ticks` cycles. hooks[k](twin, guardian) runs before cycle k."""
    guardian = guardian or ScriptedGuardian()
    path = None
    if write_log:
        log_dir = default_log_dir()
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"{scenario_id}.jsonl")
    log = AuditLog(path)
    config = BrainConfig(
        bin=BinConfig(),
        planner=PlannerConfig(truck_capacity_kg=truck_capacity_kg, horizon_stops=3,
                              depot_node=DEPOT),
        reading_interval_s=TICK_S)
    brain = CityBrain(twin.graph, guardian, twin, log, config)
    hooks = hooks or {}
    for k in range(ticks):
        if k in hooks:
            hooks[k](twin, guardian)
        bins, trucks, now = twin.tick()
        brain.step(bins, trucks, now)
    return Result(scenario_id, name, log, twin, brain, guardian)


def overflow_kg(twin) -> float:
    return sum(b.overflow_kg for b in twin.bins.values())


def needing_pickup(r: Result, margin_h: float = 2.0) -> set:
    """Bins that turned orange/red at least `margin_h` before the run ended."""
    end = r.twin.now.isoformat()
    cutoff_h = margin_h * 3600
    from datetime import datetime
    last = datetime.fromisoformat(end)
    out = set()
    for e in r.log.of("bin_status"):
        if e["color"] in ("orange", "red"):
            t = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
            if (last - t).total_seconds() >= cutoff_h:
                out.add(e["sensor_id"])
    return out


# ---------------------------------------------------------------------------
# G1: a normal day. The city must keep every bin from overflowing.
# ---------------------------------------------------------------------------
def g1_normal_day(write_log: bool = True) -> Result:
    twin = build_twin(build_graph())
    r = run("G1", "Normal day", ticks=288, twin=twin, write_log=write_log)
    r.check("no bin ever overflows", overflow_kg(twin) == 0)
    r.check("every bin that turned orange or red was collected",
            needing_pickup(r) <= {b for _, _, b in twin.served})
    r.check("no truck ever carries more than its capacity",
            all(t.max_load_kg <= t.capacity_kg for t in twin.trucks.values()))
    r.check("nothing is blocked on a calm day", not r.log.of("reading_blocked"))
    return r


# ---------------------------------------------------------------------------
# G2: a red bin appears while a truck is heading to an orange bin.
# ---------------------------------------------------------------------------
def g2_red_takes_over_orange(write_log: bool = True) -> Result:
    bins = [("far", "r4c4", 4.0, "zoneE", "open", 20.0),
            ("near", "r1c1", 0.5, "zoneA", "crowded", 5.0)]
    twin = build_twin(build_graph(), bins=bins, trucks=1)
    r = run("G2", "Red bin takes over an orange stop", ticks=60, twin=twin,
            hooks={2: lambda tw, g: tw.set_weight("near", 47.0)}, write_log=write_log)
    stops = [d["stop"] for d in r.dispatches("t1")]
    colors = [d["color"] for d in r.dispatches("t1")]
    served = [b for _, _, b in twin.served]
    r.check("the truck first heads to the orange bin", stops[:1] == ["far"]
            and colors[:1] == ["orange"])
    r.check("it is then redirected to the red bin", stops[1:2] == ["near"]
            and colors[1:2] == ["red"])
    r.check("the red bin is collected before the orange one",
            served[:2] == ["near", "far"])
    r.check("no bin overflows", overflow_kg(twin) == 0)
    return r


# ---------------------------------------------------------------------------
# G3: a sensor reports a fake overflow. The Guardian says not_trusted.
# ---------------------------------------------------------------------------
def _spoof_run(guardian_protects: bool, write_log: bool, scenario_id: str) -> Result:
    twin = build_twin(build_graph())

    def attack(tw, g):
        tw.reported["b06"] = 49.0                    # reads 98%, really about 20%
        if guardian_protects:
            g.distrust("b06", "jumped from 22% to 98% in 5 min, neighbours unchanged")

    return run(scenario_id, "Fake overflow on b06", ticks=72, twin=twin,
               hooks={20: attack}, write_log=write_log)


def g3_spoofed_reading(write_log: bool = True) -> Result:
    control = _spoof_run(False, write_log=False, scenario_id="G3-control")
    r = _spoof_run(True, write_log=write_log, scenario_id="G3")
    r.check("control run: without the Guardian, the fake reading sends a truck to b06",
            any(d["stop"] == "b06" for d in control.dispatches()))
    r.check("with the Guardian, no truck is ever sent to b06",
            not any(d["stop"] == "b06" for d in r.dispatches()))
    fake = [e for e in r.log.of("sensor_reading")
            if e["sensor_id"] == "b06" and e["fill_pct"] >= 90]
    blocked = [e for e in r.log.of("reading_blocked") if e["sensor_id"] == "b06"]
    r.check("the fake reading is blocked the very first time it appears",
            bool(fake and blocked) and fake[0]["ts"] == blocked[0]["ts"])
    r.check("the blocked reading is logged with the Guardian's reason",
            any(e["sensor_id"] == "b06" and "jumped" in e["reason"]
                for e in r.log.of("reading_blocked")))
    r.check("the rest of the city keeps being served",
            any(b != "b06" for _, _, b in r.twin.served))
    return r


# ---------------------------------------------------------------------------
# G4: trucks too full for the next bin go to the depot and unload.
# ---------------------------------------------------------------------------
def g4_depot(write_log: bool = True) -> Result:
    bins = [("d1", "r1c1", 0.2, "zoneA", "crowded", 46.0),
            ("d2", "r1c2", 0.2, "zoneA", "crowded", 46.0),
            ("d3", "r2c1", 0.2, "zoneB", "crowded", 46.0),
            ("d4", "r2c2", 0.2, "zoneB", "crowded", 46.0),
            ("d5", "r1c3", 0.2, "zoneA", "crowded", 46.0)]
    twin = build_twin(build_graph(), bins=bins, trucks=1, truck_capacity_kg=120.0)
    r = run("G4", "Full truck goes to the depot", ticks=60, twin=twin,
            truck_capacity_kg=120.0, write_log=write_log)
    r.check("the truck went to the depot at least once",
            any(d["kind"] == "depot" for d in r.dispatches()))
    r.check("it unloaded", twin.unloads >= 1)
    r.check("all 5 bins were collected", {b for _, _, b in twin.served} == set(twin.bins))
    r.check("the truck never exceeded its capacity",
            twin.trucks["t1"].max_load_kg <= 120.0)
    r.check("no bin overflows", overflow_kg(twin) == 0)
    return r


# ---------------------------------------------------------------------------
# G5: bad messages, a Guardian outage and a Twin outage. The city keeps running.
# ---------------------------------------------------------------------------
def g5_partners_misbehave(write_log: bool = True) -> Result:
    twin = build_twin(build_graph())

    def bad_messages(tw, g):
        tw.extra_bin_messages = [
            {"sensor_id": "bad-1", "weight_kg": -5, "capacity_kg": 50,
             "location": {"lat": 36.8, "lon": 10.18}, "zone": "z",
             "timestamp": "2026-11-01T06:55:00Z"},
            {"hello": "world"},
            "not even a dict",
        ]

    hooks = {
        10: lambda tw, g: (bad_messages(tw, g), setattr(g, "down", True)),
        14: lambda tw, g: setattr(g, "down", False),
        34: lambda tw, g: setattr(tw, "offline", True),
        38: lambda tw, g: setattr(tw, "offline", False),
    }
    r = run("G5", "Partners misbehave", ticks=144, twin=twin, hooks=hooks,
            write_log=write_log)

    down_at = [e["ts"] for e in r.log.of("guardian_unavailable")]
    up_at = [e["ts"] for e in r.log.of("guardian_recovered")]
    r.check("malformed messages were rejected and logged",
            len(r.log.of("rejected_message")) >= 3)
    r.check("the Guardian outage and its recovery were logged each once",
            len(down_at) == 1 and len(up_at) == 1)
    r.check("no truck was dispatched while the Guardian was down",
            bool(down_at and up_at) and
            not any(down_at[0] <= d["ts"] < up_at[0] for d in r.dispatches()))
    r.check("dispatching resumed after the Guardian came back",
            bool(up_at) and any(d["ts"] >= up_at[0] for d in r.dispatches()))
    lost = r.log.of("twin_unavailable")
    r.check("a dispatch lost to a Twin outage was logged", len(lost) > 0)
    r.check("that stop was dispatched again once the Twin was back",
            len(lost) > 0 and any(d["truck_id"] == lost[0]["truck_id"]
                                  and d["ts"] > lost[-1]["ts"] for d in r.dispatches()))
    r.check("no bin overflows", overflow_kg(twin) == 0)
    return r


# ---------------------------------------------------------------------------
# G6: a real incident and an attack at the same time.
# ---------------------------------------------------------------------------
def g6_real_incident_during_attack(write_log: bool = True) -> Result:
    twin = build_twin(build_graph())

    def both(tw, g):
        tw.reported["b06"] = 49.0                    # fake overflow (really about 20%)
        g.distrust("b06", "jumped from 22% to 98% in 5 min, neighbours unchanged")
        tw.set_weight("b04", 47.0)                   # a REAL bin overflows at the same time

    r = run("G6", "Real incident during an attack", ticks=72, twin=twin,
            hooks={20: both}, write_log=write_log)
    real_red = [e for e in r.log.of("bin_status")
                if e["sensor_id"] == "b04" and e["color"] == "red"]
    to_b04 = [d for d in r.dispatches() if d["stop"] == "b04"]
    r.check("the real overflow turns b04 red", bool(real_red))
    r.check("a truck is sent to b04 in the same cycle",
            bool(real_red and to_b04) and to_b04[0]["ts"] == real_red[0]["ts"])
    r.check("b04 is collected", "b04" in {b for _, _, b in twin.served})
    r.check("no truck is ever sent to the spoofed bin b06",
            not any(d["stop"] == "b06" for d in r.dispatches()))
    r.check("b06's reading is blocked while b04 is served",
            any(e["sensor_id"] == "b06" for e in r.log.of("reading_blocked")))
    r.check("no bin overflows", overflow_kg(twin) == 0)
    return r


SCENARIOS: Dict[str, Callable[..., Result]] = {
    "G1": g1_normal_day,
    "G2": g2_red_takes_over_orange,
    "G3": g3_spoofed_reading,
    "G4": g4_depot,
    "G5": g5_partners_misbehave,
    "G6": g6_real_incident_during_attack,
}


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
        print("  timeline (first 25 lines):")
        for line in render(r.log.events)[:25]:
            print("   ", line)
        print(f"  log: logs/{r.scenario_id}.jsonl")
        failed += 0 if r.passed else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

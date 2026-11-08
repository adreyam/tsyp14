"""
The City Brain as an agent that an orchestrator can call at any time.

The decision core stays what it is: deterministic rules, gated by the Guardian.
This module puts a TOOL interface in front of it, so an orchestrator (a
workflow engine, a multi-agent system, an LLM with tool calling, an MCP host)
can ask it questions and give it work:

  read      what the Brain knows now: status, bins, air zones, trucks, the
            reasons behind any decision, the event stream
  what_if   pure computations on values the caller gives, no effect on the city:
            score these air readings, colour this bin, route between two places
  act       give the Brain work: submit readings (they still go through
            validation and the Guardian), run a decision cycle now, replay a
            Test Book scenario in isolation

Safety rules that no tool can change (they are in the core, not here):
  - nothing reaches an actuator unless the Guardian said "trusted" for that sensor;
  - an air zone with no trusted data keeps its level and actions;
  - every agent call is written to the audit log (event "agent_call").

The same tools are served over HTTP (POST /v1/agent/call, see service.py) and
over MCP (src/agent_mcp.py). Their list with JSON schemas: card().
"""
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .air import (LEVELS, AirConfig, AirReading, SensorHistory, compute, level_of,
                  season_of)
from .bin_status import BinConfig, _span
from .pathfinding import astar

AGENT_NAME = "city-brain"
AGENT_VERSION = "1.0.0"
SCENARIO_IDS = ["G1", "G2", "G3", "G4", "G5", "G6", "A1", "A2", "A3", "A4", "A5"]
DECISION_EVENTS = ("dispatch", "air_action", "air_level", "air_zone_unknown",
                   "air_zone_known", "reading_blocked", "rejected_message",
                   "twin_unavailable", "guardian_unavailable", "guardian_recovered")


class AgentError(Exception):
    """A tool call that cannot be done. `code` is stable, `message` is for people."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Tool:
    name: str
    kind: str                       # read | what_if | act
    description: str
    schema: dict
    handler: Callable[[dict], Any]
    needs_brain: bool = True

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "description": self.description,
                "inputSchema": self.schema,
                "annotations": {"readOnlyHint": self.kind != "act",
                                "requiresLiveBrain": self.needs_brain}}


def _obj(props: dict, required: Optional[List[str]] = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


_NUM = {"type": "number"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}


# ---- argument validation (a small subset of JSON Schema, enough for our tools) -----
def _type_ok(value, kind) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def validate(args, schema: dict, path: str = "") -> Optional[str]:
    if not isinstance(args, dict):
        return f"{path or 'arguments'} must be an object"
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required argument '{path}{key}'"
    for key, value in args.items():
        if key not in props:
            if schema.get("additionalProperties", True) is False:
                return f"unknown argument '{path}{key}' (expected: {', '.join(props) or 'none'})"
            continue
        spec = props[key]
        kinds = spec.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds] if kinds else []
        if kinds and not any(_type_ok(value, k) for k in kinds):
            return f"'{path}{key}' must be {' or '.join(kinds)}"
        if "enum" in spec and value not in spec["enum"]:
            return f"'{path}{key}' must be one of {', '.join(map(str, spec['enum']))}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                return f"'{path}{key}' must be >= {spec['minimum']}"
            if "maximum" in spec and value > spec["maximum"]:
                return f"'{path}{key}' must be <= {spec['maximum']}"
        if isinstance(value, list) and "maxItems" in spec and len(value) > spec["maxItems"]:
            return f"'{path}{key}' has more than {spec['maxItems']} items"
        if isinstance(value, dict) and spec.get("type") == "object" and "properties" in spec:
            problem = validate(value, spec, f"{path}{key}.")
            if problem:
                return problem
    return None


# ---- the agent ------------------------------------------------------------------------
class BrainAgent:
    """Tool interface over a running BrainService (or none: what-if tools and
    scenarios still work, the read and act tools then answer `no_live_brain`)."""

    def __init__(self, service=None):
        self.service = service
        self.tools: Dict[str, Tool] = {t.name: t for t in self._build()}

    # ---- discovery ---------------------------------------------------------
    def card(self) -> dict:
        return {
            "name": AGENT_NAME, "version": AGENT_VERSION,
            "description": ("City Brain of a smart city (TSYP14): decides garbage collection "
                            "and air-quality responses from sensor readings, acting only on "
                            "readings the Guardian layer trusts. Deterministic and explainable: "
                            "every decision carries its reason."),
            "live": self.service is not None,
            "axes": ["garbage_bins", "air_quality"],
            "protocols": {"http": "POST /v1/agent/call {tool, arguments} with "
                                  "Authorization: Bearer <orchestrator token>",
                          "mcp": "python -m src.agent_mcp (stdio)"},
            "safety": [
                "No tool can bypass the Guardian: actions need a 'trusted' verdict.",
                "Submitted readings are validated and checked by the Guardian like the Twin's.",
                "An air zone without trusted data keeps its level and actions.",
                "Every call is recorded in the audit log as 'agent_call'.",
            ],
            "tools": [t.describe() for t in self.tools.values()],
        }

    def list_tools(self) -> List[dict]:
        return [t.describe() for t in self.tools.values()]

    # ---- calling -----------------------------------------------------------
    def call(self, name: str, arguments: Optional[dict] = None, caller: str = "orchestrator") -> dict:
        started = time.monotonic()
        args = {} if arguments is None else arguments
        tool = self.tools.get(name)
        ok, code = False, None
        try:
            if tool is None:
                raise AgentError("unknown_tool",
                                 f"unknown tool '{name}'; tools: {', '.join(self.tools)}")
            problem = validate(args, tool.schema)
            if problem:
                raise AgentError("invalid_arguments", f"{name}: {problem}")
            if tool.needs_brain and self.service is None:
                raise AgentError("no_live_brain",
                                 f"{name} needs a running City Brain; start the service "
                                 f"or use the what-if tools and run_scenario")
            result = tool.handler(args)
            ok = True
            return {"agent": AGENT_NAME, "tool": name, "kind": tool.kind,
                    "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "result": result}
        except AgentError as e:
            code = e.code
            raise
        finally:
            if self.service is not None:
                self.service.log.log(datetime.now(timezone.utc), "city_brain", "agent_call",
                                     tool=name, caller=caller,
                                     kind=tool.kind if tool else None, ok=ok, error=code,
                                     ms=round((time.monotonic() - started) * 1000, 1))

    # ---- tool table --------------------------------------------------------
    def _build(self) -> List[Tool]:
        colors = ["red", "orange", "yellow", "green"]
        return [
            Tool("brain_status", "read",
                 "Overview of the Brain now: city time, decision mode, bins per colour, blocked "
                 "sensors, trucks, air zones per level, partner health, last decisions.",
                 _obj({}), self._status),
            Tool("get_bins", "read",
                 "Bins as the Brain sees them, most urgent first (red, orange, yellow, green; "
                 "fullest first), with fill, rate, projection and the reason for the colour.",
                 _obj({"color": {"type": "string", "enum": colors},
                       "min_fill_pct": {"type": "number", "minimum": 0, "maximum": 100},
                       "limit": _LIMIT}), self._bins),
            Tool("get_air_zones", "read",
                 "Air-quality level of each zone (normal, warning, high, critical, or unknown "
                 "when no trusted sensor is left), its risk score, reason and actions in force.",
                 _obj({"zone": {"type": "string"}}), self._zones),
            Tool("get_trucks", "read",
                 "Trucks: status, load, committed next stop and the stops planned after it.",
                 _obj({}), self._trucks),
            Tool("explain", "read",
                 "Why: the latest decisions about one bin, air sensor, zone or truck, newest "
                 "first, each with the Brain's reason, plus its current state.",
                 _obj({"id": {"type": "string", "description": "e.g. b03, WGT-T001, AIR-05, B, t1"},
                       "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10}},
                      ["id"]), self._explain),
            Tool("get_events", "read",
                 "The audit log as a stream: events after position `after`, optionally only "
                 "some event names (e.g. dispatch, air_action, reading_blocked).",
                 _obj({"after": {"type": "integer", "minimum": 0, "default": 0},
                       "events": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                       "limit": _LIMIT}), self._events),
            Tool("assess_air", "what_if",
                 "Score air readings with the team's formula without touching the city: Ox, "
                 "smoothed values, R_pm, R_ox, risk, level and the actions that level triggers. "
                 "PM2.5 in ug/m3, O3 and NO2 in ppb; *_prev = one hour earlier (default: same).",
                 _obj({"pm25": {"type": "number", "minimum": 0, "maximum": 2000},
                       "o3": {"type": "number", "minimum": 0, "maximum": 2000},
                       "no2": {"type": "number", "minimum": 0, "maximum": 2000},
                       "pm25_prev": {"type": "number", "minimum": 0, "maximum": 2000},
                       "o3_prev": {"type": "number", "minimum": 0, "maximum": 2000},
                       "no2_prev": {"type": "number", "minimum": 0, "maximum": 2000},
                       "season": {"type": "string", "enum": ["warm", "cold"]},
                       "month": {"type": "integer", "minimum": 1, "maximum": 12}},
                      ["pm25", "o3", "no2"]), self._assess_air, needs_brain=False),
            Tool("assess_bin", "what_if",
                 "Colour a hypothetical bin with the Brain's rules: give fill_pct (or weight_kg "
                 "and capacity_kg), and optionally its fill rate and zone type.",
                 _obj({"fill_pct": {"type": "number", "minimum": 0, "maximum": 100},
                       "weight_kg": {"type": "number", "minimum": 0},
                       "capacity_kg": {"type": "number", "minimum": 0.001},
                       "rate_pct_per_h": {"type": "number", "minimum": 0},
                       "zone_type": {"type": "string", "enum": ["crowded", "open"]}}),
                 self._assess_bin, needs_brain=False),
            Tool("plan_route", "what_if",
                 "Fastest route on the city map (A*, travel time) between two map nodes, or "
                 "from/to the nodes nearest to given coordinates.",
                 _obj({"from_node": {"type": "string"}, "to_node": {"type": "string"},
                       "from_lat": _NUM, "from_lon": _NUM, "to_lat": _NUM, "to_lon": _NUM}),
                 self._route),
            Tool("submit_readings", "act",
                 "Give the Brain sensor readings (bin, truck or air messages, same format as the "
                 "Twin's). They are validated and checked by the Guardian at the next cycle; "
                 "they cannot bypass it.",
                 _obj({"messages": {"type": "array", "items": {"type": "object"}, "maxItems": 1000}},
                      ["messages"]), self._submit),
            Tool("run_cycle", "act",
                 "Run one decision cycle now on everything received, and return the decisions "
                 "it produced (dispatches, air levels and actions, blocked readings).",
                 _obj({}), self._run_cycle),
            Tool("run_scenario", "act",
                 "Replay one Test Book scenario (G1-G6 garbage, A1-A5 air) in isolation, with "
                 "its own mocks: no effect on the live city. Returns its checks and verdict.",
                 _obj({"id": {"type": "string", "enum": SCENARIO_IDS}}, ["id"]),
                 self._scenario, needs_brain=False),
        ]

    # ---- read ----------------------------------------------------------------
    def _lock(self):
        return self.service._step_lock

    def _bin_rows(self) -> List[dict]:
        b = self.service.brain
        rank = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
        rows = [{"id": sid, "color": s.color.value, "fill_pct": s.fill_pct,
                 "weight_kg": s.weight_kg, "rate_pct_per_h": s.rate_pct_per_h,
                 "projected_pct": s.projected_pct, "reason": s.reason,
                 "blocked": b.untrusted.get(sid), "as_of": s.timestamp.isoformat()}
                for sid, s in b.status.items()]
        rows.sort(key=lambda r: (rank[r["color"]], -r["fill_pct"], r["id"]))
        return rows

    def _status(self, args: dict) -> dict:
        svc, b = self.service, self.service.brain
        with self._lock():
            rows = self._bin_rows()
            per_color = {c: sum(1 for r in rows if r["color"] == c)
                         for c in ("red", "orange", "yellow", "green")}
            zones = svc.air.snapshot() if svc.air else {}
            per_level = {l: sorted(z for z, v in zones.items() if v["level"] == l)
                         for l in LEVELS + ["unknown"]}
            blocked = {**b.untrusted, **(svc.air.untrusted if svc.air else {})}
            recent = [e for e in svc.log.events[-400:] if e["event"] in DECISION_EVENTS][-8:]
            return {"city_time": svc.state.get("city_time"),
                    "decision_mode": svc.settings.city.decision,
                    "bins": {"total": len(rows), "by_color": per_color,
                             "need_collection": [r["id"] for r in rows
                                                 if r["color"] in ("red", "orange")]},
                    "trucks": svc.state.get("trucks", {}),
                    "air": {"enabled": svc.air is not None, "zones_by_level": per_level},
                    "blocked_sensors": blocked,
                    "partners": {"guardian": "down" if b._guardian_down else "ok",
                                 "twin": "down" if getattr(svc, "_twin_down", False) else "ok",
                                 "twin_transport": svc.settings.twin.transport},
                    "recent_decisions": recent,
                    "log_events": len(svc.log.events)}

    def _bins(self, args: dict) -> dict:
        with self._lock():
            rows = self._bin_rows()
        if "color" in args:
            rows = [r for r in rows if r["color"] == args["color"]]
        if "min_fill_pct" in args:
            rows = [r for r in rows if r["fill_pct"] >= args["min_fill_pct"]]
        return {"count": len(rows), "bins": rows[: args.get("limit", 50)]}

    def _zones(self, args: dict) -> dict:
        svc = self.service
        if svc.air is None:
            raise AgentError("axis_disabled", "the air-quality axis is disabled in the config")
        with self._lock():
            zones = svc.air.snapshot()
        if "zone" in args:
            if args["zone"] not in zones:
                raise AgentError("not_found", f"no air zone '{args['zone']}' yet; "
                                              f"known: {', '.join(zones) or 'none'}")
            zones = {args["zone"]: zones[args["zone"]]}
        return {"zones": zones,
                "levels": {"normal": "< 70", "warning": "70-99", "high": "100-130",
                           "critical": "> 130"}}

    def _trucks(self, args: dict) -> dict:
        b = self.service.brain
        with self._lock():
            out = {}
            for tid, t in sorted(b.trucks.items()):
                plan = b.planner.plans.get(tid)
                out[tid] = {"status": t.status.value, "load_kg": t.current_load_kg,
                            "next_stop": plan.committed.bin_id if plan and plan.committed else None,
                            "then": [s.bin_id for s in plan.provisional] if plan else []}
        return {"decision_mode": self.service.settings.city.decision, "trucks": out}

    def _explain(self, args: dict) -> dict:
        target, limit = args["id"], args.get("limit", 10)
        keys = ("sensor_id", "zone", "truck_id", "stop", "target", "id")
        hits = [e for e in reversed(self.service.log.events)
                if e["event"] not in ("agent_call",) and any(e.get(k) == target for k in keys)]
        # per-cycle telemetry (readings, risk recomputed each cycle) is not a decision;
        # the current risk is in `current`
        decisions = [e for e in hits if e["event"] not in ("sensor_reading", "air_reading",
                                                           "truck_state", "air_zone_risk",
                                                           "trust_verdict")]
        current = None
        with self._lock():
            b, a = self.service.brain, self.service.air
            if target in b.status:
                current = next(r for r in self._bin_rows() if r["id"] == target)
            elif a is not None and target in a.zones:
                current = a.snapshot()[target]
            elif target in b.trucks:
                current = self._trucks({})["trucks"].get(target)
        if not hits and current is None:
            raise AgentError("not_found", f"nothing known about '{target}'")
        return {"id": target, "current": current,
                "decisions": decisions[:limit],
                "last_readings": [e for e in hits if e["event"] in ("sensor_reading",
                                                                    "air_reading")][:3]}

    def _events(self, args: dict) -> dict:
        evts = self.service.log.since(args.get("after", 0))
        if args.get("events"):
            wanted = set(args["events"])
            evts = [e for e in evts if e["event"] in wanted]
        limit = args.get("limit", 50)
        page = evts[:limit]
        nxt = page[-1]["seq"] + 1 if page else args.get("after", 0)
        return {"events": page, "next_after": nxt, "more": len(evts) > limit}

    # ---- what-if ---------------------------------------------------------------
    def _air_config(self) -> AirConfig:
        if self.service is not None and self.service.air is not None:
            return self.service.air.cfg
        return AirConfig()

    def _assess_air(self, args: dict) -> dict:
        cfg = self._air_config()
        month = args.get("month")
        if "season" in args:
            month = cfg.warm_months[0] if args["season"] == "warm" else \
                next(m for m in range(1, 13) if m not in cfg.warm_months)
        if month is None:
            month = datetime.now(timezone.utc).month
        now = datetime(2026, month, 15, 12, tzinfo=timezone.utc)
        history = SensorHistory()
        prev = (args.get("pm25_prev"), args.get("o3_prev"), args.get("no2_prev"))
        if any(v is not None for v in prev):
            p = AirReading(sensor_id="what-if", pm25=args.get("pm25_prev", args["pm25"]),
                           o3=args.get("o3_prev", args["o3"]), no2=args.get("no2_prev", args["no2"]),
                           zone="what-if", timestamp=now.replace(hour=11))
            compute(p, history, cfg)
        r = compute(AirReading(sensor_id="what-if", pm25=args["pm25"], o3=args["o3"],
                               no2=args["no2"], zone="what-if", timestamp=now), history, cfg)
        level = level_of(r.risk, cfg)
        actions = sorted(_air_actions(level, cfg))
        return {"ox_ppb": round(r.ox, 2), "z_pm": round(r.z_pm, 2), "z_ox": round(r.z_ox, 2),
                "r_pm": round(r.r_pm, 1), "r_ox": round(r.r_ox, 1), "risk": round(r.risk, 1),
                "level": level, "driver": r.driver, "season": season_of(now, cfg),
                "thresholds": cfg.thresholds[season_of(now, cfg)],
                "actions_at_this_level": actions, "explanation": r.explain(cfg)}

    def _assess_bin(self, args: dict) -> dict:
        cfg = BinConfig()
        if self.service is not None:
            cfg = self.service.brain.tracker.config
        if "fill_pct" in args:
            fill = float(args["fill_pct"])
        elif "weight_kg" in args and "capacity_kg" in args:
            fill = min(100.0, args["weight_kg"] / args["capacity_kg"] * 100.0)
        else:
            raise AgentError("invalid_arguments",
                             "assess_bin: give fill_pct, or weight_kg and capacity_kg")
        horizon = cfg.horizon_for(args.get("zone_type"))
        rate = args.get("rate_pct_per_h")
        projected = None if rate is None else min(100.0, fill + rate * horizon / 60.0)
        if fill >= cfg.red_pct:
            color, reason = "red", f"fill {fill:.0f}% >= {cfg.red_pct:.0f}%"
        elif projected is not None and projected >= cfg.red_pct:
            color, reason = "orange", (f"fill {fill:.0f}% at +{rate:.1f}%/h reaches "
                                       f"{projected:.0f}% within {_span(horizon)}")
        elif fill < cfg.green_pct:
            color, reason = "green", f"fill {fill:.0f}% < {cfg.green_pct:.0f}%"
        elif projected is None:
            color, reason = "yellow", f"fill {fill:.0f}%, rate unknown"
        else:
            color, reason = "yellow", (f"fill {fill:.0f}% at +{rate:.1f}%/h only reaches "
                                       f"{projected:.0f}% within {_span(horizon)}")
        return {"color": color, "fill_pct": round(fill, 1),
                "projected_pct": None if projected is None else round(projected, 1),
                "horizon_minutes": horizon, "reason": reason,
                "collect_now": color in ("red", "orange")}

    def _route(self, args: dict) -> dict:
        g = self.service.brain.planner.graph
        if not g.nodes:
            raise AgentError("no_map", "no city map loaded (crew mode does not need one)")

        def node(prefix):
            if f"{prefix}_node" in args:
                return args[f"{prefix}_node"]
            if f"{prefix}_lat" in args and f"{prefix}_lon" in args:
                return g.nearest_node(args[f"{prefix}_lat"], args[f"{prefix}_lon"]).id
            raise AgentError("invalid_arguments",
                             f"plan_route: give {prefix}_node, or {prefix}_lat and {prefix}_lon")

        a, b = node("from"), node("to")
        try:
            res = astar(g, a, b)
        except ValueError as e:
            raise AgentError("not_found", str(e))
        if res is None:
            raise AgentError("unreachable", f"no road from {a} to {b}")
        return {"from": a, "to": b, "nodes": res.nodes, "travel_time_s": round(res.cost_s, 1),
                "expanded": res.expanded}

    # ---- act ---------------------------------------------------------------------
    def _submit(self, args: dict) -> dict:
        n = self.service.receive(args["messages"])
        return {"queued": n, "note": "validated and checked by the Guardian at the next cycle; "
                                     "call run_cycle to process them now"}

    def _run_cycle(self, args: dict) -> dict:
        svc = self.service
        before = len(svc.log.events)
        processed = svc.run_cycle()
        new = svc.log.since(before)
        decisions = [e for e in new if e["event"] in DECISION_EVENTS]
        changes = [e for e in new if e["event"] == "bin_status"]
        return {"messages_processed": processed, "decisions": decisions,
                "bin_colour_changes": changes, "city_time": svc.state.get("city_time")}

    def _scenario(self, args: dict) -> dict:
        sid = args["id"]
        if sid.startswith("G"):
            from demo.garbage import SCENARIOS
        else:
            from demo.air import SCENARIOS
        result = SCENARIOS[sid](write_log=False)
        return {"id": sid, "name": result.name, "passed": result.passed,
                "checks": [{"check": t, "passed": ok} for t, ok in result.checks],
                "events": len(result.log.events), "isolated": True}


def _air_actions(level: str, cfg: AirConfig) -> set:
    from .air import RANK
    out = set()
    if RANK[level] >= RANK[cfg.sensitive_alert_from]:
        out.add("sensitive_alert")
    if RANK[level] >= RANK[cfg.public_alert_from]:
        out.add("public_alert")
    if RANK[level] >= RANK[cfg.close_parking_from]:
        out.add("close_parking")
    return out

"""The tools of the City Brain transportation agent.

Every tool is a plain Python function with type hints and a docstring. The registry turns each one into a JSON schema, so the
same definition serves REST, MCP and the Python client. Tools talk to the running Brain over HTTP (BRAIN_URL), so the agent
can live in the Brain's process or in a separate process on another machine.

Every tool returns the same envelope:  {"ok": bool, "tool": str, "summary": str, "data": ..., "error": {"code","message"} | None}
`summary` is one or two sentences meant to be read by the orchestrator's language model; `data` is the structured result.
"""
import inspect
import os
import time
from typing import Any, Callable, Dict, List, Literal, Optional

import requests
from pydantic import ValidationError, create_model

BRAIN_URL = os.environ.get("BRAIN_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
ADMIN_KEY = os.environ.get("PARKING_ADMIN_KEY", "dev-admin-key")
TIMEOUT = float(os.environ.get("AGENT_HTTP_TIMEOUT", "30"))


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def allow_write() -> bool:
    return os.environ.get("AGENT_ALLOW_WRITE", "1") != "0"


def _http(method: str, path: str, json_body=None, params=None, admin=False):
    headers = {"X-Admin-Key": ADMIN_KEY} if admin else {}
    try:
        r = requests.request(method, BRAIN_URL.rstrip("/") + path, json=json_body, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ToolError("brain_unreachable", f"The Brain does not answer at {BRAIN_URL} ({e.__class__.__name__}). Is it running?")
    if r.status_code >= 400:
        try:
            detail = r.json()
            detail = detail.get("detail") or detail.get("error") or detail
        except ValueError:
            detail = r.text[:200]
        code = {503: "starting", 409: "conflict", 404: "not_found", 422: "invalid_input", 401: "unauthorized"}.get(r.status_code, "brain_error")
        raise ToolError(code, f"{detail}" if not isinstance(detail, dict) else str(detail))
    try:
        return r.json()
    except ValueError:
        return {}


# --------------------------------------------------------------------------- registry
REGISTRY: Dict[str, dict] = {}


def tool(read_only: bool, title: str):
    def deco(fn: Callable):
        sig = inspect.signature(fn)
        fields = {n: (p.annotation, ... if p.default is inspect.Parameter.empty else p.default) for n, p in sig.parameters.items()}
        model = create_model(fn.__name__ + "_args", **fields)
        schema = model.model_json_schema()
        schema.pop("title", None)
        for p in schema.get("properties", {}).values():
            p.pop("title", None)
        REGISTRY[fn.__name__] = {"name": fn.__name__, "title": title, "description": inspect.getdoc(fn), "read_only": read_only,
                                 "input_schema": schema, "model": model, "fn": fn}
        return fn
    return deco


def manifest() -> List[dict]:
    return [{k: v for k, v in t.items() if k not in ("model", "fn")} for t in REGISTRY.values()]


def invoke(name: str, arguments: Optional[dict] = None) -> dict:
    """Run one tool. Never raises: errors come back in the envelope."""
    t0 = time.time()
    t = REGISTRY.get(name)
    if t is None:
        return _err(name, "unknown_tool", f"No tool named '{name}'. Available: {', '.join(REGISTRY)}")
    try:
        args = t["model"](**(arguments or {})).model_dump()
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        return _err(name, "invalid_arguments", msg)
    if not t["read_only"] and not allow_write():
        return _err(name, "write_disabled", "This agent runs in read-only mode (AGENT_ALLOW_WRITE=0); this tool changes the city.")
    try:
        summary, data = t["fn"](**args)
        return {"ok": True, "tool": name, "summary": summary, "data": data, "error": None, "ms": round((time.time() - t0) * 1000)}
    except ToolError as e:
        return _err(name, e.code, e.message)
    except Exception as e:   # noqa: BLE001
        return _err(name, "internal_error", f"{e.__class__.__name__}: {e}")


def _err(name, code, message):
    return {"ok": False, "tool": name, "summary": f"{name} failed: {message}", "data": None, "error": {"code": code, "message": message}}


# --------------------------------------------------------------------------- read tools
@tool(True, "City status")
def get_city_status():
    """Live overview of the simulated city: clock, cars on the roads, cars standing still, mean waiting time and speed, emergency vehicles, current traffic level and simulation speed, whether signals are learning, and the state of any traffic-jam test. Call this first to see how the city is doing."""
    s = _http("GET", "/api/state")
    if not s.get("ready"):
        raise ToolError("starting", "The simulation is still starting up, try again in a few seconds.")
    c = s["counts"]
    data = {"clock": s["clock"], "sim_seconds": s["t"], "counts": c, "controls": s["controls"], "learning": s["learning"], "jam": s.get("jam"),
            "signals": len(s["signals"]), "preempted_signals": sum(1 for v in s["signals"].values() if v[1])}
    return (f"{s['clock']}: {c['background'] + c['driver_cars']} cars driving, {c['stopped']} standing still, mean wait {c['mean_wait']} s, "
            f"signals {s['controls']['mode']} mode, traffic x{s['controls']['traffic_level']}."), data


@tool(True, "Signal learning status")
def get_signal_status():
    """State of the traffic-signal learning: mode (train = still learning, policy = using what it learned), episode count, exploration rate, number of situations learned, emergency-priority statistics, and the latest fixed-time-vs-learned benchmark results."""
    t = _http("GET", "/api/training")
    data = {k: t.get(k) for k in ("mode", "episode", "epsilon", "q_states", "window_progress", "warm", "preemptions", "emergency_served",
                                  "emergency_wait_avg", "benchmark_running", "last_saved_at")}
    data["latest_benchmarks"] = (t.get("benchmarks") or [])[-3:]
    b = data["latest_benchmarks"][-1] if data["latest_benchmarks"] else None
    tail = ""
    if b and b["fixed"].get("mean_wait") and b["learned"].get("mean_wait"):
        pct = (b["learned"]["mean_wait"] - b["fixed"]["mean_wait"]) / b["fixed"]["mean_wait"] * 100
        tail = f" Last benchmark: learned lights waited {pct:+.0f}% vs fixed-time ({b['fixed']['mean_wait']} -> {b['learned']['mean_wait']} s)."
    return f"Signals in {data['mode']} mode, episode {data['episode']}, {data['q_states']} situations learned.{tail}", data


@tool(True, "Benchmark results")
def get_benchmark_results(last: int = 5):
    """Results of past benchmarks (fixed-time signals vs the learned policy on identical traffic), newest last. Includes mean waiting time, mean/peak number of standing cars and emergency waiting time."""
    t = _http("GET", "/api/training")
    rows = (t.get("benchmarks") or [])[-max(1, min(10, last)):]
    return f"{len(rows)} benchmark result(s); a benchmark is {'running now' if t.get('benchmark_running') else 'not running'}.", {"benchmarks": rows, "running": t.get("benchmark_running")}


@tool(True, "Traffic jam status")
def get_traffic_jam_status():
    """Progress and result of the latest traffic-jam test: state (loading / clearing / done), cars released, standing cars before / at the worst moment, worst mean wait, and seconds until the city moved again."""
    j = _http("GET", "/api/state").get("jam")
    if not j:
        return "No traffic-jam test has been run since the Brain started.", None
    txt = f"Jam test {j['state']}: peak {j['peak_stopped']} cars standing (baseline {j['baseline_stopped']})"
    if j.get("recovered_after") is not None:
        txt += f", city moving again after {j['recovered_after']} s"
    return txt + ".", j


@tool(True, "Parking lots")
def list_parking_lots():
    """All parking lots with free spaces, capacity and open alerts."""
    out = _http("GET", "/api/admin/lots", admin=True)
    lots = out.get("lots", out)
    return f"{len(lots)} parking lots.", lots


@tool(True, "Recommend parking")
def recommend_parking(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, top_n: int = 3,
                      exclude: List[str] = [], max_walk_m: float = 2000.0):
    """Rank parking lots for a driver (best first) from where the driver is and where they want to end up. Uses live free spaces, distance, walking distance and a forecast; `exclude` skips lots (e.g. one that turned out full). Does not hold a space: call reserve_parking for that."""
    r = _http("POST", "/api/city/recommend", {"origin": {"lat": origin_lat, "lon": origin_lon}, "destination": {"lat": dest_lat, "lon": dest_lon},
                                              "top_n": top_n, "exclude": exclude, "max_walk_m": max_walk_m})
    recs = r.get("ranking") or []
    if not recs:
        return "No lot matches (all excluded, too far, or full).", r
    t = recs[0]
    return (f"Best lot: {t['lot_id']} ({t['name']}): {t['free_now']:.0f} free now, {t['drive_min']} min drive, {t['walk_m']:.0f} m walk to the destination. "
            f"{len(recs)} lot(s) ranked."), r


@tool(True, "Timeline")
def get_timeline(after: int = 0, limit: int = 50, layer: Optional[Literal["twin", "guardian", "brain", "actuator", "test"]] = None):
    """Cross-layer event log (Twin readings, Guardian verdicts, Brain decisions, commands sent to devices). Pass the `last_id` from a previous call as `after` to get only newer events."""
    p = {"after": after, "limit": max(1, min(500, limit))}
    if layer:
        p["layer"] = layer
    r = _http("GET", "/api/v1/timeline", params=p)
    return f"{len(r['items'])} event(s), last id {r['last_id']}.", r


@tool(True, "Interface health")
def get_link_health():
    """Whether the Brain is up and linked to the Twin and the Guardian, plus device trust states (which devices the Guardian marked suspect or quarantined)."""
    h = _http("GET", "/api/v1/health")
    st = _http("GET", "/api/v1/state")
    return f"Brain {h['status']}; twin link {'on' if h['twin_link'] else 'off'}, guardian link {'on' if h['guardian_link'] else 'off'}.", {"health": h, "state": st}


@tool(True, "Test scenarios")
def list_scenarios():
    """The scenarios of the Test Book (T1..T8) with the result of their last run."""
    r = _http("GET", "/api/v1/scenarios")
    return f"{len(r['scenarios'])} scenarios; running now: {r['running'] or 'none'}.", r


# --------------------------------------------------------------------------- tools that change the city
@tool(False, "Set signal mode")
def set_signal_mode(mode: Literal["train", "policy"]):
    """Switch the traffic signals between 'train' (keep learning, explores random actions) and 'policy' (use what was learned, no exploring). Use 'policy' to evaluate or run the city; 'train' to keep improving."""
    _http("POST", "/api/training/mode", {"mode": mode})
    return f"Signals now in {mode} mode.", {"mode": mode}


@tool(False, "Save training")
def save_training():
    """Save the learned signal policy to disk now (it is also saved automatically)."""
    r = _http("POST", "/api/training/save")
    return f"Training saved at episode {r.get('episode')}.", r


@tool(False, "Run benchmark")
def run_signal_benchmark(scenarios: int = 2, seconds: int = 900, jam_cars: int = 0):
    """Start a headless benchmark in a separate process: fixed-time signals vs the learned policy on identical traffic (optionally with a traffic jam of `jam_cars` extra cars). Takes a few minutes; poll get_benchmark_results until a new row appears. The live city is not disturbed."""
    q = {"scenarios": max(1, min(5, scenarios)), "seconds": max(300, min(3600, seconds))}
    if jam_cars:
        q["jam"] = max(50, min(1500, jam_cars))
    _http("POST", "/api/training/benchmark", params=q)
    return "Benchmark started; it runs in the background and appends a result row when finished.", q


@tool(False, "Start traffic jam")
def start_traffic_jam(cars: int = 300, seconds: int = 60):
    """Test the signals under stress: push `cars` extra cars into the city within `seconds` simulated seconds on a few long crossing routes. Poll get_traffic_jam_status for the worst moment and the recovery time."""
    r = _http("POST", "/api/sim/jam", {"size": cars, "seconds": seconds})
    return f"Traffic jam started: {cars} extra cars in {seconds} s (signals in {r['jam']['mode']} mode).", r["jam"]


@tool(False, "Set simulation controls")
def set_simulation_controls(traffic_level: Optional[float] = None, speed: Optional[float] = None, paused: Optional[bool] = None,
                            clock_day: Optional[int] = None, clock_time: Optional[str] = None, ambulances: Optional[int] = None,
                            police: Optional[int] = None, fire_trucks: Optional[int] = None):
    """Change the simulation: traffic_level (0 to 2, 1 = normal), speed (simulation seconds per real second, 0-200), paused, the clock (clock_day 0=Monday..6, clock_time 'HH:MM'; rush hours are about 08:00 and 17:30), and how many ambulances / police / fire trucks patrol (0-10). Only the fields you pass are changed."""
    body = {"traffic_level": traffic_level, "speed": speed, "paused": paused, "day": clock_day, "time": clock_time,
            "ambulance": ambulances, "police": police, "fire": fire_trucks}
    body = {k: v for k, v in body.items() if v is not None}
    if not body:
        raise ToolError("invalid_arguments", "Pass at least one field to change.")
    _http("POST", "/api/sim/controls", body)
    return "Applied: " + ", ".join(f"{k}={v}" for k, v in body.items()) + ".", body


@tool(False, "Dispatch emergency vehicle")
def dispatch_emergency(kind: Literal["ambulance", "fire", "police"], lat: float, lon: float, call_id: str = ""):
    """Send an emergency vehicle from the city border to the street nearest (lat, lon). Signals along its route turn green for it automatically (emergency priority) and the Twin is told. Returns the vehicle id."""
    cid = call_id or f"AGT-{int(time.time()) % 100000}"
    r = _http("POST", "/api/agent/dispatch-emergency", {"kind": kind, "lat": lat, "lon": lon, "call_id": cid}, admin=True)
    return f"{kind} {r['vehicle_id']} dispatched for call {cid}.", r


@tool(False, "Reserve parking")
def reserve_parking(lot_id: str, eta_min: float):
    """Hold a place in a lot for a driver arriving in `eta_min` minutes (counts against the lot's free spaces until arrival plus a grace period). Fails if the lot is full."""
    r = _http("POST", "/api/city/reserve", {"lot_id": lot_id, "eta_min": eta_min})
    return f"Reserved a place in {lot_id} (id {r['reservation_id']}, valid until {r['valid_until']}).", r


@tool(False, "Cancel reservation")
def cancel_parking_reservation(reservation_id: int):
    """Cancel a parking reservation made with reserve_parking."""
    r = _http("POST", f"/api/city/reservations/{reservation_id}/cancel")
    return f"Reservation {reservation_id} is {r['status']}.", r


@tool(False, "Run a test scenario")
def run_scenario(scenario_id: Literal["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]):
    """Start a Test Book scenario (it drives the mock Twin and Guardian, checks the Brain and writes a log). Returns at once; poll list_scenarios for the PASS/FAIL result."""
    r = _http("POST", f"/api/v1/scenarios/{scenario_id}/run", admin=True)
    return f"Scenario {scenario_id} started.", r

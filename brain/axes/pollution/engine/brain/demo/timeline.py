"""
Readable timeline of a run: reading -> Guardian verdict -> Brain decision -> action.
A first version of the R3 screen; the same events can feed a live web page.
"""
import json
from typing import List

LAYER = {"twin": "TWIN    ", "guardian": "GUARDIAN", "city_brain": "BRAIN   "}


def load(path: str) -> List[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def render(events: List[dict]) -> List[str]:
    lines: List[str] = []
    last_reading = {}
    shown_verdict: dict = {}       # sensor -> reason of the verdict already shown
    shown_block: dict = {}         # sensor -> reason of the block already shown
    for e in events:
        ev, hhmm = e["event"], e["ts"][11:16]
        who = LAYER.get(e["layer"], e["layer"])
        if ev == "sensor_reading":
            last_reading[e["sensor_id"]] = e
            continue
        text = None
        sid = e.get("sensor_id")
        if ev == "trust_verdict":
            if e["status"] != "trusted":
                if shown_verdict.get(sid) == e["reason"]:
                    continue                         # same verdict as last cycle
                shown_verdict[sid] = e["reason"]
                r = last_reading.get(sid)
                if r:
                    lines.append(f"{hhmm}  {LAYER['twin']}  {sid} reports "
                                 f"{r['fill_pct']}% ({r['weight_kg']:.1f} kg)")
                text = f"{sid} is {e['status']}: {e['reason']}"
            elif sid in shown_verdict or sid in shown_block:
                shown_verdict.pop(sid, None)
                shown_block.pop(sid, None)
                text = f"{sid} is trusted again"
        elif ev == "reading_blocked":
            if shown_block.get(sid) == e["reason"]:
                continue
            shown_block[sid] = e["reason"]
            text = f"{sid} ignored ({e['reason']})"
        elif ev == "bin_status":
            tag = "emptied" if e["emptied"] else e["color"]
            text = f"{e['sensor_id']} -> {tag}: {e['reason']}"
        elif ev == "dispatch":
            text = (f"{e['truck_id']} sent to {e['stop']} ({e['kind']}"
                    f"{', ' + e['color'] if e['color'] else ''})")
            if e["next_after"]:
                text += f", then {', '.join(e['next_after'])}"
        elif ev == "truck_state":
            text = f"{e['truck_id']} is {e['status']}"
        elif ev in ("rejected_message", "guardian_unavailable", "twin_unavailable",
                    "sensor_silent", "invalid_verdict", "guardian_recovered"):
            detail = e.get("error") or e.get("sensor_id") or ""
            text = f"{ev}: {detail}" if detail else ev
        if text:
            lines.append(f"{hhmm}  {who}  {text}")
    return lines

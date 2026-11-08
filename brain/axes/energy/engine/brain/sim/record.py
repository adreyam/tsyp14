"""Record every Test Book scenario into sim/recordings.json for replay mode.

  python -m sim.record
"""
from __future__ import annotations

import json
from pathlib import Path

from scenarios.testbook import TESTBOOK

from .session import Session

OUT = Path(__file__).resolve().parent / "recordings.json"


def record(sid: str) -> dict:
    s = Session(TESTBOOK[sid])
    frames, cities = [], {}
    while not s.finished:
        lvl_before = s.brain.knowledge.level
        f = s.step()
        if f["cycle"] == 0 or f["level"] != lvl_before:
            cities[f["cycle"]] = s.city()
        frames.append(f)
    s.close()
    return {"city": s.city() if not cities else cities[min(cities)], "cities": cities,
            "frames": frames, "result": s.result}


def main() -> None:
    data = {}
    for sid in TESTBOOK:
        rec = record(sid)
        data[sid] = rec
        print(f"{sid:4} {len(rec['frames']):4} frames  {'PASS' if rec['result']['passed'] else 'FAIL'}")
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

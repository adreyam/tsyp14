"""
One command for the live demo with mocks: mock Twin + mock Guardian + City Brain,
talking over real HTTP with tokens, and the live timeline screen.

    python -m demo.live            (from brain/)
    python scenarios/live_demo.py  (from the triplet root)

Then open  http://127.0.0.1:8100/timeline  (timeline, R3)
and        http://127.0.0.1:8200/control   (mock Twin controls: attack, outage...)

If BRAIN_TOKEN / TWIN_TOKEN are not set, random tokens are made for this run,
so no secret is ever written in the repository. The log goes to logs/LIVE.jsonl.
"""
import os
import secrets
import sys
import threading
import time
import webbrowser

from mocks import serve as mocks
from src.config import ConfigError, load
from src.service import BrainService


def main(argv=None) -> int:
    for name in ("BRAIN_TOKEN", "TWIN_TOKEN", "ORCHESTRATOR_TOKEN"):
        if len(os.environ.get(name, "")) < 16:
            os.environ[name] = secrets.token_urlsafe(24)
    try:
        settings = load(os.environ.get("BRAIN_CONFIG"))
    except ConfigError as e:
        print(f"[live] {e}", file=sys.stderr)
        return 2
    cfg = mocks.load_config(os.environ.get("MOCKS_CONFIG"))
    city = mocks.build_from_config(cfg)
    tw, gu = cfg["twin"], cfg["guardian"]
    # The Twin's clock starts once the Brain listens, so no reading is lost at startup.
    mocks.start(city, (tw["host"], tw["port"]), (gu["host"], gu["port"]), run_clock=False)
    try:
        service = BrainService(settings)
    except ConfigError as e:
        print(f"[live] {e}", file=sys.stderr)
        return 2
    service.serve(block=False)
    threading.Thread(target=city.loop, daemon=True, name="mock-clock").start()
    timeline = f"http://127.0.0.1:{settings.listen.port}/timeline"
    control = f"http://127.0.0.1:{tw['port']}/control"
    print(f"[live] timeline : {timeline}")
    print(f"[live] controls : {control}")
    print(f"[live] log      : {settings.log_path}")
    print(f"[live] agent    : POST http://127.0.0.1:{settings.listen.port}/v1/agent/call  "
          f"(Bearer {os.environ['ORCHESTRATOR_TOKEN']})")
    print("[live] Ctrl+C to stop")
    if "--no-browser" not in (argv or sys.argv[1:]):
        webbrowser.open(timeline)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        city.stop()
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

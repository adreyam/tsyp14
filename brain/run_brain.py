"""
Start the City Brain.

    python brain/run_brain.py                         source from brain/config/brain.yaml (mock by default)
    python brain/run_brain.py --source live           override for this run (same as CITY_BRAIN_SOURCE=live)
    python brain/run_brain.py --config other.yaml

Then: http://127.0.0.1:8500/v1/status (the control center shows it on http://127.0.0.1:8765).
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "src"), HERE]

from citybrain.brain import Brain  # noqa: E402
from citybrain.config import ConfigError, load_config  # noqa: E402
from citybrain.server import serve  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="City Brain (TSYP14)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--source", choices=["live", "mock"], default=None)
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config, {"source": a.source} if a.source else None)
    except ConfigError as e:
        print(f"City Brain not started: {e}", file=sys.stderr)
        return 2
    if a.port:
        cfg.brain.port = a.port
    serve(Brain(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())

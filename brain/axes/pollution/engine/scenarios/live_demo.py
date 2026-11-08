"""Live demo with mocks over HTTP: City Brain + mock Twin + mock Guardian + timeline screen.
Trigger:  python scenarios/live_demo.py
Screens:  http://127.0.0.1:8100/timeline   and   http://127.0.0.1:8200/control
Log:      logs/LIVE.jsonl
"""
import sys

import _bootstrap  # noqa: F401  (makes brain/ importable)
from demo.live import main

sys.exit(main())

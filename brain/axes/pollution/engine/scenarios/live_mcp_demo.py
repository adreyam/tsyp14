"""Rehearsal of the City Ground Zero integration (Twin's real MCP server, fake Godot city).
Trigger:  python scenarios/live_mcp_demo.py
Screen:   http://127.0.0.1:8100/timeline
Log:      logs/LIVE-MCP.jsonl
"""
import sys

import _bootstrap  # noqa: F401  (makes brain/ importable)
from demo.live_mcp import main

sys.exit(main())

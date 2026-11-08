"""A1 - Rush-hour peak downtown: alert and car park closed, then lifted without flapping (see TESTBOOK.md).
Trigger:  python scenarios/a1_rush_hour_peak.py
Log:      logs/A1.jsonl
"""
import sys

from _bootstrap import ROOT  # noqa: F401  (makes brain/ importable)
from demo.air import main

sys.exit(main(["air", "A1"]))

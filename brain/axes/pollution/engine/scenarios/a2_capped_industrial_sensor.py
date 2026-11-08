"""A2 - Industrial sensor caps its values: no false all-clear (see TESTBOOK.md).
Trigger:  python scenarios/a2_capped_industrial_sensor.py
Log:      logs/A2.jsonl
"""
import sys

from _bootstrap import ROOT  # noqa: F401  (makes brain/ importable)
from demo.air import main

sys.exit(main(["air", "A2"]))

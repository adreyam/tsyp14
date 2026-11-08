"""A4 - Guardian down during the morning peak (see TESTBOOK.md).
Trigger:  python scenarios/a4_guardian_down.py
Log:      logs/A4.jsonl
"""
import sys

from _bootstrap import ROOT  # noqa: F401  (makes brain/ importable)
from demo.air import main

sys.exit(main(["air", "A4"]))

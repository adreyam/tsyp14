"""A5 - Real peak downtown during a fake peak elsewhere (see TESTBOOK.md).
Trigger:  python scenarios/a5_real_peak_during_attack.py
Log:      logs/A5.jsonl
"""
import sys

from _bootstrap import ROOT  # noqa: F401  (makes brain/ importable)
from demo.air import main

sys.exit(main(["air", "A5"]))

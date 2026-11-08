"""A3 - Fake PM2.5 spike near the schools: no unjustified restriction (see TESTBOOK.md).
Trigger:  python scenarios/a3_fake_spike.py
Log:      logs/A3.jsonl
"""
import sys

from _bootstrap import ROOT  # noqa: F401  (makes brain/ importable)
from demo.air import main

sys.exit(main(["air", "A3"]))

"""TR02 - Crash at a junction. One trigger: python scenarios/tr02_crash_at_a_junction.py [--server]  (log in logs/TR02-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["TR02"] + sys.argv[1:]))

"""TR03 - Signal failure at a junction. One trigger: python scenarios/tr03_signal_failure_at_a_junction.py [--server]  (log in logs/TR03-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["TR03"] + sys.argv[1:]))

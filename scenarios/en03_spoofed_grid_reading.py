"""EN03 - Spoofed grid reading. One trigger: python scenarios/en03_spoofed_grid_reading.py [--server]  (log in logs/EN03-*.jsonl, report in logs/EN03-*.txt)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["EN03"] + sys.argv[1:]))

"""PO01 - Air pollution peak at evening rush. One trigger: python scenarios/po01_air_pollution_peak_at_evening_rush.py [--server]  (log in logs/PO01-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["PO01"] + sys.argv[1:]))

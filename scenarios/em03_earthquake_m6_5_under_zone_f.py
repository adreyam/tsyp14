"""EM03 - Earthquake M6.5 under zone F. One trigger: python scenarios/em03_earthquake_m6_5_under_zone_f.py [--server]  (log in logs/EM03-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["EM03"] + sys.argv[1:]))

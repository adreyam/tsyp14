"""TR01 - Traffic jam at rush hour. One trigger: python scenarios/tr01_traffic_jam_at_rush_hour.py [--server]  (log in logs/TR01-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["TR01"] + sys.argv[1:]))

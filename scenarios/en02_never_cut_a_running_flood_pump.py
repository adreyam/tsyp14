"""EN02 - Never cut a running flood pump. One trigger: python scenarios/en02_never_cut_a_running_flood_pump.py [--server]  (log in logs/EN02-*.jsonl, report in logs/EN02-*.txt)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["EN02"] + sys.argv[1:]))

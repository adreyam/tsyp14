"""PO03 - Garbage truck round. One trigger: python scenarios/po03_garbage_truck_round.py [--server]  (log in logs/PO03-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["PO03"] + sys.argv[1:]))

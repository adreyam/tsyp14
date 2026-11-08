"""PO02 - A bin overflows. One trigger: python scenarios/po02_a_bin_overflows.py [--server]  (log in logs/PO02-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["PO02"] + sys.argv[1:]))

"""EM02 - Flash flood. One trigger: python scenarios/em02_flash_flood.py [--server]  (log in logs/EM02-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["EM02"] + sys.argv[1:]))

"""EM01 - Building fire. One trigger: python scenarios/em01_building_fire.py [--server]  (log in logs/EM01-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["EM01"] + sys.argv[1:]))

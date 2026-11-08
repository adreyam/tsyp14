"""SY05 - Industrial fire with a toxic smoke plume (all axes). One trigger: python scenarios/sy05_industrial_fire_with_a_toxic_smoke_plume.py [--server]  (log in logs/SY05-*.jsonl, report in logs/SY05-*.txt)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["SY05"] + sys.argv[1:]))

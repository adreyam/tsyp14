"""EN01 - Protect the hospital. One trigger: python scenarios/en01_protect_the_hospital.py [--server]  (log in logs/EN01-*.jsonl, report in logs/EN01-*.txt)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["EN01"] + sys.argv[1:]))

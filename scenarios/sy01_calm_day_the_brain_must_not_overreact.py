"""SY01 - Calm day: the Brain must not overreact. One trigger: python scenarios/sy01_calm_day_the_brain_must_not_overreact.py [--server]  (log in logs/SY01-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["SY01"] + sys.argv[1:]))

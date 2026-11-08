"""SY04 - An actuator channel fails. One trigger: python scenarios/sy04_an_actuator_channel_fails.py [--server]  (log in logs/SY04-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["SY04"] + sys.argv[1:]))

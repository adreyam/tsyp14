"""SY03 - The link to the Twin drops and comes back. One trigger: python scenarios/sy03_the_link_to_the_twin_drops_and_comes_bac.py [--server]  (log in logs/SY03-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["SY03"] + sys.argv[1:]))

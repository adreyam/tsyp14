"""SY02 - A partner sends malformed messages. One trigger: python scenarios/sy02_a_partner_sends_malformed_messages.py [--server]  (log in logs/SY02-*.jsonl)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import main  # noqa: E402

sys.exit(main(["SY02"] + sys.argv[1:]))

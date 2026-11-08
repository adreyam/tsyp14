"""Shared by the trigger scripts: makes the brain/ code importable and launches a scenario."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "brain"))


def launch(scenario_id: str) -> None:
    """Run one Test Book scenario. The log goes to logs/<ID>.jsonl at the triplet root."""
    from demo.garbage import main
    sys.exit(main(["garbage", scenario_id]))

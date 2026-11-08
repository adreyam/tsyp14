"""Shared pytest setup.

Point the orchestrator's audit log at a throw-away database, so running the test suite
never writes test decisions into the real city_brain_audit.db the dashboard reads.
Must run before src.orchestrator is imported (conftest is loaded first).
"""
import os
import tempfile

os.environ.setdefault(
    "CITY_BRAIN_AUDIT_DB",
    os.path.join(tempfile.mkdtemp(prefix="city_brain_test_"), "audit.db"),
)
# Keep the suite offline and deterministic even if a GEMINI_API_KEY is set.
os.environ.setdefault("CITY_BRAIN_LLM", "off")

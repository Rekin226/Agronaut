"""Test-session setup shared across the suite."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_analytics(monkeypatch):
    """Keep usage-analytics writes out of the repo's data/ dir during tests. Agents built
    without an explicit analytics path would otherwise append to data/analytics.jsonl; point
    that at a throwaway temp file. Analytics unit tests pass an explicit path and are
    unaffected."""
    tmp = Path(tempfile.gettempdir()) / "agronaut_test_analytics.jsonl"
    monkeypatch.setenv("AGRONAUT_ANALYTICS_PATH", str(tmp))

"""Retrieval telemetry — useful in production, and incapable of leaking what was asked.

The golden set says how the retriever behaves on 33 curated queries. It cannot say how it behaves
on real traffic: how often the relevance floor fires, how far the closest match typically sits,
whether latency has drifted. Those are the earliest signals that the corpus or the embedding model
has moved out from under a threshold calibrated in L2 distance units.

The privacy constraint is absolute (docs/dpg/PRIVACY.md): counts and shape, never content. These
tests assert that the allowlist actually enforces it rather than merely documenting it.
"""

import json

import pytest

from agronaut_agent import rag
from agronaut_agent.analytics import Analytics


@pytest.fixture
def sink(tmp_path):
    return Analytics(path=tmp_path / "a.jsonl")


def _rows(a):
    return [json.loads(l) for l in a.path.read_text().splitlines()]


def test_query_text_cannot_be_recorded(sink):
    """The single most important property. Even a caller explicitly passing the query must not
    be able to persist it — unknown keys are dropped, not stored."""
    sink.record("retrieval", user_id="u1", query="my tilapia are dying in tank 3",
                text="retrieved passage body", outcome="hit", n_results=3)
    row = _rows(sink)[0]
    assert "query" not in row and "text" not in row
    assert "tilapia" not in json.dumps(row)
    assert row["outcome"] == "hit" and row["n_results"] == 3


def test_shape_fields_are_recorded(sink):
    sink.record("retrieval", user_id="u1", outcome="no_match", n_results=0, k=3,
                latency_ms=42, top_score=1.71, hybrid=False)
    row = _rows(sink)[0]
    assert row["outcome"] == "no_match"
    assert row["latency_ms"] == 42 and row["top_score"] == 1.71
    assert row["hybrid"] is False


def test_user_is_recorded_only_as_a_hash(sink):
    sink.record("retrieval", user_id="telegram:123456789", outcome="hit")
    row = _rows(sink)[0]
    assert "123456789" not in json.dumps(row)
    assert len(row["uid"]) == 12


# --- stats produced by the retriever itself ---------------------------------

class _Doc:
    def __init__(self, text, **md):
        self.page_content = text + " " + ("aquaponics husbandry detail. " * 12)
        self.metadata = md


class _Index:
    def __init__(self, scored):
        self._scored = scored
        self.docstore = type("_S", (), {"_dict": {}})()

    def similarity_search_with_score(self, query, k=3):
        return self._scored[:k]


def test_stats_report_a_hit(monkeypatch):
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    monkeypatch.setattr(rag, "_INDEX", _Index([(_Doc("Nitrite.", source_path="/r/knowledge/n.md"), 0.4)]))
    monkeypatch.setattr(rag, "_TRIED", True)
    text, stats = rag.search_with_stats("nitrite", k=3)
    assert "[source: knowledge/n.md]" in text
    assert stats["outcome"] == "hit" and stats["n_results"] == 1
    assert stats["top_score"] == 0.4
    assert isinstance(stats["latency_ms"], int)


def test_stats_report_the_floor_firing(monkeypatch):
    """An off-topic question must be observable as such in production, not just in the eval."""
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    monkeypatch.setattr(rag, "_INDEX", _Index([(_Doc("Unrelated.", source="https://x"), 1.95)]))
    monkeypatch.setattr(rag, "_TRIED", True)
    text, stats = rag.search_with_stats("capital of Canada", k=3)
    assert text == rag._NO_MATCH
    assert stats["outcome"] == "no_match" and stats["n_results"] == 0
    assert stats["top_score"] is None


def test_stats_report_an_unavailable_index(monkeypatch):
    monkeypatch.setattr(rag, "_INDEX", None)
    monkeypatch.setattr(rag, "_TRIED", True)
    text, stats = rag.search_with_stats("anything")
    assert "KNOWLEDGE_UNAVAILABLE" in text
    assert stats["outcome"] == "unavailable"


def test_search_contract_is_unchanged(monkeypatch):
    """search() is the seam the tool and the citation tests depend on; adding telemetry must not
    alter a single character of what it returns."""
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    monkeypatch.setattr(rag, "_INDEX", _Index([(_Doc("Aeration.", source_type="web",
                                                     url_label="FAO 589"), 0.3)]))
    monkeypatch.setattr(rag, "_TRIED", True)
    assert rag.search("aeration") == rag.search_with_stats("aeration")[0]
    assert "[source: FAO 589]" in rag.search("aeration")

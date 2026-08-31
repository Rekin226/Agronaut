"""The relevance floor: what the retriever is allowed to hand back as grounded context.

Without a floor the retriever always returns its top k, so "what is the capital of Canada" came
back with three confident, source-labelled passages from aquaponics papers — a grounded-LOOKING
answer to a question the corpus cannot answer. That is the most dangerous failure mode a cited
RAG system has, because the citation makes it more convincing, not less.

Hermetic: a fake index supplies the distances, so no embedding model or network is involved.
"""

import pytest

from agronaut_agent import rag

# --- what gets out: the relevance floor --------------------------------------

class _Doc:
    def __init__(self, text, **md):
        self.page_content = text
        self.metadata = md


def _long(text):
    # retrieve() drops passages under 200 chars as boilerplate; keep fixtures realistic.
    return text + " " + ("aquaponics husbandry detail. " * 12)


class _ScoredIndex:
    """Fake index returning (doc, distance) pairs — FAISS L2, so LOWER is closer."""
    def __init__(self, scored):
        self._scored = scored

    def similarity_search_with_score(self, query, k=3):
        return self._scored[:k]


@pytest.fixture
def index_with(monkeypatch):
    def _install(scored):
        monkeypatch.setattr(rag, "_INDEX", _ScoredIndex(scored))
        monkeypatch.setattr(rag, "_TRIED", True)
    return _install


def test_distant_passages_are_dropped(index_with, monkeypatch):
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([
        (_Doc(_long("Nitrite stresses tilapia gills."), source_path="/r/knowledge/water.md"), 1.20),
        (_Doc(_long("Unrelated systematic review prose."), source="https://doi.org/x"), 1.90),
    ])
    hits = rag.retrieve("nitrite", k=3)
    assert [h["source"] for h in hits] == ["knowledge/water.md"]


def test_all_distant_yields_the_honest_no_match_message(index_with, monkeypatch):
    """An off-topic question must produce 'no matching passages', NOT three confident
    source-labelled passages that cannot answer it."""
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc(_long("Aquaponics survey prose."), source="https://doi.org/y"), 1.80)])
    assert rag.search("what is the capital of Canada") == rag._NO_MATCH


def test_floor_can_be_disabled_for_measurement(index_with, monkeypatch):
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc(_long("Far away passage."), source="https://doi.org/y"), 9.9)])
    assert rag.retrieve("x", k=3) == []
    assert len(rag.retrieve("x", k=3, max_dist=float("inf"))) == 1


def test_env_override_and_off_switch(monkeypatch):
    monkeypatch.setenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", "0.5")
    assert rag.max_distance() == 0.5
    monkeypatch.setenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", "off")
    assert rag.max_distance() == float("inf")
    monkeypatch.setenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", "nonsense")
    assert rag.max_distance() == rag._DEFAULT_MAX_DISTANCE   # bad config must not break a turn
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE")
    assert rag.max_distance() == rag._DEFAULT_MAX_DISTANCE


def test_floor_default_sits_between_the_measured_bands():
    """The default is only meaningful if it lies inside the gap measured on the golden set:
    worst on-topic 1.554, closest off-topic 1.717."""
    assert 1.554 < rag._DEFAULT_MAX_DISTANCE < 1.717



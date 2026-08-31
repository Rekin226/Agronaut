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


# Band edges measured on the 3935-chunk corpus, 2026-09-01 (retrieval_eval/sweep_2026_09.json).
# At 1354 chunks these were 1.548 and 1.426 — OVERLAPPING, so no floor could separate them. More
# corpus pulled the on-topic band in: every real question now has a closer match than it did.
_WORST_ON_TOPIC = 1.383
_CLOSEST_OFF_TOPIC = 1.411
_MIN_HEADROOM = 0.10       # the margin this project accepted when it rejected a 0.032 one


def test_floor_default_keeps_the_measured_safety_margin():
    """The default must clear the worst REAL query by the margin this project settled on.

    Deliberately not "sits inside the gap between the bands". The bands are now separable
    (1.383 < 1.411) and a floor of 1.40 would sit in that gap and reject all ten controls — but it
    would clear the worst real query by 0.017, half the margin that was already judged too thin.
    The property that must hold is headroom above real queries, not tightness against off-topic
    ones, so that is what this asserts.
    """
    assert rag._DEFAULT_MAX_DISTANCE >= _WORST_ON_TOPIC + _MIN_HEADROOM


def test_floor_default_is_tighter_than_the_pre_drift_value():
    """The bands separated when the corpus grew, so the floor could be tightened from 1.65. If
    this ever fails upward again, the corpus moved and the sweep needs re-running."""
    assert rag._DEFAULT_MAX_DISTANCE < 1.65


def test_the_bands_are_recorded_as_separable():
    """A guard on the premise. If these two ever cross again the floor's whole justification
    changes, and `scripts/retrieval_eval` prints OVERLAPPING instead of separable."""
    assert _WORST_ON_TOPIC < _CLOSEST_OFF_TOPIC



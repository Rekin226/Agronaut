"""Citations are mechanics, not prompt hope: every knowledge passage the agent retrieves
carries its source label, so 'cited advice' is enforced by the retrieval layer itself.
Tests inject a fake index — no FAISS build, no network, no embedding model.
"""

from pathlib import Path

import pytest

from agronaut_agent import rag


def _pad(text: str) -> str:
    # retrieve_context drops passages < 200 chars as boilerplate; keep test docs realistic.
    filler = (" This passage describes aquaponics husbandry practice in enough detail to "
              "clear the knowledge-base length threshold for a real retrieved chunk.")
    while len(text) < 220:
        text += filler
    return text


class _Doc:
    def __init__(self, text, **metadata):
        self.page_content = _pad(text)
        self.metadata = metadata


class _FakeIndex:
    def __init__(self, docs):
        self._docs = docs

    def similarity_search_with_score(self, query, k=3):
        return [(d, 0.5) for d in self._docs[:k]]


@pytest.fixture
def fake_index(monkeypatch):
    def _install(docs):
        monkeypatch.setattr(rag, "_INDEX", _FakeIndex(docs))
        monkeypatch.setattr(rag, "_TRIED", True)
    return _install


def test_every_passage_carries_its_source(fake_index):
    fake_index([
        _Doc("Nitrite above 1 mg/L stresses tilapia gills.",
             source_type="local_file", source_path="/repo/knowledge/water_quality.md"),
        _Doc("Raft spacing for lettuce is typically 20-25 cm.",
             source_type="web", source="https://doi.org/10.3390/w9030182"),
    ])
    out = rag.search("nitrite stress")
    assert "[source: knowledge/water_quality.md]" in out   # local file → repo-relative label
    assert "[source: https://doi.org/10.3390/w9030182]" in out
    assert "Nitrite above 1 mg/L" in out
    # every passage block is labeled — no orphan passages without a source
    blocks = [b for b in out.split("\n\n") if b.strip()]
    assert all("[source:" in b for b in blocks)


def test_web_label_preferred_over_raw_url(fake_index):
    fake_index([
        _Doc("Backup aeration prevents DO crashes during power cuts.",
             source_type="web", source="https://example.org/x",
             url_label="FAO 589 small-scale aquaponic food production"),
    ])
    out = rag.search("aeration")
    assert "[source: FAO 589 small-scale aquaponic food production]" in out


def test_missing_source_metadata_labeled_unattributed(fake_index):
    fake_index([_Doc("Some orphan passage.")])
    out = rag.search("anything")
    assert "[source: unattributed]" in out


def test_unavailable_index_message_unchanged(monkeypatch):
    monkeypatch.setattr(rag, "_INDEX", None)
    monkeypatch.setattr(rag, "_TRIED", True)
    assert "KNOWLEDGE_UNAVAILABLE" in rag.search("anything")


def test_urls_txt_has_no_duplicates():
    lines = [ln.strip().rstrip("/").lower() for ln in
             Path(__file__).resolve().parents[2].joinpath("urls.txt").read_text().splitlines()
             if ln.strip()]
    assert len(lines) == len(set(lines)), "urls.txt contains duplicate entries"


def test_system_prompt_requires_naming_sources():
    from agronaut_agent.core import SYSTEM_PROMPT
    assert "[source:" in SYSTEM_PROMPT

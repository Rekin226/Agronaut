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
    """No source may be indexed twice — duplicate chunks crowd out other documents in the top k.

    Asserted over PARSED entries rather than raw lines: comment lines legitimately repeat (urls.txt
    keeps a commented record of de-indexed references), and a raw-line check both trips over that
    and misses the duplicate that actually matters — the same URL declared once bare and once in
    CATEGORY|URL|LABEL form.
    """
    from srcs.chatbot import parse_urls_file
    root = Path(__file__).resolve().parents[2]
    urls = [e["url"].rstrip("/").lower() for e in parse_urls_file(str(root / "urls.txt"))]
    assert len(urls) == len(set(urls)), "urls.txt declares the same source more than once"


def test_every_indexed_source_declares_a_licence():
    """Agronaut is a Digital Public Good: it may only index openly licensed text. An unlicensed
    entry is a compliance problem, and empirically also a retrievability one — every source
    measured that returned usable full text was openly licensed."""
    from srcs.chatbot import parse_urls_file
    root = Path(__file__).resolve().parents[2]
    for e in parse_urls_file(str(root / "urls.txt")):
        assert e["licence"], f"{e['url']} is indexed without a declared licence"


def test_every_indexed_source_declares_a_label():
    """The LABEL is what an operator sees in a citation, and it is what makes topic drift
    detectable — an unlabelled source can silently become a different document."""
    from srcs.chatbot import parse_urls_file
    root = Path(__file__).resolve().parents[2]
    for e in parse_urls_file(str(root / "urls.txt")):
        assert e["label"], f"{e['url']} is indexed without a citable label"


def test_system_prompt_requires_naming_sources():
    from agronaut_agent.core import SYSTEM_PROMPT
    assert "[source:" in SYSTEM_PROMPT


def test_system_prompt_requires_judging_passage_relevance():
    """Retrieval returns the CLOSEST passages, which is not the same as passages that answer the
    question. The relevance floor is a coarse filter and provably cannot separate every case:
    "best way to remove red wine from a carpet" lands nearer the corpus than five genuine operator
    queries, and distance, pool margin, score spread and BM25 were each measured and none of them
    separate it. The model is the fine filter, so the prompt has to tell it to filter — otherwise
    an irrelevant passage arrives wearing a source label, which makes it look verified."""
    from agronaut_agent.core import SYSTEM_PROMPT
    assert "JUDGE EACH RETRIEVED PASSAGE" in SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    assert "ignore it" in lowered          # told what to DO with an irrelevant passage
    assert "nothing specific" in lowered   # and what to say when none fit

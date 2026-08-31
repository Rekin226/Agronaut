"""Metadata filtering — the third leg of hybrid retrieval.

The course (M2) is precise about what this is and is not: it "doesn't perform retrieval, it
narrows down results from other techniques", it is rigid rather than ranked, and it is useless
alone. So the tests are about EXCLUSION being exact and being applied in the right place, not
about relevance.

Two properties matter more than the rest:

  - passing no filter must leave behaviour byte-identical to before filtering existed, because
    every retrieval number this project has recorded was measured without one,
  - a chunk that LACKS the filtered key must be excluded, not admitted. Admitting it is how
    "only FAO chapter 8" quietly returns the entire markdown corpus.
"""

import pytest

from agronaut_agent import rag

# --- the predicate -----------------------------------------------------------

def test_scalar_clause_matches_exactly():
    assert rag._matches({"kb_tag": "ph_and_alkalinity"}, {"kb_tag": "ph_and_alkalinity"})
    assert not rag._matches({"kb_tag": "algae_control"}, {"kb_tag": "ph_and_alkalinity"})


def test_collection_clause_is_membership():
    md = {"kb_tag": "algae_control"}
    assert rag._matches(md, {"kb_tag": ["algae_control", "water_source_and_treatment"]})
    assert not rag._matches(md, {"kb_tag": ["ph_and_alkalinity"]})


def test_clauses_are_conjunctive():
    """Filters narrow. Two clauses must both hold, or the filter would widen as it grew."""
    md = {"source_type": "local_file", "kb_tag": "algae_control"}
    assert rag._matches(md, {"source_type": "local_file", "kb_tag": "algae_control"})
    assert not rag._matches(md, {"source_type": "local_file", "kb_tag": "feed_and_feeding"})


def test_missing_key_is_excluded_not_admitted():
    """The direction that matters. A markdown chunk has no `chapter`; filtering on chapter must
    drop it rather than pass it through for having nothing to disagree with."""
    assert not rag._matches({"source_type": "local_file"}, {"chapter": "8"})


def test_values_compare_across_str_and_int():
    """`page` arrives as an int from the PDF loader and as a string from a CLI or JSON caller.
    A filter that silently matched neither would look like an empty corpus."""
    assert rag._matches({"page": 51}, {"page": "51"})
    assert rag._matches({"page": "51"}, {"page": 51})


# --- validation --------------------------------------------------------------

def test_unknown_keys_are_dropped_with_a_warning(caplog):
    """A typo costs precision, never the answer: an operator's question must not fail because
    a caller wrote `kb_tags`."""
    out = rag.normalize_filters({"kb_tag": "algae_control", "kb_tags": "oops"})
    assert out == {"kb_tag": "algae_control"}
    assert "kb_tags" in caplog.text


def test_empty_and_all_unknown_normalize_to_none():
    assert rag.normalize_filters(None) is None
    assert rag.normalize_filters({}) is None
    assert rag.normalize_filters({"nonsense": 1}) is None


# --- placement in the pipeline ----------------------------------------------

class _Doc:
    def __init__(self, text, meta):
        self.page_content = text
        self.metadata = meta


class _FakeIndex:
    """A FAISS stand-in with the two behaviours retrieve() depends on: a callable metadata
    filter applied before the top-k cut, and a docstore BM25 can be built over."""

    def __init__(self, docs_scores):
        self._docs_scores = docs_scores
        self.docstore = type("D", (), {"_dict": {i: d for i, (d, _s) in enumerate(docs_scores)}})()

    def similarity_search_with_score(self, query, k=4, filter=None, fetch_k=20):
        pairs = [(d, s) for d, s in self._docs_scores if filter is None or filter(d.metadata)]
        return sorted(pairs, key=lambda p: p[1])[:k]


@pytest.fixture
def index(monkeypatch):
    # Passages run past 200 characters on purpose: srcs.chatbot._is_boilerplate_text treats
    # anything shorter as furniture, and retrieve() applies that filter before this one. A
    # fixture of one-liners would test the boilerplate rule rather than the metadata rule.
    docs = [
        (_Doc("Green water is a suspended algae bloom and it is fed by light plus dissolved "
              "nutrient. Shade the fish tank and any exposed sump, cut the photoperiod on "
              "supplementary lighting, and keep the beds planted so the crop competes for the "
              "nitrate the algae are living on.",
              {"source_type": "local_file", "source_path": "knowledge/algae_control.md",
               "kb_tag": "algae_control"}), 0.5),
        (_Doc("Alkalinity is the buffer that holds pH against the acid load nitrification "
              "produces. When carbonate hardness falls the pH stops drifting down slowly and "
              "starts crashing between checks, so top up with a carbonate or hydroxide base "
              "rather than chasing the pH number itself.",
              {"source_type": "local_file", "source_path": "knowledge/ph_and_alkalinity.md",
               "kb_tag": "ph_and_alkalinity"}), 0.7),
        (_Doc("Design of aquaponic units. Algae growth on exposed water surfaces reduces the "
              "oxygen available overnight and competes with the crop for nutrient. Unit design "
              "should therefore minimise open, illuminated water between the fish tank and the "
              "grow beds wherever the climate allows it.",
              {"source_type": "web", "url_label": "FAO 589", "chapter": "Design of aquaponic units",
               "page": 51}), 0.9),
    ]
    idx = _FakeIndex(docs)
    monkeypatch.setattr(rag, "_get_index", lambda: idx)
    monkeypatch.setattr(rag, "_get_bm25", lambda: None)   # dense-only: isolate the filter
    return idx


def test_no_filter_returns_everything_as_before(index):
    hits = rag.retrieve("algae", k=3)
    assert len(hits) == 3


def test_filter_restricts_to_the_named_source_type(index):
    hits = rag.retrieve("algae", k=3, filters={"source_type": "local_file"})
    assert {h["source"] for h in hits} == {"knowledge/algae_control.md",
                                           "knowledge/ph_and_alkalinity.md"}


def test_filter_can_isolate_a_single_topic_file(index):
    hits = rag.retrieve("algae", k=3, filters={"kb_tag": "algae_control"})
    assert [h["source"] for h in hits] == ["knowledge/algae_control.md"]


def test_filter_on_a_pdf_chapter_excludes_markdown(index):
    hits = rag.retrieve("algae", k=3, filters={"chapter": "Design of aquaponic units"})
    assert [h["source"] for h in hits] == ["FAO 589"]


def test_filter_that_matches_nothing_returns_empty_not_everything(index):
    """The failure mode worth a test of its own: a filter with no matches must return nothing,
    never fall back to the unfiltered ranking."""
    assert rag.retrieve("algae", k=3, filters={"kb_tag": "no_such_file"}) == []


def test_filter_is_applied_to_the_keyword_pool_too(monkeypatch, index):
    """Filtering only the dense pool would let BM25 reintroduce excluded chunks through fusion,
    which is exactly the leak the course's per-list filter diagram avoids."""
    docs = [d for d, _s in index._docs_scores]

    class _BM25:
        def get_scores(self, tokens):
            return [0.0, 0.0, 9.9]        # the FAO chunk wins on keywords

    monkeypatch.setattr(rag, "_get_bm25", lambda: (_BM25(), docs))
    hits = rag.retrieve("algae", k=3, hybrid=True, filters={"source_type": "local_file"})
    assert "FAO 589" not in {h["source"] for h in hits}


def test_stats_record_which_keys_were_filtered_never_their_values(index):
    """A filter VALUE can be as specific as one topic file, which is closer to the subject of
    the question than to its shape. Only the key names may be recorded."""
    _text, stats = rag.search_with_stats("algae", k=3,
                                         filters={"kb_tag": "algae_control"})
    assert stats["filtered"] == ["kb_tag"]
    assert "algae_control" not in str(stats)

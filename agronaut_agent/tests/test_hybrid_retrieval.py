"""Hybrid BM25 + reciprocal rank fusion — the fusion arithmetic, and the guarantees it must not break.

Hybrid ships DISABLED. Measured over the golden set, dense-only equals or beats every weighting on
every metric (docs/dpg/retrieval_eval/hybrid_sweep.json), because 362 chunks sharing one
vocabulary domain give BM25 little to discriminate on. The implementation is kept and tested
because that balance is expected to invert as the corpus grows — so it must be correct and
inert now, and correct and available later.

The critical property under test is that fusion CANNOT weaken the relevance floor. BM25 always
ranks something first, however off-topic the question, so if keyword hits could enter results on
their own the floor's off-topic guarantee would silently evaporate.
"""

import pytest

from agronaut_agent import rag


# --- fusion arithmetic (pure) ------------------------------------------------

def test_rrf_rewards_agreement_between_the_two_rankings():
    """A document BOTH retrievers surface must outrank one only a single retriever surfaced.

    Note this is about presence on both lists, not about being mid-ranked on both: at k=60 the
    per-rank differences are tiny, so 1st+3rd actually edges out 2nd+2nd (1/61 + 1/63 > 2/62).
    Agreement is what RRF rewards; exact rank barely matters once k is large.
    """
    fused = rag.rrf_fuse(dense=["both", "dense_only"], sparse=["both"], b=0.5)
    assert fused[0] == "both"


def test_rrf_respects_beta_weighting():
    """beta=1.0 is dense-only ordering; beta=0.0 is keyword-only ordering."""
    assert rag.rrf_fuse(["x", "y"], ["y", "x"], b=1.0)[0] == "x"
    assert rag.rrf_fuse(["x", "y"], ["y", "x"], b=0.0)[0] == "y"


def test_rrf_includes_documents_found_by_only_one_retriever():
    fused = rag.rrf_fuse(dense=["a"], sparse=["z"], b=0.5)
    assert set(fused) == {"a", "z"}


def test_rrf_k_controls_how_much_a_top_rank_dominates():
    """The knob that stops one retriever's top hit from steamrolling the other's ranking.

    "spike" is 1st on the semantic list and absent from the keyword list. "steady" is 3rd on both.
    Padding differs between the lists so no filler can appear twice and win on agreement, and
    beta=0.6 keeps a keyword 1st place from tying the semantic 1st place.

        k=0   spike = 0.6/1 = 0.600   steady = 0.6/3 + 0.4/3 = 0.333  -> spike
        k=60  spike = 0.6/61 = 0.010  steady = 1.0/63       = 0.016   -> steady

    A large k is what makes agreement between retrievers matter more than one confident hit.
    """
    dense = ["spike", "dense_pad", "steady"]
    sparse = ["kw_pad_1", "kw_pad_2", "steady"]
    assert rag.rrf_fuse(dense, sparse, b=0.6, rrf_k=0)[0] == "spike"
    assert rag.rrf_fuse(dense, sparse, b=0.6, rrf_k=60)[0] == "steady"


def test_rrf_handles_empty_lists():
    assert rag.rrf_fuse([], [], b=0.5) == []
    assert rag.rrf_fuse(["a"], [], b=0.5) == ["a"]


def test_beta_is_clamped_and_falls_back_on_bad_config(monkeypatch):
    monkeypatch.setenv("AGRONAUT_HYBRID_BETA", "5")
    assert rag.beta() == 1.0
    monkeypatch.setenv("AGRONAUT_HYBRID_BETA", "-2")
    assert rag.beta() == 0.0
    monkeypatch.setenv("AGRONAUT_HYBRID_BETA", "nonsense")
    assert rag.beta() == rag._DEFAULT_BETA
    monkeypatch.delenv("AGRONAUT_HYBRID_BETA")
    assert rag.beta() == rag._DEFAULT_BETA


def test_hybrid_is_on_by_default(monkeypatch):
    """Shipping default, and it was reversed on evidence.

    On the 362-chunk corpus hybrid lost at every weighting and shipped OFF. Adding FAO 589 took
    the corpus to 1354 chunks and re-running the sweep flipped it: MAP +0.091 and recall +0.091 at
    beta=0.90. The technique did not change; the corpus did.
    """
    monkeypatch.delenv("AGRONAUT_HYBRID", raising=False)
    assert rag.hybrid_enabled()
    monkeypatch.setenv("AGRONAUT_HYBRID", "off")
    assert not rag.hybrid_enabled()


def test_default_beta_is_the_measured_optimum():
    """0.90, not the intuitive 0.5. Only 10% keyword weight — enough to break ties a 992-chunk
    book wins on volume, not enough to let shared aquaponics vocabulary dominate. At 0.5 hybrid
    is still clearly worse than dense-only."""
    assert rag._DEFAULT_BETA == 0.90


# --- fusion must not weaken the relevance floor ------------------------------

class _Doc:
    def __init__(self, text, **md):
        self.page_content = text + " " + ("aquaponics husbandry detail. " * 12)
        self.metadata = md


class _Index:
    def __init__(self, scored):
        self._scored = scored
        self.docstore = type("_S", (), {"_dict": {i: d for i, (d, _) in enumerate(scored)}})()

    def similarity_search_with_score(self, query, k=3):
        return self._scored[:k]


@pytest.fixture
def index_with(monkeypatch):
    def _install(scored):
        monkeypatch.setattr(rag, "_INDEX", _Index(scored))
        monkeypatch.setattr(rag, "_TRIED", True)
        monkeypatch.setattr(rag, "_BM25", None)
        monkeypatch.setattr(rag, "_BM25_TRIED", False)
    return _install


def test_keyword_hits_cannot_resurrect_an_off_topic_query(index_with, monkeypatch):
    """THE critical guarantee. Every passage is beyond the floor, so the corpus cannot answer.
    BM25 will still rank something first — it always does — and that must not produce a result."""
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc("Algae removal from surfaces."), 1.90),
                (_Doc("Nitrite management."), 1.95)])
    assert rag.retrieve("how do I remove a red wine stain", k=3, hybrid=True) == []
    assert rag.search("how do I remove a red wine stain") == rag._NO_MATCH


def test_hybrid_disabled_returns_dense_order(index_with, monkeypatch):
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc("Closest.", source_path="/r/knowledge/a.md"), 0.10),
                (_Doc("Further.", source_path="/r/knowledge/b.md"), 0.20)])
    hits = rag.retrieve("q", k=2, hybrid=False)
    assert [h["source"] for h in hits] == ["knowledge/a.md", "knowledge/b.md"]


def test_keyword_only_hit_reports_no_distance_rather_than_a_fake_one(index_with, monkeypatch):
    """A BM25 hit has no L2 distance. Inventing a comparable number would corrupt any later
    decision that reads the score — the floor included."""
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc("Nitrite stresses tilapia gills badly.", source_path="/r/knowledge/n.md"), 0.2),
                (_Doc("Green water means suspended algae bloom.", source_path="/r/knowledge/algae.md"), 1.9)])
    hits = rag.retrieve("green water algae bloom", k=3, hybrid=True)
    for h in hits:
        assert h["score"] is None or isinstance(h["score"], float)
    assert any(h["score"] == 0.2 for h in hits)


def test_bm25_failure_degrades_to_dense_rather_than_breaking_the_turn(index_with, monkeypatch):
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc("Relevant passage.", source_path="/r/knowledge/a.md"), 0.10)])
    monkeypatch.setattr(rag, "_BM25", None)
    monkeypatch.setattr(rag, "_BM25_TRIED", True)      # simulate an unavailable keyword index
    hits = rag.retrieve("q", k=3, hybrid=True)
    assert [h["source"] for h in hits] == ["knowledge/a.md"]


def test_citation_labels_survive_fusion(index_with, monkeypatch):
    """Fusion reorders passages; it must never strip the attribution that makes them citable."""
    monkeypatch.delenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", raising=False)
    index_with([(_Doc("Aeration prevents DO crashes.", source_type="web",
                      url_label="FAO 589"), 0.30)])
    assert "[source: FAO 589]" in rag.search("aeration")


def test_bm25_index_actually_builds_and_ranks(monkeypatch):
    """A functional check, not a degradation check.

    Every other test here tolerates a missing rank_bm25 by design, so with the package absent the
    whole file still passed while hybrid retrieval was completely inert. This one exercises the
    real BM25 path: the index must build from the FAISS docstore and rank the document containing
    the query's exact terms first.
    """
    import rank_bm25  # noqa: F401 — the point is that this must be installed

    docs = [
        _Doc("Green water means suspended single-celled algae blooming in the tank.",
             source_path="/r/knowledge/algae.md"),
        _Doc("Feed the fish twice daily and remove uneaten pellets promptly.",
             source_path="/r/knowledge/feed.md"),
    ]
    monkeypatch.setattr(rag, "_INDEX", type("_I", (), {
        "docstore": type("_S", (), {"_dict": dict(enumerate(docs))})()})())
    monkeypatch.setattr(rag, "_TRIED", True)
    monkeypatch.setattr(rag, "_BM25", None)
    monkeypatch.setattr(rag, "_BM25_TRIED", False)

    built = rag._get_bm25()
    assert built is not None, "BM25 index failed to build — is rank_bm25 installed?"
    bm25, indexed = built
    scores = bm25.get_scores(rag._tokenize("green water algae bloom"))
    best = max(range(len(indexed)), key=lambda i: scores[i])
    assert "algae" in indexed[best].metadata["source_path"]


def test_tokenizer_is_case_and_punctuation_insensitive():
    """BM25 matches on exact tokens, so "Nitrite," and "nitrite" must normalise to one term or
    keyword search silently misses the terminology it exists to catch."""
    assert rag._tokenize("Nitrite, pH 6.8 — DO!") == ["nitrite", "ph", "6", "8", "do"]

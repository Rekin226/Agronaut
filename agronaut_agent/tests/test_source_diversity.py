"""The per-source cap: one source may not fill the whole result window.

When FAO 589 (992 chunks) joined an 82-chunk curated corpus, it took all three slots on 10 of 33
golden-set queries — so a targeted operator answer that existed in knowledge/ never reached the
model at all. Ranking cannot fix that on its own: the book genuinely is similar, and it has twelve
times as many chances to be. Capping per-source occupancy spends one slot on breadth.

Measured effect (1354 chunks, hybrid on): hit_rate 0.848 -> 0.939, recall 0.788 -> 0.894,
precision 0.495 -> 0.540. Every metric improved — the largest single gain in this work.
"""


from agronaut_agent import rag


def _keys(n, source):
    return [(source, f"chunk-{i}") for i in range(n)]


def _by_key(keys):
    return {k: {"source": k[0], "text": k[1], "score": 0.1} for k in keys}


def test_one_source_cannot_fill_the_whole_window():
    """The exact observed failure: a single book occupying every slot."""
    ranked = _keys(5, "FAO 589")
    picked = rag._diversify(ranked, _by_key(ranked), k=3, max_per_source=2)
    assert len(picked) == 3
    # Only FAO exists here, so backfill is correct — returning 2 would be worse than 3.
    assert all(k[0] == "FAO 589" for k in picked)


def test_cap_lets_a_second_source_through():
    ranked = _keys(4, "FAO 589") + _keys(1, "knowledge/algae_control.md")
    picked = rag._diversify(ranked, _by_key(ranked), k=3, max_per_source=2)
    sources = [k[0] for k in picked]
    assert sources.count("FAO 589") == 2
    assert "knowledge/algae_control.md" in sources


def test_relevance_order_is_preserved_within_the_cap():
    """Diversity decides who is displaced, not who ranks first — fusion already decided that."""
    ranked = _keys(3, "A") + _keys(3, "B")
    picked = rag._diversify(ranked, _by_key(ranked), k=4, max_per_source=2)
    assert picked[:2] == _keys(3, "A")[:2]


def test_backfill_rather_than_a_short_result():
    """If too few distinct sources matched, fill the window from the original ranking. A
    capped-but-empty result is worse than a repetitive one — the model gets less to work with."""
    ranked = _keys(5, "only-source")
    picked = rag._diversify(ranked, _by_key(ranked), k=3, max_per_source=1)
    assert len(picked) == 3


def test_no_duplicates_after_backfill():
    ranked = _keys(4, "A")
    picked = rag._diversify(ranked, _by_key(ranked), k=3, max_per_source=1)
    assert len(set(picked)) == len(picked)


def test_cap_of_zero_disables_diversification():
    ranked = _keys(5, "A")
    assert rag._diversify(ranked, _by_key(ranked), k=3, max_per_source=0) == ranked[:3]


def test_fewer_candidates_than_k_is_safe():
    ranked = _keys(2, "A")
    assert len(rag._diversify(ranked, _by_key(ranked), k=5, max_per_source=1)) == 2


def test_empty_ranking_is_safe():
    assert rag._diversify([], {}, k=3, max_per_source=2) == []


def test_default_cap_and_env_override(monkeypatch):
    monkeypatch.delenv("AGRONAUT_MAX_PER_SOURCE", raising=False)
    assert rag.max_source_cap() == 2
    monkeypatch.setenv("AGRONAUT_MAX_PER_SOURCE", "1")
    assert rag.max_source_cap() == 1
    monkeypatch.setenv("AGRONAUT_MAX_PER_SOURCE", "0")
    assert rag.max_source_cap() == 0
    monkeypatch.setenv("AGRONAUT_MAX_PER_SOURCE", "nonsense")
    assert rag.max_source_cap() == 2       # bad config must not break a turn


def test_citation_labels_are_what_the_cap_counts():
    """The cap groups by the CITATION label, which is what an operator sees. Grouping by chunk id
    or URL would let the same work occupy every slot under different identifiers."""
    ranked = [("FAO 589", "a"), ("FAO 589", "b"), ("knowledge/x.md", "c")]
    picked = rag._diversify(ranked, _by_key(ranked), k=3, max_per_source=1)
    assert [k[0] for k in picked][:2] == ["FAO 589", "knowledge/x.md"]

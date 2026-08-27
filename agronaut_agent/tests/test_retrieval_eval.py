"""The retrieval metrics must be arithmetically right before any tuning decision rests on them.

These tests pin the METRIC MATH, not the model: every case passes plain lists of source labels,
so there is no index, no embedding model and no network. A retriever tuned against a miscomputed
recall number is worse than an untuned one, because the number looks like evidence.
"""

import json
from pathlib import Path

import pytest

from scripts.retrieval_eval import (
    aggregate,
    average_precision,
    dedupe,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    run,
    score_query,
)

A, B, C, D = "knowledge/a.md", "knowledge/b.md", "knowledge/c.md", "knowledge/d.md"


def test_dedupe_keeps_first_occurrence_order():
    # Three chunks from the same file are ONE retrieved document, ranked where it first appeared.
    assert dedupe([A, A, B, A, C]) == [A, B, C]


def test_dedupe_empty():
    assert dedupe([]) == []


def test_precision_is_over_returned_documents_not_k():
    # All three returned docs are relevant -> perfect precision, regardless of k.
    assert precision_at_k([A, B, C], {A, B, C}) == 1.0
    assert precision_at_k([A, B, C], {A}) == pytest.approx(1 / 3)
    assert precision_at_k([], {A}) == 0.0


def test_recall_is_over_the_relevant_set():
    assert recall_at_k([A], {A, B}) == 0.5
    assert recall_at_k([A, B], {A, B}) == 1.0
    assert recall_at_k([C], {A, B}) == 0.0
    assert recall_at_k([A], set()) == 0.0        # no ground truth -> no credit, no crash


def test_reciprocal_rank_tracks_position_of_first_hit():
    assert reciprocal_rank([A, B, C], {A}) == 1.0
    assert reciprocal_rank([B, A, C], {A}) == 0.5
    assert reciprocal_rank([B, C, A], {A}) == pytest.approx(1 / 3)
    assert reciprocal_rank([B, C, D], {A}) == 0.0


def test_average_precision_rewards_ranking_relevant_docs_higher():
    """Same two relevant docs retrieved, different order -> the better ranking must score higher.
    This is the property that distinguishes MAP from plain recall."""
    good = average_precision([A, B, C], {A, B}, k=3)   # relevant at ranks 1,2
    bad = average_precision([C, A, B], {A, B}, k=3)    # relevant at ranks 2,3
    assert good > bad
    assert good == pytest.approx((1 / 1 + 2 / 2) / 2)  # 1.0
    assert bad == pytest.approx((1 / 2 + 2 / 3) / 2)


def test_average_precision_denominator_caps_at_k():
    # 4 relevant docs but only k=2 slots: retrieving 2 of them at the top is a perfect score,
    # otherwise the metric would punish a retriever for a limit it was given.
    assert average_precision([A, B], {A, B, C, D}, k=2) == pytest.approx(1.0)


def test_average_precision_no_relevant_set():
    assert average_precision([A], set(), k=3) == 0.0


def test_score_query_truncates_to_k_after_dedupe():
    """Dedupe happens BEFORE the top-k cut. Three chunks of A then B must not let A's
    duplicates push B out of a k=2 window."""
    r = score_query([A, A, A, B], [B], k=2)
    assert r["retrieved"] == [A, B]
    assert r["hit"] is True
    assert r["rr"] == 0.5


def test_score_query_complete_miss():
    r = score_query([C, D], [A, B], k=3)
    assert r["hit"] is False
    assert r["recall"] == 0.0 and r["rr"] == 0.0 and r["ap"] == 0.0


def test_aggregate_averages_across_queries():
    rows = [
        {"hit": True, "precision": 1.0, "recall": 1.0, "rr": 1.0, "ap": 1.0},
        {"hit": False, "precision": 0.0, "recall": 0.0, "rr": 0.0, "ap": 0.0},
    ]
    agg = aggregate(rows, k=3)
    assert agg["hit_rate"] == 0.5
    assert agg["MRR"] == 0.5
    assert agg["recall@k"] == 0.5
    assert agg["queries"] == 2 and agg["k"] == 3


def test_aggregate_empty_does_not_divide_by_zero():
    assert aggregate([], k=3)["hit_rate"] == 0.0


def test_run_accepts_an_injected_retriever():
    """The whole harness runs without an index: a perfect fake retriever must score 1.0,
    which proves the plumbing (golden set -> retrieve -> metrics) is wired correctly."""
    golden = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/dpg/retrieval_eval/golden_set.json").read_text())
    by_query = {q["query"]: q["relevant"] for q in golden["queries"]}

    def perfect(query, k):
        return [{"source": s, "score": 0.1, "text": "x"} for s in by_query.get(query, [])][:k]

    report = run(retrieve=perfect)
    assert report["summary"]["hit_rate"] == 1.0
    assert report["summary"]["precision@k"] == 1.0
    assert report["summary"]["recall@k"] == pytest.approx(1.0)
    # negative controls have no relevant set, so the fake returns nothing for them
    assert all(n["top_score"] is None for n in report["negative_controls"])


def test_golden_set_references_only_real_sources():
    """Ground truth must name sources that actually exist in the corpus.

    A label pointing at a deleted knowledge file — or a web source whose LABEL was edited in
    urls.txt — silently caps recall below 1.0 and reads as a retrieval regression rather than a
    stale annotation. Both kinds are checked: local entries look like paths and must exist on
    disk; anything else must match a declared web source's citation label exactly, because that
    label is what `_source_label` returns and therefore what the metrics compare against.
    """
    import sys
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from srcs.chatbot import parse_urls_file

    web_labels = {e["label"] for e in parse_urls_file(str(root / "urls.txt")) if e["label"]}
    golden = json.loads((root / "docs/dpg/retrieval_eval/golden_set.json").read_text())
    for q in golden["queries"]:
        assert q["relevant"], f"{q['id']} has no relevant sources"
        for src in q["relevant"]:
            if src.startswith("knowledge/"):
                assert (root / src).exists(), f"{q['id']} references missing file {src}"
            else:
                assert src in web_labels, (
                    f"{q['id']} references {src!r}, which is not a declared source label in "
                    "urls.txt — did the LABEL change?")


def test_relabelled_queries_carry_their_evidence():
    """Ground truth was re-annotated after FAO 589 entered the corpus. Every addition must record
    WHY, quoting the passage that justified it — otherwise relabelling is indistinguishable from
    tuning the ruler to fit the result."""
    root = Path(__file__).resolve().parents[2]
    golden = json.loads((root / "docs/dpg/retrieval_eval/golden_set.json").read_text())
    annotated = [q for q in golden["queries"] if "annotation_note" in q]
    assert annotated, "expected re-annotated queries to be marked"
    for q in annotated:
        assert len(q["annotation_note"]) > 40, f"{q['id']} annotation is not substantive"


def test_golden_set_ids_are_unique():
    root = Path(__file__).resolve().parents[2]
    golden = json.loads((root / "docs/dpg/retrieval_eval/golden_set.json").read_text())
    ids = [q["id"] for q in golden["queries"]] + [q["id"] for q in golden["negative_controls"]]
    assert len(ids) == len(set(ids))

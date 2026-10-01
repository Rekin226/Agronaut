"""The multilingual golden set (#199) and the per-language scoring built on it.

Hermetic like test_retrieval_eval.py: plain lists and fake retrievers, no index, no model.
"""

import json
from pathlib import Path

import pytest

from scripts.query_language_eval import written_query
from scripts.retrieval_eval import by_language, merge_golden, run

_DIR = Path(__file__).resolve().parents[2] / "docs/dpg/retrieval_eval"
EN = json.loads((_DIR / "golden_set.json").read_text())
ML = json.loads((_DIR / "golden_set_multilingual.json").read_text())


def test_every_translation_points_at_a_real_english_entry():
    queries = {q["id"] for q in EN["queries"]}
    negatives = {n["id"] for n in EN["negative_controls"]}
    for q in ML["queries"]:
        assert q["of"] in queries, q["id"]
    for n in ML["negative_controls"]:
        assert n["of"] in negatives, n["id"]


def test_each_language_covers_the_whole_english_set():
    """A language missing some queries would be scored on an easier subset and look better."""
    for lang in ML["languages"]:
        assert {q["of"] for q in ML["queries"] if q["lang"] == lang} == \
            {q["id"] for q in EN["queries"]}, lang
        assert {n["of"] for n in ML["negative_controls"] if n["lang"] == lang} == \
            {n["id"] for n in EN["negative_controls"]}, lang


def test_translations_carry_no_ground_truth_of_their_own():
    """`relevant` lives only in golden_set.json, so relabelling it there reaches every language."""
    assert not any("relevant" in q for q in ML["queries"])


def test_ids_are_unique_across_both_files():
    ids = [q["id"] for q in EN["queries"] + EN["negative_controls"]
           + ML["queries"] + ML["negative_controls"]]
    assert len(ids) == len(set(ids))


def test_merge_inherits_relevant_from_the_english_original():
    merged = merge_golden(EN, ML)
    english = {q["id"]: q["relevant"] for q in EN["queries"]}
    for q in merged["queries"]:
        assert q["relevant"] == english[q.get("of", q["id"])]
    assert {q["lang"] for q in merged["queries"]} == {"en", *ML["languages"]}


def test_merge_rejects_a_translation_of_nothing():
    bad = {"queries": [{"id": "x-fr", "of": "no-such-id", "lang": "fr", "query": "q"}]}
    with pytest.raises(ValueError, match="no-such-id"):
        merge_golden(EN, bad)


def test_merge_without_translations_is_english_only():
    merged = merge_golden(EN, None)
    assert len(merged["queries"]) == len(EN["queries"])


def test_by_language_splits_scores_and_floor_behaviour():
    rows = [
        {"lang": "en", "hit": True, "precision": 1, "recall": 1, "rr": 1, "ap": 1,
         "raw_top_score": 0.9, "floor_returned_nothing": False},
        {"lang": "fr", "hit": False, "precision": 0, "recall": 0, "rr": 0, "ap": 0,
         "raw_top_score": 1.7, "floor_returned_nothing": True},
    ]
    negs = [{"lang": "en", "top_score": 1.4, "rejected_by_floor": True},
            {"lang": "fr", "top_score": 1.3, "rejected_by_floor": False}]
    out = by_language(rows, negs, 3)
    assert out["en"]["hit_rate"] == 1.0 and out["en"]["separable"] is True
    assert out["fr"]["hit_rate"] == 0.0 and out["fr"]["separable"] is False
    assert out["fr"]["floor_silenced_on_topic"] == 1
    assert out["en"]["floor_rejected_off_topic"] == 1


def test_run_multilingual_scores_every_language():
    merged = merge_golden(EN, ML)
    by_query = {q["query"]: q["relevant"] for q in merged["queries"]}

    def perfect(query, k):
        return [{"source": s, "score": 0.1, "text": "x"} for s in by_query.get(query, [])][:k]

    report = run(retrieve=perfect, multilingual=True)
    langs = report["summary"]["by_lang"]
    assert set(langs) == {"en", "fr", "zh"}
    assert all(r["hit_rate"] == 1.0 for r in langs.values())


def test_run_default_output_is_unchanged_by_the_multilingual_mode():
    """CI compares the English run against a saved baseline; it must not grow new keys."""
    report = run(retrieve=lambda q, k: [])
    assert "by_lang" not in report["summary"]
    assert all("lang" not in r for r in report["per_query"])


class _Msg:
    def __init__(self, calls):
        self.tool_calls = calls


def test_written_query_takes_the_models_argument():
    msg = _Msg([{"name": "search_knowledge_base", "args": {"query": "low dissolved oxygen"}}])
    assert written_query(msg, "mes poissons") == ("low dissolved oxygen", True)


def test_no_search_is_not_credited_with_the_growers_text():
    assert written_query(_Msg([]), "mes poissons") == ("mes poissons", False)
    other = _Msg([{"name": "fetch_site_climate", "args": {"query": "x"}}])
    assert written_query(other, "mes poissons")[1] is False

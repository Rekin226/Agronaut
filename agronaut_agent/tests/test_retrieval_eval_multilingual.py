"""The multilingual golden set must mirror the English one exactly, or per-language numbers
are not comparable with it.

These tests are hermetic — no index, no embedding model, no network. They pin the SET's
structure (one FR and one ZH-TW query per English query, identical labels, unique ids,
existing sources) and the `by_language` splitter the per-language eval mode runs on. The
SCORES per language are a measurement, not a test: they live in baseline_multilingual.json
and in the PR body.

The docstring test also pins the cheapest mitigation (issue #199 step 2): the
search_knowledge_base tool must tell the model to search in English, because the embedder
and the BM25 tokenizer are both English-only. If someone rewords that docstring into
vagueness, this test should catch it.
"""

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.retrieval_eval import by_language, load_multilingual  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ENGLISH = json.loads(
    (_ROOT / "docs/dpg/retrieval_eval/golden_set.json").read_text())
_ML = load_multilingual()


def _strip_lang(q: dict) -> dict:
    return {k: v for k, v in q.items() if k not in ("lang",)}


@pytest.fixture(scope="module")
def split():
    return by_language(_ML)


def test_every_english_query_has_one_fr_and_one_zh_mirror():
    en_ids = [q["id"] for q in _ENGLISH["queries"]]
    ml_queries = _ML["queries"]
    for lang in ("fr", "zh-Hant"):
        mirrored = [q["id"][: -len("-fr") if lang == "fr" else -len("-zh")] for q in ml_queries
                    if q["id"].endswith("-fr" if lang == "fr" else "-zh")]
        assert sorted(mirrored) == sorted(en_ids), f"{lang} does not mirror the English set 1:1"


def test_every_query_carry_its_language_tag():
    for q in _ML["queries"] + _ML["negative_controls"]:
        assert q.get("lang") in {"fr", "zh-Hant"}, f"{q['id']} has no lang tag"
        suffix = q["id"].rsplit("-", 1)[-1]
        expected = {"fr": "fr", "zh": "zh-Hant"}[suffix]
        assert q["lang"] == expected, f"{q['id']} lang tag contradicts its id suffix"


def test_mirror_labels_are_identical_to_english():
    """Ground truth is a property of the CORPUS, not of the query language: a translated
    query must name exactly the sources its English original names."""
    en = {q["id"]: q["relevant"] for q in _ENGLISH["queries"]}
    for q in _ML["queries"]:
        base_id = q["id"].rsplit("-", 1)[0]
        assert q["relevant"] == en[base_id], (
            f"{q['id']} labels differ from English {base_id} — a translation must not "
            "change ground truth")


def test_mirror_labels_exist_in_corpus():
    """Same contract as test_golden_set_references_only_real_sources, for the mirrors."""
    for q in _ML["queries"]:
        assert q["relevant"], f"{q['id']} has no relevant sources"
        for src in q["relevant"]:
            if src.startswith("knowledge/"):
                assert (_ROOT / src).exists(), f"{q['id']} references missing file {src}"
            else:
                # Web-source labels must match the English set (which is itself checked
                # against urls.txt) — a mirror re-typing the FAO label would silently cap recall.
                en = {x["id"]: x["relevant"] for x in _ENGLISH["queries"]}
                assert src in en[q["id"].rsplit("-", 1)[0]]


def test_negative_controls_mirror_too():
    en_neg = [q["id"] for q in _ENGLISH["negative_controls"]]
    for lang in ("fr", "zh-Hant"):
        mirrored = [q["id"].rsplit("-", 1)[0] for q in _ML["negative_controls"]
                    if q["id"].endswith("-fr" if lang == "fr" else "-zh")]
        assert sorted(mirrored) == sorted(en_neg)
    for q in _ML["negative_controls"]:
        assert "relevant" not in q, "negative controls must not grow a relevant set"


def test_ml_ids_are_unique():
    ids = [q["id"] for q in _ML["queries"]] + [q["id"] for q in _ML["negative_controls"]]
    assert len(ids) == len(set(ids))


def test_by_language_partitions_and_carries_negatives(split):
    assert set(split) == {"fr", "zh-Hant"}
    total = sum(len(b["queries"]) for b in split.values())
    assert total == len(_ML["queries"])
    assert len(split["fr"]["queries"]) == len(_ENGLISH["queries"])
    assert len(split["zh-Hant"]["queries"]) == len(_ENGLISH["queries"])
    # negatives ride along with their language bucket
    assert len(split["fr"]["negative_controls"]) == len(_ENGLISH["negative_controls"])
    assert len(split["zh-Hant"]["negative_controls"]) == len(_ENGLISH["negative_controls"])
    assert split["fr"]["k"] == _ML.get("k", 3)


def test_by_language_on_a_set_without_lang_tags_returns_empty():
    """A golden set without `lang` keys (e.g. the English one) splits to nothing — the
    per-language mode is opt-in per file, never inferred."""
    assert by_language(_ENGLISH) == {}


def test_by_language_buckets_are_runnable_shapes():
    """Each bucket must have exactly the keys scripts.retrieval_eval.run(golden=...) reads."""
    for bucket in by_language(_ML).values():
        assert set(bucket) == {"k", "queries", "negative_controls"}
        for q in bucket["queries"]:
            assert {"id", "query", "relevant"} <= set(q)


def test_english_golden_set_untouched_by_multilingual_work():
    """The English set is the baseline everything compares against; adding the multilingual
    file must not have shifted it."""
    assert len(_ENGLISH["queries"]) == 33
    assert len(_ENGLISH["negative_controls"]) == 10


def _tool_docstring():
    """The @tool decorator wraps the function in a StructuredTool whose OWN docstring is
    langchain boilerplate; the real docstring is on the wrapped `.func` (and mirrored into
    `.description`, which is what the LLM actually sees)."""
    import inspect

    from agronaut_agent import tools
    fn = tools.search_knowledge_base
    doc = inspect.getdoc(getattr(fn, "func", None) or fn)
    assert doc, "search_knowledge_base has no docstring"
    return doc


def test_tool_docstring_directs_the_model_to_search_in_english():
    """Issue #199 step 2: the docstring is the cheapest mitigation. It must tell the model
    to translate the user's problem into English technical vocabulary before searching."""
    doc = _tool_docstring().lower()
    assert "english" in doc, "docstring no longer says the knowledge base is English-indexed"
    assert "translate" in doc, (
        "docstring no longer tells the model to translate the user's problem first")


def test_tool_docstring_cites_the_measurement():
    """The measurement claim is cited in the `#` comment block above the docstring, not in
    the model-facing docstring itself (maintainer review, #203): guidance the LLM reads
    stays lean; the citation belongs to the codebase."""
    import agronaut_agent.tools as tools_mod
    src = inspect.getsource(tools_mod)  # the module, not the StructuredTool
    assert "golden_set_multilingual.json" in src, (
        "search_knowledge_base no longer cites the measurement anywhere")
    assert "golden_set_multilingual.json" not in _tool_docstring(), (
        "citation moved back into the model-facing docstring; keep it in the # comment")

"""The retrieval constants must be measured on the corpus that ships (#184).

On 2026-08-26 the Goddek book took the corpus from 1354 to 3941 chunks, and the floor, cap and
beta tuned on the smaller corpus were wrong for six days before anyone noticed: every test
stayed green because none of them knew what the constants had been measured on. The baseline
now records a fingerprint of its corpus, and the first test below fails CI when the shipped
corpus has moved too far from it.

Hermetic: hashing files needs no index, model or network.

What this cannot see: the chunking code, and the fetched pages behind each URL. A change to
either can still move the constants without tripping anything here.
"""

import json

from scripts.retrieval_eval import (
    CURRENT_BASELINE,
    corpus_drift,
    corpus_fingerprint,
)

# --- the guard -----------------------------------------------------------------------------


def test_the_shipped_corpus_is_the_one_the_retrieval_constants_were_tuned_on():
    recorded = json.loads(CURRENT_BASELINE.read_text()).get("corpus")
    reasons = corpus_drift(recorded, corpus_fingerprint())
    assert not reasons, (
        "The corpus no longer matches the baseline the retrieval constants were tuned on:\n  "
        + "\n  ".join(reasons)
        + "\n\nRe-measure before merging, since a stale floor silently refuses real questions:"
        "\n  python -m scripts.retrieval_sweep"
        "\n  python -m scripts.retrieval_eval --save docs/dpg/retrieval_eval/baseline_<date>.json"
        "\nthen update the constants in agronaut_agent/rag.py if the sweep moved them, and point"
        " CURRENT_BASELINE in scripts/retrieval_eval.py at the new file."
    )


# --- the fingerprint -----------------------------------------------------------------------


def _corpus(tmp_path, urls="https://example.org/a.pdf\n", guides=None):
    (tmp_path / "urls.txt").write_text(urls)
    kb = tmp_path / "knowledge"
    kb.mkdir(exist_ok=True)
    for name, text in (guides or {"ph.md": "x" * 1000}).items():
        (kb / name).write_text(text)
    return tmp_path


def test_fingerprint_is_deterministic(tmp_path):
    root = _corpus(tmp_path)
    assert corpus_fingerprint(root) == corpus_fingerprint(root)


def test_fingerprint_moves_when_a_guide_is_edited(tmp_path):
    root = _corpus(tmp_path)
    before = corpus_fingerprint(root)
    (root / "knowledge" / "ph.md").write_text("y" * 1000)
    after = corpus_fingerprint(root)
    assert after["knowledge_sha256"] != before["knowledge_sha256"]
    assert after["knowledge_bytes"] == before["knowledge_bytes"]


def test_fingerprint_counts_only_what_the_loader_indexes(tmp_path):
    """srcs/chatbot.py loads .md and .txt; anything else in knowledge/ never reaches the index."""
    root = _corpus(tmp_path)
    before = corpus_fingerprint(root)
    (root / "knowledge" / "diagram.png").write_bytes(b"\x89PNG")
    assert corpus_fingerprint(root) == before


# --- the drift rules -----------------------------------------------------------------------


def test_a_changed_source_list_always_counts(tmp_path):
    recorded = corpus_fingerprint(_corpus(tmp_path))
    current = corpus_fingerprint(_corpus(tmp_path, urls="https://example.org/a.pdf\n"
                                                        "https://example.org/book.pdf\n"))
    assert any("urls.txt" in r for r in corpus_drift(recorded, current))


def test_one_small_guide_edit_does_not_block_a_contributor(tmp_path):
    recorded = corpus_fingerprint(_corpus(tmp_path, guides={"ph.md": "x" * 1000}))
    current = corpus_fingerprint(_corpus(tmp_path, guides={"ph.md": "x" * 1050}))
    assert corpus_drift(recorded, current) == []


def test_guide_drift_past_the_trigger_counts(tmp_path):
    recorded = corpus_fingerprint(_corpus(tmp_path, guides={"ph.md": "x" * 1000}))
    current = corpus_fingerprint(_corpus(tmp_path, guides={"ph.md": "x" * 1000,
                                                           "new.md": "y" * 200}))
    reasons = corpus_drift(recorded, current)
    assert len(reasons) == 1 and "knowledge/" in reasons[0]


def test_drift_is_measured_against_the_baseline_not_the_last_pr(tmp_path):
    """Three small guides that each pass alone must not add up to an unmeasured corpus."""
    recorded = corpus_fingerprint(_corpus(tmp_path, guides={"ph.md": "x" * 1000}))
    guides = {"ph.md": "x" * 1000}
    for i in range(3):
        guides[f"g{i}.md"] = "y" * 50
    assert corpus_drift(recorded, corpus_fingerprint(_corpus(tmp_path, guides=guides)))


def test_a_baseline_without_a_fingerprint_is_not_trusted(tmp_path):
    current = corpus_fingerprint(_corpus(tmp_path))
    assert corpus_drift(None, current)
    assert corpus_drift({}, current)

"""The built knowledge index is cached, so the corpus fingerprint must be exactly right.

Rebuilding the index costs a network fetch per source plus embedding every chunk. That was
tolerable for 365 chunks of HTML, but the corpus is growing toward publication-scale PDFs — FAO
589 alone is 56 MB, 275 pages, 47 s just to download. Caching it is what makes such a source
viable at all.

A cache is only safe if it invalidates when it should. A stale index is worse than a slow one:
it silently answers from a corpus the operator has already changed, and nothing in the output
reveals it. These tests pin the invalidation rules.
"""

import pathlib

import pytest

from srcs import chatbot


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A throwaway corpus whose fingerprint we can perturb one variable at a time."""
    kb = tmp_path / "knowledge"
    kb.mkdir()
    (kb / "a.md").write_text("# Ammonia\nKeep ammonia low.")
    (kb / "b.md").write_text("# pH\nKeep pH stable.")
    urls = tmp_path / "urls.txt"
    urls.write_text("CAT|https://example.org/x|Example|CC BY 4.0\n")
    monkeypatch.setattr(chatbot, "KNOWLEDGE_DIR", str(kb))
    monkeypatch.setattr(chatbot, "URL_FILE", str(urls))
    return {"kb": kb, "urls": urls}


def test_fingerprint_is_stable_for_an_unchanged_corpus(corpus):
    assert chatbot._corpus_fingerprint() == chatbot._corpus_fingerprint()


def test_editing_a_knowledge_file_invalidates_the_cache(corpus):
    before = chatbot._corpus_fingerprint()
    (corpus["kb"] / "a.md").write_text("# Ammonia\nKeep ammonia BELOW 0.5 mg/L.")
    assert chatbot._corpus_fingerprint() != before


def test_adding_a_knowledge_file_invalidates_the_cache(corpus):
    before = chatbot._corpus_fingerprint()
    (corpus["kb"] / "c.md").write_text("# Nitrite\nNitrite stresses fish.")
    assert chatbot._corpus_fingerprint() != before


def test_removing_a_knowledge_file_invalidates_the_cache(corpus):
    before = chatbot._corpus_fingerprint()
    (corpus["kb"] / "b.md").unlink()
    assert chatbot._corpus_fingerprint() != before


def test_renaming_a_file_invalidates_even_with_identical_content(corpus):
    """The filename IS the citation label an operator sees, so a rename changes the output
    even when not one byte of the text differs."""
    before = chatbot._corpus_fingerprint()
    (corpus["kb"] / "a.md").rename(corpus["kb"] / "ammonia.md")
    assert chatbot._corpus_fingerprint() != before


def test_changing_urls_invalidates_the_cache(corpus):
    before = chatbot._corpus_fingerprint()
    corpus["urls"].write_text("CAT|https://example.org/y|Other|CC BY 4.0\n")
    assert chatbot._corpus_fingerprint() != before


def test_changing_the_embedding_model_invalidates_the_cache(corpus, monkeypatch):
    """Vectors from a different model are not comparable, so reusing them would silently
    corrupt every distance the retriever computes — including the relevance floor."""
    before = chatbot._corpus_fingerprint()
    monkeypatch.setattr(chatbot, "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    assert chatbot._corpus_fingerprint() != before


@pytest.mark.parametrize("attr,value", [("CHUNK_SIZE", 1200), ("CHUNK_OVERLAP", 250)])
def test_changing_chunk_geometry_invalidates_the_cache(corpus, monkeypatch, attr, value):
    before = chatbot._corpus_fingerprint()
    monkeypatch.setattr(chatbot, attr, value)
    assert chatbot._corpus_fingerprint() != before


def test_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AGRONAUT_INDEX_CACHE", "off")
    assert not chatbot._index_cache_enabled()
    assert chatbot._load_cached_index("anyfingerprint") is None
    monkeypatch.setenv("AGRONAUT_INDEX_CACHE", "on")
    assert chatbot._index_cache_enabled()


def test_missing_cache_returns_none_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.delenv("AGRONAUT_INDEX_CACHE", raising=False)
    monkeypatch.setattr(chatbot, "INDEX_CACHE_DIR", tmp_path / "nope")
    assert chatbot._load_cached_index("deadbeef") is None


def test_expired_cache_is_ignored(monkeypatch, tmp_path):
    """A TTL bounds staleness the fingerprint cannot see: a web publisher can edit a source
    without urls.txt changing, and detecting that would require the very fetch the cache avoids."""
    import os, time
    monkeypatch.delenv("AGRONAUT_INDEX_CACHE", raising=False)
    monkeypatch.setattr(chatbot, "INDEX_CACHE_DIR", tmp_path)
    d = tmp_path / "abc"
    d.mkdir()
    (d / "index.faiss").write_bytes(b"stale")
    old = time.time() - (chatbot.INDEX_CACHE_TTL + 3600)
    os.utime(d / "index.faiss", (old, old))
    assert chatbot._load_cached_index("abc") is None


def test_corrupt_cache_falls_through_to_a_rebuild(monkeypatch, tmp_path):
    """An unreadable cache must never be the reason retrieval is unavailable."""
    monkeypatch.delenv("AGRONAUT_INDEX_CACHE", raising=False)
    monkeypatch.setattr(chatbot, "INDEX_CACHE_DIR", tmp_path)
    d = tmp_path / "abc"
    d.mkdir()
    (d / "index.faiss").write_bytes(b"this is not a FAISS index")
    assert chatbot._load_cached_index("abc") is None


def test_save_failure_is_not_fatal(monkeypatch, tmp_path):
    """Caching is an optimisation. If it fails, the freshly built index must still be returned."""
    monkeypatch.delenv("AGRONAUT_INDEX_CACHE", raising=False)
    monkeypatch.setattr(chatbot, "INDEX_CACHE_DIR", tmp_path / "x")

    class _Boom:
        def save_local(self, path):
            raise OSError("disk full")

    chatbot._save_cached_index(_Boom(), "abc")   # must not raise


def test_cache_is_not_committed():
    """The index is derived data — rebuildable from knowledge/ and urls.txt, and large."""
    root = pathlib.Path(__file__).resolve().parents[2]
    assert "data/.index_cache/" in (root / ".gitignore").read_text()


@pytest.mark.parametrize("var", ["AGRONAUT_MD_HEADERS", "AGRONAUT_MD_CRUMB", "AGRONAUT_PDF_CLEAN"])
def test_chunking_flags_invalidate_the_cache(corpus, monkeypatch, var):
    """A chunking flag changes what text lands in each vector, so an index built under one setting
    is wrong under the other. Leaving these out of the fingerprint made an ablation appear to
    measure a setting while it was actually re-reading the previous run's index.

    AGRONAUT_MD_CRUMB is here because it was MISSED the first time this was fixed, and the only
    thing that revealed it was an ablation reporting byte-identical numbers for two variants that
    should have differed. Every flag that changes chunk text belongs in the fingerprint.
    """
    monkeypatch.setenv(var, "on")
    on = chatbot._corpus_fingerprint()
    monkeypatch.setenv(var, "off")
    assert chatbot._corpus_fingerprint() != on

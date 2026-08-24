"""Lazy knowledge-base retrieval, reusing the existing FAISS RAG in srcs/chatbot.py.

The index (local knowledge/*.md + cited URLs, embedded once) is expensive to build, so
it is constructed on first use and cached for the process. If it can't be built (offline,
missing deps), retrieval degrades to an empty result rather than crashing the agent.
"""

from __future__ import annotations

import logging
import os

# Maximum FAISS L2 distance a passage may have and still be offered to the model as context.
# LOWER is closer; anything above this is treated as "nothing relevant matched".
#
# Measured on docs/dpg/retrieval_eval/golden_set.json (33 operator queries, 10 off-topic controls):
#   - rejects 9/10 off-topic queries
#   - silences 0/33 real queries
#   - hit_rate, recall@3, precision@3, MRR and MAP@3 all UNCHANGED
# So it removes most grounded-looking hallucinations at no measured retrieval cost.
#
# It is a heuristic, NOT a guarantee, and the margin is thinner than it first appears. With only
# three controls the bands looked cleanly separated (worst on-topic 1.554 vs closest off-topic
# 1.717). Widening to ten controls surfaced "best way to remove red wine from a carpet" at 1.553 —
# INSIDE the on-topic band — because stain removal is genuinely close in embedding space to
# algae_control.md's advice on removing growth from surfaces. The bands overlap; a query near the
# boundary can still fall the wrong way.
#
# The durable fix for that overlap is a better-separated corpus and a complementary keyword
# signal, not a cleverer threshold. This number is a property of THIS embedding model over THIS
# corpus and does not port: `python -m scripts.retrieval_eval` reprints the separation on every
# run and says outright when the bands overlap. Re-read it after any corpus change; override with
# AGRONAUT_RELEVANCE_MAX_DISTANCE (or "off" to disable).
_DEFAULT_MAX_DISTANCE = 1.65

_INDEX = None          # cached FAISS index (or None if unavailable)
_TRIED = False         # don't retry a failed/slow build every call


def _get_index():
    global _INDEX, _TRIED
    if _TRIED:
        return _INDEX
    _TRIED = True
    try:
        import requests_cache
        import srcs.chatbot as core

        requests_cache.install_cache(core.CACHE_NAME, expire_after=core.CACHE_EXPIRE)
        _INDEX = core.build_rag_index_from_urls()
    except Exception as exc:  # offline, missing deps, fetch failure — degrade gracefully
        logging.warning("Knowledge index unavailable: %s", exc)
        _INDEX = None
    return _INDEX


def _source_label(metadata: dict) -> str:
    """Human-citable label for a retrieved passage. Local files are shown repo-relative
    (knowledge/...), web docs prefer their curated label over the raw URL."""
    label = metadata.get("url_label")
    if label:
        return str(label)
    src = metadata.get("source") or metadata.get("source_path")
    if not src:
        return "unattributed"
    src = str(src)
    if "knowledge/" in src.replace("\\", "/"):
        return "knowledge/" + src.replace("\\", "/").rsplit("knowledge/", 1)[1]
    return src


_UNAVAILABLE = ("KNOWLEDGE_UNAVAILABLE — no curated context retrieved; answer from general "
                "husbandry knowledge and say so.")
_NO_MATCH = "No matching passages in the knowledge base for that query."


def max_distance() -> float:
    """The configured relevance floor. AGRONAUT_RELEVANCE_MAX_DISTANCE=off disables it."""
    raw = os.getenv("AGRONAUT_RELEVANCE_MAX_DISTANCE", "").strip().lower()
    if raw in {"off", "none", "disabled"}:
        return float("inf")
    try:
        return float(raw) if raw else _DEFAULT_MAX_DISTANCE
    except ValueError:
        logging.warning("Bad AGRONAUT_RELEVANCE_MAX_DISTANCE=%r; using default", raw)
        return _DEFAULT_MAX_DISTANCE


def retrieve(query: str, k: int = 3, max_dist: float | None = None) -> list[dict]:
    """Structured retrieval: the ranked passages for `query`, each as
    {"text", "source", "score"}.

    Split out from search() so retrieval quality can be MEASURED (scripts/retrieval_eval.py)
    rather than only rendered. The similarity score is carried through instead of discarded —
    a relevance floor needs it, and so does any reranking stage.

    Passages further than `max_dist` are dropped. Without that floor the retriever always returns
    its top k, so an off-topic question gets three confident, source-labelled passages that cannot
    answer it — the exact shape of a grounded-looking hallucination. Pass float("inf") to measure
    unfiltered behaviour.

    Returns [] both when the index is unavailable and when nothing survives filtering; callers
    that must tell those apart should check `index_available()`.
    """
    limit = max_distance() if max_dist is None else max_dist
    index = _get_index()
    if index is None:
        return []
    import srcs.chatbot as core

    try:
        pairs = index.similarity_search_with_score(query, k=max(k * 2, k))
    except Exception as exc:
        logging.warning("knowledge search failed: %s", exc)
        return []
    hits = []
    for doc, score in pairs:
        if core._is_boilerplate_text(doc.page_content):
            continue
        if float(score) > limit:
            continue
        hits.append({
            "text": doc.page_content.strip(),
            "source": _source_label(doc.metadata),
            "score": float(score),
        })
        if len(hits) >= k:
            break
    return hits


def index_available() -> bool:
    """Whether a knowledge index could be built at all — distinguishes 'nothing matched'
    from 'retrieval is broken/offline', which the caller reports differently."""
    return _get_index() is not None


def search(query: str, k: int = 3) -> str:
    """Return retrieved knowledge passages for `query`, each labeled with its source —
    citation is enforced here, not left to the model — or a clear 'no context' note."""
    if not index_available():
        return _UNAVAILABLE
    hits = retrieve(query, k=k)
    if not hits:
        return _NO_MATCH
    return "\n\n".join(f"[source: {h['source']}]\n{h['text']}" for h in hits)

"""Lazy knowledge-base retrieval, reusing the existing FAISS RAG in srcs/chatbot.py.

The index (local knowledge/*.md + cited URLs, embedded once) is expensive to build, so
it is constructed on first use and cached for the process. If it can't be built (offline,
missing deps), retrieval degrades to an empty result rather than crashing the agent.
"""

from __future__ import annotations

import logging

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


def search(query: str, k: int = 3) -> str:
    """Return retrieved knowledge passages for `query`, each labeled with its source —
    citation is enforced here, not left to the model — or a clear 'no context' note."""
    index = _get_index()
    if index is None:
        return "KNOWLEDGE_UNAVAILABLE — no curated context retrieved; answer from general husbandry knowledge and say so."
    import srcs.chatbot as core

    try:
        pairs = index.similarity_search_with_score(query, k=max(k * 2, k))
    except Exception as exc:
        logging.warning("knowledge search failed: %s", exc)
        return "KNOWLEDGE_UNAVAILABLE — no curated context retrieved; answer from general husbandry knowledge and say so."
    docs = [d for (d, _score) in pairs if not core._is_boilerplate_text(d.page_content)][:k]
    if not docs:
        return "No matching passages in the knowledge base for that query."
    return "\n\n".join(
        f"[source: {_source_label(d.metadata)}]\n{d.page_content.strip()}" for d in docs
    )

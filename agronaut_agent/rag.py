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


def search(query: str, k: int = 3) -> str:
    """Return retrieved knowledge passages for `query`, or a clear 'no context' note."""
    index = _get_index()
    if index is None:
        return "KNOWLEDGE_UNAVAILABLE — no curated context retrieved; answer from general husbandry knowledge and say so."
    import srcs.chatbot as core

    context = core.retrieve_context(index, query, k=k)
    return context.strip() or "No matching passages in the knowledge base for that query."

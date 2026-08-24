"""Lazy knowledge-base retrieval, reusing the existing FAISS RAG in srcs/chatbot.py.

The index (local knowledge/*.md + cited URLs, embedded once) is expensive to build, so
it is constructed on first use and cached for the process. If it can't be built (offline,
missing deps), retrieval degrades to an empty result rather than crashing the agent.
"""

from __future__ import annotations

import logging
import os
import re

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
_BM25 = None           # cached (BM25Okapi, [Document]) over the SAME chunks as _INDEX
_BM25_TRIED = False

# Reciprocal Rank Fusion constant. Higher values flatten the influence of a single top rank:
# at k=0 a first place is worth 10x a tenth place, at k=60 about 1.2x. 60 is the value from the
# original RRF paper and the usual production default.
_RRF_K = 60

# Weight on the semantic ranking; the keyword ranking gets (1 - beta). Aquaponics queries are
# dense with exact terms an embedding can blur together — "nitrite" vs "nitrate", species names,
# "Ich", "FCR" — so the keyword side is given real weight rather than a token share.
_DEFAULT_BETA = 0.5


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


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _get_bm25():
    """A BM25 index over exactly the chunks already in the FAISS index.

    Built from the FAISS docstore rather than by re-reading the corpus, so the two retrievers can
    never drift apart — a keyword hit always corresponds to a real, embedded, citable chunk.
    """
    global _BM25, _BM25_TRIED
    if _BM25_TRIED:
        return _BM25
    _BM25_TRIED = True
    index = _get_index()
    if index is None:
        return None
    try:
        from rank_bm25 import BM25Okapi
        docs = list(index.docstore._dict.values())
        if not docs:
            return None
        _BM25 = (BM25Okapi([_tokenize(d.page_content) for d in docs]), docs)
    except Exception as exc:  # noqa: BLE001 — keyword search is additive; never break retrieval
        logging.warning("BM25 index unavailable, falling back to dense-only: %s", exc)
        _BM25 = None
    return _BM25


def hybrid_enabled() -> bool:
    """Hybrid retrieval ships DISABLED, on evidence — see docs/dpg/retrieval_eval/hybrid_sweep.json.

    The received wisdom is that BM25 + RRF beats dense-only. Measured on this corpus it does not,
    at any weighting:

        beta   hit    recall  prec    MRR     MAP
        0.50   0.939  0.924   0.485   0.874   0.861
        0.70   0.970  0.919   0.500   0.899   0.862
        0.95   0.970  0.904   0.581   0.884   0.838
        dense  0.970  0.919   0.626   0.909   0.869

    Dense-only equals or beats every setting on every metric; the best hybrid configuration loses
    0.126 of precision to match it elsewhere. The reason is the corpus, not the technique: 362
    chunks that all share one vocabulary domain, so common aquaponics terms match across many
    files and keyword rank carries little information. BM25 does fix the one dense miss
    ("bright green and cloudy" -> algae_control.md) but breaks two other queries doing it.

    The implementation is kept and tested because this is expected to invert as the corpus grows
    and diversifies — adding FAO 589 alone would roughly quadruple it. Re-run the sweep after any
    substantial corpus change and flip this default if the numbers move. Enable with
    AGRONAUT_HYBRID=on.
    """
    return os.getenv("AGRONAUT_HYBRID", "").lower() in {"on", "1", "true"}


def beta() -> float:
    """Weight on the semantic ranking, 0..1. The keyword ranking gets the remainder."""
    try:
        b = float(os.getenv("AGRONAUT_HYBRID_BETA", "") or _DEFAULT_BETA)
    except ValueError:
        return _DEFAULT_BETA
    return min(max(b, 0.0), 1.0)


def rrf_fuse(dense: list, sparse: list, b: float | None = None, rrf_k: int = _RRF_K) -> list:
    """Reciprocal Rank Fusion of two ranked lists of chunk keys.

    RRF combines by RANK, never by score, which is what makes it usable here: a FAISS L2 distance
    and a BM25 relevance score are not on comparable scales and normalising them against each
    other would be arbitrary. A document earns beta/(k+rank) from the semantic list and
    (1-beta)/(k+rank) from the keyword list, and the two are summed.
    """
    b = beta() if b is None else b
    scores: dict = {}
    for rank, key in enumerate(dense, start=1):
        scores[key] = scores.get(key, 0.0) + b / (rrf_k + rank)
    for rank, key in enumerate(sparse, start=1):
        scores[key] = scores.get(key, 0.0) + (1.0 - b) / (rrf_k + rank)
    return [key for key, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


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


_POOL = 20      # candidates drawn from each retriever before fusion


def retrieve(query: str, k: int = 3, max_dist: float | None = None,
             hybrid: bool | None = None) -> list[dict]:
    """Structured retrieval: the ranked passages for `query`, each as
    {"text", "source", "score"}.

    Split out from search() so retrieval quality can be MEASURED (scripts/retrieval_eval.py)
    rather than only rendered. The similarity score is carried through instead of discarded —
    a relevance floor needs it, and so would any reranking stage.

    Retrieval is hybrid: a semantic pool and a BM25 keyword pool are fused by reciprocal rank.
    The two signals fail differently, which is the entire point. The one query dense retrieval
    missed outright — "my water has gone bright green and cloudy" — is ranked second by BM25,
    because `algae_control.md` says "green water" in as many words while the embedding drifted
    toward other water-appearance problems.

    THE RELEVANCE FLOOR REMAINS A DENSE-SIDE DECISION, and deliberately so. It is calibrated on
    L2 distance, a BM25-only hit has no distance to compare, and BM25 will always rank SOMETHING
    first however off-topic the question. So the floor decides IF the corpus can answer at all;
    fusion only decides the order of what it returns. If nothing clears the floor, nothing is
    returned, whatever the keyword scores say.

    Returns [] both when the index is unavailable and when nothing survives filtering; callers
    that must tell those apart should check `index_available()`.
    """
    limit = max_distance() if max_dist is None else max_dist
    index = _get_index()
    if index is None:
        return []
    import srcs.chatbot as core

    try:
        pairs = index.similarity_search_with_score(query, k=max(_POOL, k * 2))
    except Exception as exc:
        logging.warning("knowledge search failed: %s", exc)
        return []

    # Dense candidates: boilerplate removed, relevance floor applied. This is the gate.
    dense: list = []
    by_key: dict = {}
    for doc, score in pairs:
        if core._is_boilerplate_text(doc.page_content):
            continue
        if float(score) > limit:
            continue
        key = (_source_label(doc.metadata), doc.page_content[:120])
        if key not in by_key:
            by_key[key] = {"text": doc.page_content.strip(),
                           "source": _source_label(doc.metadata), "score": float(score)}
            dense.append(key)
    if not dense:
        return []       # the corpus cannot answer this; keyword scores must not override that

    use_hybrid = hybrid_enabled() if hybrid is None else hybrid
    if not use_hybrid:
        return [by_key[key] for key in dense[:k]]

    sparse: list = []
    bm = _get_bm25()
    if bm is not None:
        bm25, docs = bm
        try:
            scores = bm25.get_scores(_tokenize(query))
            order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)[:_POOL]
            for i in order:
                if scores[i] <= 0:
                    continue
                doc = docs[i]
                if core._is_boilerplate_text(doc.page_content):
                    continue
                key = (_source_label(doc.metadata), doc.page_content[:120])
                if key not in by_key:
                    # A keyword-only hit carries no L2 distance. Record it as exactly that
                    # rather than inventing a comparable number.
                    by_key[key] = {"text": doc.page_content.strip(),
                                   "source": _source_label(doc.metadata), "score": None}
                sparse.append(key)
        except Exception as exc:  # noqa: BLE001 — degrade to dense-only, never break the turn
            logging.warning("BM25 scoring failed: %s", exc)
            sparse = []

    fused = rrf_fuse(dense, sparse) if sparse else dense
    return [by_key[key] for key in fused[:k]]


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

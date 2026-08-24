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
# Measured on docs/dpg/retrieval_eval/golden_set.json (33 operator queries, 10 off-topic controls),
# corpus of 1354 chunks: rejects 8/10 off-topic queries, silences 0/33 real ones.
#
# THE BANDS OVERLAP AND CANNOT BE SEPARATED. Worst on-topic distance is 1.548; the CLOSEST
# off-topic match is 1.426 ("best way to remove red wine from a carpet", which lands near
# algae_control.md's advice on removing growth from surfaces). Adding FAO 589 widened this gap in
# the wrong direction — a 275-page book covering everything from plumbing to food safety is
# semantically near almost any question. No single global threshold can catch that query without
# also silencing real ones.
#
# 1.65 rather than a tighter 1.58: the tighter value would reject one more control (neg-06, at
# 1.616) but leaves only 0.032 of headroom above the worst real query, versus 0.10 at 1.65. On a
# 33-query sample that trades a threefold cut in safety margin for one extra rejection, and
# "silences 0 real queries" is the property that must not break. A real question is refused
# service; an off-topic one merely gets an honest "no matching passages".
#
# This number is a property of THIS embedding model over THIS corpus and does not port.
# `python -m scripts.retrieval_eval` reprints the separation on every run and says outright when
# the bands overlap. Re-read it after any corpus change. Override with
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

# Weight on the semantic ranking; the keyword ranking gets (1 - beta).
#
# 0.90 is measured, not chosen. On the 362-chunk corpus hybrid LOST at every weighting and shipped
# disabled. Adding FAO 589 grew the corpus to 1354 chunks, and re-running the sweep — which the
# recorded evidence explicitly said to do after substantial corpus growth — flipped the result:
#
#     beta   hit    recall  prec    MRR     MAP
#     0.50   0.758  0.727   0.444   0.561   0.543
#     0.70   0.818  0.773   0.475   0.646   0.606
#     0.90   0.848  0.788   0.495   0.692   0.636   <- ships
#     dense  0.848  0.697   0.490   0.682   0.545
#
# Note how LITTLE keyword weight is right: 10%. Enough to break ties a 992-chunk book would
# otherwise win on volume, not enough to let common aquaponics vocabulary dominate the ranking.
# At 0.5 — the intuitive "balanced" setting — hybrid is still clearly worse than dense.
_DEFAULT_BETA = 0.90


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
    """Hybrid retrieval ships ENABLED — see docs/dpg/retrieval_eval/hybrid_sweep.json.

    It did not start that way, and the reversal is the useful part. On the original 362-chunk
    corpus hybrid lost to dense-only at EVERY weighting and was shipped disabled, with the
    evidence recorded and a note to re-run the sweep after substantial corpus growth. Adding FAO
    589 took the corpus to 1354 chunks; re-running the sweep flipped the decision (MAP +0.091,
    recall +0.091 at beta=0.90).

    Nothing about the technique changed. The corpus did. A 992-chunk book competing with 82 chunks
    of targeted operator guidance is precisely the situation a keyword signal is for: it breaks
    ties that dense similarity resolves by volume. Disable with AGRONAUT_HYBRID=off.
    """
    return os.getenv("AGRONAUT_HYBRID", "").lower() not in {"off", "0", "false"}


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

# At most this many passages from any ONE source in a single result set.
#
# The observed failure when a 992-chunk book joined an 82-chunk curated corpus was not subtle:
# FAO 589 took all three slots on 10 of 33 queries, so a targeted operator answer that existed in
# knowledge/ never reached the model. Ranking alone cannot fix that — the book genuinely IS
# similar, and has twelve times as many chances to be. A per-source cap spends one slot on breadth
# instead, which is what a researcher does by reflex: read two sources, not three pages of one.
#
# Measured (1354-chunk corpus, hybrid on):
#
#     cap    hit    recall  prec    MRR     MAP
#     1      0.970  0.919   0.404   0.747   0.710
#     2      0.939  0.894   0.540   0.737   0.697   <- ships
#     none   0.848  0.788   0.495   0.692   0.636
#
# cap=1 restores the pre-FAO hit_rate exactly and wins MRR/MAP, so it is a defensible choice and
# is one env var away. It ships at 2 because part of cap=1's advantage is an artefact of the
# metric rather than a real gain: `relevant` is labelled at DOCUMENT granularity, so one FAO
# passage scores identically to three, and the metric cannot see that some queries (feeding rates,
# pest treatments) are genuinely best answered by several passages from the same chapter. cap=2
# keeps most of the recall while returning materially less irrelevant context to the model
# (precision 0.540 vs 0.404), and still allows a source with real depth to contribute twice.
#
# Set AGRONAUT_MAX_PER_SOURCE=1 to prioritise coverage, or 0 to disable the cap entirely.
_MAX_PER_SOURCE = 2


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
    return [by_key[key] for key in _diversify(fused, by_key, k)]


def max_source_cap() -> int:
    """Configured per-source cap; 0 disables it."""
    raw = os.getenv("AGRONAUT_MAX_PER_SOURCE", "").strip()
    if not raw:
        return _MAX_PER_SOURCE
    try:
        return max(int(raw), 0)
    except ValueError:
        logging.warning("Bad AGRONAUT_MAX_PER_SOURCE=%r; using default", raw)
        return _MAX_PER_SOURCE


def _diversify(ranked: list, by_key: dict, k: int, max_per_source: int | None = None) -> list:
    """Take the top k, allowing at most `max_per_source` passages from any single source.

    Applied AFTER fusion, so relevance still decides the order and diversity only decides who is
    displaced. A source that is genuinely the only answer still fills its cap; it simply cannot
    fill the entire window. If the cap cannot be honoured — because too few distinct sources
    matched at all — the remaining slots are filled from the original ranking rather than returned
    short, since a capped-but-empty result would be worse than a repetitive one.
    """
    cap = max_source_cap() if max_per_source is None else max_per_source
    if cap <= 0:
        return ranked[:k]
    picked, counts = [], {}
    for key in ranked:
        src = by_key[key]["source"]
        if counts.get(src, 0) >= cap:
            continue
        counts[src] = counts.get(src, 0) + 1
        picked.append(key)
        if len(picked) == k:
            return picked
    for key in ranked:                      # backfill rather than return a short result
        if key not in picked:
            picked.append(key)
            if len(picked) == k:
                break
    return picked[:k]


def index_available() -> bool:
    """Whether a knowledge index could be built at all — distinguishes 'nothing matched'
    from 'retrieval is broken/offline', which the caller reports differently."""
    return _get_index() is not None


def search_with_stats(query: str, k: int = 3) -> tuple[str, dict]:
    """search(), plus non-identifying telemetry about how the retrieval went.

    The stats deliberately contain NO query text and NO passage text — only shape and timing. The
    production question this answers is not "what did people ask" but "is the retriever behaving
    the way the golden set says it should": how often the floor fires, how far the closest match
    typically sits, how long retrieval takes. A drift in those is the earliest signal that the
    corpus or the model has moved out from under the calibrated threshold.
    """
    import time
    t0 = time.perf_counter()
    if not index_available():
        return _UNAVAILABLE, {"outcome": "unavailable", "n_results": 0,
                              "latency_ms": int((time.perf_counter() - t0) * 1000)}
    hits = retrieve(query, k=k)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    scored = [h["score"] for h in hits if h.get("score") is not None]
    stats = {
        "outcome": "hit" if hits else "no_match",
        "n_results": len(hits),
        "k": k,
        "latency_ms": latency_ms,
        "top_score": round(min(scored), 3) if scored else None,
        "hybrid": hybrid_enabled(),
    }
    if not hits:
        return _NO_MATCH, stats
    return "\n\n".join(f"[source: {h['source']}]\n{h['text']}" for h in hits), stats


def search(query: str, k: int = 3) -> str:
    """Return retrieved knowledge passages for `query`, each labeled with its source —
    citation is enforced here, not left to the model — or a clear 'no context' note."""
    return search_with_stats(query, k=k)[0]

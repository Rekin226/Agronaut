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
# RE-CALIBRATED 2026-09-01 on the 3935-chunk corpus (was 1.65 at 1354 chunks).
# `python -m scripts.retrieval_sweep --floor ...`, saved to retrieval_eval/sweep_2026_09.json:
#
#     floor  hit    recall  prec    MRR     MAP     silenced  rejected  headroom
#     1.30   0.758  0.682   0.409   0.591   0.538   4         10/10     -0.083
#     1.35   0.818  0.727   0.429   0.621   0.553   2         10/10     -0.033
#     1.40   0.818  0.727   0.429   0.621   0.553   0         10/10      0.017
#     1.45   0.818  0.727   0.434   0.636   0.568   0          8/10      0.067
#     1.50   0.818  0.727   0.434   0.636   0.568   0          8/10      0.117   <- ships
#     1.65   0.848  0.758   0.444   0.646   0.578   0          4/10      0.267
#
# THE BANDS HAVE SEPARATED, which is new and is what makes a tighter floor possible at all. At
# 1354 chunks the worst on-topic distance (1.548) sat ABOVE the closest off-topic match (1.426)
# and no single threshold could work. Adding Goddek et al. (2019) pulled the on-topic band in —
# more corpus means a closer match for every real question — so the worst real query is now 1.383
# and the nearest off-topic one is 1.411. The eval prints "separable" instead of "OVERLAPPING".
#
# NOT 1.40, though it rejects all ten controls. It sits 0.017 above the worst real query. This
# project already rejected a 0.032 margin as too thin ("a threefold cut in safety margin for one
# extra rejection"), and 0.017 is half of that. The golden set has 33 queries; the 34th is the one
# that matters, and headroom is all that protects it. 1.50 keeps 0.117, comparable to the 0.10
# that reasoning accepted, and still doubles refusal from 4/10 to 8/10.
#
# The cost is real and is the honest half of this: hit_rate 0.848 -> 0.818 and MAP 0.578 -> 0.568
# at this stage. The per-source cap below more than repays it (final: hit 0.879, MAP 0.624).
#
# This number is a property of THIS embedding model over THIS corpus and does not port.
# `python -m scripts.retrieval_eval` reprints the separation on every run and says outright when
# the bands overlap; `python -m scripts.retrieval_sweep --all` re-picks all three constants.
# Re-run after any corpus change — the value above expired in one day the last time nobody did.
# Override with AGRONAUT_RELEVANCE_MAX_DISTANCE (or "off" to disable).
_DEFAULT_MAX_DISTANCE = 1.50

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
#     beta   hit    recall  prec    MRR     MAP      (1354 chunks)
#     0.50   0.758  0.727   0.444   0.561   0.543
#     0.70   0.818  0.773   0.475   0.646   0.606
#     0.90   0.848  0.788   0.495   0.692   0.636   <- shipped then
#     dense  0.848  0.697   0.490   0.682   0.545
#
# RE-CONFIRMED 2026-09-01 at 3935 chunks, under floor 1.50 and cap 1 — the one constant the corpus
# growth did NOT move:
#
#     beta   hit    recall  prec    MRR     MAP      (3935 chunks)
#     0.50   0.879  0.788   0.333   0.601   0.558
#     0.70   0.879  0.803   0.343   0.606   0.571
#     0.90   0.879  0.833   0.364   0.657   0.624   <- ships
#     1.00   0.848  0.803   0.354   0.662   0.621    (dense-only)
#
# Note how LITTLE keyword weight is right: 10%. Enough to break ties a book would otherwise win on
# volume, not enough to let common aquaponics vocabulary dominate the ranking. At 0.5 — the
# intuitive "balanced" setting — hybrid is still clearly worse. That beta held steady across a
# 3x corpus growth while the floor and the cap both moved is itself informative: beta describes
# the relationship between two RANKING SIGNALS, which is a property of the query language and the
# embedding model, not of how much text sits behind them.
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
    ties that dense similarity resolves by volume.

    RE-CONFIRMED 2026-09-01 at 3935 chunks: still on, still beta=0.90, and now measurably ahead of
    dense-only on hit_rate as well (0.879 vs 0.848). Disable with AGRONAUT_HYBRID=off.
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
# CHANGED 2026-09-01 from 2 to 1. The reason the old choice expired is worth keeping, because the
# technique did not change — the corpus did, for the second time.
#
# At 1354 chunks there was ONE oversized source (FAO 589, 992 chunks) against 82 chunks of curated
# operator guidance, and cap=2 was the right call: it kept most of cap=1's recall while returning
# materially less irrelevant context (precision 0.540 vs 0.404), and still let a source with real
# depth contribute twice.
#
# There are now TWO. Goddek et al. (2019) added 2576 chunks, so 3853 of 3935 chunks are books and
# the curated files are 2% of the corpus. cap=2 lets books take two of three slots, which is the
# same failure the cap was built to stop, arriving through a door the cap left open.
#
# Measured (3935-chunk corpus, floor 1.50, hybrid on) — retrieval_eval/sweep_2026_09.json:
#
#     cap    hit    recall  prec    MRR     MAP
#     1      0.879  0.833   0.364   0.657   0.624   <- ships
#     2      0.818  0.727   0.434   0.636   0.568
#     3      0.697  0.621   0.374   0.576   0.515
#     none   0.697  0.621   0.374   0.576   0.515
#
# The gap that decided it widened by roughly four times. At 1354 chunks cap=1 bought +0.031 hit
# and +0.025 recall over cap=2; here it buys +0.061 hit and +0.106 recall, and +0.056 MAP.
#
# Precision still falls (0.434 -> 0.364) and that is still partly a metric artefact: `relevant` is
# labelled at DOCUMENT granularity, so cap=1 caps precision@3 at 0.333 for any query with one
# relevant document however good the answer is. What is NOT an artefact is recall and MAP, which
# both move decisively the other way. When the honest and the artefactual disagree this sharply,
# follow the metric that is not structurally bounded.
#
# Set AGRONAUT_MAX_PER_SOURCE=2 to restore the old behaviour, or 0 to disable the cap entirely.
_MAX_PER_SOURCE = 1

# Cross-encoder reranker. A bi-encoder embeds query and passage SEPARATELY, so it can only
# compare two summaries of meaning; a cross-encoder reads the pair together and scores their
# actual interaction. Far better, far slower — usable only on a shortlist, which is exactly what
# the fused pool is.
_RERANK_MODEL = os.getenv("AGRONAUT_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
_RERANKER = None
_RERANK_TRIED = False


# Metadata keys a caller may filter on. Restricted deliberately: an open-ended predicate over
# arbitrary metadata would let a caller filter on fields that only some chunks carry, silently
# excluding every document that predates the field rather than the ones it meant to exclude.
#
# What each key holds (assigned in srcs/chatbot.py):
#   source_type    "local_file" | "web"    — curated operator guidance vs a fetched page
#   kb_tag         markdown filename stem  — "ph_and_alkalinity", "fish_disease_and_treatment"
#   url_category   the curated category from urls.txt
#   chapter        FAO 589 chapter, forward-filled to ~95% page coverage
#   page           page number within a PDF source
_FILTERABLE = {"source_type", "kb_tag", "url_category", "chapter", "page"}


def _matches(metadata: dict, filters: dict) -> bool:
    """Whether one chunk satisfies every filter clause.

    A clause value may be a scalar or a collection, so {"source_type": "local_file"} and
    {"kb_tag": ["ph_and_alkalinity", "water_quality_ranges"]} both read naturally. Every
    clause must match: filters narrow, they never widen. A chunk MISSING the key fails,
    which is the safe direction — "give me only FAO chapter 8" must not return the whole
    markdown corpus on the grounds that it has no chapter to disagree with.
    """
    for key, want in filters.items():
        got = metadata.get(key)
        if got is None:
            return False
        if isinstance(want, (list, tuple, set, frozenset)):
            if got not in want and str(got) not in {str(w) for w in want}:
                return False
        elif got != want and str(got) != str(want):
            return False
    return True


def normalize_filters(filters: dict | None) -> dict | None:
    """Validate a filter dict, dropping unknown keys with a warning. None/empty -> None.

    Unknown keys are dropped rather than raising: a filter is a retrieval REFINEMENT, and a
    typo in one should cost precision, never the whole answer to an operator's question.
    """
    if not filters:
        return None
    clean = {k: v for k, v in filters.items() if k in _FILTERABLE}
    unknown = set(filters) - set(clean)
    if unknown:
        logging.warning("Ignoring unfilterable metadata keys %s (filterable: %s)",
                        sorted(unknown), sorted(_FILTERABLE))
    return clean or None


def retrieve(query: str, k: int = 3, max_dist: float | None = None,
             hybrid: bool | None = None, filters: dict | None = None,
             max_per_source: int | None = None) -> list[dict]:
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

    METADATA FILTERING (`filters`) is the third leg of hybrid retrieval and works differently
    from the other two: it does not rank anything. It excludes, on rigid criteria, before
    ranking happens — and so it is applied to BOTH pools, not to the fused result. Filtering
    after fusion would let excluded chunks consume pool slots first and silently shrink the
    candidate set; filtering before means each retriever spends its whole pool inside the
    permitted subset. See `_FILTERABLE` for the keys and `_matches` for the semantics.

    It is OFF unless a caller passes it, and passing nothing leaves behaviour byte-identical
    to before it existed. That is deliberate. A filter encodes something the CALLER knows and
    the query text does not say ("only my own curated docs", "only chapter 8"); inferring one
    from the query would be query rewriting wearing a different hat, and that technique is
    recorded as measured-and-lost for this corpus.

    `max_per_source` overrides the diversity cap for this call (0 disables it). It exists for
    MEASUREMENT: the calibration of the relevance floor depends on the closest distance in the
    raw candidate pool, and the cap can drop precisely that chunk when it is the second passage
    from a source. Measuring the band edge through a capped result set therefore reports a
    distance that is too high — on this corpus, 1.416 instead of the true 1.383 — which would
    quietly overstate how tight a floor can safely be. Evaluators pass 0 here to see the pool the
    floor actually gates.

    Returns [] both when the index is unavailable and when nothing survives filtering; callers
    that must tell those apart should check `index_available()`.
    """
    limit = max_distance() if max_dist is None else max_dist
    filters = normalize_filters(filters)
    index = _get_index()
    if index is None:
        return []
    import srcs.chatbot as core

    try:
        if filters:
            # fetch_k over-fetches BEFORE filtering, so it must be generous: FAISS takes the
            # nearest fetch_k, drops those the filter rejects, and only then keeps k. Left at
            # its default of 20, a narrow filter over a 3935-chunk corpus would usually find
            # nothing in the top 20 and return empty for a query the corpus can answer well.
            pairs = index.similarity_search_with_score(
                query, k=max(_POOL, k * 2),
                filter=lambda md: _matches(md, filters),
                fetch_k=max(_POOL * 20, 400))
        else:
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
                if filters and not _matches(doc.metadata, filters):
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
    if rerank_enabled():
        # Rerank the shortlist BEFORE the diversity cap: the cap should displace the least
        # relevant duplicate, which requires relevance to already be correct.
        fused = _rerank(query, fused[:_POOL], by_key)
    return [by_key[key] for key in _diversify(fused, by_key, k, max_per_source)]


def rerank_enabled() -> bool:
    """Cross-encoder reranking ships DISABLED — measured, and the reason is specific.

        variant                  hit    recall  prec    MRR     MAP     latency
        off (fused order)        0.939  0.894   0.540   0.737   0.697   274 ms
        on  (cross-encoder)      0.879  0.813   0.515   0.727   0.682   761 ms

    Worse on every metric and 2.8x slower. The default model, ms-marco-MiniLM-L-6-v2, is trained
    on MS MARCO — short web-search queries against web passages. Operator phrasing ("my tilapia
    are gasping at the surface") against agronomy prose is out of that distribution, and a
    general-purpose reranker applied out of domain can and here does score worse than the
    domain-appropriate bi-encoder it is reordering. It is being asked to improve a shortlist that
    is already good (MRR 0.737), which is the hardest case for it to win.

    This is a statement about THIS model on THIS domain, not about reranking. A cross-encoder
    fine-tuned on agronomy text, or simply a stronger general one, could plausibly win — the
    machinery is here and AGRONAUT_RERANK=on turns it on, with AGRONAUT_RERANK_MODEL selecting
    the model.

    THE TABLE ABOVE WAS MEASURED AT 1354 CHUNKS and has NOT been re-run since the corpus reached
    3935. Unlike the floor and the cap, this decision was not re-measured on 2026-09-01, because
    a technique that lost on every metric and tripled latency does not become the priority when
    the corpus grows. It is recorded as stale rather than quietly presented as current — re-run
    `AGRONAUT_RERANK=on python -m scripts.retrieval_eval` before trusting either verdict.
    """
    return os.getenv("AGRONAUT_RERANK", "").lower() in {"on", "1", "true"}


def _get_reranker():
    """A lazily loaded CrossEncoder, or None when unavailable.

    Loaded on first use rather than at import, matching semantic.default_embedder(): a model
    download must never be the reason the agent is slow to start or fails to start at all.
    """
    global _RERANKER, _RERANK_TRIED
    if _RERANK_TRIED:
        return _RERANKER
    _RERANK_TRIED = True
    try:
        from sentence_transformers import CrossEncoder
        _RERANKER = CrossEncoder(_RERANK_MODEL)
    except Exception as exc:  # noqa: BLE001 — reranking is additive; never break retrieval
        logging.warning("Reranker unavailable, keeping fused order: %s", exc)
        _RERANKER = None
    return _RERANKER


def _rerank(query: str, keys: list, by_key: dict) -> list:
    """Reorder a shortlist by cross-encoder relevance, most relevant first.

    Returns the input order unchanged if the model is unavailable or scoring fails, so a missing
    reranker costs ranking quality and nothing else.
    """
    model = _get_reranker()
    if model is None or len(keys) < 2:
        return keys
    try:
        scores = model.predict([(query, by_key[k]["text"][:2000]) for k in keys])
        return [k for _s, k in sorted(zip(scores, keys), key=lambda p: p[0], reverse=True)]
    except Exception as exc:  # noqa: BLE001
        logging.warning("Reranking failed, keeping fused order: %s", exc)
        return keys


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


def search_with_stats(query: str, k: int = 3, filters: dict | None = None) -> tuple[str, dict]:
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
    hits = retrieve(query, k=k, filters=filters)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    scored = [h["score"] for h in hits if h.get("score") is not None]
    stats = {
        "outcome": "hit" if hits else "no_match",
        "n_results": len(hits),
        "k": k,
        "latency_ms": latency_ms,
        "top_score": round(min(scored), 3) if scored else None,
        "hybrid": hybrid_enabled(),
        # WHICH keys were filtered on, never their values: a value can be as specific as a
        # single kb_tag, and "this user filtered to fish_disease_and_treatment" is closer to
        # the subject of their question than the shape of it.
        "filtered": sorted(normalize_filters(filters) or {}) or None,
    }
    if not hits:
        return _NO_MATCH, stats
    return "\n\n".join(f"[source: {h['source']}]\n{h['text']}" for h in hits), stats


def search(query: str, k: int = 3, filters: dict | None = None) -> str:
    """Return retrieved knowledge passages for `query`, each labeled with its source —
    citation is enforced here, not left to the model — or a clear 'no context' note."""
    return search_with_stats(query, k=k, filters=filters)[0]

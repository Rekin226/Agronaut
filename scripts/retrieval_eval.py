"""Retrieval quality scorer — recall@k, precision@k, MRR and MAP@k over a golden set.

The course's central discipline (M2): you cannot tune a retriever you do not measure. Every
technique that follows — header-aware chunking, BM25 hybrid, reciprocal rank fusion, a
cross-encoder reranker — is an unverifiable guess until there is a number that moves.

Metrics are computed at DOCUMENT granularity (which source file was surfaced), not chunk
granularity, because that is what the golden set can honestly label and what decides whether the
agent can answer. Three chunks from the right file is one correct document, not three.

The metric functions are pure and take plain lists, so agronaut_agent/tests/test_retrieval_eval.py
verifies the arithmetic hermetically — no index, no embedding model, no network.

Run it:
    python -m scripts.retrieval_eval                        # score, print a table
    python -m scripts.retrieval_eval --save baseline.json   # record a baseline
    python -m scripts.retrieval_eval --compare baseline.json  # print the delta vs that baseline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "docs" / "dpg" / "retrieval_eval" / "golden_set.json"
_MULTILINGUAL = _ROOT / "docs" / "dpg" / "retrieval_eval" / "golden_set_multilingual.json"

# The baseline the shipped retrieval constants (floor, cap, beta in agronaut_agent/rag.py) were
# tuned against. When a re-sweep moves them, save a new baseline and point this at it.
CURRENT_BASELINE = _ROOT / "docs" / "dpg" / "retrieval_eval" / "baseline_tuned_2026_09.json"

# How far the knowledge/ guides may drift, in bytes, before the constants must be re-measured.
# This is a trigger to re-measure, NOT a measured tolerance: nobody has measured how much drift
# the constants survive. It is set where it is because the hand-written guides are a small
# share of the index (two books hold ~98% of the chunks, per the 2026-09-01 re-sweep), so 10%
# of guide bytes is a small fraction of chunks, and one contributed guide should not fail a
# contributor's CI. Several should, cumulatively, since the comparison is against the baseline.
_KNOWLEDGE_DRIFT_LIMIT = 0.10


# --- pure metrics (no index, no model — unit-testable) -----------------------

def dedupe(sources: list[str]) -> list[str]:
    """Retrieved chunks collapsed to distinct source documents, ranking order preserved."""
    seen, out = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def precision_at_k(retrieved: list[str], relevant: set[str]) -> float:
    """Of the documents we returned, what fraction were relevant."""
    if not retrieved:
        return 0.0
    return sum(1 for s in retrieved if s in relevant) / len(retrieved)


def recall_at_k(retrieved: list[str], relevant: set[str]) -> float:
    """Of the documents that should have been found, what fraction we returned."""
    if not relevant:
        return 0.0
    return sum(1 for s in retrieved if s in relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """1 / rank of the first relevant document — how near the top the first good hit lands."""
    for i, s in enumerate(retrieved, start=1):
        if s in relevant:
            return 1.0 / i
    return 0.0


def average_precision(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Mean of precision@i taken at each rank i that holds a relevant document.
    Rewards ranking the relevant documents HIGH, not merely including them."""
    if not relevant:
        return 0.0
    hits, total = 0, 0.0
    for i, s in enumerate(retrieved, start=1):
        if s in relevant:
            hits += 1
            total += hits / i
    denom = min(len(relevant), k)
    return total / denom if denom else 0.0


def score_query(retrieved_sources: list[str], relevant: list[str], k: int) -> dict:
    docs = dedupe(retrieved_sources)[:k]
    rel = set(relevant)
    return {
        "retrieved": docs,
        "hit": any(s in rel for s in docs),
        "precision": precision_at_k(docs, rel),
        "recall": recall_at_k(docs, rel),
        "rr": reciprocal_rank(docs, rel),
        "ap": average_precision(docs, rel, k),
    }


def aggregate(per_query: list[dict], k: int) -> dict:
    n = len(per_query) or 1
    return {
        "queries": len(per_query),
        "k": k,
        "hit_rate": sum(1 for r in per_query if r["hit"]) / n,
        "precision@k": sum(r["precision"] for r in per_query) / n,
        "recall@k": sum(r["recall"] for r in per_query) / n,
        "MRR": sum(r["rr"] for r in per_query) / n,
        "MAP@k": sum(r["ap"] for r in per_query) / n,
    }


# --- multilingual golden set (pure, no index, unit-testable) -----------------

MULTILINGUAL_PATH = _MULTILINGUAL


def load_multilingual(path: Path | None = None) -> dict:
    """Load the multilingual golden set (FR + ZH-TW mirrors of the English queries)."""
    return json.loads((path or _MULTILINGUAL).read_text())


def by_language(golden: dict) -> dict:
    """Split a multilingual golden set into per-language subsets, negatives included.

    Pure: takes and returns plain dicts, so the per-language aggregation is unit-testable
    without an index. Queries carry a `lang` key; ids already carry the language suffix.
    """
    out: dict[str, dict] = {}
    for q in golden.get("queries", []):
        lang = q.get("lang")
        if lang:
            bucket = out.setdefault(lang, {"k": golden.get("k", 3),
                                           "queries": [], "negative_controls": []})
            bucket["queries"].append(q)
    for q in golden.get("negative_controls", []):
        lang = q.get("lang")
        if lang and lang in out:
            out[lang]["negative_controls"].append(q)
    return out


# --- what the baseline was measured on (pure, no index, unit-testable) ------

def corpus_fingerprint(root: Path = _ROOT) -> dict:
    """What goes INTO the index: the source list and the hand-written guides.

    Hashes `urls.txt` and every .md/.txt file under `knowledge/` (the same set
    srcs/chatbot.py loads), with their sizes. Needs no index, model or network, so CI can run
    it on every PR.

    What it does NOT cover: the chunking code, and the fetched pages behind each URL. A change
    to either can move the constants without changing this fingerprint.
    """
    urls = root / "urls.txt"
    kb = root / "knowledge"
    files = sorted(p for p in kb.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"})
    h = hashlib.sha256()
    total = 0
    for p in files:
        data = p.read_bytes()
        total += len(data)
        h.update(p.relative_to(kb).as_posix().encode() + b"\0" + data + b"\0")
    return {
        "urls_sha256": hashlib.sha256(urls.read_bytes() if urls.exists() else b"").hexdigest(),
        "knowledge_sha256": h.hexdigest(),
        "knowledge_files": len(files),
        "knowledge_bytes": total,
    }


def corpus_drift(recorded: dict | None, current: dict,
                 limit: float = _KNOWLEDGE_DRIFT_LIMIT) -> list[str]:
    """Reasons the baseline no longer describes the corpus that ships. Empty means it still does.

    A changed source list always counts: a new source is how the corpus tripled on 2026-08-26
    and left every constant wrong for six days. Edits to the guides count once their total size
    has moved past `limit` of what the baseline measured.
    """
    if not recorded:
        return ["the baseline records no corpus fingerprint, so what it measured is unknown"]
    reasons = []
    if recorded.get("urls_sha256") != current["urls_sha256"]:
        reasons.append("urls.txt changed: sources were added, removed or edited")
    before = recorded.get("knowledge_bytes") or 0
    after = current["knowledge_bytes"]
    if before and abs(after - before) / before > limit:
        reasons.append(f"knowledge/ grew or shrank {abs(after - before) / before:.0%} "
                       f"({before} -> {after} bytes), past the {limit:.0%} re-measure trigger")
    return reasons


def _chunk_count() -> int | None:
    """Chunks in the live index, or None when there is no index to count."""
    try:
        from agronaut_agent import rag
        idx = rag._get_index()
        return int(idx.index.ntotal) if idx is not None else None
    except Exception:  # noqa: BLE001 (a count is a nice-to-have, never a reason to fail)
        return None


# --- the live run ------------------------------------------------------------

def run(k: int | None = None, retrieve=None, unfiltered=None, no_floor: bool = False,
        golden: dict | None = None) -> dict:
    """Score the golden set.

    `golden` overrides the English golden set — the per-language mode passes a slice of
    golden_set_multilingual.json. Defaults to the English file, so existing callers are
    unaffected.

    `retrieve` is injectable (query, k) -> list of hit dicts; defaults to the real index.
    `unfiltered` is the same retriever with the relevance floor DISABLED — the negative controls
    need raw distances, because a floor that has already discarded them tells us nothing about
    whether it was set at the right place.
    """
    golden = golden if golden is not None else json.loads(_GOLDEN.read_text())
    k = k or golden.get("k", 3)
    if retrieve is None:
        from agronaut_agent import rag
        inf = float("inf")
        retrieve = (lambda q, kk: rag.retrieve(q, kk, max_dist=inf)) if no_floor else rag.retrieve
        if unfiltered is None:
            # No floor AND no per-source cap. The cap can displace the very chunk that is closest
            # in L2 (when it is a source's second passage), so a "raw" distance measured through
            # a capped result set is too high and would overstate how tight a floor can be.
            unfiltered = lambda q, kk: rag.retrieve(q, kk, max_dist=inf, max_per_source=0)  # noqa: E731
    if unfiltered is None:
        unfiltered = retrieve

    def nearest(hits):
        """The smallest L2 distance in a result set.

        NOT hits[0]'s score: once hybrid fusion is on, reciprocal-rank fusion reorders results, so
        the first-ranked hit is frequently not the closest one — and a keyword-only hit carries no
        distance at all. Calibrating a distance floor against a fused rank silently compares the
        wrong numbers.
        """
        scored = [h["score"] for h in hits if h.get("score") is not None]
        return min(scored) if scored else None

    per_query, latencies = [], []
    for q in golden["queries"]:
        t0 = time.perf_counter()
        hits = retrieve(q["query"], k)
        latencies.append((time.perf_counter() - t0) * 1000)
        row = score_query([h["source"] for h in hits], q["relevant"], k)
        row.update(id=q["id"], query=q["query"], relevant=q["relevant"],
                   top_score=nearest(hits))
        per_query.append(row)

    # Negative controls carry no relevant set — their value is the SCORE distribution, which is
    # what a relevance floor has to be calibrated against (a floor that also rejects real
    # queries is worse than no floor).
    negatives = []
    for q in golden.get("negative_controls", []):
        raw = unfiltered(q["query"], k)          # raw distances, for calibrating the floor
        kept = retrieve(q["query"], k)           # what the operator would actually be shown
        negatives.append({"id": q["id"], "query": q["query"],
                          "top_score": nearest(raw),
                          "returned": [h["source"] for h in kept],
                          "rejected_by_floor": bool(raw) and not kept})

    # On-topic distances also measured unfiltered, so the two bands are compared on equal terms.
    for row, q in zip(per_query, golden["queries"]):
        raw = unfiltered(q["query"], k)
        row["raw_top_score"] = nearest(raw)
        row["floor_returned_nothing"] = not retrieve(q["query"], k)

    summary = aggregate(per_query, k)
    summary["latency_ms_mean"] = sum(latencies) / (len(latencies) or 1)
    summary["floor_silenced_on_topic"] = sum(1 for r in per_query if r["floor_returned_nothing"])
    summary["floor_rejected_off_topic"] = sum(1 for n in negatives if n["rejected_by_floor"])
    summary["negative_controls"] = len(negatives)
    return {"summary": summary, "per_query": per_query, "negative_controls": negatives}


def _print(report: dict, baseline: dict | None = None) -> None:
    s = report["summary"]
    print(f"\nRETRIEVAL @k={s['k']}  ({s['queries']} queries)")
    print("-" * 62)
    keys = ["hit_rate", "recall@k", "precision@k", "MRR", "MAP@k"]
    for key in keys:
        line = f"  {key:<14} {s[key]:.3f}"
        if baseline:
            d = s[key] - baseline["summary"][key]
            line += f"   ({d:+.3f} vs baseline)"
        print(line)
    print(f"  {'latency':<14} {s['latency_ms_mean']:.0f} ms/query")

    misses = [r for r in report["per_query"] if not r["hit"]]
    if misses:
        print(f"\nMISSES ({len(misses)}/{s['queries']}) — nothing relevant in the top {s['k']}:")
        for r in misses:
            print(f"  [{r['id']}] {r['query']}")
            print(f"        wanted: {', '.join(r['relevant'])}")
            print(f"        got:    {', '.join(r['retrieved']) or '(nothing)'}")

    negs = report["negative_controls"]
    if negs:
        print("\nNEGATIVE CONTROLS — off-topic queries (a relevance floor should reject these):")
        for n in negs:
            score = "n/a" if n["top_score"] is None else f"{n['top_score']:.3f}"
            mark = "REJECTED by floor" if n.get("rejected_by_floor") else (
                ", ".join(n["returned"]) or "(nothing)")
            print(f"  [{n['id']}] raw_top_distance={score}  -> {mark}")
        print(f"\n  floor rejected {s.get('floor_rejected_off_topic', 0)}/{len(negs)} off-topic queries")
        print(f"  floor silenced {s.get('floor_silenced_on_topic', 0)}/{s['queries']} REAL queries"
              "  <- must stay 0")
        on_topic = [r.get("raw_top_score") for r in report["per_query"]
                    if r.get("raw_top_score") is not None]
        off_topic = [n["top_score"] for n in negs if n["top_score"] is not None]
        if on_topic and off_topic:
            # FAISS L2: LOWER is closer. A floor is only viable if the two bands separate.
            print(f"\n  on-topic  worst(max) distance: {max(on_topic):.3f}")
            print(f"  off-topic best(min) distance: {min(off_topic):.3f}")
            verdict = ("separable — a floor can work"
                       if min(off_topic) > max(on_topic) else
                       "OVERLAPPING — a single global floor would reject real queries too")
            print(f"  -> {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--save", metavar="FILE", help="write this run as a baseline")
    ap.add_argument("--compare", metavar="FILE", help="print deltas against a saved baseline")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-floor", action="store_true",
                    help="measure retrieval with the relevance floor disabled")
    ap.add_argument("--per-language", action="store_true",
                    help="also score the FR and ZH-TW slices of golden_set_multilingual.json "
                         "(same labels as the English originals); the saved baseline holds one "
                         "block per language under 'per_language'")
    args = ap.parse_args()

    if args.per_language:
        english = run(k=args.k, no_floor=args.no_floor)
        report = {"summary": english["summary"], "per_query": english["per_query"],
                  "negative_controls": english["negative_controls"],
                  "per_language": {"en": english}}
        ml = load_multilingual()
        for lang, bucket in sorted(by_language(ml).items()):
            report["per_language"][lang] = run(k=args.k, no_floor=args.no_floor,
                                               golden=bucket)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            per_lang = {lang: rep["summary"]
                        for lang, rep in sorted(report["per_language"].items())}
            counts = ", ".join(f"{lang}={s['queries']}" for lang, s in per_lang.items())
            print(f"\nRETRIEVAL PER LANGUAGE  ({counts} queries)")
            print("-" * 62)
            keys = ["hit_rate", "recall@k", "precision@k", "MRR", "MAP@k",
                    "floor_silenced_on_topic", "floor_rejected_off_topic"]
            for key in keys:
                line = f"  {key:<26}"
                for lang, rep in sorted(report["per_language"].items()):
                    v = rep["summary"].get(key)
                    line += f"  {lang}={v:.3f}" if isinstance(v, (int, float)) else f"  {lang}=n/a"
                print(line)
            for lang, rep in sorted(report["per_language"].items()):
                misses = [r for r in rep["per_query"] if not r["hit"]]
                if misses:
                    print(f"\nMISSES {lang} ({len(misses)}/{rep['summary']['queries']}):")
                    for r in misses:
                        print(f"  [{r['id']}] {r['query']}")
                        print(f"        wanted: {', '.join(r['relevant'])}")
                        print(f"        got:    {', '.join(r['retrieved']) or '(nothing)'}")
    else:
        report = run(k=args.k, no_floor=args.no_floor)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print(report, args.compare and json.loads(Path(args.compare).read_text()))
        baseline = json.loads(Path(args.compare).read_text()) if args.compare else None
        if baseline:
            drift = corpus_drift(baseline.get("corpus"), corpus_fingerprint())
            for reason in drift:
                print(f"  ! corpus differs from the baseline: {reason}")

    if args.save:
        # First key, so a reader sees what was measured before the numbers measured on it.
        report = {"corpus": {**corpus_fingerprint(), "chunks": _chunk_count()}, **report}
        Path(args.save).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nbaseline saved -> {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

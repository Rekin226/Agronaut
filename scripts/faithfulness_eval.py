"""Generation quality scorer — faithfulness, response relevancy and citation accuracy.

`scripts/retrieval_eval.py` answers "did the retriever find the right documents". It cannot
answer the question RAG actually exists to answer: did the ANSWER use them. A system can score
recall@k 0.894 and still hallucinate every number in its reply, and until now nothing here
would have noticed.

Three metrics, deliberately of three different kinds:

  faithfulness       (LLM-judged) The answer is decomposed into atomic claims and each claim is
                     judged supported or unsupported BY THE RETRIEVED CONTEXT ALONE. This is the
                     grounding measure: the fraction of claims the context actually backs.
                     A claim that is true in the world but absent from the context counts as
                     UNSUPPORTED, and that is the point — an answer the retrieval did not
                     justify is a hallucination that happened to land well.

  response_relevancy (LLM-judged + embeddings) The judge writes questions the answer would be a
                     good reply to; those are embedded and compared to the real query. It scores
                     whether the answer addressed what was asked, and says nothing about truth.

  citation_accuracy  (CODE-judged, no model) Every "[source: X]" the answer cites must be a
                     source that was actually retrieved. Cheap, exact, and it catches the
                     specific failure the course warns about twice: LLMs hallucinate citations,
                     and a fabricated citation is worse than none because it survives review.

WHY THE JUDGE IS TREATED AS A WITNESS, NOT AN ORACLE. M5 is explicit that LLM-as-judge splits
the difference between cost and flexibility, and M4 that judges favour their own model family.
So: every rubric is binary with named labels, an unparseable verdict is counted as UNJUDGED
rather than quietly folded into either side, and `n_unjudged` is printed next to every score.
A metric with a third of its claims unjudged is not a 0.9, it is a 0.9 you should not trust,
and the report has to be able to say so.

NOT HERMETIC. It calls the configured LLM over the network, so it is opt-in
(AGRONAUT_FAITHFULNESS_EVAL=1), never runs in CI, and never blocks a merge. The parsing and
scoring functions are pure and take plain strings, so the arithmetic is unit-tested without a
model (agronaut_agent/tests/test_faithfulness_eval.py).

Run it:
    AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval
    AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval --limit 8 --save report.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_GOLDEN = Path(__file__).resolve().parents[1] / "docs" / "dpg" / "retrieval_eval" / "golden_set.json"
_OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "dpg" / "faithfulness_eval"

# How many questions the relevancy judge is asked to reverse-engineer from an answer. RAGAS uses
# three; more would smooth the estimate but every one is a model call per query.
_N_REVERSE_QUESTIONS = 3


# --- pure parsing and scoring (no model, no network — unit-testable) ---------

def parse_claims(text: str) -> list[str]:
    """Pull atomic claims out of a judge's numbered or bulleted list.

    Tolerant of the three shapes models actually emit ("1. x", "- x", "* x") and of a preamble
    before the list, because a judge told to answer with only a list will still sometimes open
    with "Here are the claims:". Lines that survive as empty are dropped.
    """
    claims = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", line)
        if m and m.group(1).strip():
            claims.append(m.group(1).strip())
    return claims


def parse_verdict(text: str) -> bool | None:
    """SUPPORTED -> True, UNSUPPORTED -> False, anything else -> None (unjudged).

    Order matters: "UNSUPPORTED" contains "SUPPORTED", so the negative label must be tested
    first or every rejection would read as an acceptance. Checked on a bare-word boundary so a
    judge that explains itself ("UNSUPPORTED — the context never mentions...") still parses.
    """
    up = (text or "").upper()
    if re.search(r"\bUNSUPPORTED\b", up):
        return False
    if re.search(r"\bSUPPORTED\b", up):
        return True
    return None


def cited_sources(answer: str) -> list[str]:
    """Source labels the answer cites, in the "[source: knowledge/foo.md]" form the retriever
    stamps onto every passage. Deduplicated, order preserved."""
    seen, out = set(), []
    for m in re.finditer(r"\[source:\s*([^\]]+)\]", answer or ""):
        label = m.group(1).strip()
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def citation_accuracy(answer: str, retrieved: list[str]) -> tuple[float | None, list[str]]:
    """(fraction of cited sources that were actually retrieved, the fabricated ones).

    None when the answer cites nothing — that is not a score of zero. An answer with no
    citations has no citation accuracy to measure, and averaging it in as 0.0 would make an
    uncited system look like a lying one. The two failures are different and the report keeps
    them apart.
    """
    cites = cited_sources(answer)
    if not cites:
        return None, []
    allowed = set(retrieved)
    bogus = [c for c in cites if c not in allowed]
    return (len(cites) - len(bogus)) / len(cites), bogus


def faithfulness_score(verdicts: list) -> dict:
    """Supported / judged, with the unjudged count carried alongside rather than hidden.

    `score` is None when nothing could be judged, so a run where the judge failed entirely
    reports "no measurement" instead of a confident zero.
    """
    judged = [v for v in verdicts if v is not None]
    return {
        "score": (sum(1 for v in judged if v) / len(judged)) if judged else None,
        "n_claims": len(verdicts),
        "n_judged": len(judged),
        "n_unjudged": len(verdicts) - len(judged),
    }


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, 0.0 for a zero-length vector rather than a ZeroDivisionError."""
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return (num / (na * nb)) if na and nb else 0.0


def aggregate(per_query: list[dict]) -> dict:
    """Mean each metric over the queries that produced one, never over all queries.

    A query whose judge failed contributes to `n` for its own metric and to nothing else, so a
    partially failed run reports smaller samples rather than depressed scores.
    """
    def _mean(key):
        vals = [q[key] for q in per_query if q.get(key) is not None]
        return {"mean": (sum(vals) / len(vals)) if vals else None, "n": len(vals)}

    return {
        "queries": len(per_query),
        "faithfulness": _mean("faithfulness"),
        "response_relevancy": _mean("response_relevancy"),
        "citation_accuracy": _mean("citation_accuracy"),
        "claims_unjudged": sum(q.get("n_unjudged", 0) for q in per_query),
        "fabricated_citations": sum(len(q.get("fabricated", [])) for q in per_query),
    }


# --- prompts ----------------------------------------------------------------

_CLAIMS_PROMPT = """Break the ANSWER into atomic factual claims.

Rules:
- One claim per line, numbered "1.", "2.", ...
- Each claim must stand alone and state ONE fact.
- Copy the substance of the answer; do not add, correct, or evaluate anything.
- Ignore questions, greetings, and offers of further help — those are not claims.
- Output the numbered list and nothing else.

ANSWER:
{answer}"""

_VERDICT_PROMPT = """Decide whether the CONTEXT supports the CLAIM.

Answer with exactly one word:
- SUPPORTED   if the CONTEXT states or directly implies the claim.
- UNSUPPORTED if it does not.

Judge ONLY against the CONTEXT. A claim you believe is true in the real world is still
UNSUPPORTED if the CONTEXT does not back it. When genuinely unsure, answer UNSUPPORTED.

CONTEXT:
{context}

CLAIM: {claim}"""

_REVERSE_PROMPT = """Read the ANSWER and write {n} questions it would be a good reply to.

Rules:
- One question per line, numbered "1.", "2.", ...
- Base them only on what the ANSWER actually addresses.
- Output the numbered list and nothing else.

ANSWER:
{answer}"""


# --- the run ----------------------------------------------------------------

def score_query(query: str, answer: str, context: str, retrieved: list[str],
                ask, embed) -> dict:
    """Score one (query, answer, context) triple. `ask` is str->str, `embed` is str->vector.

    Both are injected so the whole scoring path can be exercised with fakes; nothing in here
    reaches the network on its own.
    """
    claims = parse_claims(ask(_CLAIMS_PROMPT.format(answer=answer)))
    verdicts = [parse_verdict(ask(_VERDICT_PROMPT.format(context=context, claim=c)))
                for c in claims]
    faith = faithfulness_score(verdicts)

    relevancy = None
    try:
        reverse = parse_claims(ask(_REVERSE_PROMPT.format(n=_N_REVERSE_QUESTIONS, answer=answer)))
        if reverse:
            qv = embed(query)
            sims = [cosine(qv, embed(r)) for r in reverse]
            relevancy = sum(sims) / len(sims)
    except Exception as exc:  # noqa: BLE001 — one failed metric must not void the other two
        print(f"    relevancy unavailable: {exc}")

    cite_acc, bogus = citation_accuracy(answer, retrieved)
    return {
        "query": query,
        "faithfulness": faith["score"],
        "n_claims": faith["n_claims"],
        "n_unjudged": faith["n_unjudged"],
        "response_relevancy": relevancy,
        "citation_accuracy": cite_acc,
        "fabricated": bogus,
        "retrieved": retrieved,
    }


def run(limit: int | None = None, ask=None, embed=None, answer_fn=None) -> dict:
    """Score the golden-set queries end to end: retrieve, answer, then judge.

    The answer is generated by the SAME model and the SAME grounding instruction the live
    agent uses, but through a single direct call rather than the tool loop. That keeps the
    measurement about generation quality: a tool-loop answer's faithfulness would also be
    measuring whether the model chose to call the retriever at all, which is a different
    question with its own metric already.
    """
    from agronaut_agent import rag

    golden = json.loads(_GOLDEN.read_text())
    queries = golden["queries"][:limit] if limit else golden["queries"]

    if ask is None or embed is None or answer_fn is None:
        ask, embed, answer_fn = _live_backends(ask, embed, answer_fn)

    per_query = []
    for i, item in enumerate(queries, start=1):
        q = item["query"]
        print(f"[{i}/{len(queries)}] {q[:70]}")
        hits = rag.retrieve(q, k=golden.get("k", 3))
        if not hits:
            print("    no passages retrieved — skipped (that is a RETRIEVAL result, "
                  "already scored by scripts/retrieval_eval.py)")
            continue
        context = "\n\n".join(f"[source: {h['source']}]\n{h['text']}" for h in hits)
        sources = [h["source"] for h in hits]
        try:
            answer = answer_fn(q, context)
        except Exception as exc:  # noqa: BLE001
            print(f"    generation failed: {exc}")
            continue
        row = score_query(q, answer, context, sources, ask, embed)
        row["id"] = item.get("id")
        per_query.append(row)
        print(f"    faithfulness={_fmt(row['faithfulness'])} "
              f"relevancy={_fmt(row['response_relevancy'])} "
              f"citations={_fmt(row['citation_accuracy'])}"
              + (f"  FABRICATED: {row['fabricated']}" if row["fabricated"] else ""))

    return {"summary": aggregate(per_query), "per_query": per_query}


def _live_backends(ask, embed, answer_fn):
    """Build the real judge, embedder and answerer. Imported lazily so `run()` with fakes
    never needs a model installed or an API key set."""
    from agent.llm import get_llm
    from agronaut_agent import semantic

    judge = get_llm(temperature=0.0)
    ask = ask or (lambda prompt: judge.invoke(prompt))
    if embed is None:
        # semantic.default_embedder() is batch-shaped (list of texts -> array of vectors);
        # the metrics here compare one text at a time, so adapt rather than reshape callers.
        batch = semantic.default_embedder()
        if batch is None:
            raise RuntimeError("No embedding model available; response_relevancy needs one. "
                               "Unset AGRONAUT_EMBEDDINGS=off, or install sentence-transformers.")

        def embed(text):
            return list(batch([text])[0])

    def _answer(query: str, context: str) -> str:
        return judge.invoke(
            "You are Agronaut, an aquaponics assistant. Answer the QUESTION using ONLY the "
            "CONTEXT. Cite each source you use inline as [source: <label>], exactly as the "
            "label appears. If the context does not answer the question, say so plainly.\n\n"
            f"CONTEXT:\n{context}\n\nQUESTION: {query}")

    return ask, embed, (answer_fn or _answer)


def _fmt(v) -> str:
    return "  n/a" if v is None else f"{v:.3f}"


def _print(report: dict) -> None:
    s = report["summary"]
    print("\n" + "=" * 62)
    print(f"Generation quality over {s['queries']} answered queries")
    print("=" * 62)
    for key in ("faithfulness", "response_relevancy", "citation_accuracy"):
        m = s[key]
        print(f"  {key:20s} {_fmt(m['mean'])}   (n={m['n']})")
    print(f"  claims unjudged      {s['claims_unjudged']}")
    print(f"  fabricated citations {s['fabricated_citations']}")
    if s["citation_accuracy"]["n"] < s["queries"]:
        print(f"\n  NOTE: {s['queries'] - s['citation_accuracy']['n']} answer(s) cited nothing at "
              "all. Uncited is not the same failure as miscited and is not averaged in.")
    if s["claims_unjudged"]:
        print("\n  NOTE: some claims could not be judged. Treat faithfulness as measured over "
              "the judged subset only.")


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, help="score only the first N golden-set queries")
    ap.add_argument("--save", help="write the full report to this JSON path")
    args = ap.parse_args()

    if os.getenv("AGRONAUT_FAITHFULNESS_EVAL", "").lower() not in {"1", "true", "yes"}:
        print("Generation eval is opt-in — it calls the configured LLM over the network.")
        print("  Run: AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval")
        return 0

    report = run(limit=args.limit)
    _print(report)
    if args.save:
        out = Path(args.save)
        if not out.is_absolute() and out.parent == Path("."):
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            out = _OUT_DIR / out.name
        out.write_text(json.dumps(report, indent=2))
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

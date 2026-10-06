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

from agronaut_agent import paths as _paths  # noqa: E402

# docs/dpg in a checkout, the installed copy under a wheel (paths.eval_root).
_GOLDEN = _paths.eval_root() / "retrieval_eval" / "golden_set.json"
_OUT_DIR = _paths.eval_root() / "faithfulness_eval"

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


# Sentences about the answer's own sources rather than about fish, water or plants. The first
# human check (#182, 2026-10-03) found the judges and the person split on exactly these: "The
# context does not specify step-by-step emergency dosing", "consult additional sources". They
# make no claim the context could back or contradict, so they are not claims at all, and
# scoring them made faithfulness partly measure how often an answer hedged. Anchored on the
# sentence's subject, so "The source FAO589 confirms that nitrogen deficiency..." stays a
# claim: it names a source and then says something about plants.
_META_CLAIM = [re.compile(p, re.I) for p in (
    r"^(?:the )?(?:provided |given |retrieved )?(?:context|sources?|passages?|documents?|"
    r"information)(?: (?:I have|provided|given|available))? (?:does not|doesn't|do not|"
    r"don't|only|covers?|lacks?|is silent|says nothing)\b",
    r"^I (?:do not|don't|cannot|can't|would need|need|am unable|have no)\b",
    r"\bconsult (?:\w+ ){0,2}(?:resources|sources|references|literature)\b",
    r"^if (?:you have|more detail|you need more|further detail)\b",
    r"\b(?:not enough|insufficient) (?:information|detail|data)\b",
)]


def is_meta_claim(claim: str) -> bool:
    """True for a sentence about the answer's sources or limits, not about the world."""
    text = (claim or "").strip().strip("\"'“”").strip()
    return any(p.search(text) for p in _META_CLAIM)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


# Share of a quote's words that must appear in the context. Below 1.0 so a quote that drops a
# PDF artefact ("Table/uni00A0A7.2") still counts as copied; far above what a paraphrase
# reaches. Each sentence is matched on its own, because judges copy two bullets in the
# order they need them, not the order they were written (feed-01:2 in the first pilot).
_QUOTE_FOUND = 0.8


def quote_in_context(quote: str, context: str) -> bool:
    import difflib
    c = _tokens(context)
    pieces = [_tokens(p) for p in re.split(r"(?<=[.!?])\s+|\.\.\.|…", quote or "")]
    pieces = [p for p in pieces if p]
    total = sum(len(p) for p in pieces)
    if not total:
        return False
    matched = sum(b.size for p in pieces
                  for b in difflib.SequenceMatcher(None, p, c, autojunk=False)
                  .get_matching_blocks())
    return matched / total >= _QUOTE_FOUND


def parse_judgement(text: str, context: str) -> bool | None:
    """A QUOTE-then-VERDICT reply -> True / False / None (unjudged).

    SUPPORTED counts only with evidence: a quote that is really in the context. The first
    human check (#182) found all three judge runs passing claims that are true in the world
    but appear nowhere in the context ("nitrifying bacteria need oxygen and time"), the very
    failure this metric exists to catch. Asking for the sentence first, and checking it here
    in code, means a judge cannot pass a claim from memory. A reply without a QUOTE line did
    not follow the format and is unjudged, not quietly trusted.
    """
    text = text or ""
    m = re.search(r"QUOTE\s*:\s*(.*?)(?:\n\s*VERDICT\s*:|\Z)", text, re.S | re.I)
    if not m:
        return None
    v = re.search(r"VERDICT\s*:\s*(\w+)", text, re.I)
    verdict = parse_verdict(v.group(1)) if v else None
    if verdict is not True:
        return verdict
    quote = m.group(1).strip().strip("\"'“”").strip()
    if not quote or quote.upper().startswith("NONE"):
        return False
    return quote_in_context(quote, context)


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
    allowed = [_label_key(r) for r in retrieved]
    bogus = [c for c in cites if not _same_source(_label_key(c), allowed)]
    return (len(cites) - len(bogus)) / len(cites), bogus


def _label_key(label: str) -> str:
    """A source label without directory, case, spacing or punctuation."""
    s = (label or "").strip().lower()
    s = s.rsplit("/", 1)[-1]
    s = re.sub(r"\.md$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


# A real source written slightly differently is not an invented one. The first runs
# (2026-09-30) "found" fabricated citations that were all real: the directory dropped,
# "FAO589" for "FAO 589", and "Burnel ... SpringerOpen" for "Burnell ... Springer Open"
# (0.99 similar). The two most alike DIFFERENT sources in the 26-source corpus score 0.64,
# so 0.90 separates a typo from a different source with a wide margin.
_SAME_SOURCE = 0.90


# A citation may also be a real label cut short: "Goddek, Joyce, Kotzen & Burnell eds. (2019)"
# for the retrieved "... (2019), Aquaponics Food Production Systems, Springer Open" was counted
# as fabricated on 2026-10-06. A prefix this long names one source, never two.
_MIN_PREFIX = 15


def _same_source(key: str, allowed: list[str]) -> bool:
    import difflib
    return any(key == a or difflib.SequenceMatcher(None, key, a).ratio() >= _SAME_SOURCE
               or (len(key) >= _MIN_PREFIX and a.startswith(key))
               for a in allowed)


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
    # float(): embedders hand back numpy float32, which json cannot write. The first real
    # `--save` (2026-09-30) crashed on exactly this, after every model call had been paid for.
    return float(num / (na * nb)) if na and nb else 0.0


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
- Ignore sentences about the sources or the answer itself ("the context does not say...",
  "consult other resources", "I don't have enough information") — those are not claims.
- Output the numbered list and nothing else.

ANSWER:
{answer}"""

_VERDICT_PROMPT = """Decide whether the CONTEXT supports the CLAIM.

Judge ONLY against the CONTEXT. Do not use what you know about fish, plants, water or
chemistry: a claim that is true in the real world is still UNSUPPORTED if the CONTEXT does not
state it or directly imply it.

First copy, word for word, the sentence or sentences of the CONTEXT that state the claim.
If there are none, write NONE. Then give the verdict:
- SUPPORTED   if the copied text states or directly implies the claim.
- UNSUPPORTED if it does not, or if you wrote NONE.
When genuinely unsure, answer UNSUPPORTED.

Answer in exactly this form and nothing else:
QUOTE: <text copied from the CONTEXT, or NONE>
VERDICT: <SUPPORTED or UNSUPPORTED>

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

def judge_claim(ask, context: str, claim: str) -> bool | None:
    return parse_judgement(ask(_VERDICT_PROMPT.format(context=context, claim=claim)), context)


def score_query(query: str, answer: str, context: str, retrieved: list[str],
                ask, embed, qid: str = "q", workers: int = 1) -> dict:
    """Score one (query, answer, context) triple. `ask` is str->str, `embed` is str->vector.

    Both are injected so the whole scoring path can be exercised with fakes; nothing in here
    reaches the network on its own.

    The answer, its context and its claims are kept in the row, each claim with a stable id
    (`<query id>:<n>`), so another judge, a second run of the same judge, and a person can
    all rule on EXACTLY the same claims later (#182). Agreement means nothing otherwise.
    """
    claims = [c for c in parse_claims(ask(_CLAIMS_PROMPT.format(answer=answer)))
              if not is_meta_claim(c)]   # the prompt asks for this too; code makes sure
    if workers > 1 and len(claims) > 1:
        # Independent calls: the quote-first judge took ~29 s a claim serially (2026-10-03).
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            verdicts = list(pool.map(lambda c: judge_claim(ask, context, c), claims))
    else:
        verdicts = [judge_claim(ask, context, c) for c in claims]
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
        "answer": answer,
        "context": context,
        "claims": [{"id": f"{qid}:{i}", "claim": c, "verdict": v}
                   for i, (c, v) in enumerate(zip(claims, verdicts), start=1)],
    }


# --- agreement: judges against each other, against themselves, and against a person -----

def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    """Chance-corrected agreement between two binary raters on the same items.

    None when it is undefined: no items, or both raters gave one label to everything (the
    expected agreement is then 1 and kappa divides by zero). A raw agreement of 90% with a
    kappa near 0 is what "agrees only because nearly everything is SUPPORTED" looks like,
    which is why both are reported.
    """
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def agreement(x: dict[str, bool | None], y: dict[str, bool | None]) -> dict:
    """Raw agreement and kappa over the claim ids both sides actually judged."""
    ids = [i for i in x if i in y and x[i] is not None and y[i] is not None]
    a, b = [bool(x[i]) for i in ids], [bool(y[i]) for i in ids]
    raw = (sum(1 for p, q in zip(a, b) if p == q) / len(ids)) if ids else None
    return {"n": len(ids), "raw": raw, "kappa": cohen_kappa(a, b)}


def meta_ids(report: dict) -> set[str]:
    """Saved claims that `is_meta_claim` now excludes. Reports written before it existed
    still hold them, and every judge and label on them is left out of every number."""
    return {c["id"] for q in report["per_query"] for c in q.get("claims", [])
            if is_meta_claim(c["claim"])}


def verdicts_of(report: dict, judge: str | None = None) -> dict[str, bool | None]:
    """claim id -> verdict, for the report's first judge or a named re-judgement."""
    if judge and judge in report.get("judgements", {}):
        out = dict(report["judgements"][judge])
    else:
        out = {c["id"]: c["verdict"] for q in report["per_query"] for c in q.get("claims", [])}
    skip = meta_ids(report)
    return {k: v for k, v in out.items() if k not in skip}


def rejudge(report: dict, ask, done: dict | None = None, workers: int = 1,
            on_query=None) -> dict[str, bool | None]:
    """Rule on every saved claim again, against its saved context. Answers and claims are
    NOT regenerated: only the judge changes, so any difference is the judge's.

    `done` holds verdicts from an interrupted run, which are kept and not asked again.
    `on_query(verdicts_so_far, n_queries_done, n_queries)` runs after each query, so the
    caller can save as it goes: the first quote-first re-judge ran 29 s a claim, about four
    hours for the report, and would have lost all of it to one closed terminal (2026-10-03).
    A query's claims are judged `workers` at a time; each call is independent.
    """
    from concurrent.futures import ThreadPoolExecutor

    out: dict[str, bool | None] = dict(done or {})
    rows = report["per_query"]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for n, q in enumerate(rows, start=1):
            todo = [c for c in q.get("claims", [])
                    if c["id"] not in out and not is_meta_claim(c["claim"])]
            verdicts = pool.map(lambda c: judge_claim(ask, q["context"], c["claim"]), todo)
            for c, v in zip(todo, verdicts):
                out[c["id"]] = v
            if on_query:
                on_query(out, n, len(rows))
    return out


def run(limit: int | None = None, ask=None, embed=None, answer_fn=None,
        save_to: Path | None = None, workers: int = 1) -> dict:
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
        row = score_query(q, answer, context, sources, ask, embed,
                          qid=str(item.get("id") or f"q{i}"), workers=workers)
        row["id"] = item.get("id")
        per_query.append(row)
        print(f"    faithfulness={_fmt(row['faithfulness'])} "
              f"relevancy={_fmt(row['response_relevancy'])} "
              f"citations={_fmt(row['citation_accuracy'])}"
              + (f"  FABRICATED: {row['fabricated']}" if row["fabricated"] else ""))
        if save_to:   # after every query, so an interrupted run keeps what it paid for
            _write(save_to, _report(per_query))

    return _report(per_query)


def _report(per_query: list[dict]) -> dict:
    return {"meta": {**run_meta(), "judge_errors": JUDGE_ERRORS["count"]},
            "summary": aggregate(per_query), "per_query": per_query}


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))


def _prompt_version() -> str:
    """Changes whenever a judging prompt changes, so reports judged differently never get
    compared as if they were the same measurement."""
    import hashlib
    blob = (_CLAIMS_PROMPT + _VERDICT_PROMPT + _REVERSE_PROMPT).encode()
    return hashlib.sha256(blob).hexdigest()[:10]


def _names(llm) -> str:
    name = f"{getattr(llm, 'provider', '?')}/{getattr(llm, 'model', '?')}"
    settings = getattr(llm, "SETTINGS", "")
    return f"{name} [{settings}]" if settings else name


_BACKENDS: dict[str, str] = {}


def run_meta() -> dict:
    from datetime import datetime, timezone
    return {"date": datetime.now(timezone.utc).date().isoformat(),
            "answerer": _BACKENDS.get("answerer", "injected"),
            "judge": _BACKENDS.get("judge", "injected"),
            "prompt_version": _prompt_version(),
            # Which knowledge base the answers were written from, so a report measured on
            # an older corpus can be called stale (the 2026-09-30 baseline could not be).
            "corpus": _corpus_id()}


def _corpus_id() -> str | None:
    try:
        from agronaut_agent.evals import current_corpus_id
        return current_corpus_id()
    except Exception:  # noqa: BLE001 (a label, never a reason to lose a paid run)
        return None


JUDGE_ERRORS = {"count": 0}


def patient(ask, tries: int = 3, wait: float = 5.0):
    """Retry a judge call, then give up to "" instead of raising.

    The first full run died on one 60 s timeout from NVIDIA's free endpoint and lost every
    answer already paid for (2026-09-30). An empty reply parses as no claims or an UNJUDGED
    verdict, which the report already counts and prints, so a flaky judge shows up as a
    smaller judged sample rather than as a crash or a silent zero.
    """
    import time

    def _ask(prompt: str) -> str:
        for attempt in range(tries):
            try:
                return ask(prompt)
            except Exception as exc:  # noqa: BLE001
                if attempt == tries - 1:
                    JUDGE_ERRORS["count"] += 1
                    print(f"    judge call failed {tries}x, counted as unjudged: "
                          f"{str(exc).splitlines()[0][:100]}")
                    return ""
                time.sleep(wait * (attempt + 1))
        return ""
    return _ask


def judge_llm():
    """The judge: AGRONAUT_JUDGE_PROVIDER / AGRONAUT_JUDGE_MODEL, else the answerer's model.

    Separate on purpose (#182): the course warns (M4) that a judge favours its own model
    family, and grading an answer with the model that wrote it is the weakest setup there is.
    """
    from agent.llm import get_llm, resolve
    provider, model = resolve(os.getenv("AGRONAUT_JUDGE_PROVIDER") or None,
                              os.getenv("AGRONAUT_JUDGE_MODEL") or None)
    if provider == "nvidia" and "gpt-oss" in model:
        return _ReasoningJudge(model)
    return get_llm(provider=provider, model=model, temperature=0.0)


class _ReasoningJudge:
    """gpt-oss on NVIDIA's endpoint, set up so it can finish a judgement at all.

    It is a reasoning model. With the client's default 1,024-token cap it spent every token
    thinking and returned an empty list of claims (finish_reason=length), which scored every
    answer "n/a". With a large cap and default effort it thought past the endpoint's 60 s
    timeout. `Reasoning: low` with an 8,192-token cap split a 317-word answer into 18 claims
    in 30 s and judged each in 3 to 9 s (measured 2026-09-30).
    """

    provider = "nvidia"
    SETTINGS = "Reasoning: low, max_tokens 8192"

    def __init__(self, model: str):
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
        self.model = model
        self._chat = ChatNVIDIA(model=model, temperature=1e-3, max_tokens=8192)

    def invoke(self, prompt: str) -> str:
        out = self._chat.invoke([("system", "Reasoning: low"), ("user", prompt)])
        return out.content if isinstance(out.content, str) else str(out.content)


def _live_backends(ask, embed, answer_fn):
    """Build the real judge, embedder and answerer. Imported lazily so `run()` with fakes
    never needs a model installed or an API key set."""
    from agent.llm import get_llm
    from agronaut_agent import semantic

    answerer = get_llm(temperature=0.0)
    judge = judge_llm()
    _BACKENDS.update(answerer=_names(answerer), judge=_names(judge))
    ask = ask or patient(lambda prompt: judge.invoke(prompt))
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
        return answerer.invoke(
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


_LABELS = _OUT_DIR / "human_labels.json"

# The report the published answer-quality numbers come from, and the one the human labels were
# made on. Other runs (experiments, other judges) sit beside it and are read by name; the newest
# file is NOT the current one: the 2026-10-03 chunking experiments were picked up that way and
# put a Claude-judged experiment where the baseline belonged.
CURRENT_REPORT = _OUT_DIR / "2026-10-06_run.json"


def next_run_name(report: dict, judge: str) -> str:
    """"<judge> (run N)" for the next re-judgement. The original run is run 1 of its judge,
    so re-judging with the same judge is run 2: the consistency pair."""
    taken = set(report.get("judgements", {}))
    n = 2 if report.get("meta", {}).get("judge") == judge else 1
    while f"{judge} (run {n})" in taken:
        n += 1
    return f"{judge} (run {n})"


def agreement_table(report: dict, labels: dict[str, bool] | None) -> list[dict]:
    """Every judgement in the report against the person, and each judge against its own
    repeat run, as rows of {a, b, n, raw, kappa}."""
    sets = {f"{report.get('meta', {}).get('judge', 'judge')} (run 1)": verdicts_of(report)}
    # (the original run's verdicts live on each claim; re-judgements live in `judgements`)
    for name in report.get("judgements", {}):
        sets[name] = verdicts_of(report, name)
    rows = []
    if labels:
        skip = meta_ids(report)
        labels = {k: v for k, v in labels.items() if k not in skip}
        for name, v in sets.items():
            rows.append({"a": "human", "b": name, **agreement(labels, v)})
    names = list(sets)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            rows.append({"a": x, "b": y, **agreement(sets[x], sets[y])})
    return rows


def reviewed_labels(data: dict) -> dict[str, bool]:
    """The blind labels with every reviewed claim's second-look label laid over them."""
    return {**data.get("labels", {}), **data.get("reviewed", {})}


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() or p.parent != Path(".") else _OUT_DIR / p.name


def print_agreement(report: dict, data: dict) -> None:
    """Every judgement against the person (blind, then reviewed) and against each other."""
    labels = data.get("labels")
    print(f"{'':2}{'a':34s} {'b':38s} {'n':>4} {'raw':>6} {'kappa':>6}")
    for r in agreement_table(report, labels):
        print(f"  {r['a']:34s} {r['b']:38s} {r['n']:>4} {_fmt(r['raw'])} {_fmt(r['kappa'])}")
    if data.get("reviewed"):
        # both, always: the blind labels are the independent measurement, the reviewed
        # ones the better-informed one, and the gap between them is worth seeing
        print(f"\nafter review ({len(data['reviewed'])} claims revisited):")
        for r in agreement_table(report, reviewed_labels(data)):
            if r["a"] == "human":
                print(f"  {'human (reviewed)':34s} {r['b']:38s} {r['n']:>4} "
                      f"{_fmt(r['raw'])} {_fmt(r['kappa'])}")
    print(f"\nfaithfulness by judgement ({len(meta_ids(report))} claims about the "
          "sources themselves left out):")
    for name in [None, *report.get("judgements", {})]:
        v = list(verdicts_of(report, name).values())
        print(f"  {_fmt(faithfulness_score(v)['score'])}  "
              f"{name or report.get('meta', {}).get('judge', 'judge') + ' (run 1)'}")
    if not labels:
        print("\nNo human labels yet: agronaut eval label")


def rejudge_report(path: Path, workers: int = 4) -> tuple[str, dict]:  # pragma: no cover - live
    """Re-judge a saved report with the current judge and write the result into it."""
    report = json.loads(path.read_text())
    judge = judge_llm()
    base = _names(judge)
    if _prompt_version() != report.get("meta", {}).get("prompt_version"):
        # a new judging prompt is a new judge: its first run is run 1, never the
        # repeat of a run made under different instructions
        base += f" prompt {_prompt_version()}"
    name = next_run_name(report, base)
    # Progress lives beside the report until the run completes, so the report never
    # holds half a judgement, and the same command picks up where a stopped run ended.
    partial = path.with_suffix(".partial.json")
    saved = json.loads(partial.read_text()) if partial.exists() else {}
    done = saved.get("verdicts", {}) if saved.get("name") == name else {}
    if done:
        print(f"resuming {name}: {len(done)} claims already judged")
    total = sum(1 for q in report["per_query"] for c in q.get("claims", [])
                if not is_meta_claim(c["claim"]))

    def _save(verdicts, n, of):
        partial.write_text(json.dumps({"name": name, "verdicts": verdicts}))
        print(f"  [{n}/{of} queries] {len(verdicts)}/{total} claims judged", flush=True)

    verdicts = rejudge(report, patient(lambda prompt: judge.invoke(prompt)), done=done,
                       workers=workers, on_query=_save)
    report.setdefault("judgements", {})[name] = verdicts
    report.setdefault("judgement_meta", {})[name] = {**run_meta(), "judge": base}
    path.write_text(json.dumps(report, indent=2))
    partial.unlink(missing_ok=True)
    print(f"{name}: faithfulness {_fmt(faithfulness_score(list(verdicts.values()))['score'])}"
          f" over {len(verdicts)} claims. Saved into {path}")
    return name, verdicts


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, help="score only the first N golden-set queries")
    ap.add_argument("--save", help="write the full report to this JSON path")
    ap.add_argument("--rejudge", metavar="REPORT",
                    help="rule on a saved report's claims again with the current judge "
                         "(AGRONAUT_JUDGE_PROVIDER/MODEL); run twice for consistency")
    ap.add_argument("--workers", type=int, default=4,
                    help="judge calls in flight at once during --rejudge (default 4)")
    ap.add_argument("--agreement", metavar="REPORT",
                    help="print agreement and kappa: judges vs human_labels.json and vs "
                         "each other (no model calls)")
    args = ap.parse_args()

    if args.agreement:
        report = json.loads(_resolve(args.agreement).read_text())
        print_agreement(report, json.loads(_LABELS.read_text()) if _LABELS.exists() else {})
        return 0

    if os.getenv("AGRONAUT_FAITHFULNESS_EVAL", "").lower() not in {"1", "true", "yes"}:
        print("Generation eval is opt-in — it calls the configured LLM over the network.")
        print("  Run: AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval")
        return 0

    if args.rejudge:
        rejudge_report(_resolve(args.rejudge), workers=args.workers)
        return 0

    out = _resolve(args.save) if args.save else None
    report = run(limit=args.limit, save_to=out)
    _print(report)
    if out:
        _write(out, report)
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

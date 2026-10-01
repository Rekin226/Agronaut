"""Score retrieval on the query the MODEL writes, not the one the grower typed (#199).

scripts/retrieval_eval.py feeds the golden queries straight to the retriever. In production they
never arrive that way: the model reads the grower's message and writes its own `query` argument
for search_knowledge_base. For an English-only retriever that step is the whole question, because
it decides whether a French or Mandarin message is searched in English or passed through as is.

So this asks the configured model, per golden query, with the real SYSTEM_PROMPT, the grower's
message and search_knowledge_base bound. The `query` it writes is then scored exactly as
retrieval_eval scores a golden query, per language.

The call is NOT forced. Forcing would be cleaner, but Anthropic rejects a forced tool choice
while extended thinking is on, and production never forces it either. So a turn where the model
answers without searching is a real outcome, counted as a miss and reported per language as
`did_not_search`: a model that searches for an English question and not for the same question in
French is part of the gap this measures.

What it simplifies: only search_knowledge_base is bound, not all 30 tools. The other tools'
descriptions do not plausibly change how this one's argument is phrased, and leaving them out cuts
each call from ~70K to ~15K characters of prompt. With them, the model would sometimes pick a
different tool, which this harness would read as not searching.

It calls a model, so it costs money on a paid provider. --limit runs a few queries per language
first. --description FILE swaps in another tool description, to measure before against after.

    python -m scripts.query_language_eval --limit 3          # a few per language first
    python -m scripts.query_language_eval --save docs/dpg/retrieval_eval/agent_query_after.json
    python -m scripts.query_language_eval --description old.txt --save ..._before.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.retrieval_eval import (  # noqa: E402
    _GOLDEN,
    _GOLDEN_ML,
    by_language,
    merge_golden,
    score_query,
)


def written_query(message, fallback: str) -> tuple[str, bool]:
    """The `query` argument the model put in its search_knowledge_base call.

    Returns (query, called). A model that did not call the tool, or called it without a query,
    counts as searching for nothing: falling back to the grower's text would credit the model with
    a search it never made.
    """
    for call in getattr(message, "tool_calls", None) or []:
        if call.get("name") == "search_knowledge_base":
            q = (call.get("args") or {}).get("query")
            if isinstance(q, str) and q.strip():
                return q.strip(), True
    return fallback, False


def run(limit: int | None = None, description: str | None = None, langs=None) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.llm import get_chat_model, resolve
    from agronaut_agent import rag
    from agronaut_agent.core import SYSTEM_PROMPT
    from agronaut_agent.tools import search_knowledge_base

    tool = search_knowledge_base
    if description is not None:
        tool = tool.model_copy(update={"description": description})
    provider, model_name = resolve()
    bound = get_chat_model().bind_tools([tool])

    golden = merge_golden(json.loads(_GOLDEN.read_text()), json.loads(_GOLDEN_ML.read_text()))
    k = golden.get("k", 3)
    queries = [q for q in golden["queries"] if not langs or q["lang"] in langs]
    if limit:
        counts: dict[str, int] = {}
        kept = []
        for q in queries:
            if counts.get(q["lang"], 0) < limit:
                counts[q["lang"]] = counts.get(q["lang"], 0) + 1
                kept.append(q)
        queries = kept

    inf = float("inf")
    per_query = []
    for q in queries:
        t0 = time.perf_counter()
        reply = bound.invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(q["query"])])
        model_ms = (time.perf_counter() - t0) * 1000
        searched, called = written_query(reply, q["query"])
        hits = rag.retrieve(searched, k) if called else []
        raw = rag.retrieve(searched, k, max_dist=inf, max_per_source=0) if called else []
        scored = [h["score"] for h in raw if h.get("score") is not None]
        row = score_query([h["source"] for h in hits], q["relevant"], k)
        row.update(id=q["id"], lang=q["lang"], query=q["query"], searched=searched,
                   called_tool=called, relevant=q["relevant"], model_ms=round(model_ms),
                   raw_top_score=min(scored) if scored else None,
                   floor_returned_nothing=called and not hits)
        per_query.append(row)
        print(f"  [{q['id']}] {'hit ' if row['hit'] else 'MISS'} {searched[:90]}", flush=True)

    summary = {"provider": provider, "model": model_name,
               "tool_description": tool.description, "queries": len(per_query),
               "did_not_search": sum(1 for r in per_query if not r["called_tool"]),
               "by_lang": by_language(per_query, [], k)}
    for lang, r in summary["by_lang"].items():
        r["did_not_search"] = sum(1 for q in per_query if q["lang"] == lang and not q["called_tool"])
    return {"summary": summary, "per_query": per_query}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only the first N queries of each language")
    ap.add_argument("--lang", action="append", help="only this language (repeatable)")
    ap.add_argument("--description", metavar="FILE",
                    help="use this text as the tool description instead of the live docstring")
    ap.add_argument("--save", metavar="FILE")
    args = ap.parse_args()

    desc = Path(args.description).read_text().strip() if args.description else None
    report = run(limit=args.limit, description=desc, langs=args.lang)
    s = report["summary"]
    print(f"\n{s['provider']}/{s['model']}: {s['queries']} queries, "
          f"{s['did_not_search']} without a search")
    print(f"  {'lang':<5}{'n':>4}{'hit':>8}{'recall':>8}{'MAP':>8}{'silenced':>10}{'no search':>11}")
    for lang, r in s["by_lang"].items():
        print(f"  {lang:<5}{r['queries']:>4}{r['hit_rate']:>8.3f}{r['recall@k']:>8.3f}"
              f"{r['MAP@k']:>8.3f}{r['floor_silenced_on_topic']:>7}/{r['queries']:<2}"
              f"{r['did_not_search']:>8}/{r['queries']}")
    if args.save:
        Path(args.save).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"\nsaved -> {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

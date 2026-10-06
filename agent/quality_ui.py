"""Streamlit Quality page: how good Agronaut is, measured, with what each number rests on.

Reads saved results only (the eval history, the published answers report, the twin
validation file, the local usage log). No model is called from this page, and the only
button that runs anything runs the free, local evals. The same numbers print in the
terminal with `agronaut eval`, from the same module (agronaut_agent/evals.py).

The rule this page follows: every number shows its n and date; a judged score never
appears without how well the judge agreed with a person; a result measured on another
corpus says it is stale. A green tile without those would claim more than is known.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agronaut_agent import evals
from agronaut_agent import paths as _paths


def _age(when: str | None) -> str:
    return evals.when_label(when, evals.age_days(when))


def _f(v, digits: int = 3) -> str:
    return evals._f(v, digits)


def _twin_section() -> None:
    st.markdown("#### 1. Can you trust the design numbers?")
    path = _paths.corpus_root() / "data" / "twin_validation.json"
    if not path.exists():
        path = _paths.PROJECT_ROOT / "data" / "twin_validation.json"
    if not path.exists():
        st.info("No twin validation record in this install.")
        return
    v = json.loads(path.read_text(encoding="utf-8"))
    s = v.get("summary", {})
    c1, c2, c3 = st.columns(3)
    c1.metric("Ponds scored", s.get("n_scored"))
    c2.metric("Direction right", f"{s.get('n_positive_shape_correlation')} of {s.get('n_scored')}")
    c3.metric("Beats a trend baseline", f"{s.get('n_beats_both_nulls')} of {s.get('n_scored')}")
    st.warning(s.get("claim", ""))
    with st.expander("How this was measured"):
        st.write(v.get("method", ""))
        st.caption(f"Dataset: {v.get('dataset', '')}. Generated {v.get('generated_utc', '')[:10]}.")
        for cav in v.get("caveats", []):
            st.write(f"- {cav}")


def _retrieval_section(last: dict, corpus_now: str | None, rows: list[dict]) -> None:
    st.markdown("#### 2. Does it find the right sources?")
    row = last.get("retrieval")
    if not row:
        st.info("Not measured on this machine yet. Use **Run free evals** below, or "
                "`agronaut eval retrieval` in a terminal.")
        return
    m = row["metrics"]
    k = m.get("k", 3)
    cols = st.columns(5)
    for col, (label, key) in zip(cols, [("Hit rate", "hit_rate"), (f"Recall@{k}", "recall@k"),
                                        ("MRR", "MRR"), (f"MAP@{k}", "MAP@k"),
                                        (f"Precision@{k}", "precision@k")]):
        col.metric(label, _f(m.get(key)))
    st.caption(f"{m.get('queries')} real grower questions, top {k} passages, scored by source "
               f"document. Measured {_age(row['at'])} on Agronaut {row.get('version')}.")
    if evals.is_stale(row, corpus_now):
        st.error("Stale: measured on a different knowledge base than the one loaded now. "
                 "Run it again.")
    st.write(f"Relevance floor: refused **{m.get('floor_rejected_off_topic')} of "
             f"{m.get('negative_controls')}** off-topic questions and wrongly refused "
             f"**{m.get('floor_silenced_on_topic')} of {m.get('queries')}** real ones.")
    langs = m.get("by_lang")
    if langs:
        st.write("By language, with the question passed to the retriever as written "
                 "(the agent rewrites it in English in real use):")
        st.dataframe(pd.DataFrame([{"language": lang, "n": v["queries"],
                                    "hit": round(v["hit_rate"], 3),
                                    f"recall@{k}": round(v["recall@k"], 3),
                                    "MRR": round(v["MRR"], 3), f"MAP@{k}": round(v["MAP@k"], 3),
                                    "wrongly refused": v["floor_silenced_on_topic"]}
                                   for lang, v in langs.items()]),
                     hide_index=True)
    trend = [{"run": r["at"][:16].replace("T", " "), f"recall@{k}": r["metrics"].get("recall@k"),
              f"MAP@{k}": r["metrics"].get("MAP@k"), "MRR": r["metrics"].get("MRR")}
             for r in rows if r["eval"] == "retrieval"]
    if len(trend) > 1:
        st.line_chart(pd.DataFrame(trend).set_index("run"))
    _topics_table(m.get("by_topic") or [], k)


def _topics_table(topics: list[dict], k: int) -> None:
    """Retrieval per topic, weakest first: where to look first."""
    if not topics:
        return
    st.markdown("**Weakest topics** (where to look first)")
    st.dataframe(pd.DataFrame([{
        "topic": t["name"], "n": t["n"], "hit": t["hit_rate"], f"recall@{k}": t["recall@k"],
        "MRR": t["MRR"], f"MAP@{k}": t["MAP@k"], "missed": " ".join(t["misses"])}
        for t in topics]), hide_index=True)
    st.caption("A few questions per topic, so one question can swing a topic from 0 to 1. "
               "Hit 1.00 with a low MRR: found but ranked low. A miss: not found at all; "
               "`agronaut eval retrieval` shows what came back instead.")


def _answers_section(a: dict | None) -> None:
    st.markdown("#### 3. Do the answers stick to those sources?")
    if not a:
        st.info("No answers report in this install. `agronaut eval answers --run` makes one "
                "(it calls models, and asks first).")
        return
    d = a.get("default") or {}
    lo, hi = a.get("range") or (None, None)
    c1, c2, c3 = st.columns(3)
    c1.metric("Faithfulness (default judge)", _f(d.get("faithfulness"), 2),
              help="Share of an answer's claims that the retrieved passages back.")
    c2.metric("Judge vs a person (kappa)", f"{_f(d.get('kappa_blind'), 2)} blind",
              f"{_f(d.get('kappa_reviewed'), 2)} after review", delta_color="off",
              help="Agreement beyond chance. 0.21 to 0.40 is fair, above 0.60 substantial."
                   + (f" Measured for this judge on {d['kappa_from']}, where the human labels "
                      "were made." if d.get("kappa_from") else ""))
    c3.metric("Citation accuracy", _f(a.get("citation_accuracy"), 2),
              help="Cited sources that were really retrieved. Checked by code, no model.")
    st.warning(f"Not yet validated. Faithfulness is {_f(lo, 2)} to {_f(hi, 2)} depending on "
               f"the judge, and the judges were checked against one person on "
               f"{a.get('labels')} claims.")
    st.caption(f"{a.get('queries')} answers by {a.get('answerer')}, {a.get('claims')} claims, "
               f"report {a.get('report')}, measured {_age(a.get('date'))}. Response relevancy "
               f"{_f(a.get('response_relevancy'), 2)}."
               + (f" Judge agreement measured on {a['kappa_from']}." if a.get("kappa_from")
                  else ""))
    if a.get("stale"):
        st.error("Stale: these answers were written from a different knowledge base than the "
                 "one loaded now.")
    elif a.get("stale") is None:
        st.caption("This report does not record which knowledge base it measured, so it "
                   "cannot tell whether it is out of date.")
    st.caption("Answer quality does not refresh by itself: a new measurement calls models and "
               "costs credit. `agronaut eval answers --run` (it names the models and asks).")
    with st.expander("Every judge"):
        st.dataframe(pd.DataFrame([{
            "judge": j["short"] + ("  (default)" if j["current_prompt"] else ""),
            "faithfulness": None if j["faithfulness"] is None else round(j["faithfulness"], 3),
            "kappa blind": None if j["kappa_blind"] is None else round(j["kappa_blind"], 2),
            "kappa reviewed": (None if j["kappa_reviewed"] is None
                               else round(j["kappa_reviewed"], 2)),
            "claims": j["claims"]} for j in a["judges"]]),
            hide_index=True)
    _answers_weak_spots()


def _answers_weak_spots() -> None:
    """Faithfulness per topic and the claims the judge found unsupported, to read."""
    try:
        loaded = evals.load_answers_report()
        w = evals.answers_weak_spots(loaded[0]) if loaded else None
    except Exception:  # noqa: BLE001 (the rest of the page must still render)
        w = None
    if not w or not w["topics"]:
        return
    with st.expander("Where answers stray: unsupported claims by topic"):
        st.dataframe(pd.DataFrame([{
            "topic": t["name"], "claims": t["claims"], "unsupported": t["unsupported"],
            "faithfulness": None if t["faithfulness"] is None else round(t["faithfulness"], 2)}
            for t in w["topics"]]), hide_index=True)
        names = ["all topics"] + [t["name"] for t in w["topics"] if t["unsupported"]]
        pick = st.selectbox("Show unsupported claims for", names, key="quality_worst_topic")
        qs = [q for q in w["questions"] if pick == "all topics" or q["name"] == pick]
        for q in qs[:10]:
            st.markdown(f"**[{q['id']}] {q['query']}** · {len(q['unsupported'])} of "
                        f"{q['claims']} claims unsupported")
            for c in q["unsupported"]:
                st.markdown(f"- {c['claim']}")
            st.caption("Sources it was given: " + ("; ".join(q["sources"]) or "none"))
        st.caption(f"Judge {w['judge']}. Its verdicts agree with a person only fairly, so treat "
                   "each claim as a lead to read. True-but-absent-from-the-sources counts as "
                   "unsupported, which points at retrieval or the knowledge base as often as at "
                   "the answer. Terminal: `agronaut eval answers --worst`.")


def _safety_section(last: dict) -> None:
    st.markdown("#### 4. Is the advice safe?")
    row = last.get("safety")
    if not row:
        st.info("Not measured on this machine yet. Use **Run free evals** below, or "
                "`agronaut eval safety`.")
        return
    m = row["metrics"]
    crit = m.get("critical_failures", 0)
    (st.error if crit else st.success)(
        f"{m.get('passed')} of {m.get('total')} probes passed"
        + (f", {crit} CRITICAL failure(s)" if crit else "") + f". Measured {_age(row['at'])}.")
    cats = m.get("by_category") or {}
    if cats:
        st.dataframe(pd.DataFrame([{"category": c, "passed": v["passed"], "total": v["total"]}
                                   for c, v in sorted(cats.items())]),
                     hide_index=True)


def _live_section(last: dict) -> None:
    st.markdown("#### 5. Is real use still where the eval says?")
    try:
        from agronaut_agent import rag
        from agronaut_agent.analytics import Analytics
        log = Analytics()
        rows, summary, floor = log.rows(), log.summarize(), rag.max_distance()
    except Exception as exc:  # noqa: BLE001 (the rest of the page must still render)
        st.info(f"Usage log unavailable: {exc}")
        return
    worst = (last.get("retrieval") or {}).get("metrics", {}).get("worst_on_topic")
    live = evals.live_retrieval(rows, floor, worst)
    if not live["n"]:
        st.info("No real searches recorded yet. This fills in as people use the bot (it "
                "records shapes and timings only, never their words).")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Real searches", live["n"])
        c2.metric("Refused by the floor", f"{live['refused']} of {live['n']}")
        if live["beyond_calibration"] is not None:
            c3.metric("Further out than any eval question",
                      f"{live['beyond_calibration']} of {live['scored']}",
                      help=f"Closest passage further than {_f(worst)}, the worst real question "
                           f"in the golden set, but inside the floor ({_f(floor, 2)}). A rising "
                           "share means people ask about things the eval does not cover.")
        if live["n"] < 30:
            st.caption(f"n={live['n']}: too few searches to read a trend from yet.")
    lat, g, fb = summary["latency"], summary["grounding"], summary["feedback"]
    c1, c2, c3 = st.columns(3)
    t = lat["turn"]
    c1.metric("Reply time p50 / p95",
              f"{t['p50']} / {t['p95']} ms" if t["n"] else "n/a", help=f"n={t['n']} turns")
    c2.metric("Replies with an unbacked figure",
              f"{g['turns_with_ungrounded']} of {g['turns_checked']}" if g["turns_checked"]
              else "n/a", help="A number in the reply that no tool or user gave (#180).")
    c3.metric("Feedback", f"{fb['up']} up / {fb['down']} down")
    traces = log.traces(limit=10)
    if traces:
        with st.expander("Latest turns (shapes only, no text)"):
            st.dataframe(pd.DataFrame([{
                "date": t2["date"], "channel": t2["channel"], "total ms": t2["latency_ms"],
                "model ms": t2["llm_ms"],
                "steps": " > ".join(e["event"] for e in t2["events"])}
                for t2 in reversed(traces)]), hide_index=True)


def _run_section() -> None:
    st.markdown("#### Run the evals")
    if st.button("Run free evals (safety + retrieval in en/fr/zh)", type="primary"):
        from agronaut_agent import eval_cli
        with st.spinner("Running the safety probes..."):
            eval_cli.run_safety()
        with st.spinner("Running retrieval (loads the index, about a minute)..."):
            eval_cli.run_retrieval(multilingual=True)
        st.rerun()
    st.caption("No model is called and nothing leaves this machine. The evals that call a "
               "model run from a terminal, and say what they will cost first:")
    st.code("agronaut eval answers --run\nagronaut eval model qwen3.5:4b", language="bash")


def render_quality() -> None:
    st.subheader("Quality")
    st.caption("How good Agronaut is, measured, with what each number rests on. Nothing on "
               "this page calls a model. In a terminal: `agronaut eval`.")
    rows = evals.history()
    last = evals.latest(rows)
    corpus_now = evals.current_corpus_id()
    try:
        answers = evals.load_answers()
    except Exception:  # noqa: BLE001
        answers = None
    _twin_section()
    st.divider()
    _retrieval_section(last, corpus_now, rows)
    st.divider()
    _answers_section(answers)
    st.divider()
    _safety_section(last)
    st.divider()
    _live_section(last)
    st.divider()
    _run_section()

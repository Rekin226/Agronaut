"""`agronaut eval`: every quality measurement behind one command, with a memory.

    agronaut eval                       status: last result of each eval, its age, stale or not
    agronaut eval all                   run every free eval, then show the status
    agronaut eval retrieval             recall@k, precision@k, MRR, MAP@k and the relevance floor
    agronaut eval retrieval --by-topic  the same per topic, weakest first, with the missed questions
    agronaut eval safety                the advice-safety golden set (exits 1 on a critical miss)
    agronaut eval answers               faithfulness and citations from the published report
    agronaut eval answers --run         a fresh run: generates and judges answers (calls models)
    agronaut eval answers --rejudge     rule on the saved claims again with the current judge
    agronaut eval answers --worst       faithfulness per topic and every unsupported claim
    agronaut eval label [--review]      label claims by hand, or take a second look at disputes
    agronaut eval model qwen3.5:4b      does this local model drive the engine on this machine?

Free by default. Retrieval, safety and status run locally with no model call; anything that
calls a model names it and asks first. Each run adds one line of numbers to the eval history,
which is what `status` and the web app's Quality page read. The `python -m scripts.*` entry
points still work and do the same thing without the history.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from . import evals

evals.scripts_from_checkout()   # the repo's scripts/, not a stale site-packages copy


def _quiet() -> None:
    """Keep the embedding library's start-up chatter (a HEAD request per config file, a
    token hint, the device it picked) out of the result. Warnings still show."""
    for key, value in (("TOKENIZERS_PARALLELISM", "false"), ("HF_HUB_VERBOSITY", "error"),
                       ("HF_HUB_DISABLE_PROGRESS_BARS", "1"), ("TRANSFORMERS_VERBOSITY", "error")):
        os.environ.setdefault(key, value)
    for name in ("httpx", "httpcore", "sentence_transformers", "faiss", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # "unauthenticated requests to the HF Hub" is advice for heavy downloaders; the
    # embedding model here is downloaded once and cached.
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    # The web loader's user-agent hint and the old embeddings-class notice come from the
    # retrieval stack's imports, not from anything this run does.
    os.environ.setdefault("USER_AGENT", "agronaut-eval")
    import warnings
    warnings.filterwarnings("ignore", message=".*HuggingFaceEmbeddings.*")


def _confirm(what: str, yes: bool) -> bool:
    if yes:
        return True
    print(what)
    try:
        return input("Continue? [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        print("No answer (not an interactive terminal). Re-run with --yes to proceed.")
        return False


# --- runners (also used by the web app's Run button) ------------------------------------------

def run_retrieval(multilingual: bool = False, k: int | None = None,
                  save: bool = True) -> tuple[dict, dict]:
    _quiet()
    from scripts import retrieval_eval as R

    report = R.run(k=k, multilingual=multilingual)
    metrics = evals.retrieval_metrics(report)
    row = (evals.record("retrieval", metrics, corpus=evals.current_corpus_id()) if save
           else {"metrics": metrics})
    return report, row


def run_safety(save: bool = True) -> tuple[dict, dict]:
    from scripts import safety_eval as S

    r = S.run()
    crit = [f for f in r["failures"] if f["severity"] == "critical"]
    metrics = {"passed": r["passed"], "total": r["total"], "score": r["score"],
               "critical_failures": len(crit),
               "by_category": r["by_category"],
               "failures": [f["id"] for f in r["failures"]]}
    row = evals.record("safety", metrics) if save else {"metrics": metrics}
    return r, row


# --- commands ---------------------------------------------------------------------------------

def _print_status(rows: list[dict], corpus_now: str | None) -> None:
    print(f"Agronaut evals   (corpus {corpus_now or 'unknown'}, version {evals._version()})\n")
    for s in rows:
        when = evals.when_label(s["when"], s["age_days"])
        print(f"  {s['eval']:<10} {when:<24} {s['headline'] or s['next']}")
        if s["source"] == "published":
            print(f"  {'':<10} {'':<24} published report, not run on this machine; a fresh "
                  f"run calls models: {s['next']}")
        if s["stale"]:
            print(f"  {'':<10} {'':<24} STALE: measured on a different knowledge base than "
                  f"the one loaded now. Re-run: {s['next']}")
        if s["eval"] == "model" and s.get("model"):
            print(f"  {'':<10} {'':<24} model: {s['model']}")
    print("\nIn the browser: agronaut web, then Quality in the sidebar.")


def cmd_status(args) -> int:
    corpus_now = evals.current_corpus_id()
    rows = evals.status(evals.history(), corpus_now, answers=_answers_or_none())
    if getattr(args, "json", False):
        print(json.dumps({"corpus": corpus_now, "evals": rows}, indent=2, default=str))
        return 0
    _print_status(rows, corpus_now)
    return 0


def _answers_or_none() -> dict | None:
    try:
        return evals.load_answers()
    except Exception as exc:  # noqa: BLE001 (a status line must survive a bad report file)
        print(f"  (could not read the answers report: {exc})", file=sys.stderr)
        return None


def cmd_retrieval(args) -> int:
    from scripts import retrieval_eval as R

    report, row = run_retrieval(multilingual=args.multilingual, k=args.k)
    if args.json:
        print(json.dumps({"metrics": row["metrics"], "report": report}, indent=2))
        return 0
    if args.by_topic:
        print(evals.headline("retrieval", row["metrics"]))
        _print_topics(row["metrics"]["by_topic"], row["metrics"].get("k", 3))
        print(f"\nRecorded in {evals.history_path()}")
        return 0
    baseline = json.loads(Path(args.compare).read_text()) if args.compare else None
    R._print(report, baseline)
    if baseline:
        for reason in R.corpus_drift(baseline.get("corpus"), R.corpus_fingerprint()):
            print(f"  ! corpus differs from the baseline: {reason}")
    print(f"\nRecorded in {evals.history_path()}")
    return 0


def _print_topics(rows: list[dict], k: int = 3) -> None:
    langs = sorted({lang for r in rows for lang in r.get("langs", {})})
    head = "".join(f"{lang + ' hit':>8}" for lang in langs)
    print(f"\nBy topic, weakest first (English; {', '.join(langs) + ' passed in raw, ' if langs else ''}"
          "untranslated)\n")
    print(f"  {'topic':<18}{'n':>3}{'hit':>7}{'recall':>8}{'MRR':>7}{'MAP':>7}{head}  missed")
    for r in rows:
        extra = "".join(f"{f'{h}/{n}':>8}" for h, n in (r.get("langs", {}).get(lang, (0, 0))
                                                         for lang in langs))
        print(f"  {r['name']:<18}{r['n']:>3}{r['hit_rate']:>7.2f}{r['recall@k']:>8.2f}"
              f"{r['MRR']:>7.2f}{r['MAP@k']:>7.2f}{extra}  {' '.join(r['misses'])}")
    print("\n  Each topic holds only a few questions, so one question can swing it from 0 to 1."
          "\n  A low MRR with hit 1.00 means found but ranked low; a miss means not found at"
          "\n  all. `agronaut eval retrieval` shows what came back instead for each miss.")


def _print_worst(w: dict, topic: str | None, limit: int) -> None:
    print(f"Where answers stray from their sources   (judge {w['judge']}, report {w['date']})\n")
    print(f"  {'topic':<18}{'claims':>7}{'unsupported':>13}{'faithful':>10}")
    for t in w["topics"]:
        print(f"  {t['name']:<18}{t['claims']:>7}{t['unsupported']:>13}"
              f"{evals._f(t['faithfulness'], 2):>10}")
    qs = [q for q in w["questions"] if topic in (None, q["topic"], q["name"])]
    shown = qs[:limit]
    print(f"\nUnsupported claims, by question ({len(shown)} of {len(qs)}"
          f"{' in ' + topic if topic else ''}):")
    for q in shown:
        print(f"\n  [{q['id']}] {q['query']}   ({len(q['unsupported'])} of {q['claims']} claims)")
        for c in q["unsupported"]:
            print(f"    - {c['claim']}")
        print(f"    sources it was given: {'; '.join(q['sources']) or '(none)'}")
    print("\n  The verdicts are a model's, agreeing with a person only fairly (see `agronaut eval"
          "\n  answers`): treat each line as a lead to read. A claim true in the world but absent"
          "\n  from the sources counts as unsupported: that points at retrieval or the knowledge"
          "\n  base, not only at the answer.")


def cmd_safety(args) -> int:
    r, row = run_safety()
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"Advice-safety golden set: {r['passed']}/{r['total']} passed "
              f"(score {r['score']:.3f})")
        for cat, s in sorted(r["by_category"].items()):
            print(f"  {cat:12s} {s['passed']}/{s['total']}")
        for f in r["failures"]:
            print(f"  FAIL [{f['severity']}] {f['id']} ({f['category']}): {f['reason']}")
    return 1 if row["metrics"]["critical_failures"] else 0


def _print_answers(a: dict) -> None:
    lo, hi = a["range"] or (None, None)
    print(f"Answer quality   ({a['report']}, {a['date']}, answers by {a['answerer']})")
    print(f"  {a['queries']} answers, {a['claims']} claims judged; {a['labels']} labelled by "
          f"one person ({a['reviewed']} revisited)\n")
    print(f"  {'judge':<40} {'faithful':>8} {'kappa blind':>12} {'reviewed':>9}")
    for j in a["judges"]:
        mark = "  <- default" if j["current_prompt"] else ""
        print(f"  {j['short']:<40} {evals._f(j['faithfulness']):>8} "
              f"{evals._f(j['kappa_blind'], 2):>12} {evals._f(j['kappa_reviewed'], 2):>9}{mark}")
    print(f"\n  faithfulness range {evals._f(lo, 2)} to {evals._f(hi, 2)}  ·  citation accuracy "
          f"{evals._f(a['citation_accuracy'], 2)} ({a['fabricated_citations']} fabricated)  ·  "
          f"response relevancy {evals._f(a['response_relevancy'], 2)}")
    print("\n  Kappa is agreement with the person beyond chance: 0.21 to 0.40 is fair, above"
          "\n  0.60 substantial. One labeller on a small sample: not yet validated.")


def cmd_answers(args) -> int:
    from scripts import faithfulness_eval as F

    if args.run or args.rejudge:
        from agent.llm import resolve
        judge = "/".join(resolve(os.getenv("AGRONAUT_JUDGE_PROVIDER") or None,
                                 os.getenv("AGRONAUT_JUDGE_MODEL") or None))
        if args.run:
            answerer = "/".join(resolve(None, None))
            what = (f"This writes {args.limit or 'all'} golden-set answers with {answerer} and "
                    f"judges every claim with {judge}. Both are model calls: on a hosted key "
                    "they cost credit, and a full run takes a while.")
        else:
            what = (f"This rules on every saved claim again with {judge} ({args.workers} at a "
                    "time). On a hosted key that costs credit; a stopped run resumes.")
        if not _confirm(what, args.yes):
            return 1
        if args.run:
            from datetime import date
            out = evals.answers_dir() / f"{date.today().isoformat()}_run.json"
            report = F.run(limit=args.limit, save_to=out)
            F._print(report)
            print(f"\nSaved {out}")
        else:
            path = (evals.answers_report_path(args.report) if args.rejudge == "__latest__"
                    else F._resolve(args.rejudge))
            if path is None:
                print("No saved report to re-judge: agronaut eval answers --run")
                return 1
            F.rejudge_report(path, workers=args.workers)
        a = evals.load_answers()
        if a:
            evals.record("answers", evals.answers_metrics(a), model=judge,
                         corpus=evals.current_corpus_id())
        return 0

    path = evals.answers_report_path(args.report)
    if path is None:
        print(f"No answers report {args.report or 'yet'}. Make one: agronaut eval answers --run"
              if not args.report else f"No answers report named {args.report}.")
        return 0
    if args.worst:
        report = json.loads(path.read_text(encoding="utf-8"))
        w = evals.answers_weak_spots(report)
        if args.json:
            print(json.dumps(w, indent=2))
        else:
            _print_worst(w, args.topic, args.limit or 10)
        return 0
    if args.agreement:
        report = json.loads(path.read_text(encoding="utf-8"))
        F.print_agreement(report, evals.labels_for(path.name, path.parent) or {})
        return 0
    a = evals.load_answers(name=args.report)
    if args.json:
        print(json.dumps(a, indent=2, default=str))
        return 0
    _print_answers(a)
    print("\n  Every comparison: agronaut eval answers --agreement")
    return 0


def cmd_label(args) -> int:
    from scripts import label_claims as L

    path = evals.answers_report_path()
    if path is None:
        print("No answers report to label yet: agronaut eval answers --run")
        return 0
    argv = [path.name, "--n", str(args.n)] + (["--review"] if args.review else [])
    return L.main(argv)


def _ollama_models(host: str) -> list[str] | None:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as f:
            return [m.get("name", "") for m in json.load(f).get("models", [])]
    except Exception:  # noqa: BLE001 (not running is an answer, not a crash)
        return None


def cmd_model(args) -> int:
    from agent.llm import DEFAULT_MODELS
    tag = args.tag or DEFAULT_MODELS["ollama"]
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    host = host if host.startswith("http") else "http://" + host
    have = _ollama_models(host)
    if have is None:
        print(f"Ollama is not answering at {host}. Start it (open the Ollama app, or "
              "`ollama serve`), then run this again.")
        return 2
    if tag not in have and f"{tag}:latest" not in have:
        print(f"{tag} is not pulled on this machine. Pulled: {', '.join(have) or 'none'}.\n"
              f"Pull it first (a download of several GB): ollama pull {tag}")
        return 2
    from scripts import local_model_check as M

    print(f"Running one real turn through {tag}. On a laptop CPU this can take minutes.\n")
    report = M.run(tag, Path(args.ollama_log) if args.ollama_log else None)
    print(M.summary_markdown(report))
    v = report["verdict"]
    evals.record("model", {"passed": v["passed"], "reasons": v["reasons"],
                           "turn_seconds": report["turn_seconds"],
                           "peak_mb": report["peak_ollama_memory_mb"],
                           "tools_called": v["tools_called"]}, model=tag)
    return 0 if v["passed"] else 1


def cmd_all(args) -> int:
    """Every free eval, then the status. A model check only when a tag is given: it is free
    but slow, and it needs a pulled model."""
    print("Advice safety ...", flush=True)
    _, srow = run_safety()
    print(f"  {evals.headline('safety', srow['metrics'])}")
    print("Retrieval (English, French, Mandarin) ...", flush=True)
    _, rrow = run_retrieval(multilingual=True)
    print(f"  {evals.headline('retrieval', rrow['metrics'])}")
    if args.model:
        print(f"Local model {args.model} ...", flush=True)
        ns = type("A", (), {"tag": args.model, "ollama_log": None})()
        cmd_model(ns)
    print()
    code = cmd_status(args)
    return 1 if srow["metrics"]["critical_failures"] else code


def main(args) -> int:
    handlers = {None: cmd_status, "status": cmd_status, "all": cmd_all,
                "retrieval": cmd_retrieval, "safety": cmd_safety, "answers": cmd_answers,
                "label": cmd_label, "model": cmd_model}
    return handlers[getattr(args, "eval_cmd", None)](args)


def add_parser(sub) -> None:
    """`agronaut eval ...`, attached to the top-level parser in cli.py."""
    ev = sub.add_parser("eval", help="measure retrieval, answers, safety and local models "
                                     "(alone: the status of each)")
    ev.add_argument("--json", action="store_true", help="status as JSON")
    es = ev.add_subparsers(dest="eval_cmd")
    st = es.add_parser("status", help="last result of each eval, its age, stale or not")
    st.add_argument("--json", action="store_true")
    al = es.add_parser("all", help="run every free eval (safety, retrieval in en/fr/zh), "
                                   "then show the status")
    al.add_argument("--model", metavar="TAG", help="also check this local Ollama model")
    al.add_argument("--json", action="store_true")
    r = es.add_parser("retrieval", help="recall@k, precision@k, MRR, MAP@k, relevance floor")
    r.add_argument("--multilingual", action="store_true",
                   help="also score the French and Mandarin questions, per language")
    r.add_argument("--compare", metavar="FILE", help="print deltas against a saved baseline")
    r.add_argument("--by-topic", action="store_true",
                   help="the metrics per topic, weakest first, with the questions that missed")
    r.add_argument("--k", type=int, default=None)
    r.add_argument("--json", action="store_true")
    s = es.add_parser("safety", help="the advice-safety golden set")
    s.add_argument("--json", action="store_true")
    a = es.add_parser("answers", help="faithfulness, citations and relevancy of answers")
    a.add_argument("--agreement", action="store_true",
                   help="every judge against the person and each other")
    a.add_argument("--run", action="store_true",
                   help="generate and judge answers now (calls models; asks first)")
    a.add_argument("--rejudge", nargs="?", const="__latest__", metavar="REPORT",
                   help="rule on a saved report's claims again with the current judge")
    a.add_argument("--workers", type=int, default=4)
    a.add_argument("--report", metavar="NAME",
                   help="read this report instead of the current one, e.g. "
                        "2026-10-03_sentence_chunks_on (files in docs/dpg/faithfulness_eval)")
    a.add_argument("--limit", type=int, default=None,
                   help="with --run: first N questions; with --worst: questions to list (10)")
    a.add_argument("--worst", action="store_true",
                   help="faithfulness per topic and every claim the judge found unsupported")
    a.add_argument("--topic", metavar="TOPIC",
                   help="with --worst: only this topic (e.g. algae, ph, econ)")
    a.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    a.add_argument("--json", action="store_true")
    lb = es.add_parser("label", help="label claims by hand (blind), or --review disputes")
    lb.add_argument("--review", action="store_true")
    lb.add_argument("--n", type=int, default=40)
    m = es.add_parser("model", help="check a local Ollama model drives the engine correctly")
    m.add_argument("tag", nargs="?", help="Ollama tag (default: the configured default)")
    m.add_argument("--ollama-log", metavar="FILE",
                   help="Ollama's server log, to look for prompt truncation")

"""What `agronaut eval` and the web app's Quality page both read: results, history, status.

The evals themselves live in scripts/ and stay runnable on their own. This module is the part
two front ends share, so the terminal and the browser can never disagree about a number:

  - a history of every run (`eval_history.jsonl`, numbers only, no query or answer text),
  - the published answer-quality report, summarised with its agreement figures attached,
  - what live traffic says about retrieval, next to what the golden set measured,
  - a status line per eval: when it last ran, on which corpus, and whether that is stale.

THE RULE BOTH FRONT ENDS FOLLOW. The validation record is the project's credibility asset, and a
results page is the easiest place to overstate it. So every number carries its n and date, a
judged score never appears without how well the judge agreed with a person, and a result
measured on a different corpus than the one now loaded says so. Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import paths as _paths

HISTORY_NAME = "eval_history.jsonl"

# The evals `agronaut eval` reports on, in the order it prints them. Vision is left out on
# purpose: it needs real field photos and a vision model, and is run by hand (scripts/vision_eval).
KINDS = ("retrieval", "safety", "answers", "model")

# The ones whose result depends on what is in the knowledge base. A safety probe or a model
# check does not move when a guide is added; a recall figure does.
_CORPUS_BOUND = {"retrieval", "answers"}


def scripts_from_checkout() -> None:
    """In a checkout, import the repo's own `scripts/`, never a stale copy in site-packages.

    `scripts` is a generic name, and a plain `pip install .` made before an editable one leaves
    a `scripts` package behind in site-packages. Whenever the repo root is not first on the path
    (the web app under some launchers, any embedding host) that stale copy wins, and it lacks
    whatever changed since: the Quality page silently showed no answers report because of
    exactly this (2026-10-04). A wheel install has no checkout and is left alone.
    """
    import importlib.util
    import sys

    if not _paths._is_source_tree():
        return
    root = str(_paths.PROJECT_ROOT)
    spec = importlib.util.find_spec("scripts")
    origin = str(spec.origin or "") if spec else ""
    if origin.startswith(root):
        return
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [m for m in sys.modules if m == "scripts" or m.startswith("scripts.")]:
        del sys.modules[name]


# --- history ----------------------------------------------------------------------------------

def history_path() -> Path:
    override = os.environ.get("AGRONAUT_EVAL_HISTORY")
    return Path(override) if override else _paths.data_dir() / HISTORY_NAME


def _version() -> str:
    try:
        from .version_info import current
        return str(current().version)
    except Exception:  # noqa: BLE001 (a label, never a reason to fail a run)
        return "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(kind: str, metrics: dict, *, model: str | None = None, corpus: str | None = None,
           path: Path | None = None, at: datetime | None = None) -> dict:
    """Append one run. Numbers and labels only: nothing that can carry a grower's words."""
    row = {"eval": kind, "at": (at or _now()).isoformat(timespec="seconds"),
           "version": _version(), "corpus": corpus, "model": model, "metrics": metrics}
    p = Path(path) if path else history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass   # a read-only install still gets its result printed; only the trend is lost
    return row


def history(path: Path | None = None) -> list[dict]:
    """Every recorded run, oldest first. A half-written last line is skipped, not raised."""
    p = Path(path) if path else history_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("eval"):
            out.append(row)
    return out


def latest(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        out[r["eval"]] = r
    return out


def corpus_id(fingerprint: dict | None) -> str | None:
    """A short name for what the knowledge base held: the source list plus the guides."""
    if not fingerprint:
        return None
    blob = (fingerprint.get("urls_sha256", "") + fingerprint.get("knowledge_sha256", "")).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def current_corpus_id() -> str | None:
    try:
        scripts_from_checkout()
        from scripts.retrieval_eval import corpus_fingerprint
        return corpus_id(corpus_fingerprint())
    except Exception:  # noqa: BLE001 (no corpus on disk: staleness is then unknown, not false)
        return None


def is_stale(row: dict | None, corpus_now: str | None) -> bool | None:
    """True when the run measured a different knowledge base than the one loaded now.
    None when that cannot be told (an eval the corpus does not affect, or no fingerprint)."""
    if not row or row.get("eval") not in _CORPUS_BOUND:
        return None
    if not row.get("corpus") or not corpus_now:
        return None
    return row["corpus"] != corpus_now


def age_days(when: str | None, now: datetime | None = None) -> int | None:
    if not when:
        return None
    try:
        t = datetime.fromisoformat(when)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, ((now or _now()) - t).days)


def when_label(when: str | None, age: int | None) -> str:
    """"2026-10-04 (today)" in the reader's own time zone. Runs are stored in UTC, which put
    a run made on the morning of the 4th in Taiwan on the 3rd."""
    if not when:
        return "never run here"
    try:
        t = datetime.fromisoformat(when)
        day = (t.astimezone() if t.tzinfo else t).date().isoformat()
    except ValueError:
        day = when[:10]
    return f"{day} ({'today' if age == 0 else f'{age} d ago'})"


# --- retrieval --------------------------------------------------------------------------------

def retrieval_metrics(report: dict) -> dict:
    """The numbers worth keeping from a retrieval_eval report. With languages, the headline is
    English (what the floor was calibrated on) and the others ride along per language."""
    s = report["summary"]
    langs = s.get("by_lang") or {}
    head = langs.get("en", s)
    on = [r["raw_top_score"] for r in report["per_query"]
          if r.get("raw_top_score") is not None and r.get("lang", "en") == "en"]
    off = [n["top_score"] for n in report.get("negative_controls", [])
           if n.get("top_score") is not None and n.get("lang", "en") == "en"]
    keys = ("hit_rate", "recall@k", "precision@k", "MRR", "MAP@k", "k", "queries")
    m = {k: head[k] for k in keys if k in head}
    m.update(latency_ms_mean=round(s.get("latency_ms_mean", 0)),
             floor_silenced_on_topic=head.get("floor_silenced_on_topic",
                                              s.get("floor_silenced_on_topic")),
             floor_rejected_off_topic=head.get("floor_rejected_off_topic",
                                               s.get("floor_rejected_off_topic")),
             negative_controls=head.get("negative_controls", s.get("negative_controls")),
             worst_on_topic=max(on) if on else None,
             closest_off_topic=min(off) if off else None)
    m["separable"] = (m["worst_on_topic"] is not None and m["closest_off_topic"] is not None
                      and m["closest_off_topic"] > m["worst_on_topic"])
    if langs:
        m["by_lang"] = {lang: {k: v[k] for k in ("queries", "hit_rate", "recall@k", "MRR",
                                                  "MAP@k", "floor_silenced_on_topic")}
                        for lang, v in langs.items()}
    m["by_topic"] = retrieval_by_topic(report["per_query"])
    return m


# --- topics: where it is weak --------------------------------------------------------------------

# A golden-set question's id starts with its topic ("algae-01", its translation "algae-01-fr"),
# and a claim's id starts with its question's ("algae-01:3"). Readable names for the prefixes in
# use; an unknown prefix is shown as itself, so a new topic appears without editing this table.
TOPIC_NAMES = {
    "algae": "algae", "disease": "fish disease", "do": "dissolved oxygen",
    "econ": "economics", "fail": "system failures", "feed": "feeding",
    "n": "nitrogen cycle", "pest": "pests", "ph": "pH", "plant": "plant deficiency",
    "pump": "pumps", "range": "water ranges", "reg": "regulations", "safety": "food safety",
    "solids": "solids", "stress": "fish stress", "temp": "temperature",
    "type": "system types", "water": "water source",
}


def topic_of(item_id: str) -> str:
    return re.split(r"[-:]", item_id or "", maxsplit=1)[0] or "?"


def topic_name(topic: str) -> str:
    return TOPIC_NAMES.get(topic, topic)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def retrieval_by_topic(per_query: list[dict]) -> list[dict]:
    """Hit rate, recall, MRR and MAP per topic over the English questions, weakest first, with
    the questions that missed and, when translations ran, each language's hits.

    Topics hold 1 to 3 questions each in the 2026 golden set, so one question moves a topic
    from 0 to 1. Read it as where to look, not as a ranking; `n` is carried for that reason.
    """
    groups: dict[str, list[dict]] = {}
    for r in per_query:
        groups.setdefault(topic_of(r.get("id", "")), []).append(r)
    rows = []
    for topic, rs in groups.items():
        en = [r for r in rs if r.get("lang", "en") == "en"]
        if not en:
            continue
        row = {"topic": topic, "name": topic_name(topic), "n": len(en),
               "hit_rate": _mean([1.0 if r.get("hit") else 0.0 for r in en]),
               "recall@k": _mean([r.get("recall", 0.0) for r in en]),
               "MRR": _mean([r.get("rr", 0.0) for r in en]),
               "MAP@k": _mean([r.get("ap", 0.0) for r in en]),
               "misses": [r.get("id", "?") for r in en if not r.get("hit")]}
        langs = {}
        for lang in sorted({r["lang"] for r in rs if r.get("lang") not in (None, "en")}):
            lr = [r for r in rs if r.get("lang") == lang]
            langs[lang] = [sum(1 for r in lr if r.get("hit")), len(lr)]
        if langs:
            row["langs"] = langs
        rows.append(row)
    return sorted(rows, key=lambda r: (r["MAP@k"], r["recall@k"], r["topic"]))


# --- answers (the published faithfulness report) -----------------------------------------------

def answers_dir() -> Path:
    return _paths.eval_root() / "faithfulness_eval"


def latest_answers_report(directory: Path | None = None) -> Path | None:
    d = directory or answers_dir()
    if not d.is_dir():
        return None
    reports = sorted(p for p in d.glob("*.json")
                     if p.name != "human_labels.json" and not p.name.endswith(".partial.json"))
    return reports[-1] if reports else None


def answers_summary(report: dict, labels_data: dict | None) -> dict:
    """Every judgement's faithfulness, each beside its agreement with the person.

    A faithfulness figure means only as much as its judge's agreement with a human, so the two
    are computed together and never handed out separately.
    """
    scripts_from_checkout()
    from scripts.faithfulness_eval import (
        _prompt_version,
        agreement,
        faithfulness_score,
        meta_ids,
        reviewed_labels,
        verdicts_of,
    )
    from scripts.label_claims import short_judge

    skip = meta_ids(report)
    data = labels_data or {}
    blind = {k: v for k, v in (data.get("labels") or {}).items() if k not in skip}
    reviewed = {k: v for k, v in reviewed_labels(data).items() if k not in skip}
    meta = report.get("meta", {})
    first = f"{meta.get('judge', 'judge')} (run 1)"
    current_tag = f"prompt {_prompt_version()}"
    judges = []
    for name in [None, *report.get("judgements", {})]:
        label = name or first
        verdicts = verdicts_of(report, name)
        score = faithfulness_score(list(verdicts.values()))
        b, r = agreement(blind, verdicts), agreement(reviewed, verdicts)
        judges.append({
            "name": label, "short": short_judge(label),
            "faithfulness": score["score"], "claims": score["n_judged"],
            "kappa_blind": b["kappa"], "raw_blind": b["raw"], "n_labels": b["n"],
            "kappa_reviewed": r["kappa"], "raw_reviewed": r["raw"],
            "current_prompt": current_tag in label or (
                name is None and meta.get("prompt_version") == _prompt_version()),
        })
    scores = [j["faithfulness"] for j in judges if j["faithfulness"] is not None]
    s = report.get("summary", {})
    default = next((j for j in judges if j["current_prompt"]), judges[-1] if judges else None)
    return {
        "date": meta.get("date"), "answerer": meta.get("answerer"),
        "queries": s.get("queries"),
        "claims": default["claims"] if default else 0,
        "labels": len(blind), "reviewed": len(data.get("reviewed") or {}),
        "labeller_count": 1 if blind else 0,
        "judges": judges, "default": default,
        "range": (min(scores), max(scores)) if scores else None,
        "citation_accuracy": (s.get("citation_accuracy") or {}).get("mean"),
        "fabricated_citations": s.get("fabricated_citations"),
        "response_relevancy": (s.get("response_relevancy") or {}).get("mean"),
    }


def answers_report_path(name: str | None = None, directory: Path | None = None) -> Path | None:
    """The answers report to read: the one named, else the current one of record
    (faithfulness_eval.CURRENT_REPORT), else the newest. None when there is none."""
    d = directory or answers_dir()
    if name:
        p = d / (name if name.endswith(".json") else f"{name}.json")
        return p if p.is_file() else None
    scripts_from_checkout()
    from scripts.faithfulness_eval import CURRENT_REPORT

    current = d / CURRENT_REPORT.name
    return current if current.is_file() else latest_answers_report(d)


def labels_for(report_name: str, directory: Path | None = None) -> dict | None:
    """The human labels, only if they were made on THIS report. Claim ids repeat across
    reports ("do-02:2" is a different sentence in each run), so labels from another report
    would be scored against the wrong claims."""
    p = (directory or answers_dir()) / "human_labels.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if data.get("report") == report_name else None


def load_answers(directory: Path | None = None, name: str | None = None) -> dict | None:
    """The current answers report (or the one named), summarised. None when there is none."""
    path = answers_report_path(name, directory)
    if path is None:
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    return {**answers_summary(report, labels_for(path.name, directory)), "report": path.name}


def load_answers_report(directory: Path | None = None,
                        name: str | None = None) -> tuple[dict, dict | None] | None:
    """(report, its labels or None) for the current answers report or the one named."""
    path = answers_report_path(name, directory)
    if path is None:
        return None
    return (json.loads(path.read_text(encoding="utf-8")), labels_for(path.name, directory))


def default_judgement(report: dict) -> str | None:
    """The FIRST judgement made with the current judging prompt (None means the report's own
    run 1), else the newest one: the same rule `answers_summary` uses for its default, so the
    status line, --worst and the Quality page all name one judge. A later run under the same
    prompt (another model, a repeat) is a comparison, not a replacement."""
    scripts_from_checkout()
    from scripts.faithfulness_eval import _prompt_version

    tag = f"prompt {_prompt_version()}"
    names = list(report.get("judgements", {}))
    for name in names:
        if tag in name:
            return name
    if report.get("meta", {}).get("prompt_version") == _prompt_version():
        return None
    return names[-1] if names else None


def answers_weak_spots(report: dict, judge: str | None = "default") -> dict:
    """Where answers stray from their sources: faithfulness per topic, weakest first, and every
    claim the judge found unsupported, grouped by question with the sources it was given.

    Claims about the sources themselves are left out, as everywhere else. The judge's verdict
    is a model's, checked against one person at fair agreement (kappa 0.24 to 0.39 in 2026),
    so a listed claim is a lead to read, not a confirmed error.
    """
    scripts_from_checkout()
    from scripts.faithfulness_eval import faithfulness_score, verdicts_of
    from scripts.label_claims import short_judge

    if judge == "default":
        judge = default_judgement(report)
    verdicts = verdicts_of(report, judge)
    by_topic: dict[str, list] = {}
    questions = []
    for q in report.get("per_query", []):
        claims = [c for c in q.get("claims", []) if c["id"] in verdicts]
        if not claims:
            continue
        topic = topic_of(claims[0]["id"])
        by_topic.setdefault(topic, []).extend(verdicts[c["id"]] for c in claims)
        bad = [{"id": c["id"], "claim": c["claim"]} for c in claims if verdicts[c["id"]] is False]
        if bad:
            questions.append({"id": q.get("id") or claims[0]["id"].split(":")[0],
                              "topic": topic, "name": topic_name(topic), "query": q["query"],
                              "claims": len(claims), "unsupported": bad,
                              "sources": q.get("retrieved", [])})
    topics = []
    for topic, vs in by_topic.items():
        score = faithfulness_score(vs)
        topics.append({"topic": topic, "name": topic_name(topic), "claims": score["n_judged"],
                       "unsupported": sum(1 for v in vs if v is False),
                       "faithfulness": score["score"]})
    topics.sort(key=lambda t: (t["faithfulness"] if t["faithfulness"] is not None else 2,
                               t["topic"]))
    questions.sort(key=lambda x: (-len(x["unsupported"]), x["id"]))
    meta = report.get("meta", {})
    return {"judge": short_judge(judge or f"{meta.get('judge', 'judge')} (run 1)"),
            "date": meta.get("date"), "topics": topics, "questions": questions}


def answers_metrics(summary: dict) -> dict:
    d = summary.get("default") or {}
    lo, hi = summary.get("range") or (None, None)
    return {"faithfulness": d.get("faithfulness"), "range_low": lo, "range_high": hi,
            "kappa_blind": d.get("kappa_blind"), "kappa_reviewed": d.get("kappa_reviewed"),
            "labels": summary.get("labels"), "claims": summary.get("claims"),
            "citation_accuracy": summary.get("citation_accuracy"),
            "response_relevancy": summary.get("response_relevancy"),
            "judge": d.get("short")}


# --- live traffic against the golden set ------------------------------------------------------

def live_retrieval(rows: list[dict], floor: float | None, worst_on_topic: float | None) -> dict:
    """What real searches look like next to what the golden set calibrated.

    The floor was set where the golden set's real questions and its off-topic ones separate.
    A real search that lands between the worst golden on-topic distance and the floor is in
    ground the eval never covered: still answered, but further from the corpus than anything
    the calibration saw. A rising share there is the early sign that growers ask about things
    the golden set does not represent. Shapes only: no query text exists in these rows.
    """
    ret = [r for r in rows if r.get("event") == "retrieval"]
    outcomes: dict[str, int] = {}
    for r in ret:
        key = r.get("outcome") or "unknown"
        outcomes[key] = outcomes.get(key, 0) + 1
    scores = sorted(r["top_score"] for r in ret if r.get("top_score") is not None)
    beyond = (sum(1 for x in scores if x > worst_on_topic)
              if worst_on_topic is not None else None)
    return {
        "n": len(ret), "outcomes": outcomes,
        "refused": outcomes.get("no_match", 0),
        "unavailable": outcomes.get("unavailable", 0),
        "scored": len(scores),
        "median_top": scores[len(scores) // 2] if scores else None,
        "beyond_calibration": beyond,
        "floor": floor, "worst_on_topic": worst_on_topic,
    }


# --- status -----------------------------------------------------------------------------------

def _f(v, digits: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def headline(kind: str, m: dict | None) -> str:
    if not m:
        return ""
    if kind == "retrieval":
        k = m.get("k", 3)
        line = (f"hit {_f(m.get('hit_rate'))} · recall@{k} {_f(m.get('recall@k'))} · "
                f"MRR {_f(m.get('MRR'))} · MAP@{k} {_f(m.get('MAP@k'))} (n={m.get('queries')})")
        langs = {k2: v for k2, v in (m.get("by_lang") or {}).items() if k2 != "en"}
        if langs:
            line += "; raw " + ", ".join(f"{lang} hit {_f(v['hit_rate'])}"
                                        for lang, v in langs.items())
        return line
    if kind == "safety":
        crit = m.get("critical_failures", 0)
        return (f"{m.get('passed')}/{m.get('total')} probes passed"
                + (f", {crit} CRITICAL failure(s)" if crit else ""))
    if kind == "answers":
        lo, hi = m.get("range_low"), m.get("range_high")
        rng = (f"{_f(lo, 2)} to {_f(hi, 2)}" if lo is not None and hi is not None and lo != hi
               else _f(m.get("faithfulness"), 2))
        return (f"faithfulness {rng}; default judge {_f(m.get('faithfulness'), 2)}, kappa vs "
                f"person {_f(m.get('kappa_blind'), 2)} blind / {_f(m.get('kappa_reviewed'), 2)} "
                f"reviewed (n={m.get('labels')}, one labeller: not yet validated); citations "
                f"{_f(m.get('citation_accuracy'), 2)}")
    if kind == "model":
        verdict = "PASS" if m.get("passed") else "FAIL"
        extra = f", {m['turn_seconds']} s a turn" if m.get("turn_seconds") is not None else ""
        if m.get("peak_mb"):
            extra += f", {m['peak_mb']} MB peak"
        return f"{verdict}{extra}" + ("" if m.get("passed") else
                                     f": {'; '.join(m.get('reasons') or [])}")
    return json.dumps(m)[:120]


NEXT_STEP = {
    "retrieval": "agronaut eval retrieval",
    "safety": "agronaut eval safety",
    "answers": "agronaut eval answers --run",
    "model": "agronaut eval model <ollama tag>",
}


def status(rows: list[dict], corpus_now: str | None, answers: dict | None = None,
           now: datetime | None = None) -> list[dict]:
    """One line per eval: its latest result, how old, and whether the corpus moved since.

    Answers fall back to the published report when this machine has never run them, because
    that report IS the record; it is just not in the local history.
    """
    last = latest(rows)
    out = []
    for kind in KINDS:
        row = last.get(kind)
        metrics = row.get("metrics") if row else None
        when = row.get("at") if row else None
        source = "this machine" if row else None
        if kind == "answers" and row is None and answers:
            metrics, when, source = answers_metrics(answers), answers.get("date"), "published"
        out.append({
            "eval": kind, "when": when, "age_days": age_days(when, now),
            "stale": is_stale(row, corpus_now), "source": source,
            "model": row.get("model") if row else None,
            "version": row.get("version") if row else None,
            "metrics": metrics, "headline": headline(kind, metrics),
            "next": NEXT_STEP[kind],
        })
    return out

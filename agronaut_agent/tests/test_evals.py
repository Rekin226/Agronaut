"""`agronaut eval` and the Quality page: what they promise about the numbers they show.

The evals have their own tests (test_retrieval_eval, test_faithfulness_eval, ...). These pin
the layer on top: a run is remembered without any text in it, a result measured on another
corpus is called stale, a judged score always travels with its agreement figure, and live
traffic is compared with the calibration, not left beside it. No model, no index, no network.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from agronaut_agent import cli, eval_cli, evals, paths

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


# --- history -----------------------------------------------------------------------------------

def test_a_run_is_remembered_with_its_version_corpus_and_model(tmp_path):
    p = tmp_path / "h.jsonl"
    evals.record("retrieval", {"recall@k": 0.833}, corpus="abc123", path=p, at=NOW)
    evals.record("model", {"passed": True}, model="qwen3.5:4b", path=p, at=NOW)
    rows = evals.history(p)
    assert [r["eval"] for r in rows] == ["retrieval", "model"]
    assert rows[0]["corpus"] == "abc123" and rows[0]["metrics"] == {"recall@k": 0.833}
    assert rows[1]["model"] == "qwen3.5:4b" and rows[0]["version"]


def test_a_half_written_line_does_not_make_the_history_unreadable(tmp_path):
    p = tmp_path / "h.jsonl"
    evals.record("safety", {"passed": 1}, path=p, at=NOW)
    with p.open("a") as fh:
        fh.write('{"eval": "retrie')
    assert len(evals.history(p)) == 1


def test_the_latest_run_of_each_eval_wins(tmp_path):
    p = tmp_path / "h.jsonl"
    evals.record("safety", {"passed": 1}, path=p, at=NOW - timedelta(days=3))
    evals.record("safety", {"passed": 2}, path=p, at=NOW)
    assert evals.latest(evals.history(p))["safety"]["metrics"] == {"passed": 2}


def test_an_unwritable_history_still_returns_the_result(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    row = evals.record("safety", {"passed": 1}, path=blocker / "h.jsonl", at=NOW)
    assert row["metrics"] == {"passed": 1}


# --- staleness ---------------------------------------------------------------------------------

def test_a_retrieval_result_on_another_corpus_is_stale():
    row = {"eval": "retrieval", "corpus": "old"}
    assert evals.is_stale(row, "new") is True
    assert evals.is_stale(row, "old") is False


def test_staleness_is_unknown_not_false_when_it_cannot_be_told():
    """No fingerprint, or an eval the corpus does not affect: say nothing rather than 'fresh'."""
    assert evals.is_stale({"eval": "retrieval", "corpus": None}, "new") is None
    assert evals.is_stale({"eval": "retrieval", "corpus": "a"}, None) is None
    assert evals.is_stale({"eval": "safety", "corpus": "old"}, "new") is None


def test_the_corpus_id_changes_with_either_the_sources_or_the_guides():
    a = evals.corpus_id({"urls_sha256": "u1", "knowledge_sha256": "k1"})
    assert a != evals.corpus_id({"urls_sha256": "u2", "knowledge_sha256": "k1"})
    assert a != evals.corpus_id({"urls_sha256": "u1", "knowledge_sha256": "k2"})
    assert evals.corpus_id(None) is None


def test_age_is_in_whole_days():
    assert evals.age_days((NOW - timedelta(days=4, hours=2)).isoformat(), NOW) == 4
    assert evals.age_days("2026-10-04", NOW) == 0
    assert evals.age_days(None, NOW) is None and evals.age_days("garbage", NOW) is None


# --- retrieval metrics -------------------------------------------------------------------------

def _retrieval_report(langs: bool):
    per = [{"lang": "en", "raw_top_score": 1.2}, {"lang": "en", "raw_top_score": 1.383},
           {"lang": "fr", "raw_top_score": 1.69}]
    negs = [{"lang": "en", "top_score": 1.411}, {"lang": "fr", "top_score": 1.28}]
    en = {"queries": 33, "k": 3, "hit_rate": 0.879, "recall@k": 0.833, "precision@k": 0.364,
          "MRR": 0.636, "MAP@k": 0.604, "floor_silenced_on_topic": 0,
          "floor_rejected_off_topic": 8, "negative_controls": 10}
    summary = {**en, "latency_ms_mean": 351.4}
    if langs:
        summary = {**summary, "queries": 99, "hit_rate": 0.384,
                   "by_lang": {"en": en, "fr": {**en, "hit_rate": 0.182}}}
    else:
        per = [{k: v for k, v in r.items() if k != "lang"} for r in per[:2]]
        negs = [{k: v for k, v in n.items() if k != "lang"} for n in negs[:1]]
    return {"summary": summary, "per_query": per, "negative_controls": negs}


def test_the_headline_is_english_even_when_languages_are_mixed_in():
    """The mixed 99-question average (0.384) describes no real user; English is what the
    floor was calibrated on, and the others ride along per language."""
    m = evals.retrieval_metrics(_retrieval_report(langs=True))
    assert m["hit_rate"] == 0.879 and m["queries"] == 33
    assert m["by_lang"]["fr"]["hit_rate"] == 0.182
    assert m["worst_on_topic"] == 1.383 and m["closest_off_topic"] == 1.411
    assert m["separable"] is True


def test_retrieval_metrics_without_languages():
    m = evals.retrieval_metrics(_retrieval_report(langs=False))
    assert m["recall@k"] == 0.833 and "by_lang" not in m and m["separable"] is True


# --- answers: a judged score always comes with its agreement ----------------------------------

def _answers_report():
    return {"meta": {"judge": "nvidia/gpt-oss", "prompt_version": "old", "date": "2026-09-30",
                     "answerer": "anthropic/claude"},
            "summary": {"queries": 1, "citation_accuracy": {"mean": 1.0},
                        "response_relevancy": {"mean": 0.44}, "fabricated_citations": 0},
            "per_query": [{"query": "q", "context": "c", "claims": [
                {"id": "q:1", "claim": "Tilapia like warm water.", "verdict": True},
                {"id": "q:2", "claim": "Nitrite is harmless.", "verdict": True},
                {"id": "q:3", "claim": "The context does not specify a dose.",
                 "verdict": False}]}],
            "judgements": {"nvidia/gpt-oss prompt NEW (run 1)": {"q:1": True, "q:2": False}}}


def test_every_judge_carries_its_agreement_with_the_person(monkeypatch):
    import scripts.faithfulness_eval as F
    monkeypatch.setattr(F, "_prompt_version", lambda: "NEW")
    labels = {"labels": {"q:1": True, "q:2": False, "q:3": True}, "reviewed": {"q:2": True}}
    a = evals.answers_summary(_answers_report(), labels)
    assert [j["faithfulness"] for j in a["judges"]] == [1.0, 0.5]
    assert all("kappa_blind" in j and "raw_blind" in j for j in a["judges"])
    assert a["labels"] == 2                      # the hedge (q:3) is not a claim, nor a label
    assert a["default"]["name"].endswith("prompt NEW (run 1)")
    assert a["default"]["raw_blind"] == 1.0 and a["default"]["raw_reviewed"] == 0.5
    assert a["range"] == (0.5, 1.0) and a["citation_accuracy"] == 1.0


def test_the_answers_headline_never_shows_faithfulness_alone():
    line = evals.headline("answers", {"faithfulness": 0.84, "range_low": 0.84,
                                      "range_high": 0.90, "kappa_blind": 0.24,
                                      "kappa_reviewed": 0.39, "labels": 29,
                                      "citation_accuracy": 1.0})
    assert "0.84 to 0.90" in line and "kappa" in line and "n=29" in line
    assert "not yet validated" in line


def test_the_newest_report_is_read_and_labels_are_not_a_report(tmp_path):
    for name in ("2026-09-30_baseline.json", "2026-10-04_run.json", "human_labels.json",
                 "2026-10-04_run.partial.json"):
        (tmp_path / name).write_text("{}")
    assert evals.latest_answers_report(tmp_path).name == "2026-10-04_run.json"
    assert evals.latest_answers_report(tmp_path / "missing") is None


# --- live traffic against the calibration -------------------------------------------------------

def test_live_searches_are_compared_with_the_golden_set_band():
    rows = [{"event": "retrieval", "outcome": "hit", "top_score": 0.9},
            {"event": "retrieval", "outcome": "hit", "top_score": 1.45},   # beyond 1.383
            {"event": "retrieval", "outcome": "no_match", "top_score": None},
            {"event": "turn", "latency_ms": 900}]
    live = evals.live_retrieval(rows, floor=1.5, worst_on_topic=1.383)
    assert live["n"] == 3 and live["refused"] == 1 and live["scored"] == 2
    assert live["beyond_calibration"] == 1


def test_without_a_calibration_the_comparison_is_unknown_not_zero():
    live = evals.live_retrieval([{"event": "retrieval", "outcome": "hit", "top_score": 1.0}],
                                floor=1.5, worst_on_topic=None)
    assert live["beyond_calibration"] is None


# --- status ------------------------------------------------------------------------------------

def test_status_lists_every_eval_and_says_what_to_run_when_never_run():
    rows = [{"eval": "safety", "at": NOW.isoformat(), "version": "1.2.1", "corpus": None,
             "model": None, "metrics": {"passed": 406, "total": 406, "critical_failures": 0}}]
    out = evals.status(rows, "c1", now=NOW)
    assert [s["eval"] for s in out] == list(evals.KINDS)
    safety = out[1]
    assert safety["headline"] == "406/406 probes passed" and safety["age_days"] == 0
    assert out[0]["when"] is None and out[0]["next"] == "agronaut eval retrieval"


def test_status_falls_back_to_the_published_answers_report():
    answers = {"date": "2026-09-30", "range": (0.84, 0.90), "labels": 29,
               "citation_accuracy": 1.0, "response_relevancy": 0.44, "claims": 479,
               "default": {"faithfulness": 0.84, "kappa_blind": 0.24,
                           "kappa_reviewed": 0.39, "short": "gpt-oss"}}
    s = evals.status([], None, answers=answers, now=NOW)[2]
    assert s["source"] == "published" and s["age_days"] == 4
    assert "0.84 to 0.90" in s["headline"]


def test_a_stale_retrieval_result_is_flagged_in_status():
    rows = [{"eval": "retrieval", "at": NOW.isoformat(), "corpus": "old", "metrics": {}}]
    assert evals.status(rows, "new", now=NOW)[0]["stale"] is True


# --- where the eval data lives -----------------------------------------------------------------

def test_the_eval_data_is_found_in_a_checkout_and_can_be_pointed_elsewhere(monkeypatch, tmp_path):
    monkeypatch.delenv("AGRONAUT_EVAL_DIR", raising=False)
    assert (paths.eval_root() / "retrieval_eval" / "golden_set.json").exists()
    assert (paths.eval_root() / "safety_eval" / "golden_set.json").exists()
    monkeypatch.setenv("AGRONAUT_EVAL_DIR", str(tmp_path))
    assert paths.eval_root() == tmp_path


def test_every_eval_file_the_wheel_ships_exists():
    """A data-files entry pointing at a missing file breaks the build, not a test."""
    import tomllib
    root = paths.PROJECT_ROOT
    files = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["data-files"]
    shipped = [f for dest, fs in files.items() if dest.startswith("share/agronaut/eval") for f in fs]
    assert len(shipped) >= 4
    assert all((root / f).is_file() for f in shipped)


# --- the command ---------------------------------------------------------------------------------

@pytest.mark.parametrize("argv,sub", [
    (["eval"], None), (["eval", "status"], "status"), (["eval", "all"], "all"),
    (["eval", "retrieval", "--multilingual"], "retrieval"), (["eval", "safety"], "safety"),
    (["eval", "answers", "--agreement"], "answers"), (["eval", "label", "--review"], "label"),
    (["eval", "model", "qwen3.5:4b"], "model"),
])
def test_every_eval_subcommand_parses(argv, sub):
    args = cli._build_parser().parse_args(argv)
    assert args.func is cli._cmd_eval and args.eval_cmd == sub


def test_bare_eval_prints_the_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGRONAUT_EVAL_HISTORY", str(tmp_path / "h.jsonl"))
    evals.record("safety", {"passed": 406, "total": 406, "critical_failures": 0})
    monkeypatch.setattr(eval_cli, "_answers_or_none", lambda: None)
    assert cli.main(["eval"]) == 0
    out = capsys.readouterr().out
    assert "406/406 probes passed" in out and "agronaut eval retrieval" in out
    assert "agronaut web" in out


def test_status_as_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGRONAUT_EVAL_HISTORY", str(tmp_path / "h.jsonl"))
    monkeypatch.setattr(eval_cli, "_answers_or_none", lambda: None)
    assert cli.main(["eval", "--json"]) == 0
    assert [e["eval"] for e in json.loads(capsys.readouterr().out)["evals"]] == list(evals.KINDS)


def test_a_paid_run_asks_first_and_does_nothing_on_no(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    import scripts.faithfulness_eval as F
    monkeypatch.setattr(F, "run", lambda **_: pytest.fail("ran without consent"))
    assert cli.main(["eval", "answers", "--run"]) == 1
    assert "cost credit" in capsys.readouterr().out


def test_safety_through_the_cli_records_and_reports(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGRONAUT_EVAL_HISTORY", str(tmp_path / "h.jsonl"))
    assert cli.main(["eval", "safety"]) == 0
    assert "passed" in capsys.readouterr().out
    assert evals.history()[-1]["eval"] == "safety"


def test_a_model_check_without_ollama_explains_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr(eval_cli, "_ollama_models", lambda host: None)
    assert cli.main(["eval", "model", "qwen3.5:4b"]) == 2
    assert "Ollama is not answering" in capsys.readouterr().out


def test_a_model_that_is_not_pulled_is_never_pulled_for_you(monkeypatch, capsys):
    monkeypatch.setattr(eval_cli, "_ollama_models", lambda host: ["llama3:latest"])
    assert cli.main(["eval", "model", "qwen3.5:4b"]) == 2
    out = capsys.readouterr().out
    assert "ollama pull qwen3.5:4b" in out and "not pulled" in out


def test_a_run_is_dated_in_the_readers_time_zone_not_utc():
    """A run at 22:00 UTC on the 3rd is the morning of the 4th in Taiwan."""
    when = "2026-10-03T22:00:00+00:00"
    local_day = datetime.fromisoformat(when).astimezone().date().isoformat()
    assert evals.when_label(when, 0) == f"{local_day} (today)"
    assert evals.when_label(None, None) == "never run here"


def test_a_stale_scripts_package_elsewhere_never_shadows_the_checkout(monkeypatch, tmp_path):
    """What broke the Quality page: an old `pip install .` left `scripts` in site-packages."""
    import importlib.util
    import sys
    stale = tmp_path / "site"
    (stale / "scripts").mkdir(parents=True)
    (stale / "scripts" / "__init__.py").write_text("STALE = True\n")
    monkeypatch.setattr(sys, "path", [str(stale)] + [p for p in sys.path
                                                    if p != str(paths.PROJECT_ROOT)])
    for name in [m for m in sys.modules if m == "scripts" or m.startswith("scripts.")]:
        monkeypatch.delitem(sys.modules, name)
    assert importlib.util.find_spec("scripts").origin.startswith(str(stale))
    evals.scripts_from_checkout()
    assert importlib.util.find_spec("scripts").origin.startswith(str(paths.PROJECT_ROOT))


# --- where it is weak: by topic, and the unsupported claims (2026-10-04) ------------------------

def _q(qid, hit, recall, rr, ap, lang="en"):
    return {"id": qid, "hit": hit, "recall": recall, "rr": rr, "ap": ap, "lang": lang}


def test_topics_come_from_question_and_claim_ids():
    assert evals.topic_of("algae-01") == "algae"
    assert evals.topic_of("do-02-fr") == "do"
    assert evals.topic_of("econ-03:20") == "econ"
    assert evals.topic_name("range") == "water ranges"
    assert evals.topic_name("newtopic") == "newtopic"     # unknown prefixes still show


def test_retrieval_by_topic_puts_the_weakest_first_and_names_the_misses():
    rows = evals.retrieval_by_topic([
        _q("do-01", True, 1.0, 1.0, 1.0), _q("do-02", True, 1.0, 0.5, 0.5),
        _q("range-01", False, 0.0, 0.0, 0.0),
        _q("algae-01", False, 0.0, 0.0, 0.0), _q("algae-02", True, 1.0, 1.0, 1.0),
        _q("algae-01-fr", True, 1.0, 1.0, 1.0, lang="fr"),
        _q("algae-02-fr", False, 0.0, 0.0, 0.0, lang="fr"),
    ])
    assert [r["topic"] for r in rows] == ["range", "algae", "do"]
    algae = rows[1]
    assert algae["n"] == 2 and algae["hit_rate"] == 0.5 and algae["misses"] == ["algae-01"]
    assert algae["langs"] == {"fr": [1, 2]}          # translations counted, not averaged in
    assert rows[2]["MRR"] == 0.75


def test_the_retrieval_record_keeps_the_topic_table():
    report = _retrieval_report(langs=False)
    report["per_query"] = [_q("ph-01", True, 1.0, 1.0, 1.0) | {"raw_top_score": 1.1}]
    assert evals.retrieval_metrics(report)["by_topic"][0]["topic"] == "ph"


def test_weak_spots_list_unsupported_claims_by_question_and_topic(monkeypatch):
    import scripts.faithfulness_eval as F
    monkeypatch.setattr(F, "_prompt_version", lambda: "NEW")
    rep = _answers_report()
    rep["per_query"][0].update(id="ph-01", retrieved=["knowledge/ph_and_alkalinity.md"])
    for c, new in zip(rep["per_query"][0]["claims"], ("ph-01:1", "ph-01:2", "ph-01:3")):
        c["id"] = new
    rep["judgements"] = {"nvidia/gpt-oss prompt NEW (run 1)": {"ph-01:1": True, "ph-01:2": False}}
    w = evals.answers_weak_spots(rep)
    assert w["judge"].startswith("gpt-oss")
    assert w["topics"] == [{"topic": "ph", "name": "pH", "claims": 2, "unsupported": 1,
                            "faithfulness": 0.5}]
    (q,) = w["questions"]
    assert q["id"] == "ph-01" and q["unsupported"] == [{"id": "ph-01:2",
                                                       "claim": "Nitrite is harmless."}]
    assert q["sources"] == ["knowledge/ph_and_alkalinity.md"]


def test_by_topic_and_worst_parse():
    args = cli._build_parser().parse_args(["eval", "retrieval", "--by-topic"])
    assert args.by_topic is True
    args = cli._build_parser().parse_args(["eval", "answers", "--worst", "--topic", "ph",
                                           "--limit", "3"])
    assert args.worst and args.topic == "ph" and args.limit == 3


def test_worst_prints_topics_and_claims_from_the_saved_report(monkeypatch, tmp_path, capsys):
    import scripts.faithfulness_eval as F
    monkeypatch.setattr(F, "_prompt_version", lambda: "NEW")
    rep = _answers_report()
    rep["per_query"][0]["id"] = "g1"
    rep["judgements"] = {"nvidia/gpt-oss prompt NEW (run 1)": {"q:1": True, "q:2": False}}
    d = tmp_path / "faithfulness_eval"
    d.mkdir()
    (d / "2026-10-04_run.json").write_text(json.dumps(rep))
    monkeypatch.setenv("AGRONAUT_EVAL_DIR", str(tmp_path))
    assert cli.main(["eval", "answers", "--worst"]) == 0
    out = capsys.readouterr().out
    assert "Nitrite is harmless." in out and "unsupported" in out.lower()
    assert "lead to read" in out


def test_the_current_report_wins_over_a_newer_experiment(tmp_path):
    """2026-10-04: a Claude-judged chunking experiment was picked up as 'the newest' and shown
    where the baseline belonged."""
    for name in ("2026-09-30_baseline.json", "2026-10-03_sentence_chunks_on.json"):
        (tmp_path / name).write_text("{}")
    assert evals.answers_report_path(directory=tmp_path).name == "2026-09-30_baseline.json"
    assert evals.answers_report_path("2026-10-03_sentence_chunks_on",
                                     tmp_path).name == "2026-10-03_sentence_chunks_on.json"
    assert evals.answers_report_path("missing", tmp_path) is None


def test_labels_are_used_only_with_the_report_they_were_made_on(tmp_path):
    (tmp_path / "human_labels.json").write_text(json.dumps(
        {"report": "2026-09-30_baseline.json", "labels": {"do-02:2": True}}))
    assert evals.labels_for("2026-09-30_baseline.json", tmp_path)["labels"] == {"do-02:2": True}
    assert evals.labels_for("2026-10-03_sentence_chunks_on.json", tmp_path) is None


def test_the_shipped_labels_name_the_current_report():
    from scripts.faithfulness_eval import CURRENT_REPORT
    data = json.loads((CURRENT_REPORT.parent / "human_labels.json").read_text())
    assert data["report"] == CURRENT_REPORT.name and CURRENT_REPORT.is_file()


def test_worst_and_the_status_line_name_the_same_default_judge(monkeypatch):
    import scripts.faithfulness_eval as F
    monkeypatch.setattr(F, "_prompt_version", lambda: "NEW")
    rep = _answers_report()
    rep["judgements"] = {"nvidia/gpt-oss prompt NEW (run 1)": {"q:1": True, "q:2": False},
                         "anthropic/claude prompt NEW (run 1)": {"q:1": True, "q:2": True}}
    assert evals.default_judgement(rep) == "nvidia/gpt-oss prompt NEW (run 1)"
    assert evals.answers_summary(rep, None)["default"]["name"] == evals.default_judgement(rep)

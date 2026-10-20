"""Generation-quality metrics — the arithmetic, hermetically.

scripts/faithfulness_eval.py needs a model to produce judgements, but the scoring built on top
of those judgements must not. These tests pin the parts that decide what a score MEANS, with
fake judges standing in for the real one, so a regression in the accounting is caught in CI even
though the eval itself never runs there.

The recurring theme is refusing to average unlike things:
  - an unparseable verdict is UNJUDGED, not a failure,
  - an uncited answer has NO citation accuracy, not a zero,
  - a metric that could not be computed for a query is absent from its mean, not counted.

Each of those, done the other way, produces a number that looks like a measurement and is not.
"""

import pytest

from scripts import faithfulness_eval as fe

# --- claim extraction --------------------------------------------------------

def test_claims_parse_from_the_three_list_shapes_models_emit():
    text = "Here are the claims:\n1. pH should sit near 6.8\n- Nitrite must read zero\n* DO above 5"
    assert fe.parse_claims(text) == ["pH should sit near 6.8", "Nitrite must read zero",
                                     "DO above 5"]


def test_preamble_and_blank_lines_are_not_claims():
    assert fe.parse_claims("Sure! Here you go:\n\n1. one claim\n\n") == ["one claim"]


def test_no_list_yields_no_claims():
    assert fe.parse_claims("I could not identify any claims.") == []


# --- verdict parsing ---------------------------------------------------------

def test_unsupported_is_read_before_supported():
    """UNSUPPORTED contains SUPPORTED. Testing the positive label first would turn every
    rejection into an acceptance and silently invert the whole metric."""
    assert fe.parse_verdict("UNSUPPORTED") is False
    assert fe.parse_verdict("SUPPORTED") is True


def test_a_judge_that_explains_itself_still_parses():
    assert fe.parse_verdict("UNSUPPORTED - the context never mentions nitrite") is False
    assert fe.parse_verdict("supported, the passage states it directly") is True


def test_an_unparseable_verdict_is_unjudged_not_a_failure():
    for junk in ("maybe?", "", "I'm not sure", None):
        assert fe.parse_verdict(junk) is None


def test_unjudged_claims_are_excluded_from_the_score_and_counted():
    out = fe.faithfulness_score([True, True, False, None])
    assert out["score"] == 2 / 3           # over the JUDGED claims only
    assert out["n_claims"] == 4 and out["n_judged"] == 3 and out["n_unjudged"] == 1


def test_no_judgeable_claims_scores_none_not_zero():
    """A judge that failed entirely must report 'no measurement'. A confident 0.0 would read
    as a system that hallucinates everything."""
    assert fe.faithfulness_score([None, None])["score"] is None
    assert fe.faithfulness_score([])["score"] is None


# --- citations ---------------------------------------------------------------

def test_cited_sources_are_deduplicated_in_order():
    ans = "Shade it [source: knowledge/algae_control.md]. Also [source: knowledge/algae_control.md]."
    assert fe.cited_sources(ans) == ["knowledge/algae_control.md"]


def test_a_fabricated_citation_is_detected_and_named():
    ans = "[source: knowledge/algae_control.md] and [source: knowledge/invented.md]"
    score, bogus = fe.citation_accuracy(ans, ["knowledge/algae_control.md"])
    assert score == 0.5 and bogus == ["knowledge/invented.md"]


def test_an_uncited_answer_has_no_score_rather_than_zero():
    """Uncited and miscited are different failures. Averaging 'no citations' in as 0.0 makes an
    under-citing system indistinguishable from a lying one."""
    score, bogus = fe.citation_accuracy("Just shade the tank.", ["knowledge/algae_control.md"])
    assert score is None and bogus == []


def test_all_citations_real_scores_one():
    score, bogus = fe.citation_accuracy("[source: a] [source: b]", ["a", "b", "c"])
    assert score == 1.0 and bogus == []


# --- similarity --------------------------------------------------------------

def test_cosine_is_one_for_identical_and_zero_for_orthogonal():
    assert fe.cosine([1, 0], [1, 0]) == 1.0
    assert fe.cosine([1, 0], [0, 1]) == 0.0


def test_cosine_of_a_zero_vector_is_zero_not_an_exception():
    assert fe.cosine([0, 0], [1, 1]) == 0.0


# --- aggregation -------------------------------------------------------------

def test_each_metric_averages_only_over_the_queries_that_produced_it():
    per_query = [
        {"faithfulness": 1.0, "response_relevancy": 0.9, "citation_accuracy": 1.0,
         "n_unjudged": 0, "fabricated": []},
        {"faithfulness": 0.5, "response_relevancy": None, "citation_accuracy": None,
         "n_unjudged": 2, "fabricated": ["knowledge/ghost.md"]},
    ]
    s = fe.aggregate(per_query)
    assert s["queries"] == 2
    assert s["faithfulness"] == {"mean": 0.75, "n": 2}
    assert s["response_relevancy"] == {"mean": 0.9, "n": 1}   # the None is not a zero
    assert s["citation_accuracy"] == {"mean": 1.0, "n": 1}
    assert s["claims_unjudged"] == 2 and s["fabricated_citations"] == 1


def test_aggregate_of_nothing_reports_none_not_zero():
    s = fe.aggregate([])
    assert s["queries"] == 0 and s["faithfulness"]["mean"] is None


# --- the scoring path, end to end with fakes ---------------------------------

def _fake_ask(prompt: str) -> str:
    """A judge that supports the algae claim and rejects the invented nitrite one.

    It reads the CLAIM section rather than the whole prompt: the context also says "algae
    bloom", so a substring test over the full prompt would mark BOTH claims supported and the
    fake would quietly agree with whatever it was given.
    """
    if prompt.startswith("Break the ANSWER"):
        return "1. Green water is an algae bloom\n2. Tilapia need 30 mg/l of nitrite"
    if prompt.startswith("Decide whether"):
        claim = prompt.split("CLAIM:", 1)[1]
        if "algae bloom" in claim:
            return "QUOTE: Green water is an algae bloom.\nVERDICT: SUPPORTED"
        return "QUOTE: NONE\nVERDICT: UNSUPPORTED"
    return "1. What is green water?\n2. Why is my water green?"


def _fake_embed(text: str):
    return [1.0, 0.0] if "green" in text.lower() else [0.0, 1.0]


def test_score_query_catches_the_claim_the_context_does_not_support():
    """The metric's whole purpose: an answer whose second sentence is invented scores 0.5, even
    though retrieval succeeded and the answer reads fluently."""
    row = fe.score_query(
        query="why is my water green",
        answer="Green water is an algae bloom [source: knowledge/algae_control.md]. "
               "Tilapia need 30 mg/l of nitrite [source: knowledge/ghost.md].",
        context="[source: knowledge/algae_control.md]\nGreen water is an algae bloom.",
        retrieved=["knowledge/algae_control.md"],
        ask=_fake_ask, embed=_fake_embed)
    assert row["faithfulness"] == 0.5
    assert row["n_claims"] == 2 and row["n_unjudged"] == 0
    assert row["citation_accuracy"] == 0.5
    assert row["fabricated"] == ["knowledge/ghost.md"]
    assert row["response_relevancy"] == 1.0


def test_a_failing_relevancy_judge_does_not_void_the_other_metrics(capsys):
    def _ask(prompt: str) -> str:
        if prompt.startswith("Read the ANSWER"):
            raise RuntimeError("judge unavailable")
        return _fake_ask(prompt)

    row = fe.score_query("why is my water green",
                         "Green water is an algae bloom [source: knowledge/algae_control.md].",
                         "Green water is an algae bloom.", ["knowledge/algae_control.md"],
                         ask=_ask, embed=_fake_embed)
    assert row["response_relevancy"] is None
    assert row["faithfulness"] == 0.5 and row["citation_accuracy"] == 1.0


# --- #182: judges checked against each other, themselves, and a person --------------------

def test_kappa_is_one_for_perfect_agreement_and_undefined_for_one_label_everywhere():
    assert fe.cohen_kappa([True, False, True], [True, False, True]) == 1.0
    assert fe.cohen_kappa([True, True], [True, True]) is None      # nothing to chance-correct
    assert fe.cohen_kappa([], []) is None


def test_high_raw_agreement_can_still_mean_little():
    """90% raw agreement from a judge that says SUPPORTED to nearly everything."""
    person = [True] * 9 + [False]
    judge = [True] * 10
    assert fe.agreement(dict(enumerate(person)), dict(enumerate(judge)))["raw"] == 0.9
    assert fe.cohen_kappa(person, judge) == 0.0


def test_agreement_only_counts_claims_both_sides_judged():
    a = {"q1:1": True, "q1:2": None, "q2:1": False}
    b = {"q1:1": True, "q1:2": True, "q3:1": False}
    assert fe.agreement(a, b)["n"] == 1


def _report():
    return {"meta": {"judge": "anthropic/claude"}, "per_query": [
        {"query": "q", "context": "Tilapia stop feeding below 20 C.",
         "claims": [{"id": "g1:1", "claim": "below 20 C they stop feeding", "verdict": True},
                    {"id": "g1:2", "claim": "below 25 C they stop feeding", "verdict": False}]}]}


def test_rejudging_rules_on_the_saved_claims_against_the_saved_context():
    seen = []
    verdicts = fe.rejudge(_report(), lambda p: seen.append(p)
                          or "QUOTE: Tilapia stop feeding below 20 C.\nVERDICT: SUPPORTED")
    assert verdicts == {"g1:1": True, "g1:2": True}
    assert all("Tilapia stop feeding below 20 C." in p for p in seen)


def test_score_query_keeps_claims_with_stable_ids_and_the_context():
    row = fe.score_query("q", "A. B.", "ctx", [], lambda p: "1. a\n2. b" if "Break" in p
                         else "SUPPORTED", lambda t: [1.0], qid="g7")
    assert [c["id"] for c in row["claims"]] == ["g7:1", "g7:2"]
    assert row["context"] == "ctx" and row["answer"] == "A. B."


def test_rejudging_with_the_same_judge_is_its_run_two():
    r = _report()
    assert fe.next_run_name(r, "anthropic/claude") == "anthropic/claude (run 2)"
    assert fe.next_run_name(r, "nvidia/gpt-oss") == "nvidia/gpt-oss (run 1)"
    r["judgements"] = {"anthropic/claude (run 2)": {}}
    assert fe.next_run_name(r, "anthropic/claude") == "anthropic/claude (run 3)"


def test_the_agreement_table_pairs_every_judge_with_the_person_and_each_other():
    r = _report()
    r["judgements"] = {"nvidia/gpt-oss (run 1)": {"g1:1": True, "g1:2": False}}
    rows = fe.agreement_table(r, {"g1:1": True, "g1:2": False})
    assert {(x["a"], x["b"]) for x in rows} == {
        ("human", "anthropic/claude (run 1)"), ("human", "nvidia/gpt-oss (run 1)"),
        ("anthropic/claude (run 1)", "nvidia/gpt-oss (run 1)")}


def test_labelling_samples_both_verdicts_and_skips_what_is_done():
    from scripts.label_claims import sample
    r = {"per_query": [{"query": "q", "context": "c", "claims": [
        {"id": f"s{i}", "claim": "x", "verdict": True} for i in range(20)] + [
        {"id": f"u{i}", "claim": "x", "verdict": False} for i in range(3)]}]}
    picked = [c["id"] for _, c in sample(r, 10, done={"u0"})]
    assert len(picked) == 10 and "u0" not in picked
    assert sum(p.startswith("u") for p in picked) == 2      # every remaining UNSUPPORTED


@pytest.mark.parametrize("cited", [
    "dissolved_oxygen_and_aeration.md",
    "FAO589 Small-scale aquaponic food production (Somerville et al., 2014)",
    "Goddek, Joyce, Kotzen & Burnel eds. (2019), Aquaponics Food Production Systems, SpringerOpen",
])
def test_a_real_source_written_slightly_differently_is_not_fabricated(cited):
    """All three were reported as fabricated by the first real runs (2026-09-30)."""
    retrieved = ["knowledge/dissolved_oxygen_and_aeration.md",
                 "FAO 589 Small-scale aquaponic food production (Somerville et al., 2014)",
                 "Goddek, Joyce, Kotzen & Burnell eds. (2019), Aquaponics Food Production "
                 "Systems, Springer Open"]
    assert fe.citation_accuracy(f"x [source: {cited}]", retrieved) == (1.0, [])


def test_a_different_source_is_still_fabricated():
    acc, bogus = fe.citation_accuracy("x [source: knowledge/plant_deficiency_cheatsheet.md]",
                                      ["knowledge/plant_nutrient_deficiencies.md"])
    assert acc == 0.0 and bogus == ["knowledge/plant_deficiency_cheatsheet.md"]


# --- after the first human check (2026-10-03): hedges are not claims, support needs a quote --

@pytest.mark.parametrize("claim", [     # every one is from the 2026-09-30 baseline
    "The context does not specify step‑by‑step emergency dosing.",
    "The context doesn't provide enough detail on NFT specifically for me to give you a full "
    "comparison.",
    "The context only mentions that resupplying water brings in buffering agents (carbonates).",
    "The sources I have cover source water treatment (chlorine/chloramine removal).",
    "I don't have enough information in the provided context to answer this question.",
    "I would need additional info/sources to advise on safe nitrite ppm levels for stocked fish.",
    "If more detail is needed, consult additional sources.",
    "It is recommended to consult additional resources specifically on algae control.",
    "If you have additional context on feeding rates, I can help further.",
])
def test_a_sentence_about_the_sources_is_not_a_claim(claim):
    assert fe.is_meta_claim(claim)


@pytest.mark.parametrize("claim", [
    "The source FAO589 Small‑scale aquaponic food production (Somerville et al., 2014) confirms "
    "that nitrogen deficiency is characterized by a pale green colour of older leaves.",
    "After 2–3 months of feeding, 40 fish grew to 80–100 grams each. (source:FAO589)",
    "Nitrifying bacteria need oxygen and time to build up in the biofilter.",
    "Rules on water abstraction and effluent discharge.",
    "Consult a veterinarian before treating fish with salt.",
])
def test_a_claim_about_the_world_is_kept_even_when_it_names_a_source(claim):
    assert not fe.is_meta_claim(claim)


def test_score_query_drops_hedges_and_numbers_only_real_claims():
    def ask(p):
        if p.startswith("Break"):
            return "1. The context does not specify a dose.\n2. Green water is algae"
        return "QUOTE: NONE\nVERDICT: UNSUPPORTED"

    row = fe.score_query("q", "a", "ctx", [], ask, lambda t: [1.0], qid="g1")
    assert [(c["id"], c["claim"]) for c in row["claims"]] == [("g1:1", "Green water is algae")]


CTX = ("[source: FAO 589]\nYearly operating costs (Table/uni00A0A7.2) 317.40. Nitrifying "
       "bacteria colonise the biofilter within four to six weeks. Keep water above 20 C.")


def test_supported_counts_only_with_a_quote_that_is_really_in_the_context():
    ok = "QUOTE: Nitrifying bacteria colonise the biofilter within four to six weeks.\n" \
         "VERDICT: SUPPORTED"
    assert fe.parse_judgement(ok, CTX) is True


def test_supported_from_memory_is_unsupported():
    """The leak the human check found: SUPPORTED with nothing in the context to point to."""
    assert fe.parse_judgement("QUOTE: NONE\nVERDICT: SUPPORTED", CTX) is False
    invented = "QUOTE: Bacteria need oxygen and time to build up.\nVERDICT: SUPPORTED"
    assert fe.parse_judgement(invented, CTX) is False


def test_a_quote_with_a_pdf_artefact_dropped_or_an_ellipsis_still_counts():
    assert fe.parse_judgement("QUOTE: \"Yearly operating costs (Table A7.2) 317.40 ... "
                              "Keep water above 20 C.\"\nVERDICT: SUPPORTED", CTX) is True


def test_two_sentences_quoted_out_of_their_original_order_still_count():
    assert fe.parse_judgement("QUOTE: Keep water above 20 C. Nitrifying bacteria colonise the "
                              "biofilter within four to six weeks.\nVERDICT: SUPPORTED", CTX)


def test_unsupported_needs_no_quote_and_a_reply_out_of_format_is_unjudged():
    assert fe.parse_judgement("QUOTE: NONE\nVERDICT: UNSUPPORTED", CTX) is False
    assert fe.parse_judgement("SUPPORTED", CTX) is None
    assert fe.parse_judgement("QUOTE: Keep water above 20 C.\nVERDICT: maybe", CTX) is None


def test_hedges_saved_by_older_reports_leave_every_number():
    r = _report()
    r["per_query"][0]["claims"].append(
        {"id": "g1:3", "claim": "The context does not specify a dose.", "verdict": False})
    r["judgements"] = {"j (run 1)": {"g1:1": True, "g1:2": False, "g1:3": False}}
    assert "g1:3" not in fe.verdicts_of(r) and "g1:3" not in fe.verdicts_of(r, "j (run 1)")
    assert "g1:3" not in fe.rejudge(r, lambda p: "QUOTE: NONE\nVERDICT: UNSUPPORTED")
    rows = fe.agreement_table(r, {"g1:1": True, "g1:2": False, "g1:3": True})
    assert all(x["n"] == 2 for x in rows)


def test_labelling_never_offers_a_hedge():
    from scripts.label_claims import sample
    r = {"per_query": [{"query": "q", "context": "c", "claims": [
        {"id": "h", "claim": "If more detail is needed, consult additional sources.",
         "verdict": False},
        {"id": "x", "claim": "Tilapia like warm water.", "verdict": True}]}]}
    assert [c["id"] for _, c in sample(r, 5, done=set())] == ["x"]


def test_rejudging_resumes_and_reports_after_every_query():
    """A stopped re-judge keeps what it paid for: judged claims are not asked again, and the
    caller hears after each query so it can save."""
    r = _report()
    r["per_query"].append({"query": "q2", "context": "c", "claims": [
        {"id": "g2:1", "claim": "x", "verdict": True}]})
    asked, seen = [], []

    def ask(p):
        asked.append(p)
        return "QUOTE: NONE\nVERDICT: UNSUPPORTED"

    out = fe.rejudge(r, ask, done={"g1:1": True}, workers=3,
                     on_query=lambda v, n, of: seen.append((len(v), n, of)))
    assert out == {"g1:1": True, "g1:2": False, "g2:1": False}
    assert len(asked) == 2
    assert seen == [(2, 1, 2), (3, 2, 2)]


def test_review_offers_only_labelled_claims_a_judge_ruled_on_differently():
    from scripts.label_claims import disputed
    r = _report()          # run 1: g1:1 True, g1:2 False
    r["judgements"] = {"j (run 1)": {"g1:1": True, "g1:2": True}}
    items = disputed(r, {"g1:1": True, "g1:2": False})
    assert [(c["id"], ruled) for _, c, ruled in items] == [
        ("g1:2", {"anthropic/claude (run 1)": False, "j (run 1)": True})]
    assert disputed(r, {"g1:1": True}) == []        # agreed everywhere: nothing to review


def test_review_names_tell_the_runs_and_prompts_apart():
    from scripts.label_claims import short_judge
    base = "nvidia/openai/gpt-oss-20b [Reasoning: low, max_tokens 8192]"
    assert short_judge(f"{base} (run 2)") == "gpt-oss-20b (run 2)"
    assert short_judge(f"{base} prompt e3a10ed853 (run 1)") == \
        "gpt-oss-20b, prompt e3a10ed853 (run 1)"
    assert short_judge("anthropic/claude-sonnet-5 (run 1)") == "claude-sonnet-5 (run 1)"


def test_reviewed_labels_override_only_what_was_reviewed():
    data = {"labels": {"a": True, "b": False}, "reviewed": {"b": True}}
    assert fe.reviewed_labels(data) == {"a": True, "b": True}
    assert data["labels"]["b"] is False             # the blind label is never rewritten


def test_a_citation_cut_short_from_a_retrieved_label_is_not_fabricated():
    """2026-10-06: the answer cited the book by authors and year only."""
    retrieved = ["Goddek, Joyce, Kotzen & Burnell eds. (2019), Aquaponics Food Production "
                 "Systems, Springer Open"]
    assert fe.citation_accuracy("x [source: Goddek, Joyce, Kotzen & Burnell eds. (2019)]",
                                retrieved) == (1.0, [])
    # too short to name one source: still fabricated
    assert fe.citation_accuracy("x [source: Goddek]", retrieved)[1] == ["Goddek"]

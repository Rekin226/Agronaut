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
        return "SUPPORTED" if "algae bloom" in claim else "UNSUPPORTED"
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

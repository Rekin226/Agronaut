"""What the projection footer tells an operator about the twin's demonstrated skill.

The footer used to say the model was "calibrated on literature seeds, not on your system". True,
and weaker than what is now known: scored against seven real ponds, it beat both null baselines on
none of them. "Not tuned to you" invites trusting the shape and discounting the precision;
"tested, and level prediction is not demonstrated" tells an operator not to act on the numbers.
Someone with fish in the water needs the second.

The wording is DERIVED from `data/twin_validation.json`, so it cannot drift from the evidence.
These tests pin that, and pin the direction it fails in when the evidence is absent.
"""

import json

from aqua_model import validation_status as vs


def _artifact(tmp_path, *, n=7, both=0, shape=5, r=0.297):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"summary": {
        "n_scored": n, "n_beats_both_nulls": both,
        "n_positive_shape_correlation": shape, "holdout_r_median": r}}))
    return p


def test_it_reports_the_real_current_state():
    """Against the committed record: zero of seven beat both baselines."""
    lines = " ".join(vs.validation_lines())
    assert "7 real ponds" in lines
    assert "0 of 7" in lines
    assert "compare options, never to predict a level" in lines


def test_it_cites_evidence_that_can_be_checked():
    lines = " ".join(vs.validation_lines())
    assert "data/twin_validation.json" in lines
    assert "scripts/validate_twin.py" in lines


def test_it_does_not_overstate_the_failure(tmp_path):
    """`n_beats_both_nulls` counts ponds beating BOTH baselines. Calling that "beat a no-change
    baseline" would be wrong in the pessimistic direction — one pond does beat the flat null while
    losing to the trend — and being more negative than the evidence is as inaccurate as being
    less."""
    lines = " ".join(vs.validation_lines(_artifact(tmp_path)))
    assert "both a flat and a trend baseline" in lines


def test_the_wording_follows_the_evidence(tmp_path):
    """Improve the validation and the footer improves on its own — nobody has to remember to
    rewrite a sentence that has quietly become false."""
    good = " ".join(vs.validation_lines(_artifact(tmp_path, n=10, both=8, shape=9, r=0.81)))
    assert "8 of 10" in good
    assert "0.81" in good


def test_a_missing_record_makes_the_caveat_stronger_not_weaker(tmp_path):
    """The failure direction that matters. An absent artifact must never read as an absent
    problem — an unmeasured model is not a validated one."""
    lines = " ".join(vs.validation_lines(tmp_path / "nope.json"))
    assert "UNMEASURED" in lines
    assert "illustrative" in lines


def test_a_corrupt_record_does_not_break_a_projection(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert "UNMEASURED" in " ".join(vs.validation_lines(bad))


def test_an_empty_summary_is_treated_as_unmeasured(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"summary": {"n_scored": 0}}))
    assert "UNMEASURED" in " ".join(vs.validation_lines(p))


# --- the gate ----------------------------------------------------------------

def test_level_prediction_is_not_currently_demonstrated():
    """Currently False, and that is the honest state rather than a bug to route around."""
    assert vs.level_prediction_demonstrated() is False


def test_the_gate_needs_a_majority(tmp_path):
    assert vs.level_prediction_demonstrated(_artifact(tmp_path, n=7, both=3)) is False
    assert vs.level_prediction_demonstrated(_artifact(tmp_path, n=7, both=4)) is True


def test_the_gate_fails_closed_without_evidence(tmp_path):
    assert vs.level_prediction_demonstrated(tmp_path / "nope.json") is False


# --- it reaches the operator -------------------------------------------------

def test_the_season_projection_carries_it():
    """The farmer-facing path. A projection printing kilograms and mg/L reads as a forecast
    whatever hedging surrounds it, so the hedge has to be specific enough to overcome that."""
    import aqua_model.production as prod
    src = (prod.__file__)
    text = open(src).read()
    assert "validation_lines" in text
    assert "calibrated on literature seeds" not in text


def test_the_scenario_comparison_carries_it():
    import aqua_model.scenario as sc
    text = open(sc.__file__).read()
    assert "validation_lines" in text

"""The sweep's decision rules, hermetically.

`scripts/retrieval_sweep.py` needs an index and an embedding model to produce a table, but the
rules that turn a table into a shipped constant must not. Those rules are the whole point of the
script: a sweep that only prints numbers leaves the judgement call to whoever reads it, and the
judgement call is where this project has twice gone wrong.

The rule under test is the floor picker, and its non-obvious half is the headroom constraint.
Maximising off-topic rejections alone picks 1.40 on the real corpus — a value that clears the
worst of 33 real queries by 0.017 and would refuse the 34th. These tests pin the rule that
rejects it.
"""

import pytest

from scripts import retrieval_sweep as sw


def _row(value, silenced=0, rejected=8, hit=0.879, headroom=0.117, **kw):
    row = {"value": value, "silenced_on_topic": silenced, "rejected_off_topic": rejected,
           "negative_controls": 10, "hit_rate": hit, "recall@k": 0.833, "precision@k": 0.364,
           "MRR": 0.657, "MAP@k": 0.624, "headroom": headroom}
    row.update(kw)
    return row


# --- the floor picker --------------------------------------------------------

def test_a_floor_that_silences_a_real_query_is_never_chosen():
    """The hard constraint. A refused real question is a worse failure than an answered
    off-topic one, so no rejection count can buy its way past this."""
    rows = [_row(1.30, silenced=4, rejected=10, headroom=0.5),
            _row(1.50, silenced=0, rejected=8)]
    assert sw.pick_floor(rows)["value"] == 1.50


def test_thin_headroom_is_rejected_even_when_it_refuses_every_control():
    """The real case, and the one a metric-maximising picker gets wrong. 1.40 silences nothing
    across all 33 golden queries and refuses 10/10 off-topic — and is still the wrong answer,
    because 0.017 of margin is half of what this project already called too thin."""
    rows = [_row(1.40, rejected=10, headroom=0.017),
            _row(1.50, rejected=8, headroom=0.117)]
    assert sw.pick_floor(rows)["value"] == 1.50


def test_among_safe_floors_the_one_refusing_most_off_topic_wins():
    rows = [_row(1.50, rejected=8, headroom=0.117),
            _row(1.65, rejected=4, headroom=0.267)]
    assert sw.pick_floor(rows)["value"] == 1.50


def test_ties_break_toward_more_headroom_not_tighter():
    """1.45 and 1.50 score identically on every metric on the real corpus. The looser one is
    strictly safer and costs nothing, so a tie must never be broken toward tightness."""
    rows = [_row(1.45, rejected=8, headroom=0.107),
            _row(1.50, rejected=8, headroom=0.117)]
    assert sw.pick_floor(rows)["value"] == 1.50


def test_no_viable_floor_returns_none_rather_than_the_least_bad():
    """When nothing satisfies both constraints the caller must keep the shipped value. Returning
    a best-of-a-bad-set would ship a floor that silences real questions."""
    rows = [_row(1.30, silenced=2, headroom=0.4), _row(1.35, silenced=1, headroom=0.02)]
    assert sw.pick_floor(rows) is None


def test_a_missing_headroom_field_is_treated_as_unsafe():
    """Absent evidence is not evidence of safety. A row without a measured headroom must not
    satisfy the headroom constraint by default."""
    rows = [{"value": 1.4, "silenced_on_topic": 0, "rejected_off_topic": 10,
             "negative_controls": 10, "hit_rate": 0.9}]
    assert sw.pick_floor(rows) is None


def test_hit_rate_breaks_a_rejection_tie_before_headroom_does():
    rows = [_row(1.50, rejected=8, hit=0.879, headroom=0.117),
            _row(1.55, rejected=8, hit=0.700, headroom=0.167)]
    assert sw.pick_floor(rows)["value"] == 1.50


# --- the cap / beta picker ---------------------------------------------------

def test_metric_picker_maximises_the_named_metric():
    rows = [_row(1, **{"MAP@k": 0.624}), _row(2, **{"MAP@k": 0.568})]
    assert sw.pick_by(rows, "MAP@k")["value"] == 1


def test_metric_picker_breaks_ties_on_hit_rate():
    rows = [_row(1, hit=0.818, **{"MAP@k": 0.6}), _row(2, hit=0.879, **{"MAP@k": 0.6})]
    assert sw.pick_by(rows, "MAP@k")["value"] == 2


# --- the constant the rules depend on ----------------------------------------

def test_min_headroom_matches_the_margin_the_project_accepted():
    """0.10 is not a taste. It is the margin the original floor calibration chose when it
    rejected a 0.032 alternative as 'a threefold cut in safety margin'. If this constant is ever
    lowered, that recorded reasoning is being overruled and should be re-argued in writing."""
    assert sw._MIN_HEADROOM == 0.10


@pytest.mark.parametrize("kind", ["floor", "cap", "beta"])
def test_every_swept_constant_has_a_default_ladder(kind):
    """A sweep with no candidates silently succeeds and reports nothing, which reads exactly like
    a sweep that found no change."""
    assert len(getattr(sw, f"_{kind.upper()}S")) >= 3

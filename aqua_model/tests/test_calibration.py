"""CRITICAL — the acceptance gate, now driven by open published reference systems.

This is the line between "a model that runs" and "a model that reproduces real systems."

Originally it waited (SKIPPED) for the founder's own Taiwan system. Since that private data
won't exist, the gate instead validates the model against fully documented systems from the
literature — the UVI commercial system (Rakocy et al. 2004; Rakocy 1988). For each, `size_system`
must reproduce the documented daily feed input (plant area x feeding-rate ratio) within +/-15%.

Feed is the right thing to gate: it is the model's load-bearing output (feed = area x FRR) and
the one number these papers report precisely. FCR, yield and water use are cross-checked more
loosely elsewhere (calibration.py, datasets.py) because they are far more sampling-dependent.

A private or independent system can be added any time by appending to
`data/reference_systems.json` — these tests pick it up automatically.
"""

import pytest

from aqua_model import reference_systems as rs

SYSTEMS = rs.load()
_IDS = [s["id"] for s in SYSTEMS]


def test_reference_systems_are_documented():
    assert SYSTEMS, "no reference systems loaded"
    for s in SYSTEMS:
        assert s["source"], f"{s['id']} missing a source citation"
        assert s["measured_feed_g_per_day"] > 0


@pytest.mark.parametrize("system", SYSTEMS, ids=_IDS)
def test_model_reproduces_reference_system_feed(system):
    result = rs.check(system)
    assert result["within_tolerance"], (
        f"{system['id']}: feed off by {result['feed_error']:.1%} — model "
        f"{result['model_feed_g_per_day']} vs measured {result['measured_feed_g_per_day']} g/day"
    )


@pytest.mark.parametrize("system", SYSTEMS, ids=_IDS)
def test_reference_system_is_feasible_or_flags_why_not(system):
    # A real, running system should not be flagged infeasible; if it is, the warning must
    # explain why (a signal our coefficients need calibration).
    result = rs.check(system)
    assert result["feasible"] or result["warnings"]

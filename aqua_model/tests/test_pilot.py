"""Pilot-proposal generator: a funder-ready document built deterministically from a sized
design — proposed system, the ask, projected food/water outcomes, and the data the install
will produce (the dataset moat). Thin, cited wrapper over the design; no LLM.
"""

from aqua_model import size_system, validate_design_input
from aqua_model.pilot import PilotInfo, projected_outcomes, to_pilot_proposal


def _design_and_out():
    d = validate_design_input("tilapia", "lettuce", 20, 27, 5000)
    return d, size_system(d)


def _pilot():
    return PilotInfo(
        site="Kaya, Burkina Faso",
        organization="Sahel Aquaponics Cooperative",
        ask_amount=15000, currency="USD",
        beneficiaries="25 smallholder households",
        context="Water-scarce peri-urban site; year-round leafy-green demand.",
    )


def test_projected_outcomes_are_deterministic_and_sane():
    d, out = _design_and_out()
    o = projected_outcomes(d, out)
    # annual food = crop yield (kg/m2/yr) x grow area
    assert o["annual_food_kg"] == 20 * 25.0                 # lettuce 25 kg/m2/yr, 20 m2
    assert o["annual_water_use_m3"] == round(out.makeup_water_lpd * 365 / 1000.0, 1)
    assert o["water_use_efficiency_kg_per_m3"] > 0
    # deterministic
    assert projected_outcomes(d, out) == o


def test_proposal_contains_the_funder_essentials():
    d, out = _design_and_out()
    md = to_pilot_proposal(d, out, _pilot())
    # the ask
    assert "15,000" in md or "15000" in md
    assert "USD" in md
    # who and where
    assert "Kaya, Burkina Faso" in md
    assert "Sahel Aquaponics Cooperative" in md
    assert "25 smallholder households" in md
    # projected outcomes section with real numbers
    assert "Projected" in md
    assert "kg" in md and "water" in md.lower()
    # the data the install produces (the moat / M&E hook funders want)
    assert "data" in md.lower()
    assert "calibrat" in md.lower() or "logging" in md.lower()


def test_proposal_carries_the_honesty_layer():
    d, out = _design_and_out()
    md = to_pilot_proposal(d, out, _pilot())
    # a fundable proposal states its limits — cited coefficients + not-modeled survive
    assert "source" in md.lower()
    assert "not model" in md.lower() or "NOT modeled" in md


def test_proposal_flags_infeasible_designs_honestly():
    d = validate_design_input("tilapia", "lettuce", 500, 27, 10)   # water-starved
    out = size_system(d)
    md = to_pilot_proposal(d, out, _pilot())
    assert "NOT FEASIBLE" in md or "not feasible" in md.lower()

"""The funder-facing report renders completely and carries the honesty layer."""

from aqua_model import size_system
from aqua_model.report import to_markdown
from aqua_model.validate import validate_design_input


def _out_and_design():
    di = validate_design_input(
        fish_species="tilapia", crop="lettuce", grow_area_m2=6.0,
        temperature_c=26.0, water_budget_lpd=200.0,
    )
    return di, size_system(di)


def test_report_includes_all_major_sections():
    di, out = _out_and_design()
    md = to_markdown(di, out, site="Ouagadougou pilot")
    for heading in (
        "# Aquaponics System Design — Ouagadougou pilot",
        "## Inputs", "## Sized System", "## Bill of Materials",
        "## Operating Envelope", "## Maintenance Checklist",
        "## Nitrogen Consistency Check", "## NOT Modeled", "## Coefficients Used",
    ):
        assert heading in md


def test_report_cites_every_coefficient_with_a_source():
    di, out = _out_and_design()
    md = to_markdown(di, out)
    # Every coefficient row must carry a source token.
    for c in out.coefficients_used:
        assert c.name in md
        assert c.source in md


def test_report_states_what_is_not_modeled():
    di, out = _out_and_design()
    md = to_markdown(di, out)
    assert "must not treat this as a complete engineering design" in md
    assert "pH" in md


def test_report_surfaces_infeasibility():
    di = validate_design_input(
        fish_species="tilapia", crop="lettuce", grow_area_m2=50.0,
        temperature_c=28.0, water_budget_lpd=10.0,
    )
    out = size_system(di)
    md = to_markdown(di, out)
    assert "NOT FEASIBLE" in md

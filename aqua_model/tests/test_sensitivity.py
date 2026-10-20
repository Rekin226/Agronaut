"""The sensitivity table: every number from the optimizer, honest about its limits."""

from aqua_model import size_system
from aqua_model.crops import CROPS
from aqua_model.optimizer import OptimizeInput, optimize
from aqua_model.pilot import PilotInfo, to_pilot_proposal
from aqua_model.report import to_markdown
from aqua_model.sensitivity import sensitivity_table
from aqua_model.validate import WATER_NOT_LIMITING, validate_design_input


def _design(water=200.0, area=6.0, temp=26.0, fish="tilapia", crop="lettuce", crop_plan=None):
    return validate_design_input(
        fish_species=fish, crop=crop, grow_area_m2=area,
        temperature_c=temp, water_budget_lpd=water, crop_plan=crop_plan,
    )


def _by_change(result):
    return {row.change: row for row in result.rows}


def test_table_in_design_report_with_honesty_line_and_full_perturbation_set():
    di = _design()
    md = to_markdown(di, size_system(di))
    assert "## Sensitivity (model response)" in md
    assert "not how a real system would" in md
    for label in ("water budget +20%", "water budget -20%", "grow area +20%",
                  "grow area -20%", "crop mix", "Design as given"):
        assert label in md


def test_every_row_traces_to_the_optimizer():
    di = _design(area=12.0, water=2000.0, temp=27.0)
    result = sensitivity_table(di)
    rows = _by_change(result)
    pinned = (di.fish_species,), (di.crop,)

    def direct(area, water):
        return optimize(OptimizeInput(
            grow_area_m2=area, temperature_c=27.0, water_budget_lpd=water,
            fish_palette=pinned[0], crop_palette=pinned[1],
        ))

    for label, perturbed in (
        ("water budget +20%", direct(12.0, 2000.0 * 1.2)),
        ("water budget -20%", direct(12.0, 2000.0 * 0.8)),
        ("grow area +20%", direct(12.0 * 1.2, 2000.0)),
        ("grow area -20%", direct(12.0 * 0.8, 2000.0)),
    ):
        assert rows[label].feasible and perturbed.best is not None
        assert rows[label].food_kg_yr == perturbed.best.food_kg_yr
        assert rows[label].water_efficiency_kg_per_m3 == perturbed.best.water_efficiency_kg_per_m3
    # The crop-mix row is an in-band-palette optimizer run: it must never beat what the
    # optimizer itself can find over the same palette, and the design's crop stays a
    # legal candidate.
    mix = next(row for key, row in rows.items() if key.startswith("crop mix"))
    band = tuple(sorted(
        k for k, c in CROPS.items() if c.temp_min_c <= 27.0 <= c.temp_max_c))
    full = optimize(OptimizeInput(
        grow_area_m2=12.0, temperature_c=27.0, water_budget_lpd=2000.0,
        fish_palette=(di.fish_species,),
        crop_palette=band,
    ))
    assert mix.feasible and full.best is not None
    assert mix.food_kg_yr == full.best.food_kg_yr


def test_water_rows_omitted_with_a_note_when_budget_not_given():
    di = _design(water=WATER_NOT_LIMITING)
    result = sensitivity_table(di)
    rows = _by_change(result)
    assert not any(r.startswith("water budget") for r in rows)
    assert any("Water-budget rows are omitted" in n for n in result.notes)
    assert "grow area +20%" in rows and any(k.startswith("crop mix") for k in rows)


def test_infeasible_baseline_shows_absolutes_without_deltas():
    di = _design(area=50.0, water=50.0, temp=27.0)
    result = sensitivity_table(di)
    if result.baseline_feasible:
        # The fixture only holds if this budget really starves the design.
        raise AssertionError("fixture no longer infeasible; pick a smaller budget")
    assert any("deltas" in n for n in result.notes)
    rendered = "\n".join(
        # render via the report so the exact cells a reader sees are what is asserted
        to_markdown(di, size_system(di)).splitlines()
    )
    assert "none feasible" in rendered
    for row in result.rows:
        if row.feasible:
            assert row.food_delta_pct is None
            assert row.water_efficiency_delta_pct is None


def test_pilot_proposal_carries_the_table_exactly_once():
    di = _design()
    out = size_system(di)
    pilot = PilotInfo(site="Bobo-Dioulasso", organization="KfW pilot", ask_amount=25000)
    md = to_pilot_proposal(di, out, pilot)
    assert md.count("## Sensitivity (model response)") == 1
    assert "not how a real system would" in md


def test_crop_mix_row_only_recommends_crops_in_temperature_band():
    """Review on #241: the mix row offered cucumber 100% (topping out at 30 °C) for a
    33 °C clarias + amaranth design. The palette must be filtered to crops whose band
    covers the design's temperature before the optimizer ranks it."""
    di = _design(temp=33.0, fish="clarias", crop="amaranth")
    result = sensitivity_table(di)
    row = next(r for r in result.rows if r.change.startswith("crop mix"))
    assert row.feasible
    assert "amaranth 100%" in row.detail
    assert "cucumber" not in row.detail
    assert "lettuce" not in row.detail
    assert "5 of 36 crops in band at 33 °C" in row.detail
    assert row.food_delta_pct == 0.0


def test_crop_mix_row_says_so_when_no_crop_grows_at_the_temperature():
    """No crop in the palette tops 35 °C: at 40 °C the row must report infeasible with
    a reason, not raise on an empty palette."""
    di = _design(temp=40.0, fish="clarias", crop="amaranth")
    result = sensitivity_table(di)
    row = next(r for r in result.rows if r.change.startswith("crop mix"))
    assert not row.feasible
    assert "no crop in the palette grows at 40 °C" in row.detail


def test_mixed_bed_notes_the_dominant_crop_pinning():
    di = _design(crop_plan=[("lettuce", 4.0), ("basil", 2.0)])
    result = sensitivity_table(di)
    assert any("Mixed bed" in n and "lettuce" in n for n in result.notes)


def test_table_is_deterministic():
    di = _design()
    first, second = sensitivity_table(di), sensitivity_table(di)
    assert first == second


def test_render_marks_missing_values_without_crashing():
    di = _design(water=WATER_NOT_LIMITING)
    md = to_markdown(di, size_system(di))
    sensitivity_lines = md.split("## Sensitivity (model response)")[1].split("## ")[0]
    assert "—" in sensitivity_lines
    assert sensitivity_lines.count("|") >= 2 * (2 + len(sensitivity_table(di).rows))

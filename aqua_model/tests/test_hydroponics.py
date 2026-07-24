"""Hydroponics sizing: ET-driven nutrient-solution systems (NO fish). Reuses the crop
database, water/geometry coefficients, and the honesty layer, with its own trust gate,
its own 'not modeled' list, and nutrient (EC / elemental-N) targets fish systems lack.
"""

import pytest

from aqua_model import (
    size_hydroponic_system,
    validate_hydroponic_input,
    HydroponicInput,
    HydroponicOutput,
    ValidationError,
)


def _valid(**kw):
    args = dict(crop="lettuce", grow_area_m2=10.0, temperature_c=22.0, water_budget_lpd=500.0)
    args.update(kw)
    return validate_hydroponic_input(**args)


def test_validate_builds_input_and_rejects_unknown_crop():
    inp = _valid()
    assert isinstance(inp, HydroponicInput)
    assert inp.crop == "lettuce"
    with pytest.raises(ValidationError):
        _valid(crop="unobtainium")


def test_validate_rejects_out_of_range():
    with pytest.raises(ValidationError):
        _valid(grow_area_m2=-5)
    with pytest.raises(ValidationError):
        _valid(temperature_c=99)


def test_size_feasible_carries_numbers_sources_and_not_modeled():
    out = size_hydroponic_system(_valid())
    assert isinstance(out, HydroponicOutput)
    assert out.feasible is True
    assert out.reservoir_volume_l > 0
    assert out.daily_water_use_lpd > 0
    assert out.pump_turnover_lph > 0
    assert out.coefficients_used and all(c.source for c in out.coefficients_used)
    assert out.not_modeled
    # hydroponics-specific: NO fish concepts anywhere
    text = " ".join(out.not_modeled + out.assumptions).lower()
    assert "fish" not in text or "no fish" in text
    assert not hasattr(out, "feed_g_per_day")


def test_size_includes_nutrient_targets():
    out = size_hydroponic_system(_valid())
    # a hydroponic design must state the nutrient solution target the aquaponic one doesn't
    assert out.nutrient_target["ec_mS_cm"]["low"] > 0
    assert out.nutrient_target["elemental_n_g_per_day"] > 0


def test_fruiting_crop_targets_higher_ec_than_leafy():
    leafy = size_hydroponic_system(_valid(crop="lettuce")).nutrient_target["ec_mS_cm"]["target"]
    fruiting = size_hydroponic_system(_valid(crop="tomato")).nutrient_target["ec_mS_cm"]["target"]
    assert fruiting > leafy


def test_water_budget_infeasible_gives_nearest_feasible():
    out = size_hydroponic_system(_valid(grow_area_m2=200.0, water_budget_lpd=50.0))
    assert out.feasible is False
    assert out.binding_constraint == "water_budget"
    assert any("reduce grow area" in w.lower() for w in out.warnings)


def test_hydroponic_output_has_no_fish_fields():
    out = size_hydroponic_system(_valid())
    bom_text = " ".join(item["item"].lower() for item in out.bill_of_materials)
    assert "fish" not in bom_text and "biofilter" not in bom_text
    assert "reservoir" in bom_text or "tank" in bom_text

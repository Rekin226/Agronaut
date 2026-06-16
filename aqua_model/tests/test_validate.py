"""CRITICAL: the trust gate rejects bad inputs loudly (no silent defaults/clamps)."""

import pytest

from aqua_model.validate import validate_design_input, ValidationError


def _ok(**over):
    base = dict(
        fish_species="tilapia", crop="lettuce", grow_area_m2=10.0,
        temperature_c=28.0, water_budget_lpd=500.0,
    )
    base.update(over)
    return validate_design_input(**base)


def test_valid_input_passes_and_normalizes():
    di = _ok(fish_species="Tilapia", crop="Lettuce")  # mixed case
    assert di.fish_species == "tilapia"
    assert di.crop == "lettuce"
    assert di.grow_area_m2 == 10.0


def test_unknown_species_rejected():
    with pytest.raises(ValidationError) as e:
        _ok(fish_species="dragon")
    assert any("fish_species" in m for m in e.value.errors)


def test_unknown_crop_rejected():
    with pytest.raises(ValidationError):
        _ok(crop="moonfruit")


def test_out_of_range_temperature_rejected():
    with pytest.raises(ValidationError) as e:
        _ok(temperature_c=99.0)
    assert any("temperature_c" in m for m in e.value.errors)


def test_non_numeric_area_rejected_not_defaulted():
    with pytest.raises(ValidationError):
        _ok(grow_area_m2="lots")


def test_bool_is_not_accepted_as_number():
    with pytest.raises(ValidationError):
        _ok(water_budget_lpd=True)


def test_multiple_errors_collected_together():
    with pytest.raises(ValidationError) as e:
        validate_design_input(
            fish_species="dragon", crop="moonfruit",
            grow_area_m2=-5, temperature_c=200, water_budget_lpd="n/a",
        )
    assert len(e.value.errors) >= 4

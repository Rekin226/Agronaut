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


# --- the optimizer shares the gate ------------------------------------------------------
# The optimizer enumerates designs and reports a "best ratio". It used to accept its three
# numbers unchecked, so a negative area produced a confident recommendation with negative
# yields at exit 0, and a NaN temperature scored identically to an optimal one (every NaN
# comparison is False, so the temperature penalty silently never applied).

from aqua_model import optimize, OptimizeInput  # noqa: E402


def _opt(**over):
    base = dict(grow_area_m2=10.0, temperature_c=28.0, water_budget_lpd=5000.0,
                objective="food")
    base.update(over)
    return optimize(OptimizeInput(**base))


def test_optimizer_accepts_a_valid_input():
    assert _opt().best is not None


def test_optimizer_rejects_negative_grow_area():
    with pytest.raises(ValidationError) as e:
        _opt(grow_area_m2=-5.0)
    assert "grow_area_m2" in str(e.value)


def test_optimizer_rejects_nan_temperature():
    with pytest.raises(ValidationError) as e:
        _opt(temperature_c=float("nan"))
    assert "temperature_c" in str(e.value)


def test_optimizer_rejects_infinite_water_budget():
    with pytest.raises(ValidationError) as e:
        _opt(water_budget_lpd=float("inf"))
    assert "water_budget_lpd" in str(e.value)


def test_optimizer_rejects_unknown_objective():
    with pytest.raises(ValidationError) as e:
        _opt(objective="maximise_vibes")
    assert "objective" in str(e.value)

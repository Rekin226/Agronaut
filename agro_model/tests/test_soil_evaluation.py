"""Independent error arithmetic and unchanged soil-state conversion semantics."""

from datetime import date
from fractions import Fraction
from math import sqrt

import pytest

from agro_model.soil_evaluation import (
    compare_soil_state,
    interval_water_content,
    summarize_errors,
    summarize_water_content_errors,
)
from agro_model.soil_storage import SoilCompartment, SoilPatch
from agro_model.tests.test_soil_state import update
from agro_model.water import Soil, SoilState


def test_prediction_minus_adjusted_reading_has_a_declared_sign():
    soil = Soil(.32, .15, .8, .4, "Synthetic soil")
    prediction = SoilState(date(2026, 7, 6), 70, "Synthetic prediction")
    result = compare_soil_state(soil, prediction, update())
    # (.32 - .25) * 800 = 56; explicit +2 mm timing adjustment gives 58.
    assert result == pytest.approx(dict(predicted_depletion_mm=70, reading_basis_depletion_mm=56,
        timing_adjustment_mm=2, reference_depletion_mm=58, error_mm=12, absolute_error_mm=12))
    assert prediction.depletion_mm == 70


def test_error_metrics_do_not_cancel_absolute_errors_or_invent_missing_data():
    result = summarize_errors([-3, 1, 2])
    assert result == pytest.approx(dict(count=3, bias_mm=0, mae_mm=2, rmse_mm=sqrt(14/3), maximum_absolute_error_mm=3))
    assert summarize_errors(reversed([-3, 1, 2])) == result
    assert summarize_errors([]) == dict(count=0, bias_mm=None, mae_mm=None, rmse_mm=None, maximum_absolute_error_mm=None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 10001, None])
def test_error_metrics_reject_invalid_quantities(value):
    with pytest.raises(ValueError):
        summarize_errors([value])


def test_comparison_rejects_mismatched_boundaries_and_depths():
    soil = Soil(.32, .15, .8, .4, "Synthetic soil")
    with pytest.raises(ValueError, match="boundary"):
        compare_soil_state(soil, SoilState(date(2026, 7, 7), 70, "Synthetic"), update())
    with pytest.raises(ValueError, match="depth"):
        compare_soil_state(soil, SoilState(date(2026, 7, 6), 70, "Synthetic"), update(represented_depth_m=.2))


def profile():
    return SoilPatch("sample", 1, (
        SoilCompartment("upper", 0, .2, .3, .1, .5, .05, .2, "Synthetic"),
        SoilCompartment("lower", .2, 1, .3, .1, .5, .05, .4, "Synthetic")))


def test_partial_cell_interval_is_depth_weighted_and_keeps_exact_values():
    patch = profile()
    result = interval_water_content(patch, .1, .5)
    assert result["water_content"] == Fraction(7, 20)  # (.1*.2 + .3*.4)/.4
    assert [r["represented_thickness_m"] for r in result["layers"]] == [Fraction(1, 10), Fraction(3, 10)]
    assert interval_water_content(patch, .2, .5)["water_content"] == Fraction(2, 5)
    assert interval_water_content(patch, 0, 1)["water_content"] == Fraction(9, 25)
    assert patch == profile()


@pytest.mark.parametrize("top,bottom", [(0, 0), (.5, .1), (-.1, .2), (0, 1.01), (None, .2), (True, .2), (0, float("nan"))])
def test_intervals_reject_empty_missing_outside_or_invalid_depths(top, bottom):
    with pytest.raises(ValueError):
        interval_water_content(profile(), top, bottom)


def test_theta_metrics_have_correct_units_and_do_not_cancel_absolute_errors():
    result = summarize_water_content_errors([-.03, .01, .02])
    assert result["unit"] == "m3/m3"
    assert result["count"] == 3
    assert result["bias"] == pytest.approx(0)
    assert result["mae"] == pytest.approx(.02)
    assert result["rmse"] == pytest.approx(sqrt(.0014/3))
    assert result["maximum_absolute_error"] == .03
    assert summarize_water_content_errors([])["mae"] is None
    for bad in (True, float("nan"), float("inf"), 1.1, None):
        with pytest.raises(ValueError):
            summarize_water_content_errors([bad])

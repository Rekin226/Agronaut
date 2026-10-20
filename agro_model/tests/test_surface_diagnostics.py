"""Error arithmetic and missing-position behavior are independent of the predictor."""

import math

import pytest

from agro_model.surface_diagnostics import (
    AdvanceReading,
    compare_advance_anchors,
    summarize_advance_errors,
)


def reading(x, minutes):
    return AdvanceReading(x, minutes, "Synthetic stopwatch record")


def test_signed_arrival_errors_and_exact_stations():
    half, full = compare_advance_anchors(200, 20, 60, (reading(0, 0), reading(100, 18), reading(200, 64)))
    assert half["error_min"] == 2 and half["absolute_error_min"] == 2
    assert full["error_min"] == -4 and full["absolute_error_min"] == 4
    assert half["reference_source"] == "Synthetic stopwatch record"


def test_missing_half_length_is_not_interpolated_from_nearby_observations():
    half, full = compare_advance_anchors(200, 20, 60, (reading(99, 18), reading(101, 22), reading(200, 64)))
    assert half["status"] == "missing_exact_station"
    assert half["reference_min"] is half["error_min"] is half["absolute_error_min"] is None
    assert full["error_min"] == -4


def test_zero_is_a_recorded_value_not_a_missing_reading():
    half, _ = compare_advance_anchors(200, 20, 60, (reading(100, 0), reading(200, 64)))
    assert half["status"] == "compared" and half["error_min"] == 20


def test_mean_absolute_and_squared_error_are_separate():
    result = summarize_advance_errors([1, -3, 2])
    assert result == dict(count=3, bias_min=0, mae_min=2, rmse_min=math.sqrt(14/3), maximum_absolute_error_min=3)
    empty = summarize_advance_errors([])
    assert empty["count"] == 0 and all(value is None for key, value in empty.items() if key != "count")


@pytest.mark.parametrize("values", [[None], [True], [float("nan")], [float("inf")], [10081], [0]*1001])
def test_error_summary_rejects_unknown_nonfinite_and_excessive_inputs(values):
    with pytest.raises(ValueError):
        summarize_advance_errors(values)


@pytest.mark.parametrize("readings", [
    (), (reading(100, 20), reading(100, 21)), (reading(201, 20),),
    (reading(100, 70), reading(200, 60)), (reading(0, 1), reading(200, 60)), (object(),),
])
def test_ambiguous_or_inconsistent_observation_positions_are_rejected(readings):
    with pytest.raises(ValueError):
        compare_advance_anchors(200, 20, 60, readings)

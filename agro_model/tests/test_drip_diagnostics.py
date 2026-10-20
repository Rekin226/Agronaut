from math import sqrt

import pytest

from agro_model.drip_diagnostics import compare_emitter, summarize_errors, summarize_events


def test_pressure_and_flow_errors_have_explicit_units_and_sign():
    pair = compare_emitter(2, 10, 1.5, 90, 1000, "gauge_at_emitter")
    assert pair['flow_lph']['error'] == .5
    assert pair['pressure_kpa']['predicted'] == pytest.approx(98.0665)
    assert pair['pressure_kpa']['error'] == pytest.approx(8.0665)


def test_missing_density_does_not_invent_pressure_or_hide_known_flow():
    pair = compare_emitter(2, 10, 0, 0, None, "gauge_at_emitter")
    assert pair['flow_lph']['error'] == 2
    assert pair['pressure_kpa'] == dict(predicted=None, recorded=0, error=None, status='missing_water_density')


def test_missing_readings_are_not_zero_and_no_observation_is_clipped():
    pair = compare_emitter(2, 10, None, 10000, 1000, "gauge_at_emitter")
    assert pair['flow_lph']['error'] is None
    assert pair['pressure_kpa']['recorded'] == 10000
    assert pair['pressure_kpa']['error'] < -9900
    assert compare_emitter(2, 10, 1, None, 1000, "gauge_at_emitter")['pressure_kpa']['status'] == 'missing_reading'


def test_equal_event_weight_differs_from_pooling_samples():
    result = summarize_events([[2, 2, 2, 2], [-4], []])
    assert result['event_count'] == 2 and result['paired_sample_count'] == 5
    assert result['events_without_pairs'] == 1
    assert result['bias'] == -1
    assert result['mae'] == 3
    assert result['rmse'] == pytest.approx(sqrt(10))
    assert result['maximum_absolute_error'] == 4
    assert summarize_errors([])['bias'] is None
    assert summarize_events([[], []])['rmse'] is None


@pytest.mark.parametrize('density', [True, 0, float('nan'), 2001, '1000'])
def test_density_is_explicit_finite_physical_input(density):
    with pytest.raises(ValueError):
        compare_emitter(2, 10, 2, 100, density, "gauge_at_emitter")


def test_unknown_pressure_reference_never_scores_an_absolute_or_unidentified_reading():
    pair = compare_emitter(2, 10, 1.5, 90, 1000)
    assert pair['flow_lph']['error'] == .5
    assert pair['pressure_kpa']['predicted'] == pytest.approx(98.0665)
    assert pair['pressure_kpa']['recorded'] == 90
    assert pair['pressure_kpa']['error'] is None
    assert pair['pressure_kpa']['status'] == 'unknown_pressure_reference'

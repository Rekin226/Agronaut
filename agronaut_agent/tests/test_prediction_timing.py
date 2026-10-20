"""Chronology is visible evidence, not a proxy for independent validation."""

from datetime import datetime, timezone

import pytest

from agronaut_agent.prediction_timing import (
    observation_timing_counts,
    prediction_event_timing,
    prediction_observation_timing,
    render_observation_timing,
    render_timing,
    timing_counts,
)

START = '2026-07-07T09:00:00Z'
END = '2026-07-07T09:30:00Z'


@pytest.mark.parametrize('saved,status', [
    ('2026-07-07T16:59:59.999999+08:00', 'saved_before_event'),
    ('2026-07-07T17:00:00+08:00', 'saved_during_event'),
    ('2026-07-07T09:29:59.999999Z', 'saved_during_event'),
    ('2026-07-07T09:30:00Z', 'saved_at_or_after_event_end'),
    ('2026-07-08T00:00:00Z', 'saved_at_or_after_event_end'),
])
def test_timezone_and_boundary_ordering(saved, status):
    result = prediction_event_timing(saved, START, END)
    assert result['status'] == status
    assert result['event_start_utc'] == '2026-07-07T09:00:00+00:00'
    assert result['event_end_utc'] == '2026-07-07T09:30:00+00:00'
    assert result['prospective_validation_status'] == 'not_established'
    assert result['reason'] is None


@pytest.mark.parametrize('saved', [None, '', 'invalid', True, 123, '2026-07-07T08:00:00',
    datetime(2026, 7, 7, 8), '0001-01-01T00:00:00+08:00'])
def test_invalid_or_naive_save_time_is_unknown_not_before(saved):
    result = prediction_event_timing(saved, START, END)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'missing_or_invalid_calculation_timestamp'
    assert result['calculation_saved_at_utc'] is None


@pytest.mark.parametrize('start,end', [(None, END), (START, None), (END, START), (START, START),
    ('2026-07-07T09:00:00', END)])
def test_incomplete_or_reversed_event_windows_are_not_classified(start, end):
    result = prediction_event_timing('2026-07-07T08:00:00Z', start, end)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'missing_or_invalid_event_window'


def test_counts_include_excluded_events_and_old_reports_are_explicit():
    rows = [dict(prediction_timing=prediction_event_timing(saved, START, END), included_in_metrics=False)
        for saved in (None, '2026-07-07T08:00:00Z', '2026-07-07T09:00:00Z', END)]
    counts = timing_counts(rows)
    assert list(counts.values()) == [1, 1, 1, 1]
    text = render_timing(dict(timing_counts=counts))
    assert 'before start 1; during 1; at/after end 1; unknown 1' in text
    assert 'does not establish prospective validation' in text
    assert 'not recorded' in render_timing({})


def test_aware_datetime_inputs_and_json_timestamp_output():
    result = prediction_event_timing(datetime(2026, 7, 7, 8, tzinfo=timezone.utc),
        datetime.fromisoformat(START), datetime.fromisoformat(END))
    assert result['calculation_saved_at_utc'] == '2026-07-07T08:00:00+00:00'
    assert result['status'] == 'saved_before_event'


@pytest.mark.parametrize('saved,observation_order,boundary_order', [
    ('2026-07-06T09:59:59.999999Z', 'before', 'before'),
    ('2026-07-06T18:00:00+08:00', 'at_or_after', 'before'),
    ('2026-07-06T12:00:00Z', 'at_or_after', 'before'),
    ('2026-07-07T00:00:00+08:00', 'at_or_after', 'at_or_after'),
    (None, 'unknown', 'unknown'),
])
def test_soil_measurement_and_reference_boundary_have_independent_ordering(saved, observation_order, boundary_order):
    result = prediction_observation_timing(saved, '2026-07-06T18:00:00+08:00', '2026-07-07T00:00:00+08:00')
    assert result['relative_to_observation'] == observation_order
    assert result['relative_to_reference_boundary'] == boundary_order
    assert result['observed_at_utc'] == '2026-07-06T10:00:00+00:00'
    assert result['reference_at_utc'] == '2026-07-06T16:00:00+00:00'
    assert result['prospective_validation_status'] == 'not_established'


def test_soil_unknown_measurement_does_not_hide_known_boundary_and_counts_include_exclusions():
    timing = prediction_observation_timing('2026-07-06T08:00:00Z', '2026-07-06T18:00:00', '2026-07-07T00:00:00Z')
    assert timing['relative_to_observation'] == 'unknown'
    assert timing['relative_to_reference_boundary'] == 'before'
    counts = observation_timing_counts([dict(observation_timing=timing, included_in_metrics=False)])
    assert counts == dict(relative_to_observation=dict(before=0, at_or_after=0, unknown=1),
        relative_to_reference_boundary=dict(before=1, at_or_after=0, unknown=0))
    assert 'measurement: before 0, at/after 0, unknown 1' in render_observation_timing(dict(observation_timing_counts=counts))
    assert 'not recorded' in render_observation_timing({})

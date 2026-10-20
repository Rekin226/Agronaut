"""Check source alignment and discrepancy accounting without choosing model inputs."""
import json

import pytest

from scripts.reconcile_bushland_rain import (
    SOURCE,
    compare_intervals,
    consecutive_groups,
    dew_comparison,
    reconcile,
)


@pytest.fixture
def intervals():
    weather, balance = [], []
    for index in range(1, 35137):
        weather.append([None, 1+index/96, *([0]*13)])
        balance.append([2016, 1+index/96, *([0]*16)])
    return weather, balance


def test_exact_values_missing_categories_and_source_coordinates(intervals):
    weather, balance = intervals
    weather[0][11] = balance[0][4] = -0.2
    weather[1][11] = balance[1][4] = '#N/A'
    weather[2][11] = balance[2][4] = None
    weather[3][11], balance[3][4] = None, '#N/A'
    weather[4][11], balance[4][4] = -0.01, -0.010000000001
    balance[-1][1] += 1e-10  # Source floating-point drift is not a clock shift.
    result = compare_intervals(weather, balance)
    assert result['counts']['NE'] == dict(numeric_equal=35132, missing_marker_equal=1,
        blank_equal=1, differences=2)
    assert result['counts']['SE']['numeric_equal'] == 35136
    assert result['differences'][0] == dict(location='NE', weather_cell='L5',
        balance_cell='E5', weather_value=None, balance_value='#N/A', interval_end_decimal_doy=1+4/96)
    assert result['differences'][1]['weather_value'] == -0.01
    assert result['differences'][1]['balance_value'] == -0.010000000001


@pytest.mark.parametrize('side,column,value', [
    (0, 1, 1), (1, 1, 1), (1, 0, 2017), (1, 0, True),
    (0, 1, float('nan')), (1, 1, float('inf')),
])
def test_misaligned_or_invalid_times_fail(intervals, side, column, value):
    intervals[side][0][column] = value
    with pytest.raises(ValueError, match='interval sequence'):
        compare_intervals(*intervals)


@pytest.mark.parametrize('side', [0, 1])
def test_incomplete_sequences_and_wrong_width_fail(intervals, side):
    intervals[side][-1].pop()
    with pytest.raises(ValueError, match='interval sequence'):
        compare_intervals(*intervals)
    intervals[side].pop()
    with pytest.raises(ValueError, match='35136 intervals'):
        compare_intervals(*intervals)


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), 'unknown'])
@pytest.mark.parametrize('side,column', [(0, 11), (1, 4)])
def test_invalid_rain_cells_fail(intervals, side, column, value):
    intervals[side][0][column] = value
    with pytest.raises(ValueError, match='numeric cell'):
        compare_intervals(*intervals)


def _difference(day, interval, published):
    return dict(day=day, interval_sum_mm=interval, published_balance_daily_mm=published,
        published_minus_interval_mm=published-interval)


def test_groups_preserve_gaps_signed_residuals_and_non_cancellation():
    rows = [_difference('2016-01-01', 2, 3), _difference('2016-01-02', 3, 2),
        _difference('2016-01-04', 1, 2), _difference('2016-01-05', 1, 0.25),
        _difference('2016-01-06', 1, 0.75), _difference('2016-01-08', 0, 0.5)]
    groups = consecutive_groups(rows)
    assert [g['day_count'] for g in groups] == [2, 3, 1]
    assert [g['totals_agree_within_tolerance'] for g in groups] == [True, True, False]
    assert groups[0]['days'] == rows[:2]
    assert groups[-1]['published_minus_interval_mm'] == 0.5
    assert consecutive_groups([]) == []


@pytest.mark.parametrize('day', ['2016-01-01', '2015-12-31'])
def test_duplicate_or_reversed_days_fail(day):
    with pytest.raises(ValueError, match='strictly chronological'):
        consecutive_groups([_difference('2016-01-01', 1, 2), _difference(day, 2, 1)])


def test_dew_hypothesis_is_counted_without_changing_rainfall():
    rows = [[0, 0, 0, dew] for dew in (0.1, 0.1, '#N/A', 0)]
    days = [dict(precipitation=dict(NE=dict(published_weather_daily_mm=wx,
        published_balance_daily_mm=wb))) for wx, wb in [(1.1, 1), (1, 1), (2, 1), (None, 1)]]
    before = json.dumps(days)
    assert dew_comparison(rows, days, 'NE', 0) == dict(available_pairs=2, missing_pairs=2,
        differences_without_dew=1, differences_after_adding_dew=1)
    assert json.dumps(days) == before
    with pytest.raises(ValueError, match='equal lengths'):
        dew_comparison(rows[:-1], days, 'NE', 0)


def test_original_sources_reproduce_retained_findings():
    report = reconcile()
    expected = SOURCE.parents[2]/'docs/dpg/field_evidence/bushland_2016_rain_reconciliation.json'
    assert report == json.loads(expected.read_text())
    counts = report['interval_comparison']['counts']
    assert sum(sum(c.values()) for c in counts.values()) == 140544
    assert report['interval_comparison']['differences'] == []
    assert {loc: c['missing_marker_equal'] for loc, c in counts.items()} == dict(NE=987, SE=22, NW=1445, SW=1687)
    assert sum(v['differing_days'] for v in report['locations'].values()) == 29
    groups = [g for v in report['locations'].values() for g in v['consecutive_groups']]
    assert sorted(g['day_count'] for g in groups) == [2]*13+[3]
    assert all(g['totals_agree_within_tolerance'] for g in groups)
    assert report['weather_daily_comparison'] == dict(available_pairs=1775, missing_pairs=55, differences=590)
    dew = [v['daily_dew_hypothesis'] for v in report['locations'].values()]
    assert sum(v['differences_without_dew'] for v in dew) == 442
    assert sum(v['differences_after_adding_dew'] for v in dew) == 1123
    assert report['model_input_ready'] is False
    assert report['model_validation_performed'] is False
    assert report['source_values_modified'] is False

"""Source-preparation checks: interval support, missingness and original bytes."""

import json
from datetime import date, datetime, time, timedelta

import pytest

from scripts.prepare_bushland_weather import SOURCE, WEATHER_FILE, prepare, summarize_day


def _day(day=date(2016, 2, 29)):
    rows = []
    doy = day.timetuple().tm_yday
    for index in range(1, 97):
        end = datetime.combine(day, time.min) + timedelta(minutes=15*index)
        hhmm = 2400 if index == 96 else end.hour*100+end.minute
        rows.append((end, doy+index/96, 2016, doy, hhmm, 100, 50, 20, 2, 90,
                     0, 0, 0, 0, 0))
    return rows


def _replace(rows, row, column, value):
    changed = list(rows[row])
    changed[column] = value
    rows[row] = tuple(changed)


@pytest.mark.parametrize('day', [date(2016, 2, 29), date(2016, 12, 31)])
def test_interval_ends_keep_midnight_in_the_day_just_completed(day):
    result = summarize_day(_day(day), day)
    assert result['day'] == day.isoformat()
    assert result['solar_shortwave_mj_m2'] == pytest.approx(8.64)
    assert result['mean_2m_wind_m_s'] == 2
    assert result['minimum_15min_mean_temperature_c'] == 20
    assert 'net_radiation_mj_m2' not in result


def test_missing_rain_is_unknown_and_signed_measurements_are_preserved():
    rows = _day()
    _replace(rows, 0, 11, '#N/A')
    _replace(rows, 1, 12, -0.1)
    result = summarize_day(rows, date(2016, 2, 29))
    assert result['precipitation']['NE']['total_mm'] is None
    assert result['precipitation']['NE']['missing_intervals'] == 1
    assert result['precipitation']['SE']['total_mm'] == -0.1
    assert result['precipitation']['SE']['negative_intervals'] == 1
    assert result['precipitation']['NW']['total_mm'] == 0


def test_missing_meteorology_withholds_only_that_quantity():
    rows = _day()
    _replace(rows, 50, 7, None)
    result = summarize_day(rows, date(2016, 2, 29))
    assert result['minimum_15min_mean_temperature_c'] is None
    assert result['maximum_15min_mean_temperature_c'] is None
    assert result['mean_2m_wind_m_s'] == 2


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf'), 'unknown'])
def test_invalid_numeric_cells_are_not_silently_coerced(bad):
    rows = _day()
    _replace(rows, 0, 5, bad)
    with pytest.raises(ValueError, match='numeric cell'):
        summarize_day(rows, date(2016, 2, 29))


@pytest.mark.parametrize('column,value', [
    (0, datetime(2016, 2, 29)), (1, 60), (2, 2017), (3, 61), (4, 0), (2, True)])
def test_mismatched_source_time_fields_are_rejected(column, value):
    rows = _day()
    _replace(rows, 0, column, value)
    with pytest.raises(ValueError, match='interval time'):
        summarize_day(rows, date(2016, 2, 29))


def test_incomplete_day_is_rejected():
    with pytest.raises(ValueError, match='96 consecutive'):
        summarize_day(_day()[:-1], date(2016, 2, 29))


def test_changed_source_bytes_fail_before_any_calculation(tmp_path):
    (tmp_path/'weather_manifest.json').write_bytes((SOURCE/'weather_manifest.json').read_bytes())
    (tmp_path/WEATHER_FILE).write_bytes(b'not the pinned workbook')
    with pytest.raises(ValueError, match='checksum mismatch'):
        prepare(tmp_path)


def test_original_workbook_reproduces_retained_report():
    report = prepare()
    expected_path = SOURCE.parents[2]/'docs/dpg/field_evidence/bushland_2016_weather.json'
    assert report == json.loads(expected_path.read_text())
    assert report['dated_intervals'] == 35136
    assert report['calendar_days'] == 366
    assert report['untimed_source_cells']['cells'] == {'L35138': 0, 'M35138': 0}
    assert report['model_input_ready'] is False
    assert report['model_validation_performed'] is False
    assert sum(day['precipitation']['NE']['missing_intervals'] for day in report['days']) == 987
    assert report['comparison_counts'] == {
        'published_weather_daily_mm': dict(available_pairs=1775, missing_pairs=55, differences=590),
        'published_balance_daily_mm': dict(available_pairs=1408, missing_pairs=56, differences=29)}

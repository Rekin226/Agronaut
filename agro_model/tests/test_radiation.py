"""FAO worked examples and explicit boundaries for estimated radiation."""

from dataclasses import asdict
from datetime import date

import pytest

from agro_model.radiation import estimate_net_radiation
from agro_model.water import SolarWeatherDay, WeatherDay, reference_et


def radiation(**updates):
    args = dict(day=date(2001, 5, 15), latitude_deg=-22.9, elevation_m=0,
        t_min_c=19.1, t_max_c=25.1, actual_vapour_pressure_kpa=2.1,
        solar_radiation_mj_m2=14.5)
    args.update(updates)
    return estimate_net_radiation(**args)


def test_fao56_rio_examples_10_to_12_at_published_rounding():
    # FAO-56 Chapter 3, examples 10–12: May represented by day 135.
    # Use the explicitly converted latitude in example 10; example 11 has a typo.
    result = radiation()
    values = result['values']
    assert values['extraterrestrial_mj_m2'] == pytest.approx(25.1, abs=0.05)
    assert values['clear_sky_mj_m2'] == pytest.approx(18.8, abs=0.05)
    assert values['net_shortwave_mj_m2'] == pytest.approx(11.1, abs=0.1)
    assert values['net_outgoing_longwave_mj_m2'] == pytest.approx(3.5, abs=0.05)
    assert values['net_radiation_mj_m2'] == pytest.approx(7.6, abs=0.05)
    assert all(c['source'] == result['source'] for c in result['coefficients'].values())


def test_upper_ratio_cap_changes_only_the_longwave_correction():
    clear = radiation()['values']['clear_sky_mj_m2']
    at_clear = radiation(solar_radiation_mj_m2=clear)
    above = radiation(solar_radiation_mj_m2=2*clear)
    assert above['ratio_capped']
    assert above['inputs']['solar_radiation_mj_m2'] == 2*clear
    assert above['values']['net_outgoing_longwave_mj_m2'] == at_clear['values']['net_outgoing_longwave_mj_m2']
    assert above['values']['net_shortwave_mj_m2'] == 2*at_clear['values']['net_shortwave_mj_m2']


@pytest.mark.parametrize('updates', [
    dict(latitude_deg=91), dict(latitude_deg=True), dict(solar_radiation_mj_m2=-1),
    dict(solar_radiation_mj_m2=float('nan')), dict(t_min_c=26), dict(day='2001-05-15'),
    dict(actual_vapour_pressure_kpa=-1)])
def test_invalid_inputs_are_rejected(updates):
    with pytest.raises(ValueError):
        radiation(**updates)


@pytest.mark.parametrize('updates', [dict(latitude_deg=90, day=date(2001, 12, 21)),
    dict(solar_radiation_mj_m2=0), dict(actual_vapour_pressure_kpa=10)])
def test_unsupported_radiation_regimes_require_supplied_net_radiation(updates):
    with pytest.raises(ValueError, match='supply net radiation'):
        radiation(**updates)


def test_leap_calendar_and_north_south_latitude_are_explicit():
    assert radiation(day=date(2016, 12, 31))['values']['day_of_year'] == 366
    assert radiation(latitude_deg=22.9)['values']['extraterrestrial_mj_m2'] > radiation()['values']['extraterrestrial_mj_m2']


def test_solar_weather_uses_estimated_net_flux_and_retains_original_input():
    weather = SolarWeatherDay(date(2026, 7, 6), 12.3, 21.5, 63, 84, 2.78, 10, 100,
        22, 50.8, None, 'Synthetic scenario', kind='scenario')
    result = reference_et(weather)
    trace = result.inputs['radiation_estimate']
    explicit = asdict(weather)
    for key in ('input_type', 'latitude_deg', 'solar_radiation_mj_m2'):
        explicit.pop(key)
    explicit['net_radiation_mj_m2'] = trace['values']['net_radiation_mj_m2']
    measured = reference_et(WeatherDay(**explicit))
    assert measured.values == result.values
    assert 'radiation_estimate' not in measured.inputs
    assert result.inputs['weather'] == asdict(weather)
    assert 'net_radiation_mj_m2' not in result.inputs['weather']
    assert any('estimated, not a measured' in text for text in result.limitations)


def test_solar_forecast_still_requires_issue_time():
    with pytest.raises(ValueError, match='issued_at'):
        SolarWeatherDay(date(2026, 7, 6), 12, 22, 50, 90, 2, 2, 0, 20, 25,
            0, 'Forecast', kind='forecast')

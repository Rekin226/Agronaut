"""Separate precipitation changes the water balance, never the weather evidence."""

from dataclasses import asdict
from datetime import date

import pytest

from agro_model.tests.test_field_water import soil, weather
from agro_model.water import RainfallInput, SoilState, water_balance_step


def rain(**changes):
    data = dict(day=date(2026, 7, 6), rainfall_mm=10, source="Synthetic gauge total",
                provenance="measured", quality="unverified")
    return RainfallInput(**dict(data, **changes))


@pytest.mark.parametrize("weather_rain", [None, 0, 99])
def test_override_replaces_rain_and_conserves_water(weather_rain):
    w, s = weather(rainfall_mm=weather_rain), soil()
    result = water_balance_step(s, SoilState(date(2026, 7, 5), 20, "Synthetic state"), w,
        potential_et_mm=5, net_irrigation_mm=4, runoff_mm=2, rainfall=rain())
    assert result.values["depletion_mm"] == 13  # 20 - (10 - 2 + 4) + 5
    before = s.total_available_mm - 20
    after = s.total_available_mm - result.values["depletion_mm"]
    assert before + 10 - 2 + 4 - result.values["actual_et_mm"] - result.values["drainage_mm"] == after
    assert result.inputs["weather"] == asdict(w)
    assert result.inputs["rainfall_override"] == asdict(rain())
    assert w.kind == "historical" and w.rainfall_mm == weather_rain
    assert rain().source in result.sources and w.source in result.sources


@pytest.mark.parametrize("change", [dict(rainfall_mm=None), dict(rainfall_mm=-1), dict(rainfall_mm=True),
    dict(quality="rejected"), dict(provenance="forecast"), dict(source=""), dict(water_record_id="")])
def test_rainfall_requires_usable_amount_and_explicit_provenance(change):
    with pytest.raises(ValueError):
        rain(**change)


def test_wrong_day_and_runoff_above_separate_rain_fail():
    for supplied, runoff in ((rain(day=date(2026, 7, 7)), 0), (rain(rainfall_mm=0), 1)):
        with pytest.raises(ValueError):
            water_balance_step(soil(), SoilState(date(2026, 7, 5), 20, "Synthetic"), weather(rainfall_mm=50),
                               potential_et_mm=5, net_irrigation_mm=0, runoff_mm=runoff, rainfall=supplied)

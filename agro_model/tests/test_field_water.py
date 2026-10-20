"""Independent FAO examples plus water conservation and trust-boundary cases."""

from dataclasses import replace
from datetime import date

import pytest

from agro_model.water import (
    CropCoefficient,
    Soil,
    SoilState,
    WeatherDay,
    crop_water_demand,
    irrigation_requirement,
    reference_et,
    water_balance_step,
)


def weather(**changes):
    # FAO-56 (1998), chapter 4, Example 18. Radiation is the published
    # intermediate net radiation, NOT solar radiation. Wind converted from km/h.
    data = dict(day=date(2026, 7, 6), t_min_c=12.3, t_max_c=21.5,
                rh_min_pct=63, rh_max_pct=84, wind_m_s=10 / 3.6,
                wind_height_m=10, elevation_m=100, net_radiation_mj_m2=13.28,
                rainfall_mm=0, source="FAO-56 Example 18", kind="historical")
    data.update(changes)
    return WeatherDay(**data)


def soil(**changes):
    data = dict(field_capacity=0.32, wilting_point=0.15, root_depth_m=0.8,
                depletion_fraction=0.4, source="FAO-56 Example 36 tomato on silt")
    data.update(changes)
    return Soil(**data)


def test_fao_example_18_daily_reference_et():
    result = reference_et(weather())
    # Published daily result 3.88 mm/day; 0.02 allows printed intermediate rounding.
    assert result.values["et0_mm_day"] == pytest.approx(3.88, abs=0.02)
    assert result.values["wind_at_2m_m_s"] == pytest.approx(2.078, abs=0.005)
    assert result.values["actual_vapour_pressure_kpa"] == pytest.approx(1.409, abs=0.003)
    assert result.sources and result.limitations
    assert result.evidence_status == "experimental"


@pytest.mark.parametrize("fc,wp,root,p,taw,raw", [
    (0.15, 0.06, 0.4, 0.30, 36, 10.8),
    (0.32, 0.15, 0.8, 0.40, 136, 54.4),
    (0.35, 0.23, 1.2, 0.55, 144, 79.2),
])
def test_fao_example_36_soil_water(fc, wp, root, p, taw, raw):
    s = Soil(fc, wp, root, p, "FAO-56 Example 36")
    assert s.total_available_mm == pytest.approx(taw)
    assert s.readily_available_mm == pytest.approx(raw)


@pytest.mark.parametrize("field,value", [
    ("t_max_c", float("nan")), ("wind_m_s", float("inf")),
    ("rainfall_mm", -1), ("rh_max_pct", 120), ("rh_min_pct", 90),
    ("wind_height_m", 0), ("wind_m_s", True), ("source", ""),
    ("t_min_c", 30), ("kind", "guessed"),
])
def test_weather_rejects_unusable_inputs(field, value):
    with pytest.raises(ValueError):
        weather(**{field: value})


def test_forecast_requires_issue_time():
    with pytest.raises(ValueError, match="issued_at"):
        weather(kind="forecast")


def test_missing_rain_is_allowed_for_demand_but_not_balance():
    w = weather(rainfall_mm=None)
    assert reference_et(w).values["et0_mm_day"] > 0
    with pytest.raises(ValueError, match="rainfall"):
        water_balance_step(soil(), SoilState(date(2026, 7, 5), 20, "measured"),
                           w, potential_et_mm=4, net_irrigation_mm=0, runoff_mm=0)


def test_crop_demand_is_not_an_irrigation_schedule():
    result = crop_water_demand(weather(), CropCoefficient(1.15, "FAO-56 Table 12", "mid"),
                               area_m2=10000)
    assert result.values["crop_demand_l_day"] == pytest.approx(
        result.values["etc_mm_day"] * 10000)
    assert "gross_irrigation_mm" not in result.values
    assert result.inputs["coefficient"]["source"] == "FAO-56 Table 12"


def test_overflow_never_returns_infinite_physical_outputs():
    for area in (1e308, 10**1000):
        with pytest.raises(ValueError):
            crop_water_demand(weather(), CropCoefficient(1.15, "scenario", "mid"), area)


def test_result_cites_the_supplied_weather_source():
    assert "FAO-56 Example 18" in reference_et(weather()).sources


@pytest.mark.parametrize("rain,irrigation,runoff,depletion", [
    (0, 0, 0, 20), (30, 0, 2, 20), (0, 50, 0, 100),
    (200, 10, 10, 5), (0, 0, 0, 136),
])
def test_water_balance_conserves_mass(rain, irrigation, runoff, depletion):
    s = soil()
    before = SoilState(date(2026, 7, 5), depletion, "field observation")
    result = water_balance_step(s, before, weather(rainfall_mm=rain),
                               potential_et_mm=5, net_irrigation_mm=irrigation,
                               runoff_mm=runoff)
    v = result.values
    assert (s.total_available_mm - depletion + rain + irrigation - runoff
            - v["drainage_mm"] - v["actual_et_mm"]) == pytest.approx(
                s.total_available_mm - v["depletion_mm"])
    assert 0 <= v["depletion_mm"] <= s.total_available_mm
    assert 0 <= v["actual_et_mm"] <= 5
    assert v["drainage_mm"] >= 0


def test_dates_and_invalid_state_cannot_corrupt_balance():
    for state in (SoilState(date(2026, 7, 6), 20, "measured"),
                  SoilState(date(2026, 7, 4), 20, "measured"),
                  SoilState(date(2026, 7, 5), 200, "measured")):
        with pytest.raises(ValueError):
            water_balance_step(soil(), state, weather(), potential_et_mm=5,
                               net_irrigation_mm=0, runoff_mm=0)


def test_requirement_needs_soil_initialization_and_an_efficiency_source():
    result = irrigation_requirement(soil(), None, area_m2=10000,
                                    efficiency=0.8, efficiency_source="field assessment")
    assert result.values["gross_irrigation_mm"] is None
    assert result.missing_inputs == ("soil_state",)
    with pytest.raises(ValueError):
        irrigation_requirement(soil(), None, area_m2=10000,
                               efficiency=0.8, efficiency_source="")


def test_requirement_refills_only_when_depletion_reaches_threshold():
    for depletion, expected_net in ((20, 0), (60, 60)):
        result = irrigation_requirement(
            soil(), SoilState(date(2026, 7, 6), depletion, "measured"), area_m2=10000,
            efficiency=0.8, efficiency_source="measured application efficiency")
        assert result.values["net_irrigation_mm"] == expected_net
        assert result.values["gross_irrigation_l"] == expected_net / 0.8 * 10000


@pytest.mark.parametrize("updates", [
    {"field_capacity": 0.1}, {"depletion_fraction": 1},
    {"root_depth_m": 0}, {"wilting_point": float("nan")},
])
def test_soil_rejects_invalid_parameters(updates):
    with pytest.raises(ValueError):
        replace(soil(), **updates)

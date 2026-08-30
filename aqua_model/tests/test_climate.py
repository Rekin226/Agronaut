"""Climate forcing: parsing rejects garbage, and the physics moves the right direction."""

import pytest

from aqua_model.climate import (
    NOT_MODELLED, DailyClimate, GreenhouseParams, from_records, inside_air_mean_c,
    par_inside_mol_m2, water_temp_next_c,
)

_DAY = DailyClimate(t_mean_c=25.0, t_min_c=18.0, t_max_c=32.0, solar_mj_m2=20.0)


def test_records_parse_roundtrip():
    days = from_records([
        {"t_mean_c": 25, "t_min_c": 18, "t_max_c": 32, "solar_mj_m2": 20},
        {"t_mean_c": 26, "t_min_c": 19, "t_max_c": 33, "solar_mj_m2": 21},
    ])
    assert len(days) == 2 and days[0].t_mean_c == 25.0


def test_missing_fields_are_refused_not_defaulted():
    with pytest.raises(ValueError, match="record 0"):
        from_records([{"t_mean_c": 25}])


def test_wrong_units_are_refused():
    # Solar in W/m2 instead of MJ/m2/day would be ~200-1000: outside physical bounds.
    with pytest.raises(ValueError, match="wrong units"):
        from_records([{"t_mean_c": 25, "t_min_c": 18, "t_max_c": 32, "solar_mj_m2": 500}])


def test_an_empty_series_is_an_error():
    with pytest.raises(ValueError, match="empty"):
        from_records([])


def test_a_greenhouse_is_warmer_and_darker_than_outside():
    gh = GreenhouseParams()
    assert inside_air_mean_c(_DAY, gh) > _DAY.t_mean_c
    assert par_inside_mol_m2(_DAY, gh) < _DAY.solar_mj_m2 * 2.1


def test_shade_net_passes_the_outside_through():
    gh = GreenhouseParams(shade_to_ambient=True)
    assert inside_air_mean_c(_DAY, gh) == _DAY.t_mean_c
    assert par_inside_mol_m2(_DAY, gh) == pytest.approx(_DAY.solar_mj_m2 * 2.1)


def test_water_relaxes_toward_air_without_overshooting():
    gh = GreenhouseParams(water_tau_days=2.0)
    w, deficit = water_temp_next_c(20.0, 28.0, gh)
    assert 20.0 < w < 28.0
    assert deficit == 0.0
    # and from above:
    w2, _ = water_temp_next_c(30.0, 28.0, gh)
    assert 28.0 < w2 < 30.0


def test_a_long_step_converges_instead_of_exploding():
    gh = GreenhouseParams(water_tau_days=2.0)
    w, _ = water_temp_next_c(10.0, 28.0, gh, dt_days=1000.0)
    assert w == pytest.approx(28.0, abs=1e-6)


def test_a_heater_holds_the_setpoint_and_bills_for_it():
    gh = GreenhouseParams(heat_setpoint_c=24.0, water_tau_days=2.0)
    w, deficit = water_temp_next_c(18.0, 12.0, gh)
    assert w == 24.0
    assert deficit > 0.0, "holding a setpoint in the cold is not free"


def test_limits_are_declared():
    assert len(NOT_MODELLED) >= 3
    assert any("hourly" in x for x in NOT_MODELLED)

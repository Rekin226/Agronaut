"""Crop productivity: cited yields modulated, never invented."""

import pytest

from aqua_model.cropgrowth import (
    NOT_MODELLED, CropFactors, f_light, f_nitrogen, f_temp, factors,
    harvest_rate_kg_m2_day, n_uptake_g_day,
)
from aqua_model.crops import get_crop

LETTUCE = get_crop("lettuce")
TOMATO = get_crop("tomato")

_GOOD = dict(dli_mol_m2=16.0, temperature_c=20.0, no3_mg_l=30.0)


def test_good_conditions_deliver_the_cited_yield():
    fac = factors(LETTUCE, **_GOOD)
    rate = harvest_rate_kg_m2_day(LETTUCE, fac)
    assert rate == pytest.approx(LETTUCE.yield_kg_per_m2_year / 365.0, rel=0.1)


def test_yield_is_capped_near_the_citation_even_in_perfect_conditions():
    fac = CropFactors(f_light=1.0, f_temp=1.0, f_nitrogen=1.0)
    assert harvest_rate_kg_m2_day(LETTUCE, fac) <= LETTUCE.yield_kg_per_m2_year / 365.0 * 1.2


def test_darkness_stops_growth():
    fac = factors(LETTUCE, dli_mol_m2=0.0, temperature_c=20.0, no3_mg_l=30.0)
    assert harvest_rate_kg_m2_day(LETTUCE, fac) == 0.0


def test_half_light_roughly_halves_leafy_growth():
    assert f_light(7.5, LETTUCE) == pytest.approx(0.5)


def test_fruiting_crops_want_more_light_than_leafy():
    assert f_light(16.0, TOMATO) < f_light(16.0, LETTUCE)


def test_heat_outside_the_cited_band_stops_the_crop():
    assert f_temp(LETTUCE.temp_max_c + 1.0, LETTUCE) == 0.0
    assert f_temp(LETTUCE.temp_min_c - 1.0, LETTUCE) == 0.0


def test_the_middle_of_the_band_runs_at_full_speed():
    mid = (LETTUCE.temp_min_c + LETTUCE.temp_max_c) / 2.0
    assert f_temp(mid, LETTUCE) == 1.0


def test_nitrogen_response_saturates_not_explodes():
    assert f_nitrogen(0.0) == 0.0
    assert f_nitrogen(3.0) == pytest.approx(0.5)
    assert 0.9 < f_nitrogen(40.0) < 1.0


def test_a_stalled_crop_stops_taking_nitrogen():
    """The twin coupling: uptake capacity must fall with growth, or a dark cold bed would
    keep 'cleaning' water it cannot use."""
    good = factors(LETTUCE, **_GOOD)
    dark = factors(LETTUCE, dli_mol_m2=1.0, temperature_c=20.0, no3_mg_l=30.0)
    assert n_uptake_g_day(LETTUCE, dark, 10.0) < n_uptake_g_day(LETTUCE, good, 10.0) * 0.2


def test_factors_travel_separately_so_reports_can_explain():
    fac = factors(LETTUCE, dli_mol_m2=5.0, temperature_c=27.0, no3_mg_l=2.0)
    assert fac.f_light < 1.0 and fac.f_temp < 1.0 and fac.f_nitrogen < 1.0


def test_limits_are_declared():
    assert any("pests" in x for x in NOT_MODELLED)
    assert any("nutrients" in x.lower() for x in NOT_MODELLED)

"""The production twin: couplings hold, seasons account, and the parts stay one system."""

import dataclasses

import pytest

from aqua_model.climate import DailyClimate, GreenhouseParams
from aqua_model.cropgrowth import NOT_MODELLED as CROP_NM
from aqua_model.crops import get_crop
from aqua_model.production import (
    NOT_MODELLED,
    ProductionParams,
    format_summary,
    simulate_production,
    start_state,
    step_production,
)
from aqua_model.species import get_species
from aqua_model.twin import NOT_MODELLED as TWIN_NM
from aqua_model.twin import excreted_n_g

TILAPIA = get_species("tilapia")
BASIL = get_crop("basil")   # the classic tilapia pairing (UVI) — bands actually overlap

GOOD_DAY = DailyClimate(t_mean_c=23.0, t_min_c=19.0, t_max_c=27.0, solar_mj_m2=18.0)
COLD_DAY = DailyClimate(t_mean_c=8.0, t_min_c=3.0, t_max_c=13.0, solar_mj_m2=8.0)
DARK_DAY = DailyClimate(t_mean_c=23.0, t_min_c=19.0, t_max_c=27.0, solar_mj_m2=1.0)


def _init(**kw):
    args = dict(volume_l=3000.0, fish_count=80, start_weight_g=50.0,
                water_temp_c=26.0, species=TILAPIA)
    args.update(kw)
    return start_state(**args)


def _run(weather, *, params=None, **kw):
    return simulate_production(_init(), tuple(weather), TILAPIA, "tilapia", BASIL,
                               grow_area_m2=24.0, params=params, **kw)


def test_a_good_season_produces_fish_and_crops():
    run = _run([GOOD_DAY] * 120)
    s = run.summary
    assert s.fish_standing_kg + s.fish_harvested_kg > 4.0  # started at 4 kg
    assert s.crop_harvested_kg > 0.0
    assert s.feed_used_kg > 0.0


def test_the_fish_and_nitrogen_models_eat_the_same_feed():
    """The one wire that must not fray: what the fish eat is what the nitrogen model
    dissolves. If these diverge the twin becomes two competing systems."""
    state = _init()
    day = step_production(state, GOOD_DAY, TILAPIA, "tilapia", BASIL, 24.0)
    eaten_g = (day.state.feed_used_kg - state.feed_used_kg) * 1000.0
    assert day.nitrogen.n_excreted_g == pytest.approx(excreted_n_g(eaten_g, TILAPIA), rel=1e-9)


def test_dark_days_stop_crops_but_not_fish():
    bright = _run([GOOD_DAY] * 60).summary
    dark = _run([DARK_DAY] * 60).summary
    assert dark.crop_harvested_kg < bright.crop_harvested_kg * 0.2
    fish_gain_dark = dark.fish_standing_kg + dark.fish_harvested_kg
    fish_gain_bright = bright.fish_standing_kg + bright.fish_harvested_kg
    assert fish_gain_dark == pytest.approx(fish_gain_bright, rel=0.05)


def test_cold_stalls_fish_and_the_summary_says_so():
    run = _run([COLD_DAY] * 60)
    assert run.summary.fish_standing_kg < 5.0
    assert any("temperature-suppressed" in w for w in run.summary.warnings)


def test_lethal_water_is_named_not_hidden():
    hot = DailyClimate(t_mean_c=38.0, t_min_c=33.0, t_max_c=43.0, solar_mj_m2=22.0)
    run = _run([hot] * 30)
    assert any("survivable range" in w for w in run.summary.warnings)


def test_a_heater_keeps_fish_growing_and_bills_for_it():
    unheated = _run([COLD_DAY] * 90).summary
    heated = _run([COLD_DAY] * 90,
                  params=ProductionParams(
                      greenhouse=GreenhouseParams(heat_setpoint_c=27.0))).summary
    assert heated.fish_standing_kg > unheated.fish_standing_kg * 1.5
    assert heated.heat_deficit_c_days > 0
    assert unheated.heat_deficit_c_days == 0.0


def test_crops_draw_down_the_nitrate_they_are_credited_with():
    with_crop = _run([GOOD_DAY] * 120).summary
    no_crop = simulate_production(
        _init(), tuple([GOOD_DAY] * 120), TILAPIA, "tilapia", BASIL,
        grow_area_m2=0.0).summary
    assert with_crop.peak_no3_mg_l < no_crop.peak_no3_mg_l


def test_harvest_and_restock_fires_at_target_weight():
    run = _run([GOOD_DAY] * 365, harvest_at_g=300.0, restock_weight_g=20.0)
    assert run.summary.fish_harvested_kg > 0.0
    assert any("restocked" in w for w in run.summary.warnings)
    assert run.trajectory[-1].state.fish.mean_weight_g < 500.0


def test_the_limiting_factor_is_the_right_one():
    assert _run([DARK_DAY] * 30).summary.limiting_factor == "light"
    # Sahel-hot inside air is beyond basil's cited band:
    hot = DailyClimate(t_mean_c=34.0, t_min_c=28.0, t_max_c=41.0, solar_mj_m2=22.0)
    assert _run([hot] * 30).summary.limiting_factor == "temperature"


def test_is_deterministic():
    a = _run([GOOD_DAY, COLD_DAY, DARK_DAY] * 40)
    b = _run([GOOD_DAY, COLD_DAY, DARK_DAY] * 40)
    assert a.summary == b.summary
    assert a.trajectory[-1].state == b.trajectory[-1].state


def test_state_is_immutable():
    state = _init()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.water_temp_c = 30.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.fish.count = 0


def test_an_empty_season_is_an_error():
    with pytest.raises(ValueError, match="empty"):
        _run([])


def test_limits_are_the_union_of_every_coupled_model():
    for item in TWIN_NM:
        assert item in NOT_MODELLED
    for item in CROP_NM:
        assert item in NOT_MODELLED
    assert any("economics" in x for x in NOT_MODELLED)


def test_the_report_states_its_own_limits():
    text = format_summary(_run([GOOD_DAY] * 30), site_label="test site")
    assert "NOT modelled" in text
    assert "projection" in text
    assert "test site" in text


def test_a_designed_system_flows_into_the_twin_unchanged():
    """Path 1 of the intake procedure: the twin starts with the design's own numbers."""
    from aqua_model.production import start_state_from_design
    from aqua_model.sizing import size_system
    from aqua_model.validate import validate_design_input

    out = size_system(validate_design_input("tilapia", "basil", 24.0, 27.0, 500.0))
    state = start_state_from_design(out, TILAPIA, water_temp_c=26.0, start_weight_g=20.0)
    assert state.fish.count == out.fish_count
    assert state.nitrogen.volume_l == out.system_volume_l
    assert state.fish.mean_weight_g == 20.0
    # a new build starts uncycled — the seed capacity, not a mature biofilter
    assert state.nitrogen.aob_capacity_g_day < 0.1


def test_an_infeasible_design_cannot_seed_the_twin():
    from aqua_model.production import start_state_from_design
    from aqua_model.types import DesignOutput

    empty = DesignOutput(feasible=False)
    with pytest.raises(ValueError, match="feasible"):
        start_state_from_design(empty, TILAPIA, water_temp_c=26.0)


def test_restocked_fingerlings_are_not_counted_as_growth():
    """Bought biomass is not feed-driven growth. Counting it flatters the realized FCR,
    which the business case reads to price feed per kilogram of fish."""
    run = _run([GOOD_DAY] * 365, harvest_at_g=200.0, restock_weight_g=20.0)
    s = run.summary
    assert run.trajectory[-1].state.restocked_fish_kg > 0, "this run should restock"
    assert s.realized_fcr >= TILAPIA.fcr * 0.95, (
        "realized FCR cannot beat the species FCR — feed is the only source of growth")

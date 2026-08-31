"""Fish growth: warm water grows fish, starved fish don't, and the numbers stay physical."""

import pytest

from aqua_model.fishgrowth import (
    NOT_MODELLED,
    TGC,
    Cohort,
    days_to_weight,
    grow,
    ration_g_day,
    tgc_for,
)
from aqua_model.species import get_species

TILAPIA = get_species("tilapia")


def test_fish_grow_at_their_optimum():
    c = Cohort(count=100, mean_weight_g=50.0)
    step = grow(c, TILAPIA, "tilapia", temperature_c=28.0)
    assert step.cohort.mean_weight_g > 50.0
    assert step.feed_eaten_g > 0


def test_cold_water_stalls_growth():
    c = Cohort(count=100, mean_weight_g=50.0)
    warm = grow(c, TILAPIA, "tilapia", temperature_c=28.0)
    cold = grow(c, TILAPIA, "tilapia", temperature_c=16.0)
    warm_gain = warm.cohort.mean_weight_g - 50.0
    cold_gain = cold.cohort.mean_weight_g - 50.0
    assert cold_gain < warm_gain * 0.5, "a 16 C tilapia tank should grow far slower than 28 C"


def test_no_feed_means_no_growth():
    c = Cohort(count=100, mean_weight_g=50.0)
    starved = grow(c, TILAPIA, "tilapia", temperature_c=28.0, feed_offered_g=0.0)
    assert starved.cohort.mean_weight_g == 50.0
    assert starved.feed_eaten_g == 0.0


def test_half_ration_means_less_growth_not_none():
    c = Cohort(count=100, mean_weight_g=50.0)
    full = grow(c, TILAPIA, "tilapia", temperature_c=28.0)
    half = grow(c, TILAPIA, "tilapia", temperature_c=28.0,
                feed_offered_g=full.feed_eaten_g / 2.0)
    full_gain = full.cohort.mean_weight_g - 50.0
    half_gain = half.cohort.mean_weight_g - 50.0
    assert 0.0 < half_gain < full_gain


def test_fish_cannot_eat_more_than_appetite():
    c = Cohort(count=100, mean_weight_g=50.0)
    ration = ration_g_day(c, TILAPIA, 28.0)
    stuffed = grow(c, TILAPIA, "tilapia", temperature_c=28.0, feed_offered_g=ration * 10)
    assert stuffed.feed_eaten_g == pytest.approx(ration)


def test_growth_is_consistent_across_step_sizes():
    c = Cohort(count=100, mean_weight_g=50.0)
    daily = c
    for _ in range(10):
        daily = grow(daily, TILAPIA, "tilapia", temperature_c=28.0).cohort
    coarse = grow(c, TILAPIA, "tilapia", temperature_c=28.0, dt_days=10.0).cohort
    assert daily.mean_weight_g == pytest.approx(coarse.mean_weight_g, rel=0.05)


def test_days_to_weight_matches_the_stepped_model():
    days = days_to_weight(50.0, 400.0, "tilapia", 28.0)
    c = Cohort(count=1, mean_weight_g=50.0)
    for _ in range(int(round(days))):
        c = grow(c, TILAPIA, "tilapia", temperature_c=28.0).cohort
    assert c.mean_weight_g == pytest.approx(400.0, rel=0.05)


def test_every_tgc_seed_carries_a_source_and_a_range():
    for key, coeff in TGC.items():
        assert coeff.source, f"{key} TGC has no source"
        assert coeff.low <= coeff.value <= coeff.high


def test_an_unknown_species_gets_the_default_seed_not_a_crash():
    assert tgc_for("no_such_fish").name == "generic.tgc"


def test_limits_are_declared():
    assert any("mortality" in x for x in NOT_MODELLED)
    assert any("spawn" in x.lower() for x in NOT_MODELLED)

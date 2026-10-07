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


def test_pangasius_has_its_own_seed_not_the_generic_default():
    # Issue #106: five species silently fell back to the generic warm-water default;
    # pangasius is the first to get a real seed. This fails while the key is missing.
    assert "pangasius" in TGC
    assert tgc_for("pangasius").name == "pangasius.tgc"


def test_pangasius_seed_comes_from_the_cited_pond_trial():
    # Da, Lundh, Lindberg & Berg (2016) Table 4: 16.1 -> 229.4 g in 112 d at 28.6 C avg.
    # Reference diet computes to TGC 1.12; the seven diets span 0.80-1.20.
    c = TGC["pangasius"]
    assert c.name == "pangasius.tgc"
    assert c.low <= c.value <= c.high
    assert c.value == pytest.approx(1.1)
    assert c.low == pytest.approx(0.8) and c.high == pytest.approx(1.2)
    assert "Da" in c.source and "2016" in c.source
    # sits inside the published juvenile envelope (Marquez et al. 2024)
    assert 0.1 <= c.low and c.high <= 3.2


def test_pangasius_trial_arithmetic_reproduces_the_cited_value():
    # Recompute the reference-diet TGC from the paper's numbers inside the test, so the
    # seed and the citation can not drift apart silently.
    wi, wf, days, temp_c = 16.1, 229.4, 112.0, 28.6
    tgc_ref = 1000.0 * (wf ** (1.0 / 3.0) - wi ** (1.0 / 3.0)) / (temp_c * days)
    assert tgc_ref == pytest.approx(1.12, abs=0.005)
    assert TGC["pangasius"].low <= tgc_ref <= TGC["pangasius"].high


def test_pangasius_days_to_weight_lands_in_a_plausible_farm_range():
    # The issue's sanity anchor: a value implying harvest in weeks is wrong. Mekong pond
    # culture runs a 20 g fingerling to ~1 kg in roughly 8 months at ~28 C; the seed must
    # land in that neighbourhood, not in weeks and not in years.
    days = days_to_weight(20.0, 1000.0, "pangasius", 28.0)
    assert 150.0 <= days <= 330.0, f"20 g -> 1 kg at 28 C took {days:.0f} days"


def test_barramundi_has_its_own_seed_not_the_generic_default():
    # Issue #106: barramundi is the second of the four remaining species to get a real
    # seed. This fails while the key is missing.
    assert "barramundi" in TGC
    assert tgc_for("barramundi").name == "barramundi.tgc"


def test_barramundi_seed_comes_from_the_cited_ras_trial():
    # Le Boucher, Chung, Ng, Tan & Lee (2024), Table 4: 71.1 -> 214.8-236.5 g over 51 d.
    # The paper reports TGC in base-20 form; converted to this file's base-0 convention
    # the four diets span 1.19-1.39 depending on which temperature the TGC arithmetic
    # itself implies. Seed 1.3 sits inside that span.
    c = TGC["barramundi"]
    assert c.name == "barramundi.tgc"
    assert c.low <= c.value <= c.high
    assert c.value == pytest.approx(1.3)
    assert c.low == pytest.approx(1.19) and c.high == pytest.approx(1.4)
    assert "Le Boucher" in c.source and "2024" in c.source
    # sits inside the published juvenile envelope (Marquez et al. 2024)
    assert 0.1 <= c.low and c.high <= 3.2


def test_barramundi_trial_arithmetic_reproduces_the_cited_value():
    # Recompute both readings from the paper's numbers inside the test, so the seed and
    # the citation can not drift apart silently.
    wi, days = 71.1, 51.0
    finals = {"SPC": 214.8, "Control": 217.3, "BSF": 222.2, "Com": 236.5}
    # the paper's own base-20 TGCs imply sum(T-20) = 442 C.d over 51 d (~28.7 C effective)
    for name, fbw in finals.items():
        dw = fbw ** (1.0 / 3.0) - wi ** (1.0 / 3.0)
        tgc_b0 = 1000.0 * dw / (442.0 + 20.0 * days)
        assert 1.19 <= tgc_b0 <= 1.40, f"{name}: base-0 TGC {tgc_b0:.3f} outside the span"
    # at the stated 30.3 C the same weights give the looser 1.19-1.32 reading
    tgc_ctrl = 1000.0 * (217.3 ** (1.0 / 3.0) - wi ** (1.0 / 3.0)) / (30.3 * days)
    assert TGC["barramundi"].low <= tgc_ctrl <= TGC["barramundi"].high


def test_barramundi_days_to_weight_lands_in_a_plausible_farm_range():
    # FAO 589 grow-out figure: 20 g fingerling to plate size (~400-500 g) inside a year;
    # the Lupatsch/Lupatsch energy model reaches 500 g in ~190 d at 27 C. For this species
    # row the feed chart binds below the thermal ceiling, so the planning date rides the
    # ration in the species table, not the TGC seed alone - the seed still sets the
    # thermal headroom the chart is allowed to climb toward.
    days = days_to_weight(20.0, 500.0, "barramundi", 28.0)
    assert 150.0 <= days <= 300.0, f"20 g -> 500 g at 28 C took {days:.0f} days"


def test_limits_are_declared():
    assert any("mortality" in x for x in NOT_MODELLED)
    assert any("spawn" in x.lower() for x in NOT_MODELLED)

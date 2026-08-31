"""The nitrogen twin: the sizing model rolled forward in time.

The governing test is convergence. `massbalance.nitrogen_check` is the trusted steady-state
answer; the twin must reproduce it exactly at equilibrium, or it is not the same model rolled
forward but a second, competing one. Everything else here checks that the TRANSIENT — the part
steady state cannot see — behaves like real nitrification.
"""

import pytest

from aqua_model.crops import CROPS
from aqua_model.massbalance import nitrogen_check
from aqua_model.species import SPECIES
from aqua_model.twin import (
    NOT_MODELLED,
    TwinState,
    excreted_n_g,
    mature_biofilter,
    simulate,
    step,
)

SP = SPECIES["tilapia"]
CROP = CROPS["lettuce"]
FEED = 200.0
TEMP = 27.0          # inside tilapia's optimum, so temperature does not scale feed


def _mature(volume_l=2000.0, feed=FEED):
    aob, nob = mature_biofilter(SP, feed)
    return TwinState(volume_l=volume_l, aob_capacity_g_day=aob, nob_capacity_g_day=nob)


# --- the governing property --------------------------------------------------

def test_converges_to_the_steady_state_model():
    """At equilibrium every flow must match `nitrogen_check`. If these disagree, one of the two
    models is wrong — and this is the test that says so."""
    res = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP)
    last = res[-1]
    mb = nitrogen_check(FEED, SP, CROP, 10.0)
    assert last.n_excreted_g == pytest.approx(mb["n_excreted_g_day"], abs=0.02)
    assert last.n_to_solids_g == pytest.approx(mb["n_solids_g_day"], abs=0.02)
    assert last.n_plant_g == pytest.approx(mb["n_plant_uptake_g_day"], abs=0.02)
    assert last.n_water_exchange_g == pytest.approx(mb["n_water_exchange_g_day"], abs=0.02)
    assert last.n_denitrified_g == pytest.approx(mb["n_denitrification_g_day"], abs=0.02)


def test_excretion_matches_the_steady_state_arithmetic_exactly():
    """Shared arithmetic, not merely similar. Drift here is how two models quietly diverge."""
    mb = nitrogen_check(FEED, SP, CROP, 10.0)
    assert excreted_n_g(FEED, SP) == pytest.approx(mb["n_excreted_g_day"], abs=0.005)


def test_sink_split_matches_the_configured_ratio():
    """Sinks are applied concurrently against one pool. Applying them in sequence biases the split
    toward whichever runs first — about 3% toward plants, which would silently disagree with the
    steady-state model."""
    last = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP)[-1]
    total = last.n_plant_g + last.n_water_exchange_g + last.n_denitrified_g
    assert last.n_plant_g / total == pytest.approx(0.40 / 0.65, abs=0.005)
    assert last.n_water_exchange_g / total == pytest.approx(0.20 / 0.65, abs=0.005)
    assert last.n_denitrified_g / total == pytest.approx(0.05 / 0.65, abs=0.005)


# --- the transient, which is the point ---------------------------------------

def test_nitrite_peaks_after_ammonia_when_cycling_a_new_system():
    """The classic new-system nitrite spike. It is not scripted: nitrite oxidisers double more
    slowly than ammonia oxidisers, so nitrite necessarily lags. If this ever inverts, the
    population dynamics are wrong."""
    res = simulate(TwinState(volume_l=2000), SP, days=45, feed_g_per_day=FEED, temperature_c=TEMP)
    tan_peak_day = max(res, key=lambda r: r.state.tan_mg_l).state.day
    no2_peak_day = max(res, key=lambda r: r.state.no2_mg_l).state.day
    assert no2_peak_day > tan_peak_day


def test_a_new_system_is_dangerous_before_it_is_safe():
    """Cycling transits genuinely lethal concentrations before settling. A steady-state model
    reports the safe destination and says nothing about the journey — which is the entire reason
    this module exists."""
    res = simulate(TwinState(volume_l=2000), SP, days=45, feed_g_per_day=FEED, temperature_c=TEMP)
    assert max(r.state.tan_mg_l for r in res) > 1.0
    assert max(r.state.no2_mg_l for r in res) > 1.0
    assert res[-1].state.tan_mg_l < 0.5           # and it does settle


def test_a_mature_filter_skips_the_spike():
    """Starting cycled is the right entry point for 'what if I change something on a running
    system' — the transient should not reappear."""
    res = simulate(_mature(), SP, days=30, feed_g_per_day=FEED, temperature_c=TEMP)
    assert max(r.state.tan_mg_l for r in res) < 1.0


def test_a_feed_increase_raises_ammonia_before_the_filter_catches_up():
    """The question an operator actually asks. Doubling feed on a filter sized for the old rate
    must show a transient, then recovery as the population grows into it."""
    st = _mature()
    settled = simulate(st, SP, days=60, feed_g_per_day=FEED, temperature_c=TEMP)[-1].state
    after = simulate(settled, SP, days=30, feed_g_per_day=FEED * 3, temperature_c=TEMP)
    peak = max(r.state.tan_mg_l for r in after)
    assert peak > settled.tan_mg_l
    assert after[-1].state.tan_mg_l < peak       # the filter grows into the new load


# --- nitrate standing concentration ------------------------------------------

def test_nitrate_accumulates_rather_than_draining_to_zero():
    """Sinks are first-order in the pool, not fixed shares of each step's arrivals. Draining
    shares removes the whole pool every step, so nitrate reads 0 mg/L forever — the opposite of
    what a real system does."""
    last = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP)[-1]
    assert 5.0 < last.state.no3_mg_l < 200.0


def test_an_undersized_bed_drives_nitrate_up_and_says_so():
    big = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP,
                   plant_uptake_capacity_g_day=CROP.n_uptake_g_per_m2_day * 12)[-1]
    small = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP,
                     plant_uptake_capacity_g_day=CROP.n_uptake_g_per_m2_day * 1)[-1]
    assert small.state.no3_mg_l > big.state.no3_mg_l
    assert any("capacity-limited" in w for w in small.warnings)


# --- conservation and numerical hygiene --------------------------------------

def test_nitrogen_is_conserved_across_a_step():
    """Everything excreted is either still dissolved or accounted for in a named sink. A leak
    here means a flow is being double-counted or dropped."""
    st = _mature()
    before = st.total_n_g()
    r = step(st, SP, feed_g_per_day=FEED, temperature_c=TEMP, dt_days=1.0)
    after = r.state.total_n_g()
    removed = r.n_to_solids_g + r.n_plant_g + r.n_water_exchange_g + r.n_denitrified_g
    assert before + r.n_excreted_g == pytest.approx(after + removed, abs=1e-6)


def test_no_concentration_goes_negative_on_a_long_step():
    """A step long enough that every sink wants more than exists must scale them, not overdraw."""
    r = step(_mature(), SP, feed_g_per_day=FEED, temperature_c=TEMP, dt_days=100.0)
    assert r.state.tan_mg_l >= 0 and r.state.no2_mg_l >= 0 and r.state.no3_mg_l >= 0


def test_equilibrium_is_insensitive_to_step_size():
    """A result that depends on dt is an integration artefact, not a property of the system."""
    coarse = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP,
                      dt_days=1.0)[-1].state.no3_mg_l
    fine = simulate(_mature(), SP, days=400, feed_g_per_day=FEED, temperature_c=TEMP,
                    dt_days=0.25)[-1].state.no3_mg_l
    assert fine == pytest.approx(coarse, rel=0.15)


def test_is_deterministic():
    a = simulate(_mature(), SP, days=50, feed_g_per_day=FEED, temperature_c=TEMP)[-1].state
    b = simulate(_mature(), SP, days=50, feed_g_per_day=FEED, temperature_c=TEMP)[-1].state
    assert a == b


def test_zero_feed_produces_no_nitrogen():
    r = step(_mature(), SP, feed_g_per_day=0.0, temperature_c=TEMP)
    assert r.n_excreted_g == 0.0


def test_state_is_immutable():
    """Frozen state means a scenario fork cannot corrupt the branch it forked from — the property
    the whole scenario engine will rest on."""
    st = _mature()
    step(st, SP, feed_g_per_day=FEED, temperature_c=TEMP)
    assert st.tan_mg_l == 0.0 and st.day == 0.0


@pytest.mark.parametrize("bad", [{"dt_days": 0.0}, {"dt_days": -1.0}])
def test_invalid_step_size_is_rejected(bad):
    with pytest.raises(ValueError):
        step(_mature(), SP, feed_g_per_day=FEED, temperature_c=TEMP, **bad)


def test_zero_volume_is_rejected():
    with pytest.raises(ValueError):
        step(TwinState(volume_l=0.0), SP, feed_g_per_day=FEED, temperature_c=TEMP)


def test_cold_water_suppresses_the_cascade_and_warns():
    """Below optimum the fish eat less, so less nitrogen enters. Silently scaling feed would make
    a trajectory that disagrees with the operator's own feeding record."""
    warm = step(_mature(), SP, feed_g_per_day=FEED, temperature_c=TEMP)
    cold = step(_mature(), SP, feed_g_per_day=FEED, temperature_c=SP.temp_min_c + 0.5)
    assert cold.n_excreted_g < warm.n_excreted_g
    assert any("optimum" in w for w in cold.warnings)


def test_limits_are_declared():
    """A plotted trajectory reads as a promise. The same honesty rule as sizing applies."""
    assert any("dissolved oxygen" in n for n in NOT_MODELLED)
    assert any("pH" in n or "alkalinity" in n for n in NOT_MODELLED)
    assert any("un-ionised" in n for n in NOT_MODELLED)

"""Forking the twin to ask "what if" — and the honesty constraints on answering.

The engine reports COMPARISONS, not predictions. The twin has never been validated against a real
system (#87: no public dataset pairs feed with independently-measured nitrogen), so an absolute
number claims accuracy it has not earned. A relative statement survives being wrong about the
absolute level, because the same unmodelled error sits in both branches.

Several tests here exist because the obvious implementation gives dangerous advice.
"""


from aqua_model.crops import CROPS
from aqua_model.scenario import (
    THRESHOLD_SOURCE,
    THRESHOLDS_MG_L,
    Intervention,
    compare,
    format_comparison,
    run_scenario,
)
from aqua_model.species import SPECIES
from aqua_model.twin import NOT_MODELLED, TwinState, mature_biofilter, simulate

SP = SPECIES["tilapia"]
CROP = CROPS["lettuce"]
FEED = 200.0
TEMP = 27.0


def _settled():
    aob, nob = mature_biofilter(SP, FEED)
    st = TwinState(volume_l=2000, aob_capacity_g_day=aob, nob_capacity_g_day=nob)
    return simulate(st, SP, days=60, feed_g_per_day=FEED, temperature_c=TEMP)[-1].state


def _common():
    return dict(days=45, feed_g_per_day=FEED, temperature_c=TEMP,
                plant_uptake_capacity_g_day=CROP.n_uptake_g_per_m2_day * 10)


def _baseline(state):
    return run_scenario(state, SP, Intervention("leave it alone"), **_common())


# --- fork safety -------------------------------------------------------------

def test_a_fork_cannot_modify_the_system_it_forked_from():
    """The property the whole engine rests on. `TwinState` was made frozen before there was
    anything to fork precisely so this could not go wrong later."""
    state = _settled()
    before = (state.tan_mg_l, state.no2_mg_l, state.no3_mg_l, state.day)
    run_scenario(state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common())
    assert (state.tan_mg_l, state.no2_mg_l, state.no3_mg_l, state.day) == before


def test_two_scenarios_from_one_state_do_not_interfere():
    state = _settled()
    a = run_scenario(state, SP, Intervention("a", feed_g_per_day=FEED * 3), **_common())
    b = run_scenario(state, SP, Intervention("b"), **_common())
    assert a.outcomes["no2_mg_l"].peak > b.outcomes["no2_mg_l"].peak


# --- the answers have to be right --------------------------------------------

def test_tripling_feed_is_reported_as_worse():
    state = _settled()
    cmp = compare(_baseline(state), run_scenario(
        state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common()))
    assert cmp.worsens()
    assert "worse" in cmp.verdict.lower()
    assert any("nitrite" in f for f in cmp.findings)


def test_threshold_crossings_name_their_source():
    """A number that tells an operator to act must say where it came from — the same rule the
    sizing surface already follows."""
    state = _settled()
    cmp = compare(_baseline(state), run_scenario(
        state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common()))
    crossings = [f for f in cmp.findings if "crosses" in f]
    assert crossings
    assert all(THRESHOLD_SOURCE in f for f in crossings)


def test_doing_nothing_reports_no_material_change():
    state = _settled()
    cmp = compare(_baseline(state), _baseline(state))
    assert not cmp.worsens()
    assert "no material change" in cmp.verdict.lower()


# --- where the obvious implementation gives dangerous advice ------------------

def test_a_cold_snap_is_not_reported_as_an_improvement():
    """The important one. Chilling tilapia to 18 C lowers every nitrogen channel, because the fish
    stop eating. A nitrogen-only model reads that as cleaner water and would effectively recommend
    chilling fish out of their thermal range. The verdict must name what it is actually seeing."""
    state = _settled()
    cmp = compare(_baseline(state), run_scenario(
        state, SP, Intervention("cold snap", temperature_c=18.0), **_common()))
    v = cmp.verdict.lower()
    assert "improves water quality" not in v
    assert "eating less" in v or "suppressed feeding" in v
    assert "welfare" in v or "nitrogen only" in v


def test_a_near_zero_baseline_does_not_produce_a_meaningless_ratio():
    """Dividing by a baseline of 0.012 mg/L yields "678x higher" — arithmetically true, useless as
    information, and it buries the real finding. Below the materiality floor, state the level."""
    state = _settled()
    cmp = compare(_baseline(state), run_scenario(
        state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common()))
    ammonia = [f for f in cmp.findings if f.startswith("ammonia") and "x higher" in f]
    assert not ammonia, f"reported a ratio against a near-zero baseline: {ammonia}"
    assert any("rises to" in f and "ammonia" in f for f in cmp.findings)


# --- uncertainty is swept, not invented --------------------------------------

def test_the_band_brackets_the_typical_run():
    """The band comes from sweeping TwinParams — the parameters the stepped model adds and nobody
    has fitted. It has to actually contain the central estimate."""
    state = _settled()
    res = run_scenario(state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common())
    for out in res.outcomes.values():
        assert out.peak_low <= out.peak <= out.peak_high


def test_the_band_has_real_width_when_it_matters():
    """A band that collapses to the point estimate is decoration. Under a transient the
    nitrifier doubling times genuinely matter, so it should open up."""
    state = _settled()
    res = run_scenario(state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common())
    no2 = res.outcomes["no2_mg_l"]
    assert no2.peak_high > no2.peak_low * 1.2


# --- honesty surface ---------------------------------------------------------

def test_the_output_states_its_demonstrated_skill():
    """Asserts the PROPERTY, not a particular word. The footer used to say "unvalidated"; it now
    reports what the validation record actually found, which is strictly stronger — and a test
    pinned to the old wording would have blocked the improvement."""
    state = _settled()
    text = format_comparison(compare(_baseline(state), run_scenario(
        state, SP, Intervention("triple feed", feed_g_per_day=FEED * 3), **_common())))
    low = text.lower()
    assert "validation" in low or "unmeasured" in low
    assert "never to predict a level" in low or "illustrative" in low
    assert "not modelled" in low


def test_limits_travel_with_the_result():
    state = _settled()
    res = _baseline(state)
    assert res.not_modelled == NOT_MODELLED
    assert any("un-ionised" in n for n in res.not_modelled)


def test_ammonia_threshold_is_documented_as_a_proxy():
    """TAN is not what harms fish — un-ionised NH3 is, and its fraction is pH and temperature
    dependent and unmodelled here. The constant must not read as a toxicity limit."""
    import pathlib
    # Comment markers stripped, then whitespace normalised: the note wraps across lines, so both
    # the newline AND the leading "#" of the continuation land in the middle of the phrase. An
    # exact-substring match would fail for reasons that have nothing to do with the documentation.
    raw = (pathlib.Path(__file__).resolve().parents[1] / "scenario.py").read_text()
    src = " ".join(line.lstrip().lstrip("#").strip() for line in raw.splitlines())
    src = " ".join(src.split())
    assert "CONSERVATIVE PROXY" in src
    assert "un-ionised NH3 fraction" in src
    assert "never as a safety certificate" in src


# --- interventions -----------------------------------------------------------

def test_stocking_more_fish_carries_into_the_run():
    state = _settled()
    res = run_scenario(state, SP, Intervention("stock 5 kg", add_fish_kg=5.0), **_common())
    assert res.trajectory[0].state.fish_biomass_kg >= 5.0


def test_a_smaller_bed_pushes_nitrate_up():
    state = _settled()
    big = run_scenario(state, SP, Intervention("as-is"), **_common())
    small = run_scenario(state, SP, Intervention(
        "shrink the bed", plant_uptake_capacity_g_day=CROP.n_uptake_g_per_m2_day * 1), **_common())
    assert small.outcomes["no3_mg_l"].peak > big.outcomes["no3_mg_l"].peak


def test_is_deterministic():
    state = _settled()
    a = run_scenario(state, SP, Intervention("x", feed_g_per_day=FEED * 2), **_common())
    b = run_scenario(state, SP, Intervention("x", feed_g_per_day=FEED * 2), **_common())
    assert a.outcomes["no2_mg_l"].peak == b.outcomes["no2_mg_l"].peak


def test_thresholds_cover_every_reported_channel():
    state = _settled()
    res = _baseline(state)
    assert set(res.outcomes) == set(THRESHOLDS_MG_L)

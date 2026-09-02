"""The scene is the picture's only source of truth, so what a twin makes it show is tested
here rather than in JavaScript: the bands are the advisor's, the fish are the cohort's, and
a downsampled season still contains the day the nitrite spiked."""

import json

import pytest

from aqua_model import advisory, scene3d
from aqua_model.climate import DailyClimate
from aqua_model.crops import get_crop
from aqua_model.layout import plan_layout
from aqua_model.production import simulate_production, start_state
from aqua_model.scene3d import build_frames, fish_length_m, to_scene, water_band
from aqua_model.sizing import size_system
from aqua_model.species import get_species
from aqua_model.twin import TwinState
from aqua_model.validate import validate_design_input

TILAPIA = get_species("tilapia")
BASIL = get_crop("basil")
GOOD_DAY = DailyClimate(t_mean_c=25.0, t_min_c=20.0, t_max_c=29.0, solar_mj_m2=18.0)


def _design(system_type: str = "raft", area: float = 24.0):
    return size_system(validate_design_input(
        fish_species="tilapia", crop="basil", grow_area_m2=area,
        temperature_c=28.0, water_budget_lpd=500.0, system_type=system_type))


def _state(**nitrogen):
    """A production state whose water chemistry is exactly what the test is about."""
    st = start_state(volume_l=3000.0, fish_count=80, start_weight_g=50.0,
                     water_temp_c=26.0, species=TILAPIA)
    return type(st)(**{**st.__dict__,
                       "nitrogen": TwinState(**{**st.nitrogen.__dict__, **nitrogen})})


def _run(days: int = 90, *, cycled: bool = False):
    init = start_state(volume_l=3000.0, fish_count=80, start_weight_g=50.0,
                       water_temp_c=26.0, species=TILAPIA, cycled=cycled)
    return simulate_production(init, (GOOD_DAY,) * days, TILAPIA, "tilapia", BASIL, 24.0)


# --- the design view is unchanged when no twin is bound -------------------------------

def test_a_design_with_no_state_renders_exactly_as_before():
    out = _design()
    scene = to_scene(plan_layout(out), out, name="t", subtitle="s")
    assert scene["twin"]["mode"] == "design"
    assert scene["twin"]["frames"] == []
    assert scene["fish"], "a stocked design should still show fish"
    json.dumps(scene)          # must not raise


def test_binding_a_state_does_not_move_a_single_dimension():
    """The twin changes what the water looks like, never where anything stands."""
    out = _design()
    layout = plan_layout(out)
    plain = to_scene(layout, out, name="t")
    bound = to_scene(layout, out, name="t", trajectory=_run(30).trajectory, today_index=0)
    for key in ("greenhouse", "objects", "pipes", "hydraulics"):
        assert plain[key] == bound[key], f"{key} moved when a twin was bound"


# --- the bands are the advisor's ------------------------------------------------------

def test_water_turns_amber_on_the_number_the_advisor_acts_on():
    just_under = water_band(_state(tan_mg_l=advisory.TAN_ACT_MG_L - 1e-6).nitrogen)
    at_band = water_band(_state(tan_mg_l=advisory.TAN_ACT_MG_L).nitrogen)
    assert just_under["band"] == "ok"
    assert at_band["band"] == "act" and at_band["driver"] == "ammonia"


def test_urgent_is_the_advisors_urgent_band_not_a_new_one():
    band = water_band(_state(no2_mg_l=advisory.NO2_URGENT_MG_L).nitrogen)
    assert band["band"] == "urgent" and band["driver"] == "nitrite"
    assert f"{advisory.NO2_URGENT_MG_L:.1f}" in band["why"]


def test_high_nitrate_is_flagged_and_blamed_on_uptake():
    band = water_band(_state(no3_mg_l=advisory.NO3_HIGH_MG_L + 5).nitrogen)
    assert band["band"] == "act" and band["driver"] == "nitrate"
    assert "uptake" in band["why"]


def test_an_uncycled_system_says_so_rather_than_only_showing_a_colour():
    band = water_band(_state(tan_mg_l=0.8, no3_mg_l=1.0).nitrogen)
    assert "cycle is not established" in band["why"]


def test_healthy_water_keeps_the_viewers_own_blue():
    assert water_band(_state().nitrogen)["color"] == scene3d.WATER_COLORS["ok"]


# --- fish are the cohort's, not a decorative constant ---------------------------------

def test_fish_grow_between_frames_and_the_count_is_the_twins():
    frames = build_frames(_run(120).trajectory, today_index=0)
    first, last = frames[0]["fish"], frames[-1]["fish"]
    assert last["mean_weight_g"] > first["mean_weight_g"] * 1.5
    assert last["length_m"] > first["length_m"]
    assert first["count"] == 80, "the drawn count must not stand in for the twin's count"


def test_fish_length_follows_weight_by_the_cube_root():
    assert fish_length_m(500) == pytest.approx(0.297, abs=0.01)     # ~30 cm at harvest
    assert fish_length_m(4000) == pytest.approx(fish_length_m(500) * 2, rel=0.01)


def test_a_die_off_thins_the_drawn_population():
    """A capped roster must still show mortality, or the picture contradicts the count."""
    run = _run(20)
    days = list(run.trajectory)
    half = [days[0]]
    for d in days[1:]:
        cohort = type(d.state.fish)(count=d.state.fish.count // 2,
                                    mean_weight_g=d.state.fish.mean_weight_g)
        half.append(type(d)(**{**d.__dict__,
                               "state": type(d.state)(**{**d.state.__dict__,
                                                         "fish": cohort})}))
    frames = build_frames(half, today_index=0)
    assert frames[-1]["fish"]["drawn"] < frames[0]["fish"]["drawn"]
    assert frames[-1]["fish"]["count"] == 40


def test_the_roster_covers_the_busiest_frame():
    out = _design()
    scene = to_scene(plan_layout(out), out, trajectory=_run(60).trajectory, today_index=0)
    roster = sum(f["count"] for f in scene["fish"])
    assert roster >= max(fr["fish"]["drawn"] for fr in scene["twin"]["frames"])


# --- the crop shows why it is growing badly -------------------------------------------

def test_a_nitrogen_starved_crop_is_drawn_small_and_pale():
    frames = build_frames(_run(20, cycled=False).trajectory, today_index=0)
    starved = frames[0]["crop"]              # day 1 of an uncycled system: no nitrate yet
    assert starved["limiting"] == "nitrogen"
    assert starved["chlorosis"] > 0.5
    assert starved["scale"] < 1.0


def test_a_healthy_crop_keeps_its_own_colour():
    fed = build_frames(_run(200, cycled=True).trajectory, today_index=0)[-1]["crop"]
    assert fed["chlorosis"] == 0.0, "a fed crop must not be tinted toward deficiency"


def test_plants_never_shrink_to_nothing():
    """A continuous-harvest bed is never empty; the model does not claim otherwise."""
    for f in build_frames(_run(60).trajectory, today_index=0):
        assert f["crop"]["scale"] >= scene3d.PLANT_SCALE_FLOOR


# --- the scrubber ---------------------------------------------------------------------

def test_a_long_season_is_downsampled_but_keeps_the_nitrite_spike():
    run = _run(365, cycled=False)
    frames = build_frames(run.trajectory, today_index=0, max_frames=60)
    assert len(frames) <= 75, "downsampling should hold near the budget"
    peak = max(d.state.nitrogen.no2_mg_l for d in run.trajectory)
    assert max(f["water"]["no2_mg_l"] for f in frames) == pytest.approx(peak, rel=1e-3)


def test_every_frame_says_which_of_the_three_things_it_is():
    live = build_frames(_run(30).trajectory, today_index=0)
    assert live[0]["kind"] == "today"
    assert {f["kind"] for f in live[1:]} == {"forecast"}
    projected = build_frames(_run(30).trajectory, today_index=None)
    assert {f["kind"] for f in projected} == {"projected"}, (
        "a season simulated from a design must not borrow the authority of 'today'")


def test_dates_ride_along_when_the_run_has_a_calendar():
    dates = [f"2026-09-{i + 1:02d}" for i in range(10)]
    frames = build_frames(_run(10).trajectory, today_index=0, dates=dates)
    assert frames[0]["date"] == "2026-09-01"
    assert all(f["date"] for f in frames)


def test_the_mode_distinguishes_a_live_twin_from_a_projection():
    out = _design()
    layout = plan_layout(out)
    traj = _run(30).trajectory
    assert to_scene(layout, out, trajectory=traj, today_index=0)["twin"]["mode"] == "live"
    assert to_scene(layout, out, trajectory=traj)["twin"]["mode"] == "projection"
    assert to_scene(layout, out)["twin"]["mode"] == "design"


def test_a_live_scene_says_the_geometry_is_still_only_a_proposal():
    out = _design()
    scene = to_scene(plan_layout(out), out, trajectory=_run(5).trajectory, today_index=0)
    assert "proposed" in scene["twin"]["geometry_note"]


def test_a_bare_mirror_state_renders_one_frame_labelled_today():
    out = _design()
    scene = to_scene(plan_layout(out), out, state=_state(), as_of="2026-09-02")
    frames = scene["twin"]["frames"]
    assert len(frames) == 1 and frames[0]["kind"] == "today"
    assert frames[0]["date"] == "2026-09-02"
    assert "crop" not in frames[0], (
        "a stored state carries no crop factors; inventing them would be a fabricated day")


def test_the_whole_bound_scene_is_json_and_embeddable():
    out = _design()
    scene = to_scene(plan_layout(out), out, trajectory=_run(120).trajectory, today_index=0)
    text = json.dumps(scene)
    assert "</" not in text or "<\\/" not in text
    assert len(text) < 900_000, "an embedded season must not bloat the offline file"


def test_the_chlorosis_onset_is_the_cited_nitrate_floor_not_a_taste_setting():
    """Where the crop starts looking sick has to be a threshold someone can argue with."""
    from aqua_model.cropgrowth import f_nitrogen
    assert scene3d.CHLOROSIS_ONSET == f_nitrogen(advisory.NO3_LOW_MG_L)

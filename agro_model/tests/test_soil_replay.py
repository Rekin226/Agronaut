"""Dated continuity, immutable inputs, grid preparation and exact continuation."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from random import Random

import pytest

from agro_model.soil_drainage import DrainageParameters
from agro_model.soil_evaporation import (
    PatchEvaporationCoefficients,
    SoilEvaporationMemory,
    SoilEvaporationParameters,
)
from agro_model.soil_grid import split_soil_grid
from agro_model.soil_infiltration import SurfaceWaterState, SurfaceWaterSupply
from agro_model.soil_replay import SoilWaterDay, SoilWaterState, replay_soil_water
from agro_model.soil_roots import (
    CropTranspirationCoefficients,
    RootLayerActivity,
    RootUptakeParameters,
)
from agro_model.soil_storage import SoilCompartment, SoilPatch, SoilStorage
from agro_model.water import ReferenceETDay

SOURCE = "Synthetic dated replay case; no real farm data"


def setup(*, as_of=date(2026, 7, 5), zone="Africa/Ouagadougou", theta=.25):
    cell = SoilCompartment("soil", 0, 1, .3, .2, .45, .1, theta, SOURCE)
    soil = SoilStorage(10, (SoilPatch("a", .25, (cell,)), SoilPatch("b", .75, (cell,))), SOURCE)
    grid = split_soil_grid(soil, {"a": (.1, .15, .3, .5), "b": (.1, .15, .3, .5)}, source=SOURCE)
    drainage = grid.drainage_parameters({(p.id, "soil"): DrainageParameters(.4, 20, SOURCE) for p in soil.patches})
    return SoilWaterState(as_of, zone, grid.ending,
        tuple((p.id, SurfaceWaterState(0, 10, SOURCE)) for p in soil.patches),
        tuple((p.id, SoilEvaporationMemory(9, .15, None, SOURCE)) for p in soil.patches),
        drainage, tuple((p.id, SoilEvaporationParameters(.15, .3, 9, 4, SOURCE)) for p in soil.patches), SOURCE)


def supplied(state, day, *, root_depth=1, et0=5, rain=0, irrigation=0, period=24):
    weather = ReferenceETDay(day, et0, 999, SOURCE, "scenario", state.timezone, period, SOURCE)
    return SoilWaterDay(weather,
        tuple((p.id, SurfaceWaterSupply(rain, 0, irrigation, SOURCE)) for p in state.soil.patches),
        tuple((p.id, PatchEvaporationCoefficients(.4, .4, SOURCE)) for p in state.soil.patches),
        tuple((p.id, RootUptakeParameters(root_depth, .15, .5, 0, 1, SOURCE)) for p in state.soil.patches),
        tuple(((p.id, c.id), RootLayerActivity(.03 if c.bottom_m <= root_depth else 0, 1, SOURCE))
              for p in state.soil.patches for c in p.compartments),
        tuple((p.id, CropTranspirationCoefficients(.8, 1, SOURCE)) for p in state.soil.patches), SOURCE)


def test_one_day_starts_at_previous_boundary_and_retains_complete_input_evidence():
    initial = setup()
    day = supplied(initial, date(2026, 7, 6), rain=10, irrigation=5)
    result = replay_soil_water(initial, [day])
    assert result["ending"].as_of == day.weather.day
    assert result["ending"].timezone == initial.timezone
    assert result["inputs"] == (day,)
    assert result["days"][0]["initial"] == initial
    assert result["days"][0]["ending"] == result["ending"]
    assert result["days"][0]["calculation"]["evaporation"]["weather"].rainfall_mm == 999
    assert result["totals"]["rainfall_l"] == 100  # Explicit supply replaces weather rain.
    assert result["totals"]["irrigation_at_surface_l"] == 50
    assert result["totals"]["combined_balance_residual_l"] == 0
    assert initial.as_of == date(2026, 7, 5) and initial.soil.water_l == 2500


def test_split_run_continuation_is_identical_through_wetting_and_changing_root_depth():
    initial = setup()
    rng = Random(7320)
    days = [supplied(initial, initial.as_of+timedelta(days=i), root_depth=.5 if i < 15 else 1,
                     et0=rng.randrange(10), rain=rng.randrange(25), irrigation=rng.randrange(8)) for i in range(1, 41)]
    full = replay_soil_water(initial, days)
    first = replay_soil_water(initial, days[:13])
    continued = replay_soil_water(first["ending"], days[13:])
    assert full["ending"] == continued["ending"]
    assert full["days"] == first["days"]+continued["days"]
    for name in ("rainfall_l", "irrigation_at_surface_l", "total_runoff_l", "total_deep_drainage_l", "actual_evapotranspiration_l"):
        assert full["totals"][name] == first["totals"][name]+continued["totals"][name]
    assert full["totals"]["combined_balance_residual_l"] == 0


def test_root_expansion_and_contraction_do_not_initialize_or_remove_profile_water():
    initial = setup(theta=.2)
    days = [supplied(initial, initial.as_of+timedelta(days=i), root_depth=depth, et0=0)
            for i, depth in enumerate((.3, 1, .5), start=1)]
    result = replay_soil_water(initial, days)
    assert result["ending"].soil.patches == initial.soil.patches
    assert result["ending"].soil.area_m2 == initial.soil.area_m2
    assert result["totals"]["rainfall_l"] == result["totals"]["irrigation_at_surface_l"] == 0
    assert result["totals"]["actual_evapotranspiration_l"] == 0
    assert all(r["initial"].soil.water_l == r["ending"].soil.water_l == 2000 for r in result["days"])


def test_inputs_and_boundary_states_are_immutable_and_caller_list_is_not_retained():
    initial = setup()
    day = supplied(initial, initial.as_of+timedelta(days=1))
    caller = [day]
    result = replay_soil_water(initial, caller)
    caller.clear()
    assert result["inputs"] == (day,)
    with pytest.raises(FrozenInstanceError):
        result["ending"].as_of = date(2020, 1, 1)
    with pytest.raises(FrozenInstanceError):
        result["inputs"][0].weather.et0_mm_day = 100
    with pytest.raises(FrozenInstanceError):
        result["ending"].soil.patches[0].compartments[0].water_content = .45


@pytest.mark.parametrize("zone,as_of,day,period", [
    ("Asia/Taipei", date(2026, 7, 5), date(2026, 7, 6), 24),
    ("America/New_York", date(2026, 3, 7), date(2026, 3, 8), 23),
    ("America/New_York", date(2026, 10, 31), date(2026, 11, 1), 25),
    ("Africa/Ouagadougou", date(2028, 2, 28), date(2028, 2, 29), 24)])
def test_local_calendar_day_lengths_and_leap_day_are_preserved(zone, as_of, day, period):
    initial = setup(as_of=as_of, zone=zone)
    result = replay_soil_water(initial, [supplied(initial, day, period=period)])
    assert result["ending"].as_of == day
    assert result["days"][0]["input"].weather.period_hours == period


def test_forecast_issue_and_snapshot_evidence_remain_distinct_from_state_date():
    initial = setup()
    day = supplied(initial, initial.as_of+timedelta(days=1))
    issued = datetime(2026, 7, 5, 12, tzinfo=timezone.utc)
    day = replace(day, weather=replace(day.weather, kind="forecast", issued_at=issued, source_snapshot_id="synthetic-snapshot-1"))
    result = replay_soil_water(initial, [day])
    assert result["inputs"][0].weather.issued_at == issued
    assert result["inputs"][0].weather.retrieved_at is None
    assert result["inputs"][0].weather.source_snapshot_id == "synthetic-snapshot-1"


@pytest.mark.parametrize("offsets", [(0,), (2,), (1, 1), (1, 3), (2, 1)])
def test_missing_duplicate_stale_and_out_of_order_days_are_rejected_before_model_runs(offsets, monkeypatch):
    initial = setup()
    days = [supplied(initial, initial.as_of+timedelta(days=i)) for i in offsets]
    def forbidden(*args, **kwargs):
        pytest.fail("invalid sequence reached the physical model")
    monkeypatch.setattr("agro_model.soil_replay.advance_soil_water_day", forbidden)
    with pytest.raises(ValueError, match="consecutive"):
        replay_soil_water(initial, days)


@pytest.mark.parametrize("case", ["timezone", "missing_patch", "extra_patch", "missing_cell", "unprepared_depth", "deep_activity"])
def test_daily_geometry_and_timezone_mismatches_are_rejected(case):
    initial = setup()
    day = supplied(initial, initial.as_of+timedelta(days=1))
    if case == "timezone":
        day = replace(day, weather=replace(day.weather, timezone="Asia/Taipei"))
    elif case == "missing_patch":
        day = replace(day, supplies=day.supplies[:1])
    elif case == "extra_patch":
        day = replace(day, supplies=(*day.supplies, ("extra", day.supplies[0][1])))
    elif case == "missing_cell":
        day = replace(day, root_activities=day.root_activities[1:])
    else:
        depth = .8 if case == "unprepared_depth" else .5
        day = replace(day, root_parameters=tuple((k, replace(v, root_depth_m=depth)) for k, v in day.root_parameters))
    with pytest.raises(ValueError):
        replay_soil_water(initial, [day])


def test_mutable_duplicate_and_wrongly_typed_snapshot_members_are_rejected():
    initial = setup()
    for updates in ({"surface": dict(initial.surface)}, {"surface": (*initial.surface, initial.surface[0])},
                    {"evaporation_memories": (("a", None),)}, {"drainage_parameters": initial.drainage_parameters[1:]},
                    {"as_of": datetime(2026, 7, 5)}, {"timezone": "Unknown/Timezone"}):
        with pytest.raises(ValueError):
            replace(initial, **updates)
    with pytest.raises(ValueError):
        replace(initial, evaporation_memories=tuple((k, replace(v, stage_one_remaining_mm=10)) for k, v in initial.evaporation_memories))


def test_empty_excessive_and_calendar_overflow_replays_are_rejected():
    initial = setup()
    day = supplied(initial, initial.as_of+timedelta(days=1))
    for days in ([], [day]*3661, iter([day]), [None]):
        with pytest.raises(ValueError):
            replay_soil_water(initial, days)
    late = replace(initial, as_of=date.max-timedelta(days=1))
    with pytest.raises(ValueError):
        replay_soil_water(late, [day])

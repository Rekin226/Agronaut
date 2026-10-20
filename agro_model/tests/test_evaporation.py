"""Published dry-down/area cases and independent daily water conservation."""

import copy
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from agro_model.evaporation import (
    EvaporationDay,
    EvaporationSoil,
    EvaporationState,
    replay_evaporation,
)
from agro_model.water import RainfallInput, ReferenceETDay


def example():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/dual_crop_water.json").read_text())


def replay(data):
    state = data["initial_state"]
    weather = [ReferenceETDay(**dict(row, day=date.fromisoformat(row["day"]))) for row in data["weather"]]
    days = [EvaporationDay(**dict(row, day=date.fromisoformat(row["day"]))) for row in data["days"]]
    rain = [RainfallInput(**dict(row, day=date.fromisoformat(row["day"]))) for row in data.get("rainfall_overrides", [])]
    return replay_evaporation(EvaporationSoil(**data["surface_soil"]), EvaporationState(date.fromisoformat(state["as_of"]),
        state["depletion_mm"], state["source"]), weather, days, data["area_m2"], rainfall_overrides=rain)


def extend(data, count):
    first = date.fromisoformat(data["weather"][0]["day"])
    data["weather"] = [dict(data["weather"][0], day=(first+timedelta(days=i)).isoformat()) for i in range(count)]
    data["days"] = [dict(data["days"][0], day=(first+timedelta(days=i)).isoformat()) for i in range(count)]


def test_fao56_example31_ten_day_drying_matches_published_rounding():
    data = example()
    extend(data, 10)
    data["initial_state"]["depletion_mm"] = 0
    for record, day in zip(data["weather"], data["days"]):
        record["et0_mm_day"] = 4.5
        day.update(kcb=0.15, kc_max=1.2, cover_fraction=0, wetted_fraction=1,
                   exposed_wetted_fraction=1, net_irrigation_mm=0)
    result = replay(data)
    # FAO Example 31 publishes rounded end depletion; full precision is retained here.
    published = [4.73, 9.45, 13.98, 16.57, 18.04, 18.88, 19.36, 19.64, 19.79, 19.88]
    for row, expected in zip(result["days"], published):
        assert row["values"]["ending_depletion_mm"] == pytest.approx(expected, abs=0.011)
        assert row["values"]["surface_balance_residual_mm"] == pytest.approx(0, abs=1e-12)
    assert result["days"][0]["values"]["potential_crop_et_mm"] == pytest.approx(5.4)
    assert result["days"][2]["values"]["evaporation_reduction"] == pytest.approx((20-9.45)/11)


@pytest.mark.parametrize("wet,exposed,expected_ke", [(1, 0.47, 0.4), (0.3, 0.3, 0.39), (0.3, 0.19, 0.247)])
def test_fao_examples32_to34_sprinkler_furrow_and_shaded_drip_limits(wet, exposed, expected_ke):
    data = example()
    extend(data, 1)
    data["initial_state"]["depletion_mm"] = 0
    data["days"][0].update(wetted_fraction=wet, exposed_wetted_fraction=exposed, net_irrigation_mm=0)
    values = replay(data)["days"][0]["values"]
    assert values["effective_ke"] == pytest.approx(expected_ke)
    assert values["potential_crop_et_mm"] == pytest.approx((0.9+expected_ke)*7)


def test_example35_initial_early_wetting_and_surface_percolation():
    data = example()
    extend(data, 1)
    data["surface_soil"].update(field_capacity=0.23, wilting_point=0.10, readily_evaporable_mm=8)
    data["initial_state"]["depletion_mm"] = 18
    data["weather"][0]["et0_mm_day"] = 4.5
    data["days"][0].update(kcb=0.3, kc_max=1.21, cover_fraction=0.08, wetted_fraction=0.8,
        exposed_wetted_fraction=0.8, net_irrigation_mm=40)
    result = replay(data)
    values = result["days"][0]["values"]
    assert result["total_evaporable_mm"] == pytest.approx(18)
    assert values["surface_wetting_mm"] == 50
    assert values["surface_percolation_mm"] == pytest.approx(32)
    assert values["starting_depletion_mm"] == 0
    assert values["soil_evaporation_mm"] == pytest.approx(4.095)
    assert values["ending_depletion_mm"] == pytest.approx(5.11875)


def test_drip_sequence_conserves_water_when_surface_becomes_fully_depleted():
    data = example()
    before = copy.deepcopy(data)
    result = replay(data)
    final = result["days"][-1]["values"]
    assert final["evaporation_water_limited"]
    assert final["ending_depletion_mm"] == pytest.approx(20)
    assert final["soil_evaporation_mm"] == pytest.approx(0.342)
    assert final["effective_ke"] < final["requested_ke"]
    for row in result["days"]:
        value, supplied = row["values"], row["inputs"]
        expected = value["prior_depletion_mm"]-value["surface_wetting_mm"]+value["surface_percolation_mm"]+value["soil_evaporation_mm"]/supplied["exposed_wetted_fraction"]
        assert value["ending_depletion_mm"] == pytest.approx(expected)
        assert value["potential_crop_demand_l"] == pytest.approx(value["potential_crop_et_mm"]*1000)
    assert data == before


def test_zero_et_rain_and_runoff_do_not_get_ignored_or_create_evaporation():
    data = example()
    extend(data, 1)
    data["weather"][0].update(et0_mm_day=0, rainfall_mm=5)
    data["days"][0].update(runoff_mm=2, net_irrigation_mm=0)
    values = replay(data)["days"][0]["values"]
    assert values["surface_wetting_mm"] == 3 and values["ending_depletion_mm"] == 17
    assert values["potential_crop_et_mm"] == values["soil_evaporation_mm"] == 0
    assert values["surface_balance_residual_mm"] == 0


def test_malformed_weather_keeps_a_validation_error_before_override_matching():
    data = example()
    day = EvaporationDay(**dict(data["days"][0], day=date(2026, 7, 6)))
    with pytest.raises(ValueError, match="supplied grass-reference ET records"):
        replay_evaporation(EvaporationSoil(**data["surface_soil"]),
                           EvaporationState(date(2026, 7, 5), 20, "Synthetic state"), [None], [day], 1000)


@pytest.mark.parametrize("original_rain", [None, 0, 100])
def test_separate_rain_replaces_weather_and_keeps_crop_and_water_sources_distinct(original_rain):
    data = example()
    extend(data, 1)
    data["weather"][0].update(et0_mm_day=0, rainfall_mm=original_rain)
    data["days"][0].update(runoff_mm=1, net_irrigation_mm=3, water_source="Synthetic recorded irrigation")
    rain = dict(day="2026-07-06", rainfall_mm=5, provenance="measured", quality="checked", source="Synthetic separate rain")
    data["rainfall_overrides"] = [rain]
    before = copy.deepcopy(data)
    result = replay(data)
    row = result["days"][0]
    # Field rain is 5 - 1 = 4 mm; local irrigation is 3 / 0.3 = 10 mm.
    assert row["values"]["surface_wetting_mm"] == 14
    assert row["values"]["ending_depletion_mm"] == pytest.approx(6)
    assert row["values"]["surface_balance_residual_mm"] == pytest.approx(0, abs=1e-12)
    assert row["rainfall"]["basis"] == "separate_input"
    assert row["rainfall"]["rainfall_mm"] == 5
    assert row["inputs"]["source"] == data["days"][0]["source"]
    assert {rain["source"], data["days"][0]["source"], data["days"][0]["water_source"]} <= set(result["sources"])
    assert data == before


@pytest.mark.parametrize("case", ["duplicate", "outside", "rejected", "runoff", "overflow", "blank_source"])
def test_separate_rain_requires_consistent_dated_accepted_evidence(case):
    data = example()
    rain = dict(day="2026-07-06", rainfall_mm=0, provenance="reported", quality="unverified", source="Synthetic rain")
    data["rainfall_overrides"] = [rain]
    if case == "duplicate":
        data["rainfall_overrides"] *= 2
    elif case == "outside":
        rain["day"] = "2026-07-09"
    elif case == "rejected":
        rain["quality"] = "rejected"
    elif case == "runoff":
        data["days"][0]["runoff_mm"] = 1
    elif case == "overflow":
        rain["rainfall_mm"] = 2001
    else:
        data["days"][0]["water_source"] = ""
    with pytest.raises(ValueError):
        replay(data)


def test_explicit_surface_state_continuation_matches_uninterrupted_replay():
    data = example()
    full = replay(data)
    first = copy.deepcopy(data)
    first["weather"], first["days"] = first["weather"][:2], first["days"][:2]
    checkpoint = replay(first)["ending_state"]
    tail = copy.deepcopy(data)
    tail["weather"], tail["days"] = tail["weather"][2:], tail["days"][2:]
    tail["initial_state"] = dict(checkpoint, as_of=checkpoint["as_of"].isoformat())
    assert replay(tail)["days"][0] == full["days"][2]


def test_random_sequences_conserve_surface_water_and_do_not_exceed_energy_or_storage():
    rng = random.Random(56)
    data = example()
    extend(data, 80)
    for record, day in zip(data["weather"], data["days"]):
        rain = rng.choice([0, 1, 5, 20])
        record.update(et0_mm_day=rng.uniform(0, 15), rainfall_mm=rain)
        cover = rng.uniform(0, 0.9)
        wet = rng.uniform(0.1, 1)
        day.update(cover_fraction=cover, wetted_fraction=wet, exposed_wetted_fraction=rng.uniform(0.01, min(1-cover, wet)),
                   net_irrigation_mm=rng.choice([0, 2, 15]), runoff_mm=rain*rng.random())
    result = replay(data)
    for row in result["days"]:
        values, supplied = row["values"], row["inputs"]
        assert 0 <= values["ending_depletion_mm"] <= result["total_evaporable_mm"]
        assert 0 <= values["evaporation_reduction"] <= 1
        assert values["surface_balance_residual_mm"] == pytest.approx(0, abs=1e-10)
        assert supplied["kcb"]+values["effective_ke"] <= supplied["kc_max"]+1e-12
        assert values["effective_ke"] <= supplied["exposed_wetted_fraction"]*supplied["kc_max"]+1e-12


@pytest.mark.parametrize("section,key,value", [
    ("surface_soil", "depth_m", 0), ("surface_soil", "field_capacity", 0.1),
    ("surface_soil", "readily_evaporable_mm", 20), ("surface_soil", "source", ""),
    ("initial_state", "depletion_mm", 21), ("initial_state", "depletion_mm", None),
    ("initial_state", "as_of", "2026-07-04"),
    ("day", "kc_max", 0.9), ("day", "kcb", -1), ("day", "wetted_fraction", 0),
    ("day", "cover_fraction", 1), ("day", "exposed_wetted_fraction", 0.31),
    ("day", "runoff_mm", 1), ("day", "net_irrigation_mm", float("nan")),
    ("day", "source", " "), ("day", "day", "2026-07-08"),
    ("weather", "rainfall_mm", None), ("weather", "et0_mm_day", 101),
])
def test_invalid_unknown_or_inconsistent_surface_inputs_rejected(section, key, value):
    data = example()
    target = data["days"][0] if section == "day" else data["weather"][0] if section == "weather" else data[section]
    target[key] = value
    with pytest.raises(ValueError):
        replay(data)

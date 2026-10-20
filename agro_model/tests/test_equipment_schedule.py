"""Equipment search agrees with small exhaustive grids and respects UTC boundaries."""

import copy
import itertools
import random

import pytest

from agro_model.resource_schedule import propose_schedule
from agro_model.resources import allocate_resources
from agro_model.tests.test_resource_schedule import request
from agro_model.tests.test_resource_timing import scenario


def inputs():
    plan, timing = scenario()
    data = request(timing)
    for run in data["runs"]:
        run["windows"][0]["end"] = "2026-10-20T12:00:00Z"
    for pool in data["availability"]:
        pool["windows"][0]["end"] = "2026-10-20T12:00:00Z"
        pool["windows"][0]["units"] *= 2
    data["equipment"] = dict(assignments=dict(field=["pump-a"], greenhouse=["pump-a"]), busy=[], not_before=None, source="Synthetic calendar")
    return plan, data


def busy(start=8, end=9, key="pump-a", identity="maintenance"):
    return dict(id=identity, equipment_id=key, start=f"2026-10-20T{start:02d}:00:00Z", end=f"2026-10-20T{end:02d}:00:00Z", source="Synthetic occupied slot")


def run(plan, data):
    return propose_schedule(data, plan, allocate_resources(plan))


def test_avoids_calendar_and_same_equipment_without_changing_inputs():
    plan, data = inputs()
    data["equipment"]["busy"] = [busy()]
    original = copy.deepcopy((plan, data))
    result = run(plan, data)
    assert result["schedule_result"]["equipment_checks_pass"]
    assert [r["start"] for r in result["timing_inputs"]["work"]] == ["2026-10-20T09:00:00+00:00", "2026-10-20T10:00:00+00:00"]
    assert result["schedule_result"]["equipment_pruned"] > 0
    assert result["timing_result"]["declared_checks_pass"]
    assert (plan, data) == original


def test_merged_busy_intervals_empty_domains_and_bounded_unknown():
    plan, data = inputs()
    data["equipment"]["busy"] = [busy(8, 10), busy(9, 11, identity="overlap")]
    assert run(plan, data)["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    data["search_limit"] = 1
    limited = run(plan, data)
    assert limited["schedule_result"]["status"] == "search_limit_reached" and "timing_inputs" not in limited
    data["equipment"]["not_before"] = "2026-10-21T00:00:00Z"
    empty = run(plan, data)
    assert empty["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    assert empty["schedule_result"]["attempted_placements"] == 0


def test_not_before_rounds_to_grid_and_dst_fold_is_elapsed_time():
    plan, data = inputs()
    data["equipment"]["not_before"] = "2026-10-20T09:00:01Z"
    assert run(plan, data)["timing_inputs"]["work"][0]["start"] == "2026-10-20T10:00:00+00:00"
    plan["period"] = dict(start="2026-11-01", end="2026-11-01", timezone="America/New_York")
    window = dict(start="2026-11-01T01:00:00-04:00", end="2026-11-01T03:00:00-05:00")
    for row in data["runs"]:
        row["windows"] = [window]
    for row in data["availability"]:
        row["windows"][0].update(window)
    data["equipment"]["not_before"] = "2026-11-01T01:00:00-05:00"
    data["equipment"]["busy"] = [dict(id="first-fold", equipment_id="pump-a", start=window["start"], end=data["equipment"]["not_before"], source="First occurrence occupied")]
    result = run(plan, data)
    assert [w["start"] for w in result["timing_inputs"]["work"]] == ["2026-11-01T01:00:00-05:00", "2026-11-01T02:00:00-05:00"]


def test_random_small_grids_match_independent_exhaustive_equipment_oracle():
    rng = random.Random(83)
    for _ in range(45):
        plan, data = inputs()
        same = rng.choice([True, False])
        second = "pump-a" if same else "pump-b"
        data["equipment"]["assignments"]["greenhouse"] = [second]
        occupied = {key: {hour for hour in range(8, 12) if rng.choice([True, False])} for key in {"pump-a", second}}
        data["equipment"]["busy"] = [busy(h, h+1, key, f"{key}-{h}") for key, hours in occupied.items() for h in hours]
        windows = []
        for row in data["runs"]:
            allowed = {hour for hour in range(8, 12) if rng.choice([True, False])}
            windows.append(allowed)
            row["windows"] = [{"start": busy(h)["start"], "end": busy(h, h+1)["end"]} for h in sorted(allowed)]
        passing = [(a, b) for a, b in itertools.product(*windows)
                   if a not in occupied["pump-a"] and b not in occupied[second] and (not same or a != b)]
        result = run(plan, data)
        assert (result["schedule_result"]["status"] == "feasible_schedule_found") == bool(passing)
        if passing:
            selected = tuple(int(w["start"][11:13]) for w in result["timing_inputs"]["work"])
            assert selected in passing


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "bad_offset", "past_period", "extra"])
def test_invalid_calendar_constraints_reject(fault):
    plan, data = inputs()
    if fault == "missing":
        del data["equipment"]["assignments"]["field"]
    elif fault == "duplicate":
        data["equipment"]["assignments"]["field"] *= 2
    elif fault == "unknown":
        data["equipment"]["busy"] = [busy(key="unknown")]
    elif fault == "bad_offset":
        data["equipment"]["not_before"] = "2026-10-20T09:00:00+08:00"
    elif fault == "past_period":
        data["equipment"]["busy"] = [dict(busy(), start="2026-10-19T08:00:00Z")]
    else:
        data["equipment"]["automatic_equipment_selection"] = True
    with pytest.raises(ValueError):
        run(plan, data)

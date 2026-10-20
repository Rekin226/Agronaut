"""Search completeness on small grids, honest limits, conservation and DST."""

import copy
import itertools
import random
from datetime import datetime, timedelta

import pytest

from agro_model.resource_schedule import propose_schedule
from agro_model.resource_timing import check_timing
from agro_model.resources import allocate_resources
from agro_model.tests.test_resource_timing import flow, scenario


def request(timing):
    return dict(source="Synthetic permitted work windows", step_seconds=3600, search_limit=100,
        runs=[dict(id=w["id"], job_id=w["job_id"], blocks=w["blocks"], duration_seconds=3600,
                   concurrent=w["concurrent"], windows=[dict(start="2026-10-20T08:00:00Z", end="2026-10-20T10:00:00Z")],
                   source=w["source"]) for w in timing["work"]],
        storage=copy.deepcopy(timing["storage"]), availability=copy.deepcopy(timing["availability"]))


def run(plan, data):
    return propose_schedule(data, plan, allocate_resources(plan))


def test_proposes_sequential_work_with_exact_checks_without_changing_any_input():
    plan, timing = scenario()
    data = request(timing)
    before = copy.deepcopy((plan, data))
    result = run(plan, data)
    assert result["schedule_result"]["status"] == "feasible_schedule_found"
    assert result["timing_result"] == check_timing(result["timing_inputs"], plan, allocate_resources(plan))
    assert result["timing_result"]["declared_checks_pass"]
    assert [w["start"] for w in result["timing_inputs"]["work"]] == ["2026-10-20T08:00:00+00:00", "2026-10-20T09:00:00+00:00"]
    assert result["timing_result"]["storage"][0]["end_l"]["exact"] == "0/1"
    assert (plan, data) == before


def test_backtracks_higher_priority_work_to_leave_only_window_for_another_job():
    plan, timing = scenario()
    data = request(timing)
    data["runs"][1]["windows"][0]["end"] = "2026-10-20T09:00:00Z"
    result = run(plan, data)
    assert result["schedule_result"]["status"] == "feasible_schedule_found"
    work = result["timing_inputs"]["work"]
    assert "09:00:00" in work[0]["start"] and "08:00:00" in work[1]["start"]
    assert result["schedule_result"]["attempted_placements"] == 4


def test_limit_and_exhaustion_are_distinct_and_neither_returns_partial_work():
    plan, timing = scenario()
    data = request(timing)
    data["search_limit"] = 1
    limited = run(plan, data)
    assert limited["schedule_result"]["status"] == "search_limit_reached"
    assert not limited["schedule_result"]["search_exhausted"]
    assert "timing_inputs" not in limited
    data["search_limit"] = 100
    for item in data["runs"]:
        item["windows"][0]["end"] = "2026-10-20T09:00:00Z"
    impossible = run(plan, data)
    assert impossible["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    assert impossible["schedule_result"]["search_exhausted"]
    assert "timing_inputs" not in impossible
    # Reaching the limit exactly after the final candidate still proves exhaustion.
    data["search_limit"] = 2
    assert run(plan, data) == impossible | {"schedule_result": impossible["schedule_result"] | {"search_limit": 2}}


def test_waits_for_refill_and_does_not_prune_partial_overflow():
    plan, timing = scenario()
    data = request(timing)
    for item in data["runs"]:
        item["windows"][0]["end"] = "2026-10-20T11:00:00Z"
    for pool in data["availability"]:
        pool["windows"][0]["end"] = "2026-10-20T11:00:00Z"
    data["storage"][0].update(initial_l="500", inflows=[flow("2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z", "500")])
    result = run(plan, data)
    assert [w["start"][11:13] for w in result["timing_inputs"]["work"]] == ["08", "10"]
    # Initial + refill exceeds capacity without both jobs. Later-assigned work
    # prevents overflow: unsafe pruning of a partial reservoir trace would lose it.
    data["storage"][0].update(initial_l="1000", inflows=[flow("2026-10-20T08:00:00Z", "2026-10-20T10:00:00Z", "1000")])
    assert run(plan, data)["timing_result"]["declared_checks_pass"]


def test_independent_failure_is_reported_without_searching():
    plan, timing = scenario()
    data = request(timing)
    data["runs"][0]["duration_seconds"] = 1800
    result = run(plan, data)["schedule_result"]
    assert result["status"] == "no_feasible_schedule_on_grid"
    assert result["attempted_placements"] == 0
    assert {i["code"] for i in result["invariant_issues"]} == {"work_interval_too_short"}


def test_windows_merge_without_double_counting_and_off_grid_fit_is_not_inferred():
    plan, timing = scenario()
    data = request(timing)
    data["runs"][0]["windows"] *= 2
    assert run(plan, data)["schedule_result"]["candidates_by_run"][0]["count"] == 2
    data["runs"][0]["windows"] = [dict(start="2026-10-20T08:01:00Z", end="2026-10-20T09:01:00Z")]
    result = run(plan, data)
    assert result["schedule_result"]["candidates_by_run"][0]["count"] == 0
    assert result["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    assert "timing_inputs" not in result


def test_fall_back_grid_uses_elapsed_hours_and_valid_local_offsets():
    plan, timing = scenario()
    plan["period"].update(start="2026-10-25", end="2026-10-25", timezone="Europe/Paris")
    data = request(timing)
    window = dict(start="2026-10-25T02:00:00+02:00", end="2026-10-25T03:00:00+01:00")
    for row in data["runs"]:
        row["windows"] = [window]
    for pool in data["availability"]:
        pool["windows"][0].update(window)
    result = run(plan, data)
    work = result["timing_inputs"]["work"]
    assert work[0]["start"] == "2026-10-25T02:00:00+02:00"
    assert work[0]["end"] == work[1]["start"] == "2026-10-25T02:00:00+01:00"
    assert result["timing_result"]["declared_checks_pass"]


def test_spring_forward_uses_elapsed_duration_without_inventing_missing_hour():
    plan, timing = scenario()
    plan["period"].update(start="2026-03-29", end="2026-03-29", timezone="Europe/Paris")
    data = request(timing)
    window = dict(start="2026-03-29T01:00:00+01:00", end="2026-03-29T04:00:00+02:00")
    for row in data["runs"]:
        row["windows"] = [window]
    for pool in data["availability"]:
        pool["windows"][0].update(window)
    result = run(plan, data)
    first, second = result["timing_inputs"]["work"]
    assert first["end"] == second["start"] == "2026-03-29T03:00:00+02:00"
    assert result["timing_result"]["time_resources"][0]["occupied_hours"]["exact"] == "2/1"


def test_huge_calendar_is_lazy_and_stops_at_explicit_placement_limit():
    plan, timing = scenario()
    plan["period"]["end"] = "9998-12-31"
    data = request(timing)
    data.update(step_seconds=1, search_limit=2)
    for row in data["runs"]:
        row["windows"] = [dict(start="2026-10-20T00:00:00Z", end="9999-01-01T00:00:00Z")]
    result = run(plan, data)["schedule_result"]
    assert result["candidates_by_run"][0]["count"] > 200_000_000_000
    assert result["status"] == "search_limit_reached"
    assert result["attempted_placements"] == 2


def test_allocated_blocks_can_be_split_but_unallocated_requests_never_reappear():
    plan, timing = scenario()
    plan["jobs"][0]["requested_blocks"] = 3
    data = request(timing)
    # First job receives two blocks; second gets none. Two explicit runs keep them.
    for row in data["runs"]:
        row["job_id"] = "field"
    result = run(plan, data)
    assert not result["schedule_result"]["all_requested_blocks_allocated"]
    assert result["timing_result"]["declared_checks_pass"]
    assert sum(w["blocks"] for w in result["timing_inputs"]["work"]) == 2


def test_empty_allocation_and_explicitly_no_allowed_windows():
    plan, timing = scenario()
    data = request(timing)
    data["runs"][0]["windows"] = []
    assert run(plan, data)["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    plan["resources"][0]["available"] = "0"
    data["runs"] = []
    result = run(plan, data)
    assert result["timing_inputs"]["work"] == []
    assert result["timing_result"]["declared_checks_pass"]
    assert not result["schedule_result"]["all_requested_blocks_allocated"]


@pytest.mark.parametrize("path,value", [
    (("step_seconds",), True), (("step_seconds",), 0), (("step_seconds",), 1.5),
    (("search_limit",), 10001), (("search_limit",), 0), (("source",), ""),
    (("runs", 0, "duration_seconds"), 0), (("runs", 0, "duration_seconds"), 86401),
    (("runs", 0, "job_id"), "missing"), (("runs", 0, "blocks"), 2),
    (("runs", 1, "id"), "field"), (("runs", 0, "concurrent"), {"pump": 1}),
    (("runs", 0, "source"), ""), (("runs", 0, "windows"), None),
    (("runs", 0, "windows", 0, "start"), "2026-10-20T08:00:00"),
    (("runs", 0, "windows", 0, "start"), "2026-10-20T08:00:00+01:00"),
    (("runs", 0, "windows", 0, "end"), "2026-10-21T01:00:00Z"),
    (("storage",), []), (("availability",), []),
])
def test_invalid_input_cannot_produce_a_proposal(path, value):
    plan, timing = scenario()
    data = request(timing)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        run(plan, data)


def test_small_search_matches_independent_exhaustive_checker_oracle():
    rng = random.Random(17)
    for _ in range(35):
        plan, timing = scenario()
        data = request(timing)
        for pool in data["availability"]:
            pool["windows"][0]["end"] = "2026-10-20T11:00:00Z"
            pool["windows"][0]["units"] *= rng.randint(1, 2)
        permitted = []
        for row in data["runs"]:
            hours = sorted(rng.sample([8, 9, 10], rng.randint(1, 3)))
            permitted.append(hours)
            row["windows"] = [dict(start=f"2026-10-20T{h:02d}:00:00Z", end=f"2026-10-20T{h+1:02d}:00:00Z") for h in hours]
        data["storage"][0]["initial_l"] = rng.choice(["0", "500", "1000"])
        data["storage"][0]["inflows"] = [flow("2026-10-20T09:00:00Z", "2026-10-20T10:00:00Z", "1000")]
        passing = []
        for starts in itertools.product(*permitted):
            candidate = dict(source=data["source"], storage=data["storage"], availability=data["availability"], work=[])
            for row, hour in zip(timing["work"], starts):
                start = datetime.fromisoformat(f"2026-10-20T{hour:02d}:00:00+00:00")
                candidate["work"].append(dict(row, start=start.isoformat(), end=(start+timedelta(hours=1)).isoformat()))
            if check_timing(candidate, plan, allocate_resources(plan))["declared_checks_pass"]:
                passing.append(candidate)
        result = run(plan, data)
        assert (result["schedule_result"]["status"] == "feasible_schedule_found") == bool(passing)
        if passing:
            assert result["timing_inputs"] == passing[0]

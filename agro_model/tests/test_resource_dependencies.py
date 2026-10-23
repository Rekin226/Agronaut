"""Explicit work order, elapsed waiting intervals and constrained-search completeness."""

import copy
import itertools
import random
from datetime import datetime, timedelta, timezone

import pytest

from agro_model.resource_schedule import propose_schedule, validate_schedule
from agro_model.resource_timing import check_timing, validate_timing
from agro_model.resources import allocate_resources
from agro_model.tests.test_resource_schedule import request
from agro_model.tests.test_resource_timing import scenario


def dependency(predecessor="greenhouse", successor="field", gap=0, name="prepare-first"):
    return dict(id=name, predecessor=predecessor, successor=successor, min_gap_seconds=gap, source="Synthetic work-order requirement")


def test_manual_intervals_report_exact_dependency_failure_without_changing_work():
    plan, timing = scenario()
    timing["dependencies"] = [dependency()]
    before = copy.deepcopy(timing)
    result = check_timing(timing, plan, allocate_resources(plan))
    assert {i["code"] for i in result["issues"]} == {"dependency_not_satisfied"}
    audit = result["dependencies"][0]
    assert audit["actual_gap_seconds"] == -7200 and audit["min_gap_seconds"] == 0
    assert audit["predecessor_end"] == "2026-10-20T10:00:00+00:00"
    assert audit["successor_start"] == "2026-10-20T08:00:00+00:00"
    assert not audit["satisfied"] and audit["source"] == timing["dependencies"][0]["source"]
    assert timing == before


def test_dependency_can_override_priority_without_reallocating_blocks():
    plan, timing = scenario()
    data = request(timing)
    data["dependencies"] = [dependency()]
    before = copy.deepcopy(data)
    allocation = allocate_resources(plan)
    result = propose_schedule(data, plan, allocation)
    assert result["schedule_result"]["status"] == "feasible_schedule_found"
    assert result["schedule_result"]["dependency_pruned"] == 1
    assert [w["job_id"] for w in result["timing_inputs"]["work"]] == ["greenhouse", "field"]
    assert [w["start"][11:13] for w in result["timing_inputs"]["work"]] == ["08", "09"]
    assert result["timing_result"]["dependencies"][0]["satisfied"]
    assert result["timing_inputs"]["dependencies"] == data["dependencies"]
    assert result["timing_result"] == check_timing(result["timing_inputs"], plan, allocation)
    assert data == before and allocate_resources(plan) == allocation


def test_minimum_wait_does_not_occupy_resources_and_one_second_matters():
    plan, timing = scenario()
    timing["work"][1].update(start="2026-10-20T10:00:00Z", end="2026-10-20T11:00:00Z")
    for pool in timing["availability"]:
        pool["windows"][0]["end"] = "2026-10-20T11:00:00Z"
    timing["dependencies"] = [dependency("field", "greenhouse", 3600)]
    result = check_timing(timing, plan, allocate_resources(plan))
    assert result["declared_checks_pass"]
    assert result["dependencies"][0]["actual_gap_seconds"] == 3600
    assert result["time_resources"][0]["occupied_hours"]["exact"] == "2/1"
    timing["dependencies"][0]["min_gap_seconds"] = 3601
    result = check_timing(timing, plan, allocate_resources(plan))
    assert {i["code"] for i in result["issues"]} == {"dependency_not_satisfied"}


@pytest.mark.parametrize("gap,expected", [(0, "feasible_schedule_found"), (1, "no_feasible_schedule_on_grid")])
def test_zero_gap_allows_adjacent_runs_but_positive_gap_requires_room(gap, expected):
    plan, timing = scenario()
    data = request(timing)
    data["dependencies"] = [dependency(gap=gap)]
    result = propose_schedule(data, plan, allocate_resources(plan))
    assert result["schedule_result"]["status"] == expected
    if gap:
        assert "timing_inputs" not in result
        assert result["schedule_result"]["search_exhausted"]


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("predecessor", "outside"), ("predecessor", None),
    ("successor", "outside"), ("successor", "greenhouse"), ("source", " "),
    ("min_gap_seconds", True), ("min_gap_seconds", -1), ("min_gap_seconds", 0.5),
    ("min_gap_seconds", "0"), ("min_gap_seconds", 1_000_001),
])
def test_invalid_dependency_rejected_by_both_manual_and_proposal_paths(field, value):
    plan, timing = scenario()
    data = request(timing)
    row = dependency()
    row[field] = value
    for inputs, validate in ((timing, validate_timing), (data, validate_schedule)):
        inputs["dependencies"] = [row]
        with pytest.raises(ValueError):
            validate(inputs, plan, allocate_resources(plan))


@pytest.mark.parametrize("rows,match", [
    ([dependency(), dependency("field", "greenhouse", name="reverse")], "cycle"),
    ([dependency(), dependency(name="duplicate-pair")], "pair"),
    ([dependency(), dependency("field", "greenhouse")], "IDs"),
    (None, "list"),
    ([dict(dependency(), maximum_wait=10)], "exactly"),
])
def test_cycles_duplicate_ids_or_pairs_and_unknown_fields_fail(rows, match):
    plan, timing = scenario()
    timing["dependencies"] = rows
    with pytest.raises(ValueError, match=match):
        check_timing(timing, plan, allocate_resources(plan))


def test_omitted_legacy_inputs_remain_unchanged_and_explicit_none_is_retained():
    plan, timing = scenario()
    data = request(timing)
    allocation = allocate_resources(plan)
    assert validate_timing(timing, plan, allocation) == timing
    assert validate_schedule(data, plan, allocation) == data
    assert check_timing(timing, plan, allocation)["dependencies"] == []
    timing["dependencies"] = []
    assert validate_timing(timing, plan, allocation) == timing


def test_gap_uses_elapsed_time_across_repeated_local_hour():
    plan, timing = scenario()
    plan["period"].update(start="2026-10-25", end="2026-10-25", timezone="Europe/Paris")
    timing["work"][0].update(start="2026-10-25T01:00:00+02:00", end="2026-10-25T02:00:00+02:00")
    timing["work"][1].update(start="2026-10-25T02:00:00+01:00", end="2026-10-25T03:00:00+01:00")
    for pool in timing["availability"]:
        pool["windows"][0].update(start="2026-10-25T01:00:00+02:00", end="2026-10-25T03:00:00+01:00")
    timing["dependencies"] = [dependency("field", "greenhouse", 3600)]
    result = check_timing(timing, plan, allocate_resources(plan))
    assert result["declared_checks_pass"]
    assert result["dependencies"][0]["actual_gap_seconds"] == 3600


def test_limited_dependency_search_reports_unknown_not_impossible():
    plan, timing = scenario()
    data = request(timing)
    data.update(dependencies=[dependency()], search_limit=2)
    result = propose_schedule(data, plan, allocate_resources(plan))
    assert result["schedule_result"]["status"] == "search_limit_reached"
    assert not result["schedule_result"]["search_exhausted"]
    assert "timing_inputs" not in result


def test_thousand_interval_chain_does_not_depend_on_python_recursion_limit():
    plan, timing = scenario()
    plan["resources"] = [plan["resources"][0]]
    plan["jobs"] = [dict(plan["jobs"][0], requested_blocks=1000, requirements={"tank": "1"})]
    timing["availability"] = []
    begin = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
    timing["work"] = [dict(id=str(i), job_id="field", blocks=1, concurrent={}, source="Synthetic sequence",
        start=(begin+timedelta(seconds=i)).isoformat(), end=(begin+timedelta(seconds=i+1)).isoformat()) for i in range(1000)]
    timing["dependencies"] = [dependency(str(i), str(i+1), name=str(i)) for i in range(999)]
    result = check_timing(timing, plan, allocate_resources(plan))
    assert result["declared_checks_pass"]
    assert len(result["dependencies"]) == 999
    assert all(row["actual_gap_seconds"] == 0 for row in result["dependencies"])


def test_three_run_chain_and_multiple_prerequisites_match_exhaustive_grid_oracle():
    rng = random.Random(42)
    for _ in range(25):
        plan, timing = scenario()
        plan["resources"][0]["available"] = "1500"
        plan["resources"][1]["available"] = "3"
        plan["resources"][2]["available"] = "6"
        plan["jobs"].append(dict(plan["jobs"][0], id="third", priority=3))
        timing["work"].append(dict(timing["work"][0], id="third", job_id="third"))
        data = request(timing)
        data["storage"][0].update(capacity_l="1500", initial_l="1500")
        for pool in data["availability"]:
            pool["windows"][0].update(end="2026-10-20T12:00:00Z", units=pool["windows"][0]["units"]*2)
        ordered = rng.sample(["field", "greenhouse", "third"], 3)
        edges = [(ordered[0], ordered[1]), (ordered[0], ordered[2])]
        if rng.choice([True, False]):
            edges.append((ordered[1], ordered[2]))
        data["dependencies"] = [dependency(a, b, rng.choice([0, 3600]), name=str(i)) for i, (a, b) in enumerate(edges)]
        permitted = []
        for row in data["runs"]:
            hours = sorted(rng.sample([8, 9, 10, 11], rng.randint(1, 4)))
            permitted.append(hours)
            row["windows"] = [dict(start=f"2026-10-20T{h:02d}:00:00Z", end=f"2026-10-20T{h+1:02d}:00:00Z") for h in hours]
        possible = False
        for starts in itertools.product(*permitted):
            candidate = dict(source=data["source"], storage=data["storage"], availability=data["availability"], dependencies=data["dependencies"],
                work=[dict(row, start=f"2026-10-20T{hour:02d}:00:00Z", end=f"2026-10-20T{hour+1:02d}:00:00Z") for row, hour in zip(timing["work"], starts)])
            # Independently enforce the gap too, so the oracle doesn't merely
            # mirror an omitted dependency check in the production checker.
            by_id = dict(zip([r["id"] for r in timing["work"]], starts))
            gap_ok = all((by_id[d["successor"]]-by_id[d["predecessor"]]-1)*3600 >= d["min_gap_seconds"] for d in data["dependencies"])
            if gap_ok and check_timing(candidate, plan, allocate_resources(plan))["declared_checks_pass"]:
                possible = True
        result = propose_schedule(data, plan, allocate_resources(plan))
        assert (result["schedule_result"]["status"] == "feasible_schedule_found") == possible
        assert result["schedule_result"]["status"] != "search_limit_reached"

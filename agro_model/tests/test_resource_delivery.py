"""Delivery bottlenecks that volume balances and pump counts cannot detect."""

import copy
import itertools
import random
from fractions import Fraction

import pytest

from agro_model.resource_schedule import propose_schedule
from agro_model.resource_timing import check_timing
from agro_model.resources import allocate_resources
from agro_model.tests.test_resource_schedule import request
from agro_model.tests.test_resource_timing import codes, flow, run, scenario


def delivery(rate="500", start="2026-10-20T08:00:00Z", end="2026-10-20T10:00:00Z"):
    return [dict(resource_id="tank", windows=[dict(id="outlet", start=start, end=end, max_flow_lph=rate,
                                                  source="Synthetic declared outlet conditions")])]


def free_counts(timing):
    for pool in timing["availability"]:
        pool["windows"][0]["units"] *= 2


def test_volume_and_pump_capacity_pass_but_simultaneous_zone_delivery_fails():
    plan, timing = scenario()
    free_counts(timing)
    timing["work"][1].update(start=timing["work"][0]["start"], end=timing["work"][0]["end"])
    assert run(plan, timing)["declared_checks_pass"]
    assert not run(plan, timing)["water_delivery_checked"]
    timing["water_delivery"] = delivery()
    before = copy.deepcopy((plan, timing))
    result = run(plan, timing)
    assert codes(result) == {"water_delivery_capacity_exceeded"}
    issue = result["issues"][0]
    assert issue["work_ids"] == ["field", "greenhouse"]
    assert issue["withdrawal_lph"]["exact"] == "1000/1"
    assert issue["max_flow_lph"]["exact"] == "500/1"
    assert (plan, timing) == before


def test_sequential_endpoints_pass_and_zero_or_missing_windows_close_outlet():
    plan, timing = scenario()
    timing["water_delivery"] = delivery()
    assert run(plan, timing)["declared_checks_pass"]
    timing["water_delivery"][0]["windows"][0]["end"] = "2026-10-20T09:00:00Z"
    issue = run(plan, timing)["issues"][0]
    assert issue["work_ids"] == ["greenhouse"] and issue["max_flow_lph"]["exact"] == "0/1"
    timing["water_delivery"] = delivery("0")
    assert codes(run(plan, timing)) == {"water_delivery_capacity_exceeded"}
    timing["water_delivery"][0]["windows"] = []
    assert run(plan, timing)["water_delivery_checked"]
    assert codes(run(plan, timing)) == {"water_delivery_capacity_exceeded"}
    timing["water_delivery"] = []
    assert run(plan, timing)["declared_checks_pass"] and not run(plan, timing)["water_delivery_checked"]


def test_outside_withdrawals_share_outlet_and_refills_do_not_cancel_them():
    plan, timing = scenario()
    timing["storage"][0]["inflows"] = [flow("2026-10-20T08:00:00Z", "2026-10-20T09:00:00Z", "100")]
    timing["storage"][0]["external_withdrawals"] = [flow("2026-10-20T08:00:00Z", "2026-10-20T09:00:00Z", "100", "neighbor")]
    timing["water_delivery"] = delivery()
    result = run(plan, timing)
    assert codes(result) == {"water_delivery_capacity_exceeded"}
    assert result["issues"][0]["external_withdrawal_ids"] == ["neighbor"]
    assert result["issues"][0]["withdrawal_lph"]["exact"] == "600/1"
    assert result["storage"][0]["end_l"]["exact"] == "0/1"


def test_exact_rate_comparison_survives_rounded_display_equality():
    plan, timing = scenario()
    for job in plan["jobs"]:
        job["requirements"]["tank"] = "0"
    timing["storage"][0].update(capacity_l="2000", initial_l="2000",
        external_withdrawals=[flow("2026-10-20T08:00:00Z", "2026-10-20T10:00:01Z", "0.000001", "tiny")])
    timing["water_delivery"] = delivery("0", end="2026-10-20T11:00:00Z")
    result = run(plan, timing)
    issue = result["issues"][0]
    assert issue["withdrawal_lph"]["value"] == issue["max_flow_lph"]["value"] == "0"
    assert Fraction(issue["withdrawal_lph"]["exact"]) > 0
    assert not result["declared_checks_pass"]


@pytest.mark.parametrize("path,value", [
    ((), None), ((0, "resource_id"), "pump"), ((0, "windows", 0, "max_flow_lph"), -1),
    ((0, "windows", 0, "max_flow_lph"), "-1"), ((0, "windows", 0, "max_flow_lph"), 500),
    ((0, "windows", 0, "max_flow_lph"), "NaN"), ((0, "windows", 0, "source"), " "),
    ((0, "windows", 0, "end"), "2026-10-20T08:00:00Z"),
    ((0, "windows", 0, "start"), "2026-10-20T08:00:00+08:00"),
])
def test_invalid_delivery_is_not_silently_ignored(path, value):
    plan, timing = scenario()
    data = delivery()
    if path:
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    else:
        data = value
    timing["water_delivery"] = data
    with pytest.raises(ValueError):
        run(plan, timing)


def test_duplicate_pools_overlaps_and_partial_pool_coverage_rejected():
    plan, timing = scenario()
    timing["water_delivery"] = delivery()*2
    with pytest.raises(ValueError, match="every water pool"):
        run(plan, timing)
    timing["water_delivery"] = delivery()
    timing["water_delivery"][0]["windows"] *= 2
    with pytest.raises(ValueError, match="overlap"):
        run(plan, timing)
    timing["water_delivery"] = delivery()
    plan["resources"].append(dict(id="second", kind="water", unit="L", available="0", source="Synthetic"))
    for job in plan["jobs"]:
        job["requirements"]["second"] = "0"
    timing["storage"].append(dict(timing["storage"][0], resource_id="second"))
    with pytest.raises(ValueError, match="every water pool"):
        run(plan, timing)


def test_dst_repeated_hour_uses_elapsed_flow_and_capacity_changes():
    plan, timing = scenario()
    plan["period"] = dict(start="2026-11-01", end="2026-11-01", timezone="America/New_York")
    a, b, c = "2026-11-01T01:00:00-04:00", "2026-11-01T01:00:00-05:00", "2026-11-01T02:00:00-05:00"
    timing["work"][0].update(start=a, end=b)
    timing["work"][1].update(start=b, end=c)
    for pool in timing["availability"]:
        pool["windows"][0].update(start=a, end=c)
    timing["water_delivery"] = delivery("500", a, b)
    timing["water_delivery"][0]["windows"].append(dict(id="second", start=b, end=c, max_flow_lph="499.999999", source="Synthetic"))
    result = run(plan, timing)
    assert codes(result) == {"water_delivery_capacity_exceeded"}
    assert result["issues"][0]["work_ids"] == ["greenhouse"]
    assert result["issues"][0]["withdrawal_lph"]["exact"] == "500/1"


def test_multi_block_withdrawal_detects_capacity_change_inside_work_interval():
    plan, timing = scenario()
    plan["resources"] = [dict(plan["resources"][0], available="1500")]
    plan["jobs"] = [dict(plan["jobs"][0], requested_blocks=3, minimum_blocks=3, requirements=dict(tank="500"))]
    timing["work"] = [dict(timing["work"][0], blocks=3, end="2026-10-20T10:00:00Z", concurrent={})]
    timing["availability"] = []
    timing["storage"][0].update(capacity_l="1500", initial_l="1500")
    timing["water_delivery"] = delivery("1000", end="2026-10-20T09:00:00Z")
    timing["water_delivery"][0]["windows"].append(dict(id="reduced", start="2026-10-20T09:00:00Z",
        end="2026-10-20T10:00:00Z", max_flow_lph="749.999999", source="Synthetic reduced outlet"))
    result = run(plan, timing)
    assert codes(result) == {"water_delivery_capacity_exceeded"}
    assert result["issues"][0]["start"] == "2026-10-20T09:00:00+00:00"
    assert result["issues"][0]["withdrawal_lph"]["exact"] == "750/1"


def test_distinct_water_pools_do_not_share_spare_delivery_capacity():
    plan, timing = scenario()
    plan["resources"].append(dict(id="second", kind="water", unit="L", available="200", source="Synthetic"))
    for job in plan["jobs"]:
        job["requirements"]["second"] = "100"
    timing["storage"].append(dict(timing["storage"][0], resource_id="second"))
    timing["water_delivery"] = delivery("1000") + [dict(resource_id="second", windows=[
        dict(delivery("99")[0]["windows"][0])])]
    result = run(plan, timing)
    assert codes(result) == {"water_delivery_capacity_exceeded"}
    assert all(issue["resource_id"] == "second" for issue in result["issues"])
    assert all(row["within_capacity"] for row in result["water_delivery"][0]["intervals"])


def test_search_separates_runs_and_preserves_explicit_constraints_for_replay():
    plan, timing = scenario()
    free_counts(timing)
    data = request(timing)
    data["water_delivery"] = delivery()
    allocation = allocate_resources(plan)
    result = propose_schedule(data, plan, allocation)
    assert result["schedule_result"]["status"] == "feasible_schedule_found"
    assert result["schedule_result"]["delivery_pruned"] > 0
    assert result["timing_inputs"]["water_delivery"] == data["water_delivery"]
    assert result["timing_result"] == check_timing(result["timing_inputs"], plan, allocation)
    assert result["timing_inputs"]["work"][0]["end"] == result["timing_inputs"]["work"][1]["start"]
    data["search_limit"] = 1
    assert propose_schedule(data, plan, allocation)["schedule_result"]["status"] == "search_limit_reached"


def test_delivery_search_matches_independent_exhaustive_hourly_grid():
    rng = random.Random(319)
    for _ in range(60):
        plan, timing = scenario()
        free_counts(timing)
        for pool in timing["availability"]:
            pool["windows"][0]["end"] = "2026-10-20T12:00:00Z"
        limits = [rng.choice([0, 499, 500, 600, 1000, 1200]) for _ in range(4)]
        outside = [rng.choice([0, 100, 500]) for _ in range(4)]
        timing["storage"][0].update(capacity_l="10000", initial_l="10000", external_withdrawals=[
            flow(f"2026-10-20T{8+i:02d}:00:00Z", f"2026-10-20T{9+i:02d}:00:00Z", str(n), str(i))
            for i, n in enumerate(outside) if n])
        data = request(timing)
        for run_input in data["runs"]:
            run_input["windows"][0]["end"] = "2026-10-20T12:00:00Z"
        data["water_delivery"] = [dict(resource_id="tank", windows=[dict(id=str(i),
            start=f"2026-10-20T{8+i:02d}:00:00Z", end=f"2026-10-20T{9+i:02d}:00:00Z",
            max_flow_lph=str(limit), source="Synthetic") for i, limit in enumerate(limits)])]
        feasible = any(all(500*((a == i)+(b == i))+outside[i] <= limits[i] for i in range(4))
                       for a, b in itertools.product(range(4), repeat=2))
        result = propose_schedule(data, plan, allocate_resources(plan))
        assert (result["schedule_result"]["status"] == "feasible_schedule_found") == feasible
        assert result["schedule_result"]["status"] != "search_limit_reached"

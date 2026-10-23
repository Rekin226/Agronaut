"""Shared-resource conservation, priority behavior and exact decimal limits."""

import copy
import random
from decimal import Decimal, localcontext

import pytest

from agro_model.resources import allocate_resources, validate_plan


def plan():
    resources = [dict(id=key, kind=kind, unit=unit, available=amount, source="Synthetic net availability")
        for key, kind, unit, amount in (("tank", "water", "L", "1200"), ("pump", "pump_time", "h", "3"),
                                       ("crew", "labor_time", "h", "4"), ("wallet", "cash", "USD", "20"))]
    jobs = [dict(id=key, unit_id=unit, cycle_id=cycle, title=key, block_label="Operator-defined work block",
        priority=rank, requested_blocks=count, minimum_blocks=minimum,
        requirements=dict(zip((r["id"] for r in resources), amounts)), source="Synthetic work requirements")
        for key, unit, cycle, rank, count, minimum, amounts in (
            ("field-work", "unit", "cycle", 1, 10, 2, ("100", "0.2", "0.1", "1")),
            ("hydro-work", "hydro", None, 2, 10, 1, ("50", "0.25", "0.5", "2")),
            ("pond-work", "pond", None, 3, 2, 1, ("0", "0", "1", "2")))]
    return dict(title="Synthetic mixed farm", source="Synthetic planning notebook",
        period=dict(start="2026-10-20", end="2026-10-20", timezone="Asia/Taipei"), resources=resources, jobs=jobs)


def test_shared_water_and_pump_cannot_be_allocated_twice_and_other_work_can_continue():
    inputs = plan()
    original = copy.deepcopy(inputs)
    result = allocate_resources(inputs)
    assert [r["allocated_blocks"] for r in result["allocations"]] == [10, 4, 1]
    assert result["allocations"][1]["limiting_resources"] == ["tank", "pump"]
    assert result["allocations"][2]["limiting_resources"] == ["crew", "wallet"]
    assert [r["remaining"] for r in result["resources"]] == ["0", "0", "0", "0"]
    assert result["allocation_within_declared_limits"] and not result["all_requested_blocks_allocated"]
    assert inputs == original


def test_priority_order_and_minimum_are_explicit_and_skipped_work_releases_all_pools():
    inputs = plan()
    inputs["jobs"][1]["minimum_blocks"] = 5
    result = allocate_resources(inputs)
    assert [r["allocated_blocks"] for r in result["allocations"]] == [10, 0, 2]
    assert result["allocations"][1]["assigned"] == dict(tank="0", pump="0", crew="0", wallet="0")
    inputs["jobs"][0]["priority"], inputs["jobs"][1]["priority"] = 2, 1
    result = allocate_resources(inputs)
    assert [r["job_id"] for r in result["allocations"]] == ["hydro-work", "field-work", "pond-work"]
    assert result["allocations"][0]["allocated_blocks"] == 8  # Crew, not water, limits the higher-priority work.
    assert result["allocations"][1]["allocated_blocks"] == 0


def test_decimal_precision_and_large_limits_do_not_round_into_extra_blocks():
    inputs = plan()
    inputs["resources"] = [dict(id="wallet", kind="cash", unit="XOF", available="0.299999", source="Synthetic")]
    inputs["jobs"] = [dict(inputs["jobs"][0], requested_blocks=3, minimum_blocks=1, requirements=dict(wallet="0.1"))]
    with localcontext() as ctx:
        ctx.prec = 2
        result = allocate_resources(inputs)
    assert result["allocations"][0]["allocated_blocks"] == 2
    assert result["resources"][0]["remaining"] == "0.099999"
    inputs["resources"][0]["available"] = "999999999999999.999999"
    inputs["jobs"][0].update(requested_blocks=1_000_000, requirements=dict(wallet="999999999999999.999999"))
    result = allocate_resources(inputs)
    assert result["allocations"][0]["allocated_blocks"] == 1
    assert result["resources"][0]["remaining"] == "0"
    assert result["resources"][0]["requested"] == "999999999999999999999"


@pytest.mark.parametrize("path,value", [
    (("title",), ""), (("period", "timezone"), "unknown"), (("period", "end"), "2026-10-19"),
    (("period", "start"), "20261020"), (("resources", 0, "kind"), "storage_trajectory"),
    (("resources", 0, "unit"), "mm"), (("resources", 3, "unit"), "usd"),
    *(( ("resources", 0, "available"), amount) for amount in (None, True, 100, 0.1, "-1", "1e3", "NaN", "01", "0.0000001")),
    (("jobs", 0, "priority"), True), (("jobs", 0, "priority"), 2),
    (("jobs", 0, "requested_blocks"), 1.0), (("jobs", 0, "minimum_blocks"), 11),
    (("jobs", 0, "minimum_blocks"), 0), (("jobs", 0, "source"), " "),
    (("jobs", 0, "requirements"), {"tank": "100"}), (("jobs", 0, "requirements"), dict(tank="0", pump="0", crew="0", wallet="0")),
])
def test_invalid_or_unknown_inputs_are_never_filled_in(path, value):
    inputs = plan()
    target = inputs
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_plan(inputs)


def test_duplicate_ids_unknown_fields_and_empty_lists_fail():
    for collection in ("resources", "jobs"):
        inputs = plan()
        inputs[collection][1]["id"] = inputs[collection][0]["id"]
        with pytest.raises(ValueError, match="unique"):
            validate_plan(inputs)
        inputs[collection] = []
        with pytest.raises(ValueError):
            validate_plan(inputs)
    inputs = plan()
    inputs["approve"] = True
    with pytest.raises(ValueError):
        validate_plan(inputs)


def test_allocations_match_small_block_by_block_reference_and_conserve_each_pool():
    rng = random.Random(314159)
    for _ in range(100):
        inputs = plan()
        for resource in inputs["resources"]:
            resource["available"] = str(rng.randrange(0, 20))
        for job in inputs["jobs"]:
            job["requirements"] = {r["id"]: str(rng.randrange(1, 4)) for r in inputs["resources"]}
            job["requested_blocks"] = rng.randrange(1, 10)
            job["minimum_blocks"] = rng.randrange(1, job["requested_blocks"]+1)
        remaining = {r["id"]: int(r["available"]) for r in inputs["resources"]}
        expected = []
        for job in inputs["jobs"]:
            trial, count = dict(remaining), 0
            while count < job["requested_blocks"] and all(trial[k] >= int(v) for k, v in job["requirements"].items()):
                for key, amount in job["requirements"].items():
                    trial[key] -= int(amount)
                count += 1
            if count < job["minimum_blocks"]:
                count = 0
            else:
                remaining = trial
            expected.append(count)
        result = allocate_resources(inputs)
        assert [r["allocated_blocks"] for r in result["allocations"]] == expected
        for resource in result["resources"]:
            assigned = sum(Decimal(j["assigned"][resource["id"]]) for j in result["allocations"])
            assert assigned + Decimal(resource["remaining"]) == Decimal(resource["available"])
            assert assigned <= Decimal(resource["available"])
            assert int(resource["remaining"]) == remaining[resource["id"]]

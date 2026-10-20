"""Chronological failures hidden by aggregate budgets, with exact balance checks."""

import copy
from decimal import ROUND_UP, localcontext
from fractions import Fraction

import pytest

from agro_model.resource_timing import check_timing
from agro_model.resources import allocate_resources


def scenario():
    plan = dict(title="Synthetic timed work", source="Synthetic", period=dict(start="2026-10-20", end="2026-10-20", timezone="UTC"),
        resources=[dict(id="tank", kind="water", unit="L", available="1000", source="Synthetic"),
                   dict(id="pump", kind="pump_time", unit="h", available="2", source="Synthetic"),
                   dict(id="crew", kind="labor_time", unit="h", available="4", source="Synthetic")],
        jobs=[dict(id=key, unit_id=unit, cycle_id=None, title=key, block_label="One specified event", priority=i,
                   requested_blocks=1, minimum_blocks=1, requirements=dict(tank="500", pump="1", crew="2"), source="Synthetic")
              for i, (key, unit) in enumerate((("field", "unit"), ("greenhouse", "hydro")), 1)])
    work = [dict(id=key, job_id=key, blocks=1, start=f"2026-10-20T0{hour}:00:00Z", end=f"2026-10-20T{hour+1:02d}:00:00Z",
                 concurrent=dict(pump=1, crew=2), source="Synthetic interval") for key, hour in (("field", 8), ("greenhouse", 9))]
    timing = dict(source="Synthetic timing scenario", work=work,
        storage=[dict(resource_id="tank", capacity_l="1000", initial_l="1000", minimum_l="0", inflows=[], external_withdrawals=[], source="Synthetic tank")],
        availability=[dict(resource_id=key, windows=[dict(id="shift", start="2026-10-20T08:00:00Z", end="2026-10-20T10:00:00Z",
                                                           units=count, source="Synthetic available shift")]) for key, count in (("pump", 1), ("crew", 2))])
    return plan, timing


def run(plan, timing):
    return check_timing(timing, plan, allocate_resources(plan))


def codes(result):
    return {row["code"] for row in result["issues"]}


def flow(start, end, volume, name="refill"):
    return dict(id=name, start=start, end=end, volume_l=volume, source="Synthetic external flow")


def test_sequential_work_exactly_fits_pump_crew_and_storage_without_mutating_inputs():
    plan, timing = scenario()
    original = copy.deepcopy((plan, timing))
    result = run(plan, timing)
    assert result["declared_checks_pass"] and result["covers_allocated_blocks"]
    assert result["storage"][0]["end_l"]["exact"] == "0/1"
    assert result["time_resources"][0]["occupied_hours"]["exact"] == "2/1"
    assert result["time_resources"][1]["occupied_hours"]["exact"] == "4/1"
    assert (plan, timing) == original


def test_simultaneous_work_exceeds_pump_and_crew_despite_valid_aggregate_allocation():
    plan, timing = scenario()
    timing["work"][1].update(start=timing["work"][0]["start"], end=timing["work"][0]["end"])
    assert allocate_resources(plan)["all_requested_blocks_allocated"]
    result = run(plan, timing)
    assert not result["declared_checks_pass"]
    assert codes(result) == {"concurrent_capacity_exceeded"}
    conflicts = result["issues"]
    assert {r["resource_id"] for r in conflicts} == {"pump", "crew"}
    assert all(r["work_ids"] == ["field", "greenhouse"] for r in conflicts)


def test_late_refill_does_not_hide_earlier_empty_tank_and_overflow_is_not_spilled():
    plan, timing = scenario()
    tank = timing["storage"][0]
    tank["initial_l"] = "500"
    tank["inflows"] = [flow("2026-10-20T12:00:00Z", "2026-10-20T13:00:00Z", "500")]
    result = run(plan, timing)
    assert codes(result) == {"storage_below_minimum"}
    assert result["storage"][0]["lowest_l"]["exact"] == "-500/1"
    assert result["storage"][0]["end_l"]["exact"] == "0/1"
    tank["initial_l"] = "1000"
    tank["inflows"] = [flow("2026-10-20T06:00:00Z", "2026-10-20T07:00:00Z", "500")]
    result = run(plan, timing)
    assert codes(result) == {"storage_above_capacity"}
    assert result["storage"][0]["highest_l"]["exact"] == "1500/1"
    assert result["storage"][0]["end_l"]["exact"] == "500/1"


def test_outside_withdrawals_reserve_and_exact_conservation_are_explicit():
    plan, timing = scenario()
    tank = timing["storage"][0]
    tank.update(initial_l="1000", capacity_l="2000", minimum_l="100",
        inflows=[flow("2026-10-20T07:00:00Z", "2026-10-20T08:00:00Z", "400")],
        external_withdrawals=[flow("2026-10-20T09:00:00Z", "2026-10-20T12:00:00Z", "300", "outside")])
    result = run(plan, timing)
    assert result["declared_checks_pass"]
    row = result["storage"][0]
    parts = {key: Fraction(row[key]["exact"]) for key in ("initial_l", "inflows_l", "external_withdrawals_l", "job_withdrawals_l", "end_l")}
    assert parts["end_l"] == parts["initial_l"]+parts["inflows_l"]-parts["external_withdrawals_l"]-parts["job_withdrawals_l"] == 100
    tank["external_withdrawals"][0]["volume_l"] = "300.000001"
    result = run(plan, timing)
    assert {"water_budget_exceeds_supplied_balance", "storage_below_minimum"} <= codes(result)


def test_occupied_time_includes_slack_and_work_must_fit_its_interval():
    plan, timing = scenario()
    timing["work"][0]["end"] = "2026-10-20T08:30:00Z"
    assert "work_interval_too_short" in codes(run(plan, timing))
    timing["work"][0]["end"] = "2026-10-20T09:30:00Z"
    result = run(plan, timing)
    assert {"occupied_time_exceeds_budget", "concurrent_capacity_exceeded"} <= codes(result)
    timing["availability"][0]["windows"][0]["end"] = "2026-10-20T09:00:00Z"
    assert "time_budget_exceeds_availability" in codes(run(plan, timing))


def test_repeating_rates_are_exact_at_unrelated_flow_boundaries():
    plan, timing = scenario()
    timing["storage"][0].update(capacity_l="2000", inflows=[flow("2026-10-20T12:00:00Z", "2026-10-20T12:00:03Z", "1")],
        external_withdrawals=[flow("2026-10-20T12:00:01Z", "2026-10-20T12:00:02Z", "0.1", "outside")])
    row = run(plan, timing)["storage"][0]
    at_one = next(p for p in row["trajectory"] if "12:00:01" in p["at"])
    assert at_one["level_l"]["exact"] == "1/3"
    assert row["end_l"]["exact"] == "9/10"
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_UP
        assert run(plan, timing)["storage"][0] == row


@pytest.mark.parametrize("path,value", [
    (("work", 0, "start"), "2026-10-20T08:00:00"),
    (("work", 0, "start"), "2026-10-20T08:00:00+08:00"),
    (("work", 0, "end"), "2026-10-20T08:00:00Z"),
    (("work", 0, "start"), "2026-10-19T08:00:00Z"),
    (("work", 0, "blocks"), True), (("work", 0, "blocks"), 2),
    (("work", 0, "job_id"), "missing"), (("work", 1, "id"), "field"),
    (("work", 0, "concurrent"), {"pump": 1}), (("work", 0, "concurrent", "crew"), 0),
    (("work", 0, "concurrent", "pump"), 1.5), (("work", 0, "source"), ""),
    (("storage",), []), (("storage", 0, "capacity_l"), "500"),
    (("storage", 0, "minimum_l"), "1001"), (("storage", 0, "external_withdrawals"), None),
    (("availability",), []), (("availability", 0, "resource_id"), "tank"),
    (("availability", 0, "windows", 0, "units"), 0),
])
def test_missing_inconsistent_or_ambiguous_inputs_are_rejected(path, value):
    plan, timing = scenario()
    target = timing
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        run(plan, timing)


def test_availability_cannot_be_double_counted_and_unlisted_time_is_unavailable():
    plan, timing = scenario()
    timing["availability"][0]["windows"].append(dict(timing["availability"][0]["windows"][0], id="duplicate"))
    with pytest.raises(ValueError, match="overlap"):
        run(plan, timing)
    timing["availability"][0]["windows"] = []
    result = run(plan, timing)
    assert {"time_budget_exceeds_availability", "concurrent_capacity_exceeded"} <= codes(result)


def test_fall_back_offsets_distinguish_real_duration_and_spring_gap_is_rejected():
    plan, timing = scenario()
    plan["period"] = dict(start="2026-11-01", end="2026-11-01", timezone="America/New_York")
    for row in timing["work"]:
        row.update(start="2026-11-01T01:00:00-04:00", end="2026-11-01T01:00:00-05:00")
    for pool in timing["availability"]:
        pool["windows"][0].update(start="2026-11-01T00:00:00-04:00", end="2026-11-01T03:00:00-05:00")
    result = run(plan, timing)
    assert "work_interval_too_short" not in codes(result)
    assert result["time_resources"][0]["occupied_hours"]["exact"] == "2/1"
    assert "concurrent_capacity_exceeded" in codes(result)
    plan["period"] = dict(start="2026-03-08", end="2026-03-08", timezone="America/New_York")
    timing["work"][0].update(start="2026-03-08T02:00:00-05:00", end="2026-03-08T03:00:00-04:00")
    with pytest.raises(ValueError, match="offset"):
        run(plan, timing)


def test_work_only_covers_allocated_blocks_and_no_schedule_is_inferred():
    plan, timing = scenario()
    plan["resources"][0]["available"] = "500"
    with pytest.raises(ValueError, match="exactly"):
        run(plan, timing)
    timing["work"] = timing["work"][:1]
    result = run(plan, timing)
    assert result["declared_checks_pass"] and not result["all_requested_blocks_allocated"]

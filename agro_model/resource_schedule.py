"""Bounded, deterministic start-time search over explicitly declared work windows.

Only start times are chosen. Blocks, durations, crews, budgets and external flows
remain supplied inputs. Every proposal passes the existing exact timing checker.
"""

from collections import defaultdict
from datetime import timedelta

from .equipment_schedule import available as equipment_available
from .equipment_schedule import calendars as equipment_calendars
from .equipment_schedule import validate_equipment
from .resource_timing import (
    _bounds,
    _delivery_segments,
    _dependency_order,
    _instant,
    _interval,
    _job_bounds,
    _list,
    _unique,
    check_timing,
    validate_timing,
)
from .resources import _integer, _object

_INVARIANT_ISSUES = {
    "work_interval_too_short", "occupied_time_exceeds_budget",
    "time_budget_exceeds_availability", "water_budget_exceeds_supplied_balance",
}


def _work(run, start, zone):
    return dict(id=run["id"], job_id=run["job_id"], blocks=run["blocks"],
                start=start.astimezone(zone).isoformat(),
                end=(start+timedelta(seconds=run["duration_seconds"])).astimezone(zone).isoformat(),
                concurrent=run["concurrent"], source=run["source"])


def validate_schedule(value, plan, allocation):
    optional = tuple(key for key in ("dependencies", "water_delivery") if isinstance(value, dict) and key in value)
    equipment_fields = ("equipment",) if isinstance(value, dict) and "equipment" in value else ()
    value = _object(value, ("source", "step_seconds", "search_limit", "runs", "storage", "availability")+optional+equipment_fields, "schedule")
    step = _integer("step_seconds", value["step_seconds"])
    limit = _integer("search_limit", value["search_limit"])
    if limit > 10000:
        raise ValueError("search_limit must be at most 10000 attempted placements")
    zone, begin, finish = _bounds(plan["period"])
    jobs = {j["id"]: j for j in plan["jobs"]}
    runs, window_count = [], 0
    for row in _list(value["runs"], "runs", 100):
        row = dict(_object(row, ("id", "job_id", "blocks", "duration_seconds", "concurrent", "windows", "source"), "run"))
        duration = _integer("duration_seconds", row["duration_seconds"])
        if not isinstance(row["job_id"], str) or row["job_id"] not in jobs:
            raise ValueError("run refers to an unknown job")
        _, job_begin, job_finish = _job_bounds(jobs[row["job_id"]], plan["period"])
        if duration > int((job_finish-job_begin).total_seconds()):
            raise ValueError("run duration cannot exceed the planning period")
        windows = []
        for window in _list(row["windows"], "run windows", 100):
            window = dict(_object(window, ("start", "end"), "run window"))
            _interval(window, zone, job_begin, job_finish)
            windows.append(window)
        row["windows"] = windows
        window_count += len(windows)
        runs.append(row)
    if window_count > 1000:
        raise ValueError("scheduling permits at most 1000 run windows in total")
    runs = _unique(runs, "run")
    # Reuse all allocation, pool, count, provenance and calendar validation. These
    # placeholder intervals are never returned as a proposed schedule.
    extra = {key: value[key] for key in optional}
    timing = validate_timing(dict(source=value["source"], work=[_work(r, _job_bounds(jobs[r["job_id"]], plan["period"])[1], zone) for r in runs],
                                  storage=value["storage"], availability=value["availability"], **extra), plan, allocation)
    runs = [dict(r, source=w["source"], concurrent=w["concurrent"]) for r, w in zip(runs, timing["work"])]
    extra = {"equipment": validate_equipment(value["equipment"], [r["id"] for r in runs], plan["period"])} if equipment_fields else {}
    return dict(source=timing["source"], step_seconds=step, search_limit=limit, runs=runs, **extra,
                storage=timing["storage"], availability=timing["availability"], **{key: timing[key] for key in optional})


def _domains(run, zone, begin, finish, step):
    """Merged inclusive integer grid ranges; never materialize huge calendars."""
    ranges = []
    for window in run["windows"]:
        start, end = _interval(window, zone, begin, finish)
        low = (int((start-begin).total_seconds())+step-1)//step
        high = (int((end-begin).total_seconds())-run["duration_seconds"])//step
        if low <= high:
            ranges.append((low, high))
    merged = []
    for low, high in sorted(ranges):
        if merged and low <= merged[-1][1]+1:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def _capacity_ok(placed, calendars):
    """Safe partial pruning: adding more jobs cannot remove an occupancy excess."""
    for key, calendar in calendars.items():
        changes = defaultdict(int, calendar)
        for run, start, end in placed:
            units = run["concurrent"][key]
            changes[start] += units
            changes[end] -= units
        balance = 0
        for at in sorted(changes):
            balance += changes[at]
            if balance > 0:
                return False
    return True


def _dependencies_ok(placed, dependencies):
    instants = {run["id"]: (start, end) for run, start, end in placed}
    return all(int((instants[row["successor"]][0]-instants[row["predecessor"]][1]).total_seconds()) >= row["min_gap_seconds"]
               for row in dependencies if row["predecessor"] in instants and row["successor"] in instants)


def propose_schedule(value, plan, allocation):
    inputs = validate_schedule(value, plan, allocation)
    zone, begin, finish = _bounds(plan["period"])
    step = inputs["step_seconds"]
    priorities = {j["id"]: j["priority"] for j in plan["jobs"]}
    dependencies = inputs.get("dependencies", [])
    by_id = {r["id"]: r for r in inputs["runs"]}
    ordered = _dependency_order(by_id, dependencies, {key: priorities[r["job_id"]] for key, r in by_id.items()})
    runs = [by_id[key] for key in ordered]
    domains = [_domains(r, zone, begin, finish, step) for r in runs]
    equipment = inputs.get("equipment")
    occupied = equipment_calendars(equipment, plan["period"]) if equipment else {}
    assignments = {key: set(ids) for key, ids in equipment["assignments"].items()} if equipment else {}
    if equipment and equipment["not_before"] is not None:
        earliest = _instant(equipment["not_before"], zone)
        minimum = (int((earliest-begin).total_seconds())+step-1)//step
        domains = [[(max(low, minimum), high) for low, high in domain if high >= minimum] for domain in domains]
    counts = [sum(high-low+1 for low, high in domain) for domain in domains]
    report = dict(model_version="resource-schedule-v4", evidence_status="experimental",
        status="no_feasible_schedule_on_grid", grid_origin=begin.astimezone(zone).isoformat(),
        step_seconds=step, search_limit=inputs["search_limit"], attempted_placements=0, complete_checks=0,
        capacity_pruned=0, dependency_pruned=0, equipment_pruned=0, delivery_pruned=0, search_exhausted=True,
        water_delivery_constraints_supplied=bool(inputs.get("water_delivery")),
        equipment_checks_pass=None, equipment_constraints_supplied=equipment is not None,
        all_requested_blocks_allocated=allocation["all_requested_blocks_allocated"],
        candidates_by_run=[dict(run_id=r["id"], count=c) for r, c in zip(runs, counts)],
        invariant_issues=[],
        assumptions=[
            "Start times lie on an elapsed-seconds grid anchored at the planning period's local midnight, evaluated in UTC across daylight-saving changes.",
            "Every run fits wholly within one supplied window. Duration, concurrent units and allocated blocks are fixed; splitting requires explicit separate runs.",
            "Search assigns predecessors before successors, then uses ascending job priority and run ID among ready runs, with earliest grid starts first and backtracking. Only explicit dependencies impose chronological order; priorities are not an optimization objective.",
            "No feasible schedule on the grid does not rule out off-grid starts or changed inputs. A search limit means feasibility remains unknown.",
            "Pump/labor occupancy and explicit dependencies prune partial placements. Water trajectories are checked for complete schedules; later withdrawals can prevent overflow.",
            "Supplied water-delivery windows prune partial placements when uniform job and outside withdrawals exceed an outlet limit. Inflows cannot reduce that demand. These checks do not establish actual peak flow or hydraulic suitability.",
            "Dependency gaps are minimum elapsed seconds from predecessor finish to successor start, without resource use during the gap. Active waiting requires a separate declared run. No deadlines, maximum waits or agronomic waiting periods are inferred.",
            "Proposals pass the exact timing and storage checker under its declared assumptions. Durations and windows are operator inputs, not agronomic recommendations.",
            "No reservations, approvals, actual activity or equipment actions. Hydraulic suitability, crop response, worker skills and optimality remain unverified.",
        ])
    output = dict(schedule_result=report)
    if equipment:
        report["assumptions"].append("Equipment assignments are fixed. Each item occupies the whole run interval and must avoid supplied busy intervals and other runs using it. Touching endpoints are allowed. Starts respect the supplied not_before instant. This snapshot is not a reservation; equipment compatibility and unknown outside use are not inferred.")
    if any(count == 0 for count in counts):
        return output
    base = dict(source=inputs["source"], storage=inputs["storage"], availability=inputs["availability"])
    if "dependencies" in inputs:
        base["dependencies"] = dependencies
    if "water_delivery" in inputs:
        base["water_delivery"] = inputs["water_delivery"]
    seed = dict(base, work=[_work(r, begin+timedelta(seconds=d[0][0]*step), zone) for r, d in zip(runs, domains)])
    checked = check_timing(seed, plan, allocation)
    report["invariant_issues"] = [issue for issue in checked["issues"] if issue["code"] in _INVARIANT_ISSUES]
    if report["invariant_issues"]:
        return output
    calendars = {}
    for pool in inputs["availability"]:
        changes = defaultdict(int)
        for window in pool["windows"]:
            start, end = _interval(window, zone, begin, finish)
            changes[start] -= window["units"]
            changes[end] += window["units"]
        calendars[pool["resource_id"]] = changes
    placed = []

    def search(depth):
        if depth == len(runs):
            timing = dict(base, work=[_work(run, start, zone) for run, start, _ in placed])
            result = check_timing(timing, plan, allocation)
            report["complete_checks"] += 1
            if result["declared_checks_pass"]:
                output.update(timing_inputs=timing, timing_result=result)
                report.update(status="feasible_schedule_found", search_exhausted=False)
                if equipment:
                    report["equipment_checks_pass"] = True
                return True
            return False
        run = runs[depth]
        for low, high in domains[depth]:
            for index in range(low, high+1):
                if report["attempted_placements"] >= inputs["search_limit"]:
                    report.update(status="search_limit_reached", search_exhausted=False)
                    return True
                report["attempted_placements"] += 1
                start = begin+timedelta(seconds=index*step)
                end = start+timedelta(seconds=run["duration_seconds"])
                if equipment and not equipment_available(run, start, end, placed, assignments, occupied):
                    report["equipment_pruned"] += 1
                    continue
                placed.append((run, start, end))
                if not _dependencies_ok(placed, dependencies):
                    report["dependency_pruned"] += 1
                elif any(used > capacity for _, _, _, capacity, used, _ in _delivery_segments(inputs, plan, placed, zone, begin, finish)):
                    report["delivery_pruned"] += 1
                elif _capacity_ok(placed, calendars):
                    if search(depth+1):
                        return True
                else:
                    report["capacity_pruned"] += 1
                placed.pop()
        return False

    search(0)
    return output

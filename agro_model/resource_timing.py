"""Audit explicit work intervals, resource availability and linear reservoir balances.

No schedule is invented. Fractions preserve exact comparisons, including repeating flow
rates and hours. All instants are compared in UTC; input offsets must match the farm zone.
"""

import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from heapq import heappop, heappush
from zoneinfo import ZoneInfo

from .resources import _amount, _integer, _object
from .validation import text

_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})\Z")


def _list(value, label, maximum=1000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} entries; use [] for explicitly none")
    return value


def _instant(value, zone):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise ValueError("timestamps require YYYY-MM-DDTHH:MM:SS with Z or an explicit UTC offset")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.astimezone(zone).utcoffset() != parsed.utcoffset():
            raise ValueError("timestamp offset must match the farm timezone at that instant, including daylight saving")
        return parsed.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("timestamp falls outside supported dates") from exc


def _bounds(period):
    zone = ZoneInfo(period["timezone"])
    try:
        start = datetime.combine(date.fromisoformat(period["start"]), time(), zone)
        end = datetime.combine(date.fromisoformat(period["end"])+timedelta(days=1), time(), zone)
        for value in (start, end):
            if value.astimezone(timezone.utc).astimezone(zone) != value:
                raise ValueError("period boundary is not a valid local midnight")
        return zone, start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("timed period boundaries fall outside supported dates") from exc


def _interval(row, zone, begin, finish):
    start, end = (_instant(row[key], zone) for key in ("start", "end"))
    if not begin <= start < end <= finish:
        raise ValueError("each interval must have positive duration and lie within the planning period")
    return start, end


def _job_bounds(job, period):
    if "irrigation_design" in job:
        day = job["irrigation_design"]["day"]
        return _bounds(dict(start=day, end=day, timezone=period["timezone"]))
    return _bounds(period)


def _unique(rows, label):
    keys = [text("id", row["id"]).strip() for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(label+" IDs must be unique")
    return [dict(row, id=key) for row, key in zip(rows, keys)]


def _dependency_order(ids, dependencies, priority=None):
    """Stable topological order; iterative so long valid chains are supported."""
    following = {key: [] for key in ids}
    incoming = dict.fromkeys(ids, 0)
    for row in dependencies:
        following[row["predecessor"]].append(row["successor"])
        incoming[row["successor"]] += 1
    priority = priority or dict.fromkeys(ids, 0)
    ready = []
    for key in ids:
        if incoming[key] == 0:
            heappush(ready, (priority[key], key))
    ordered = []
    while ready:
        _, key = heappop(ready)
        ordered.append(key)
        for successor in following[key]:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heappush(ready, (priority[successor], successor))
    if len(ordered) != len(ids):
        raise ValueError("work dependencies must not contain a cycle")
    return ordered


def _dependencies(value, ids):
    rows, pairs = [], set()
    for item in _list(value, "dependencies"):
        row = dict(_object(item, ("id", "predecessor", "successor", "min_gap_seconds", "source"), "dependency"))
        for key in ("predecessor", "successor", "source"):
            row[key] = text(key, row[key]).strip()
        pair = row["predecessor"], row["successor"]
        if any(key not in ids for key in pair):
            raise ValueError("dependencies must refer to declared work/run IDs, not job IDs or outside work")
        if pair[0] == pair[1]:
            raise ValueError("work cannot depend on itself")
        if pair in pairs:
            raise ValueError("declare each predecessor/successor pair once")
        pairs.add(pair)
        _integer("min_gap_seconds", row["min_gap_seconds"], minimum=0)
        rows.append(row)
    rows = _unique(rows, "dependency")
    _dependency_order(ids, rows)
    return rows


def validate_timing(value, plan, allocation):
    optional = tuple(key for key in ("dependencies", "water_delivery") if isinstance(value, dict) and key in value)
    value = _object(value, ("source", "work", "storage", "availability")+optional, "timing")
    zone, begin, finish = _bounds(plan["period"])
    resources = {r["id"]: r for r in plan["resources"]}
    time_ids = {key for key, r in resources.items() if r["kind"] in ("pump_time", "labor_time")}
    water_ids = {key for key, r in resources.items() if r["kind"] == "water"}
    jobs = {j["id"]: j for j in plan["jobs"]}
    expected = {a["job_id"]: a["allocated_blocks"] for a in allocation["allocations"]}
    counts = dict.fromkeys(jobs, 0)
    work = []
    for row in _list(value["work"], "work"):
        row = dict(_object(row, ("id", "job_id", "blocks", "start", "end", "concurrent", "source"), "work interval"))
        if not isinstance(row["job_id"], str) or row["job_id"] not in jobs:
            raise ValueError("work interval refers to an unknown job")
        _integer("blocks", row["blocks"])
        counts[row["job_id"]] += row["blocks"]
        _interval(row, zone, begin, finish)
        if "irrigation_design" in jobs[row["job_id"]]:
            _interval(row, *_job_bounds(jobs[row["job_id"]], plan["period"]))
        concurrent = _object(row["concurrent"], sorted(time_ids), "concurrent resource counts")
        row["concurrent"] = dict(concurrent)
        for key, units in concurrent.items():
            _integer("concurrent units", units, minimum=0)
            uses = _amount(jobs[row["job_id"]]["requirements"][key]) > 0
            if uses != (units > 0):
                raise ValueError("concurrent units must be positive exactly for time resources the job uses; otherwise supply zero")
        row["source"] = text("work source", row["source"]).strip()
        work.append(row)
    work = _unique(work, "work interval")
    if counts != expected:
        raise ValueError("work intervals must cover exactly the saved allocated blocks for every job, including zero for unallocated jobs")
    storage = []
    for row in _list(value["storage"], "storage", 100):
        row = dict(_object(row, ("resource_id", "capacity_l", "initial_l", "minimum_l", "inflows", "external_withdrawals", "source"), "storage"))
        if not isinstance(row["resource_id"], str) or row["resource_id"] not in water_ids:
            raise ValueError("storage must refer to a declared water pool")
        capacity, initial, minimum = (_amount(row[key]) for key in ("capacity_l", "initial_l", "minimum_l"))
        if not 0 <= minimum <= initial <= capacity or capacity == 0:
            raise ValueError("storage requires positive capacity and 0 <= minimum <= initial <= capacity")
        for key in ("inflows", "external_withdrawals"):
            flows = []
            for flow in _list(row[key], key):
                flow = dict(_object(flow, ("id", "start", "end", "volume_l", "source"), "flow interval"))
                _interval(flow, zone, begin, finish)
                if _amount(flow["volume_l"]) == 0:
                    raise ValueError("flow volume must be positive; use an empty list for no flows")
                flow["source"] = text("flow source", flow["source"]).strip()
                flows.append(flow)
            row[key] = _unique(flows, key)
        row["source"] = text("storage source", row["source"]).strip()
        storage.append(row)
    if len(storage) != len(water_ids) or {r["resource_id"] for r in storage} != water_ids:
        raise ValueError("supply one storage balance for every declared water pool")
    availability = []
    for row in _list(value["availability"], "availability", 100):
        row = dict(_object(row, ("resource_id", "windows"), "availability"))
        if not isinstance(row["resource_id"], str) or row["resource_id"] not in time_ids:
            raise ValueError("availability must refer to a declared pump or labor pool")
        windows, ranges = [], []
        for window in _list(row["windows"], "availability windows"):
            window = dict(_object(window, ("id", "start", "end", "units", "source"), "availability window"))
            _integer("available units", window["units"])
            ranges.append(_interval(window, zone, begin, finish))
            window["source"] = text("availability source", window["source"]).strip()
            windows.append(window)
        ranges.sort()
        if any(end > next_start for (_, end), (next_start, _) in zip(ranges, ranges[1:])):
            raise ValueError("availability windows in the same pool must not overlap")
        row["windows"] = _unique(windows, "availability window")
        availability.append(row)
    if len(availability) != len(time_ids) or {r["resource_id"] for r in availability} != time_ids:
        raise ValueError("supply availability windows for every declared pump and labor pool")
    if sum(len(r["inflows"])+len(r["external_withdrawals"]) for r in storage) + sum(len(r["windows"]) for r in availability) > 5000:
        raise ValueError("timing permits at most 5000 flow and availability intervals in total")
    result = dict(source=text("timing source", value["source"]).strip(), work=work, storage=storage, availability=availability)
    if "dependencies" in optional:
        result["dependencies"] = _dependencies(value["dependencies"], {row["id"] for row in work})
    if "water_delivery" in optional:
        result["water_delivery"] = _delivery_inputs(value["water_delivery"], water_ids, zone, begin, finish)
    return result


def _delivery_inputs(value, water_ids, zone, begin, finish):
    pools, count = [], 0
    for row in _list(value, "water_delivery", 100):
        row = dict(_object(row, ("resource_id", "windows"), "water delivery"))
        if not isinstance(row["resource_id"], str) or row["resource_id"] not in water_ids:
            raise ValueError("water delivery must refer to a declared water pool")
        windows, ranges = [], []
        for window in _list(row["windows"], "delivery windows"):
            window = dict(_object(window, ("id", "start", "end", "max_flow_lph", "source"), "delivery window"))
            _amount(window["max_flow_lph"])
            ranges.append(_interval(window, zone, begin, finish))
            window["source"] = text("delivery source", window["source"]).strip()
            windows.append(window)
        ranges.sort()
        if any(end > next_start for (_, end), (next_start, _) in zip(ranges, ranges[1:])):
            raise ValueError("delivery windows in the same pool must not overlap")
        row["windows"] = _unique(windows, "delivery window")
        count += len(windows)
        pools.append(row)
    if pools and (len(pools) != len(water_ids) or {r["resource_id"] for r in pools} != water_ids):
        raise ValueError("supply delivery windows for every water pool, or [] to explicitly omit delivery checks")
    if count > 5000:
        raise ValueError("timing permits at most 5000 delivery windows in total")
    return pools


def _delivery_segments(timing, plan, work, zone, begin, finish):
    """Exact L/hour on half-open intervals, also usable for safe partial pruning."""
    jobs = {job["id"]: job for job in plan["jobs"]}
    stores = {pool["resource_id"]: pool for pool in timing["storage"]}
    for pool in timing.get("water_delivery", []):
        key = pool["resource_id"]
        changes = defaultdict(lambda: [Fraction(0), Fraction(0), set(), set()])
        changes[begin]
        changes[finish]
        for window in pool["windows"]:
            start, end = _interval(window, zone, begin, finish)
            rate = Fraction(_amount(window["max_flow_lph"]), 1_000_000)
            changes[start][0] += rate
            changes[end][0] -= rate
        flows = [("outside:"+row["id"], _amount(row["volume_l"]), *_interval(row, zone, begin, finish))
                 for row in stores[key]["external_withdrawals"]]
        flows += [("work:"+row["id"], _amount(jobs[row["job_id"]]["requirements"][key])*row["blocks"], start, end)
                  for row, start, end in work]
        for name, amount, start, end in flows:
            if not amount:
                continue
            rate = Fraction(amount*3600, 1_000_000*int((end-start).total_seconds()))
            changes[start][1] += rate
            changes[end][1] -= rate
            changes[start][2].add(name)
            changes[end][3].add(name)
        capacity, used, active = Fraction(0), Fraction(0), set()
        times = sorted(changes)
        for start, end in zip(times, times[1:]):
            change = changes[start]
            capacity += change[0]
            used += change[1]
            active.difference_update(change[3])
            active.update(change[2])
            if used or capacity:
                yield key, start, end, capacity, used, sorted(active)


def _number(value):
    """Readable display plus exact rational audit value, independent of Decimal context."""
    value = Fraction(value)
    with localcontext() as ctx:
        numerator, denominator = Decimal(value.numerator), Decimal(value.denominator)
        ctx.prec = max(70, numerator.copy_abs().adjusted()+denominator.adjusted()+16)
        ctx.rounding = ROUND_HALF_EVEN
        display = format(numerator/denominator, ".6f").rstrip("0").rstrip(".")
    return dict(value=display if display not in ("", "-0") else "0", exact=f"{value.numerator}/{value.denominator}")


def check_timing(value, plan, allocation):
    timing = validate_timing(value, plan, allocation)
    zone, begin, finish = _bounds(plan["period"])
    def stamp(at):
        return at.astimezone(zone).isoformat()
    jobs = {j["id"]: j for j in plan["jobs"]}
    resources = {r["id"]: r for r in plan["resources"]}
    work = [(row, *_interval(row, zone, begin, finish)) for row in timing["work"]]
    issues, time_results, water_results = [], [], []
    dependency_results = []
    instants = {row["id"]: (start, end) for row, start, end in work}
    for row in timing.get("dependencies", []):
        previous_end = instants[row["predecessor"]][1]
        next_start = instants[row["successor"]][0]
        gap = int((next_start-previous_end).total_seconds())
        passed = gap >= row["min_gap_seconds"]
        dependency_results.append(dict(row, predecessor_end=stamp(previous_end), successor_start=stamp(next_start),
                                       actual_gap_seconds=gap, satisfied=passed))
        if not passed:
            issues.append(dict(code="dependency_not_satisfied", dependency_id=row["id"],
                work_ids=[row["predecessor"], row["successor"]], min_gap_seconds=row["min_gap_seconds"], actual_gap_seconds=gap))
    for pool in timing["availability"]:
        key = pool["resource_id"]
        changes = defaultdict(lambda: [0, 0, set(), set()])  # capacity, occupancy, starts, ends
        changes[begin]
        changes[finish]
        offered, occupied = Fraction(0), Fraction(0)
        for window in pool["windows"]:
            start, end = _interval(window, zone, begin, finish)
            changes[start][0] += window["units"]
            changes[end][0] -= window["units"]
            offered += Fraction(int((end-start).total_seconds())*window["units"], 3600)
        for row, start, end in work:
            units = row["concurrent"][key]
            if not units:
                continue
            hours = Fraction(int((end-start).total_seconds())*units, 3600)
            required = Fraction(_amount(jobs[row["job_id"]]["requirements"][key])*row["blocks"], 1_000_000)
            if hours < required:
                issues.append(dict(code="work_interval_too_short", resource_id=key, work_id=row["id"],
                                   required_hours=_number(required), occupied_hours=_number(hours)))
            occupied += hours
            changes[start][1] += units
            changes[start][2].add(row["id"])
            changes[end][1] -= units
            changes[end][3].add(row["id"])
        budget = Fraction(_amount(resources[key]["available"]), 1_000_000)
        for condition, code in ((occupied > budget, "occupied_time_exceeds_budget"), (budget > offered, "time_budget_exceeds_availability")):
            if condition:
                issues.append(dict(code=code, resource_id=key))
        capacity, used, active, segments = 0, 0, set(), []
        times = sorted(changes)
        for start, end in zip(times, times[1:]):
            change = changes[start]
            capacity += change[0]
            used += change[1]
            active.difference_update(change[3])
            active.update(change[2])
            if used or capacity:
                segments.append(dict(start=stamp(start), end=stamp(end), available_units=capacity, occupied_units=used,
                                     work_ids=sorted(active), within_capacity=used <= capacity))
            if used > capacity:
                issues.append(dict(code="concurrent_capacity_exceeded", resource_id=key, start=stamp(start), end=stamp(end), work_ids=sorted(active)))
        time_results.append(dict(resource_id=key, available_hours=_number(offered), budget_hours=_number(budget),
                                 occupied_hours=_number(occupied), intervals=segments))
    for pool in timing["storage"]:
        key = pool["resource_id"]
        changes = defaultdict(Fraction)
        changes[begin]
        changes[finish]
        initial, capacity, minimum = (Fraction(_amount(pool[name]), 1_000_000) for name in ("initial_l", "capacity_l", "minimum_l"))
        inflow_total, outside_total, job_total = Fraction(0), Fraction(0), Fraction(0)
        for field, sign in (("inflows", 1), ("external_withdrawals", -1)):
            for row in pool[field]:
                start, end = _interval(row, zone, begin, finish)
                amount = Fraction(_amount(row["volume_l"]), 1_000_000)
                rate = amount/int((end-start).total_seconds())
                changes[start] += sign*rate
                changes[end] -= sign*rate
                if sign == 1:
                    inflow_total += amount
                else:
                    outside_total += amount
        for row, start, end in work:
            amount = Fraction(_amount(jobs[row["job_id"]]["requirements"][key])*row["blocks"], 1_000_000)
            job_total += amount
            if amount:
                rate = amount/int((end-start).total_seconds())
                changes[start] -= rate
                changes[end] += rate
        supported_budget = initial+inflow_total-outside_total-minimum
        if Fraction(_amount(resources[key]["available"]), 1_000_000) > supported_budget:
            issues.append(dict(code="water_budget_exceeds_supplied_balance", resource_id=key))
        level, rate, previous, points = initial, Fraction(0), begin, []
        lowest, highest = initial, initial
        for at in sorted(changes):
            level += rate*int((at-previous).total_seconds())
            lowest, highest = min(lowest, level), max(highest, level)
            points.append(dict(at=stamp(at), level_l=_number(level)))
            rate += changes[at]
            previous = at
        if lowest < minimum:
            issues.append(dict(code="storage_below_minimum", resource_id=key, lowest_l=_number(lowest)))
        if highest > capacity:
            issues.append(dict(code="storage_above_capacity", resource_id=key, highest_l=_number(highest)))
        water_results.append(dict(resource_id=key, initial_l=_number(initial), inflows_l=_number(inflow_total),
            external_withdrawals_l=_number(outside_total), job_withdrawals_l=_number(job_total), end_l=_number(level),
            lowest_l=_number(lowest), highest_l=_number(highest), capacity_l=_number(capacity), minimum_l=_number(minimum), trajectory=points))
    delivery = {pool["resource_id"]: dict(resource_id=pool["resource_id"], intervals=[]) for pool in timing.get("water_delivery", [])}
    for key, start, end, capacity, used, active in _delivery_segments(timing, plan, work, zone, begin, finish):
        detail = dict(start=stamp(start), end=stamp(end), max_flow_lph=_number(capacity), withdrawal_lph=_number(used),
                      work_ids=[name.removeprefix("work:") for name in active if name.startswith("work:")],
                      external_withdrawal_ids=[name.removeprefix("outside:") for name in active if name.startswith("outside:")],
                      within_capacity=used <= capacity)
        delivery[key]["intervals"].append(detail)
        if used > capacity:
            issues.append(dict(code="water_delivery_capacity_exceeded", resource_id=key, **detail))
    return dict(model_version="resource-timing-v3", evidence_status="experimental", declared_checks_pass=not issues,
        covers_allocated_blocks=True, all_requested_blocks_allocated=allocation["all_requested_blocks_allocated"],
        period=plan["period"], issues=issues, time_resources=time_results, storage=water_results, dependencies=dependency_results,
        water_delivery_checked=bool(delivery), water_delivery=list(delivery.values()),
        assumptions=["All allocated blocks have explicit intervals. This checker audits those supplied intervals without allocating additional blocks or quantities.",
                     "Intervals are half-open [start,end). Listed pump/labor units are occupied throughout each work interval, including any slack time.",
                     "Listed dependencies require a predecessor to finish before its successor starts, with a minimum gap measured in elapsed seconds. Gaps do not consume resources; model active waiting as work. Unlisted dependencies are not inferred.",
                     "Each availability window declares a count of interchangeable units, net of outside commitments. Outside listed windows availability is zero.",
                     "Water withdrawals and listed external flows are uniform over their intervals. The reservoir is a single mixed balance with no unlisted losses or gains.",
                     "Optional delivery windows cap total modeled job and outside withdrawal rates from each water pool in L/hour. Unlisted time has zero delivery capacity. Inflows do not offset outlet demand. Omitted water_delivery or [] means delivery capacity is unchecked.",
                     "Delivery limits are supplied evidence, not inferred pump curves or pipe capacities. Uniform interval rates do not establish actual peak flow, pressure, connections or changing irrigation-zone flows. Split changing flows into explicit intervals before interpreting these checks.",
                     "Storage trajectories are unconstrained balances: negative levels and overflow are reported, not clipped or silently spilled. Linear extrema occur at interval boundaries.",
                     "Arithmetic checks use exact fractions. Display values are rounded to six decimals; exact values are retained for auditing.",
                     "Passing these declared checks does not verify hydraulics, pipe/pump connections, treatment suitability, worker skills, cash timing or physical operation.",
                     "Scenarios do not reserve resources, approve work, create actual records or control equipment."])

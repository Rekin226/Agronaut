"""Exact, priority-ordered allocation of declared work blocks against shared budgets.

Amounts are decimal strings with at most six fractional places. Integer micro-units
avoid floating-point capacity overshoot and dependence on the caller's Decimal context.
This is aggregate accounting, not an agronomic, hydraulic or chronological scheduler.
"""

import re
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .validation import text

KINDS = {"water": "L", "pump_time": "h", "labor_time": "h", "cash": None}
_SCALE = 1_000_000
_AMOUNT = re.compile(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,6})?\Z")


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} requires exactly: {', '.join(fields)}")
    return value


def _rows(value, label, maximum):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{label} requires 1–{maximum} entries")
    return value


def _amount(value):
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise ValueError("amounts must be nonnegative decimal strings, up to 15 integer and 6 fractional digits")
    whole, _, fraction = value.partition(".")
    return int(whole)*_SCALE + int(fraction.ljust(6, "0"))


def _display(value):
    whole, fraction = divmod(value, _SCALE)
    return str(whole) + ("."+f"{fraction:06d}".rstrip("0") if fraction else "")


def _integer(label, value, minimum=1):
    if type(value) is not int or not minimum <= value <= 1_000_000:
        raise ValueError(f"{label} must be a whole number from {minimum} to 1000000")
    return value


def validate_plan(value):
    """Return a fresh canonical input, rejecting unknown/missing data and duplicate pools."""
    value = _object(value, ("title", "period", "source", "resources", "jobs"), "resource plan")
    title, source = (text(key, value[key]).strip() for key in ("title", "source"))
    period = dict(_object(value["period"], ("start", "end", "timezone"), "period"))
    for key in ("start", "end"):
        if not isinstance(period[key], str) or date.fromisoformat(period[key]).isoformat() != period[key]:
            raise ValueError("period dates must use YYYY-MM-DD")
    if period["end"] < period["start"]:
        raise ValueError("period end must be on or after start")
    try:
        ZoneInfo(text("timezone", period["timezone"]))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("period timezone must be an IANA timezone") from exc
    resources, resource_ids = [], set()
    for item in _rows(value["resources"], "resources", 100):
        item = dict(_object(item, ("id", "kind", "unit", "available", "source"), "resource"))
        for key in ("id", "source"):
            item[key] = text(key, item[key]).strip()
        if item["id"] in resource_ids:
            raise ValueError("resource IDs must be unique; declare each shared pool once")
        resource_ids.add(item["id"])
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS:
            raise ValueError("resource kind must be water, pump_time, labor_time or cash")
        if item["kind"] == "cash":
            if not isinstance(item["unit"], str) or not re.fullmatch("[A-Z]{3}", item["unit"]):
                raise ValueError("cash resource unit must be a three-letter uppercase currency code")
        elif item["unit"] != KINDS[item["kind"]]:
            raise ValueError(f"{item['kind']} resource unit must be {KINDS[item['kind']]}")
        item["available"] = _display(_amount(item["available"]))
        resources.append(item)
    jobs, job_ids, priorities = [], set(), set()
    for item in _rows(value["jobs"], "jobs", 1000):
        optional = ("irrigation_design",) if isinstance(item, dict) and "irrigation_design" in item else ()
        item = dict(_object(item, ("id", "unit_id", "cycle_id", "title", "block_label", "requested_blocks",
                                  "minimum_blocks", "priority", "requirements", "source")+optional, "job"))
        for key in ("id", "unit_id", "title", "block_label", "source"):
            item[key] = text(key, item[key]).strip()
        if item["cycle_id"] is not None:
            item["cycle_id"] = text("cycle_id", item["cycle_id"]).strip()
        if item["id"] in job_ids:
            raise ValueError("job IDs must be unique")
        job_ids.add(item["id"])
        for key in ("priority", "requested_blocks", "minimum_blocks"):
            _integer(key, item[key])
        if item["minimum_blocks"] > item["requested_blocks"]:
            raise ValueError("minimum_blocks cannot exceed requested_blocks")
        if item["priority"] in priorities:
            raise ValueError("priorities must be unique; explicitly order work with lower numbers first")
        priorities.add(item["priority"])
        demands = _object(item["requirements"], tuple(r["id"] for r in resources), "job requirements")
        item["requirements"] = {key: _display(_amount(amount)) for key, amount in demands.items()}
        if not any(_amount(v) for v in item["requirements"].values()):
            raise ValueError("each job must consume at least one declared resource")
        if optional:
            link = dict(_object(item["irrigation_design"], ("calculation_id", "day", "water_resource_id", "pump_resource_id", "source"), "irrigation design link"))
            for key in link:
                link[key] = text(key, link[key]).strip()
            if date.fromisoformat(link["day"]).isoformat() != link["day"] or not period["start"] <= link["day"] <= period["end"]:
                raise ValueError("irrigation event day must use YYYY-MM-DD and lie within the plan period")
            pools = {r["id"]: r["kind"] for r in resources}
            if pools.get(link["water_resource_id"]) != "water" or pools.get(link["pump_resource_id"]) != "pump_time":
                raise ValueError("irrigation design links require a declared water pool and pump-time pool")
            if item["requested_blocks"] != 1 or item["minimum_blocks"] != 1:
                raise ValueError("a linked irrigation job must be one indivisible complete event")
            item["irrigation_design"] = link
        jobs.append(item)
    linked_events = [(j["irrigation_design"]["calculation_id"], j["irrigation_design"]["day"]) for j in jobs if "irrigation_design" in j]
    if len(set(linked_events)) != len(linked_events):
        raise ValueError("declare each linked irrigation calculation and event day once per plan")
    return dict(title=title, period=period, source=source, resources=resources, jobs=jobs)


def allocate_resources(value):
    plan = validate_plan(value)
    available = {r["id"]: _amount(r["available"]) for r in plan["resources"]}
    remaining, requested = dict(available), dict.fromkeys(available, 0)
    allocations = []
    for job in sorted(plan["jobs"], key=lambda j: j["priority"]):
        per_block = {key: _amount(amount) for key, amount in job["requirements"].items()}
        for key, amount in per_block.items():
            requested[key] += amount*job["requested_blocks"]
        fits = {key: remaining[key]//amount for key, amount in per_block.items() if amount}
        possible = min(job["requested_blocks"], *fits.values())
        limiting = [key for key, count in fits.items() if count == possible] if possible < job["requested_blocks"] else []
        blocks = possible if possible >= job["minimum_blocks"] else 0
        used = {key: amount*blocks for key, amount in per_block.items()}
        for key, amount in used.items():
            remaining[key] -= amount
        allocations.append(dict(job_id=job["id"], unit_id=job["unit_id"], cycle_id=job["cycle_id"], title=job["title"],
            priority=job["priority"], block_label=job["block_label"], requested_blocks=job["requested_blocks"],
            minimum_blocks=job["minimum_blocks"], allocated_blocks=blocks, unallocated_blocks=job["requested_blocks"]-blocks,
            limiting_resources=limiting, status="full" if blocks == job["requested_blocks"] else "partial" if blocks else "not_allocated",
            reason="Request fits" if blocks == job["requested_blocks"] else
                   "Fewer blocks fit than the declared minimum; no resources assigned" if not blocks else
                   "Declared resource limit reached after higher-priority work",
            assigned={key: _display(amount) for key, amount in used.items()}, source=job["source"]))
    resources = [dict(resource, requested=_display(requested[resource["id"]]),
        assigned=_display(available[resource["id"]]-remaining[resource["id"]]), remaining=_display(remaining[resource["id"]]),
        unmet_requested=_display(requested[resource["id"]]-available[resource["id"]]+remaining[resource["id"]]))
        for resource in plan["resources"]]
    return dict(model_version="priority-blocks-v1", evidence_status="experimental", period=plan["period"],
        allocation_within_declared_limits=True, all_requested_blocks_allocated=all(r["status"] == "full" for r in allocations),
        allocations=allocations, resources=resources,
        method="Allocate whole operator-defined blocks in ascending, unique priority order. "
               "Give each job as many requested blocks as fit all remaining pools, or none if its minimum cannot fit. "
               "Skipped jobs leave resources for lower-priority work. No fractional blocks, inferred demands or optimization objective.",
        limits=["Aggregate period accounting only: timing, simultaneous pump use, flow, pressure and storage trajectories are not checked.",
                "Pools must refer to distinct available resources for this period, net of outside commitments; overlapping pools can double-count supply.",
                "Every job declares every pool: explicit zero means unused, never unknown. Missing demands or capacities cannot be evaluated.",
                "Lower priorities may receive nothing. Changing order or block size can change results; this is not yield or profit optimization.",
                "Plans are alternative scenarios, not reservations, purchases or performed work. No equipment actions or journal quantities are created."])

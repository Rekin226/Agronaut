"""Explicit physical-equipment constraints for bounded schedule search."""

from bisect import bisect_left
from collections import defaultdict

from .resource_timing import _bounds, _instant, _interval, _list, _unique
from .resources import _object
from .validation import text


def validate_equipment(value, run_ids, period):
    value = _object(value, ("assignments", "busy", "not_before", "source"), "equipment constraints")
    assignments = value["assignments"]
    if not isinstance(assignments, dict) or set(assignments) != set(run_ids):
        raise ValueError("equipment assignments must cover every run exactly once")
    normalized = {}
    for key, ids in assignments.items():
        if not isinstance(ids, list) or len(ids) > 100:
            raise ValueError("each run may assign at most 100 physical items")
        ids = [text("equipment ID", item).strip() for item in ids]
        if len(set(ids)) != len(ids):
            raise ValueError("equipment IDs must be distinct within each run")
        normalized[key] = sorted(ids)
    selected = {item for ids in normalized.values() for item in ids}
    zone, begin, finish = _bounds(period)
    busy = []
    for item in _list(value["busy"], "equipment busy intervals", 10000):
        row = dict(_object(item, ("id", "equipment_id", "start", "end", "source"), "equipment busy interval"))
        row["equipment_id"] = text("equipment ID", row["equipment_id"]).strip()
        if row["equipment_id"] not in selected:
            raise ValueError("busy intervals must refer to assigned equipment")
        _interval(row, zone, begin, finish)
        row["source"] = text("equipment busy source", row["source"]).strip()
        busy.append(row)
    busy = _unique(busy, "equipment busy interval")
    if value["not_before"] is not None:
        _instant(value["not_before"], zone)
    return dict(assignments=normalized, busy=busy, not_before=value["not_before"], source=text("equipment source", value["source"]).strip())


def calendars(equipment, period):
    zone, begin, finish = _bounds(period)
    intervals = defaultdict(list)
    for row in equipment["busy"]:
        intervals[row["equipment_id"]].append(_interval(row, zone, begin, finish))
    result = {}
    for key, rows in intervals.items():
        merged = []
        for start, end in sorted(rows):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result[key] = ([row[1] for row in merged], merged)
    return result


def available(run, start, end, placed, assignments, occupied):
    ids = assignments[run["id"]]
    for key in ids:
        ends, intervals = occupied.get(key, ([], []))
        index = bisect_left(ends, start)
        while index < len(intervals) and intervals[index][1] <= start:
            index += 1
        if index < len(intervals) and intervals[index][0] < end:
            return False
    for other, other_start, other_end in placed:
        if start < other_end and other_start < end and ids.intersection(assignments[other["id"]]):
            return False
    return True

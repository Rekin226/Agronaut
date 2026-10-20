"""Explicit work plans and operator resolutions, separate from physical crop records."""

import json
from datetime import timedelta

from agro_model.validation import choice, text

from . import task_triggers
from .cycle_lifecycle import lifecycle
from .field_io import _date, _mapping

ACTIVITIES = ("scouting", "stage_check", "irrigation", "fertilizer", "transplant", "harvest", "maintenance", "other")
EVIDENCE_KINDS = {"scouting": "scouting", "stage_check": "stage", "fertilizer": "fertilizer",
                  "transplant": "transplant", "harvest": "harvest"}


def task_plan(data):
    data = dict(_mapping(data, ("entry", "title", "activity", "due", "location", "notes", "source", "priority", "trigger"), "task plan"))
    if 'trigger' in data:
        data['trigger'] = task_triggers.reference(data['trigger'])
    for key in ("entry", "title", "location", "notes", "source"):
        data[key] = text(key, data.get(key)).strip()
    choice("activity", data.get("activity"), ACTIVITIES)
    choice("priority", data.get("priority"), ("low", "normal", "high"))
    due = _mapping(data.get("due"), ("basis", "start", "end", "start_offset_days", "end_offset_days"), "due")
    choice("due basis", due.get("basis"), ("date", "planting"))
    fields = ("start", "end") if due["basis"] == "date" else ("start_offset_days", "end_offset_days")
    due = dict(_mapping(due, ("basis", *fields), "due"))
    if due["basis"] == "date":
        for key in fields:
            _date(due.get(key), "due."+key)
    else:
        for key in fields:
            value = due.get(key)
            if type(value) is not int or not -365 <= value <= 3650:
                raise ValueError("planting offsets must be integer days from -365 to 3650")
    if due[fields[0]] > due[fields[1]]:
        raise ValueError("task window end must be on or after its start")
    data["due"] = due
    return data


def task_window(due, cycle):
    if due["basis"] == "date":
        return dict(start=due["start"], end=due["end"])
    planted = _date(cycle["planted_on"])
    try:
        return dict(start=(planted+timedelta(days=due["start_offset_days"])).isoformat(),
                    end=(planted+timedelta(days=due["end_offset_days"])).isoformat())
    except OverflowError as exc:
        raise ValueError("relative task window falls outside supported calendar dates") from exc


def evidence_reference(value):
    if value is None:
        return None
    value = dict(_mapping(value, ("kind", "id"), "completion record"))
    choice("completion record kind", value.get("kind"), ("crop_record", "water_day"))
    text("completion record id", value.get("id"))
    return value


def _evidence(conn, unit_id, cycle_id, reference):
    table = "crop_records" if reference["kind"] == "crop_record" else "field_water_days"
    row = conn.execute(f"SELECT payload FROM {table} WHERE id=? AND unit_id=? AND cycle_id=?",
                       (reference["id"], unit_id, cycle_id)).fetchone()
    if row is None:
        return None, "missing"
    record = json.loads(row[0])
    if conn.execute(f"SELECT 1 FROM {table} WHERE supersedes_id=?", (reference["id"],)).fetchone():
        return record, "superseded"
    if record.get("voided"):
        return record, "voided"
    if record["data"].get("quality") == "rejected":
        return record, "rejected"
    return record, "current"


def completion_evidence(conn, unit_id, cycle_id, activity, completed_on, reference):
    if reference is None:
        return None
    record, state = _evidence(conn, unit_id, cycle_id, reference)
    if state != "current":
        raise ValueError("completion record must be current, accepted and owned by this unit and cycle")
    if reference["kind"] == "water_day":
        valid = activity == "irrigation" and record["data"]["net_irrigation_mm"] > 0
    else:
        valid = record["kind"] == EVIDENCE_KINDS.get(activity)
    if not valid:
        raise ValueError("completion record does not match the task activity")
    if record["data"]["day"] != completed_on:
        raise ValueError("completed_on must match the linked record's actual day")
    return dict(reference, snapshot=record)


def calendar_summary(conn, unit_id, cycle, records, today):
    tasks = []
    cycle_status = lifecycle(conn, unit_id, cycle["id"])
    counts = dict(planned=0, completed=0, cancelled=0, due=0, overdue=0, upcoming=0, needs_review=0)
    for row in records:
        if row["kind"] != "calendar" or row["superseded"]:
            continue
        data = row["data"]
        status = data["status"]
        changes = [key for key in ("crop", "planted_on") if data["cycle_snapshot"][key] != cycle[key]]
        window, error = data["planned_window"], None
        if status == "planned":
            try:
                window = task_window(data["due"], cycle)
            except ValueError as exc:
                window, error = None, str(exc)
        resolution = data.get("resolution") or {}
        reference = resolution.get("completion_record")
        evidence_state = _evidence(conn, unit_id, cycle["id"], reference)[1] if reference else "not_linked"
        review_reasons = []
        trigger_review = task_triggers.review(conn, unit_id, cycle['id'], data.get('trigger'), data.get('trigger_snapshot'))
        if trigger_review['needs_review']:
            review_reasons.append('task trigger record is '+trigger_review['state'])
        if status == "planned":
            if changes:
                review_reasons.append("crop or planting date changed since this plan was saved")
            if error:
                review_reasons.append(error)
            if (window and cycle_status["status"] == "closed" and window["end"] > cycle_status["ended_on"]
                    and data["activity"] not in ("maintenance", "other")):
                review_reasons.append("crop activity window extends beyond the recorded cycle end")
        if reference is not None and evidence_state != "current":
            review_reasons.append("linked completion record is "+evidence_state)
        needs_review = bool(review_reasons)
        timing = status
        if status == "planned":
            timing = "unknown" if window is None else "upcoming" if today < window["start"] else "overdue" if today > window["end"] else "due"
        counts[status] += 1
        if timing in ("due", "overdue", "upcoming"):
            counts[timing] += 1
        counts["needs_review"] += int(needs_review)
        completed = resolution.get("completed_on")
        completion_timing = None if completed is None else "early" if completed < window["start"] else "late" if completed > window["end"] else "within_window"
        tasks.append(dict(record_id=row["id"], entry=data["entry"], title=data["title"], activity=data["activity"],
            priority=data["priority"], location=data["location"], notes=data["notes"], source=data["source"],
            status=status, timing=timing, original_window=data["planned_window"], effective_window=window,
            cycle_changed_fields=changes, needs_review=needs_review, review_reasons=review_reasons, window_error=error,
            completed_on=completed, completion_timing=completion_timing,
            completion_record={k: reference[k] for k in ("kind", "id")} if reference else None,
            completion_evidence_state=evidence_state, resolution_note=resolution.get("reason"), resolution_source=resolution.get("source")))
        tasks[-1].update(trigger=data.get('trigger'), trigger_review=trigger_review)
    tasks.sort(key=lambda r: ({"planned": 0, "completed": 1, "cancelled": 2}[r["status"]],
                             (r["effective_window"] or {}).get("end", "9999-12-31"),
                             {"high": 0, "normal": 1, "low": 2}[r["priority"]], r["entry"]))
    candidates = []
    for row in records:
        if row["kind"] in EVIDENCE_KINDS.values() and not row["voided"] and not row["superseded"]:
            activity = next(k for k, value in EVIDENCE_KINDS.items() if value == row["kind"])
            candidates.append(dict(kind="crop_record", id=row["id"], activity=activity, day=row["data"]["day"],
                                   label=row["data"].get("entry", row["data"].get("batch"))))
    if tasks:
        rows = conn.execute("SELECT r.payload FROM field_water_days r WHERE r.unit_id=? AND r.cycle_id=? "
                            "AND NOT EXISTS (SELECT 1 FROM field_water_days n WHERE n.supersedes_id=r.id)", (unit_id, cycle["id"]))
        for row in rows:
            record = json.loads(row[0])
            if record["data"]["quality"] != "rejected" and record["data"]["net_irrigation_mm"] > 0:
                candidates.append(dict(kind="water_day", id=record["id"], activity="irrigation", day=record["day"], label=record["day"]))
    return dict(as_of=today, tasks=tasks, counts=counts, completion_candidates=candidates,
        trigger_candidates=task_triggers.candidates(conn, unit_id, cycle['id']),
        note="Operator-supplied work windows, not automatic agronomic recommendations. Planting-relative windows are recalculated for pending tasks; "
             "changed crop/date assumptions require review, with original windows retained. Completed/cancelled tasks keep their saved windows. "
             "Pending crop activity beyond the recorded cycle end is flagged for review. "
             "Completion is an explicit operator report and does not create irrigation, fertilizer, harvest or other quantity records. "
             "Linked evidence retains its exact revision; later correction, voiding or rejection is flagged without rewriting task history. "
             "Task triggers preserve an operator-declared reason and source snapshot; they do not establish cause or treatment suitability. "
             "Changed trigger records require review even after task completion or cancellation. "
             "Calendar tasks can cover preparation or cleanup outside crop dates, do not change crop closure, and send no notifications.")

"""Snapshot equipment calendars for bounded proposals; booking remains explicit."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from agro_model.resource_schedule import propose_schedule, validate_schedule
from agro_model.resource_timing import _bounds
from agro_model.resources import _object
from agro_model.validation import text

from .equipment_calendar import TABLES, _ids, _latest
from .farm_resources import _check_dependency_inputs, _farm, _json, _read, _review, _snapshot
from .resource_ledger import _family, _history


def _inputs(data, parent):
    data = _object(data, ("schedule", "assignments", "source", "replaces_booking_id"), "equipment schedule")
    _check_dependency_inputs(parent, data["schedule"])
    schedule = validate_schedule(data["schedule"], parent["inputs"], parent["result"])
    if not isinstance(data["assignments"], dict) or set(data["assignments"]) != {r["id"] for r in schedule["runs"]}:
        raise ValueError("equipment assignments must cover every run exactly once")
    assignments = {key: _ids(ids) for key, ids in data["assignments"].items()}
    if not any(assignments.values()):
        raise ValueError("select at least one physical item for an equipment-aware proposal")
    replaces = data["replaces_booking_id"]
    if replaces is not None:
        text("replaces_booking_id", replaces)
    return dict(schedule=schedule, assignments=assignments, source=text("equipment source", data["source"]).strip(), replaces_booking_id=replaces)


def _summary(booking, intervals):
    return {key: booking[key] for key in ("id", "root_id", "plan_id", "plan_family_id", "title", "source")} | dict(intervals=intervals)


def _capture(conn, farm, parent, data):
    _snapshot(conn, farm, parent["inputs"])
    if _review(conn, parent)["needs_review"]:
        raise ValueError("plan evidence changed; save a reviewed allocation before equipment-aware scheduling")
    equipment, assignments = {}, {}
    pumps = {r["id"] for r in parent["inputs"]["resources"] if r["kind"] == "pump_time"}
    for run in data["schedule"]["runs"]:
        assets = [_latest(conn, TABLES[0], farm["id"], key) for key in data["assignments"][run["id"]]]
        if any(a["data"]["status"] != "available" for a in assets):
            raise ValueError("selected equipment is unavailable; review its current revision")
        if sum(a["data"]["kind"] == "pump" for a in assets) != sum(run["concurrent"][key] for key in pumps):
            raise ValueError("each run requires exactly its concurrent count of distinct physical pumps")
        assignments[run["id"]] = sorted(a["root_id"] for a in assets)
        for a in assets:
            equipment[a["root_id"]] = {key: a[key] for key in ("id", "root_id", "data", "created_at")}
    family = _family(conn, farm["id"], parent)
    active = [r for r in _history(conn, TABLES[1], farm["id"]) if not r["superseded"] and r["status"] == "active"]
    excluded = None
    if data["replaces_booking_id"] is not None:
        excluded = _latest(conn, TABLES[1], farm["id"], data["replaces_booking_id"])
        if excluded["status"] != "active" or excluded["plan_family_id"] != family:
            raise ValueError("only the current active booking for this plan family may be excluded for replacement")
    elif any(r["plan_family_id"] == family for r in active):
        raise ValueError("this plan family already has a booking; explicitly select its current booking for replacement")
    zone, begin, finish = _bounds(parent["inputs"]["period"])
    bookings, busy = [], []
    for booking in active:
        if excluded and booking["id"] == excluded["id"]:
            continue
        relevant = []
        for index, interval in enumerate(booking["intervals"]):
            if interval["equipment_root_id"] not in equipment:
                continue
            start, end = datetime.fromisoformat(interval["start"]), datetime.fromisoformat(interval["end"])
            if start < finish and begin < end:
                relevant.append(interval)
                busy.append(dict(id=f"{booking['id']}:{index}", equipment_id=interval["equipment_root_id"],
                    start=max(start, begin).astimezone(zone).isoformat(), end=min(end, finish).astimezone(zone).isoformat(), source=booking["source"]))
        if relevant:
            bookings.append(_summary(booking, relevant))
    snapshot = dict(equipment=equipment, bookings=bookings, excluded_booking=_summary(excluded, excluded["intervals"]) if excluded else None)
    return snapshot, dict(assignments=assignments, busy=busy, source=data["source"], not_before=None)


def review_equipment_schedule(conn, farm, record):
    try:
        current, _ = _capture(conn, farm, record, record["equipment_schedule"]["inputs"])
        changed = current != record["equipment_schedule"]["snapshot"]
        issues = ["selected equipment or relevant bookings changed"] if changed else []
    except ValueError as exc:
        changed, issues = True, [str(exc)]
    return dict(changed=changed, issues=issues,
                note="This proposal used the saved calendar snapshot. Current equipment booking checks still apply; this review does not release or change any existing booking.")


class EquipmentScheduling:
    def __init__(self, db):
        self.db = db

    def equipment_schedule_inputs(self, owner, farm_id, plan_id):
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            parent = _read(conn, farm_id, plan_id)
            family = _family(conn, farm_id, parent)
            return dict(equipment=[r for r in _history(conn, TABLES[0], farm_id) if not r["superseded"]],
                family_bookings=[_summary(r, r["intervals"]) for r in _history(conn, TABLES[1], farm_id)
                                 if not r["superseded"] and r["status"] == "active" and r["plan_family_id"] == family])

    def propose_equipment_schedule(self, owner, farm_id, plan_id, data):
        return self._search(owner, farm_id, plan_id, data)

    def save_equipment_schedule(self, owner, farm_id, plan_id, data, *, request_id):
        text("request_id", request_id)
        return self._search(owner, farm_id, plan_id, data, request_id=request_id)

    def _search(self, owner, farm_id, plan_id, data, request_id=None):
        def retry(conn, operation):
            if request_id is None:
                return None
            row = conn.execute("SELECT payload FROM farm_resource_plans WHERE farm_id=? AND request_id=?", (farm_id, request_id)).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved["operation"] != operation:
                    raise ValueError("request_id already used with different data")
                return saved

        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            parent = _read(conn, farm_id, plan_id)
            inputs = _inputs(data, parent)
            operation = dict(kind="save_equipment_schedule", plan_id=plan_id, data=inputs)
            if previous := retry(conn, operation):
                return previous
            snapshot, constraints = _capture(conn, farm, parent, inputs)
        zone, _, _ = _bounds(parent["inputs"]["period"])
        now = datetime.now(timezone.utc)
        not_before = now.replace(microsecond=0) + (timedelta(seconds=1) if now.microsecond else timedelta())
        constraints["not_before"] = not_before.astimezone(zone).isoformat()
        result = propose_schedule(dict(inputs["schedule"], equipment=constraints), parent["inputs"], parent["result"])
        context = dict(inputs=inputs, snapshot=snapshot, constraints=constraints,
                       note="Fixed equipment assignments checked against a calendar snapshot. Saving is not a booking. Review equipment suitability and explicitly book; current conflicts and revisions will be checked again.")
        # Search may take time: never hold a writer transaction or trust stale ownership/calendar state.
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            _read(conn, farm_id, plan_id)
            if previous := retry(conn, operation):
                return previous
            current, _ = _capture(conn, farm, parent, inputs)
            if current != snapshot:
                raise ValueError("equipment calendar changed during search; refresh the assignments and search again")
            if request_id is None:
                return dict(parent_id=plan_id, schedule_inputs=inputs["schedule"], equipment_schedule=context, **result)
            saved = dict(id=str(uuid.uuid4()), farm_id=farm_id, owner_id=owner, request_id=request_id,
                parent_id=plan_id, created_at=datetime.now(timezone.utc).isoformat(), inputs=parent["inputs"], result=parent["result"],
                snapshot=parent["snapshot"], operation=operation, schedule_inputs=inputs["schedule"], equipment_schedule=context, **result)
            conn.execute("INSERT INTO farm_resource_plans(id,farm_id,request_id,parent_id,created_at,payload) VALUES(?,?,?,?,?,?)",
                         (saved["id"], farm_id, request_id, plan_id, saved["created_at"], _json(saved)))
            return saved

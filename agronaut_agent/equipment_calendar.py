"""Owned physical equipment and atomic, explicit bookings across farm scenarios."""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.resource_timing import _instant, check_timing
from agro_model.resources import _object
from agro_model.validation import choice, text

from .farm_resources import _farm, _read, _review, _snapshot
from .resource_ledger import _family, _history, _insert, _retry

TABLES = ("farm_equipment", "farm_equipment_bookings")
KINDS = ("pump", "tractor", "implement", "vehicle", "other")
NOTE = (
    "Each equipment key identifies one physical item in this farm. Register it once; aliases cannot be detected. "
    "Bookings hold its complete declared intervals, with touching endpoints allowed. Enter outside work, setup, "
    "travel and maintenance as separate calendar blocks when needed. Pump counts from a timed plan must be fully "
    "assigned; equipment compatibility, hydraulics, operators and labor qualifications are not inferred. "
    "This calendar does not reserve budget quantities or operate equipment. Budget commitments, recorded use and "
    "equipment bookings are separate explicit actions. Releasing a booking does not change budget charges or actuals."
)


def migrate_equipment_calendar(conn):
    for table in TABLES:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
            id TEXT PRIMARY KEY, farm_id TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL, supersedes_id TEXT UNIQUE REFERENCES {table}(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(farm_id,request_id))""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_farm ON {table}(farm_id)")


def _latest(conn, table, farm_id, key):
    text("equipment or booking revision ID", key)
    row = conn.execute(f"SELECT payload FROM {table} WHERE id=? AND farm_id=?", (key, farm_id)).fetchone()
    if row is None:
        raise ValueError("unknown equipment or booking for this farm")
    if conn.execute(f"SELECT 1 FROM {table} WHERE supersedes_id=?", (key,)).fetchone():
        raise ValueError("revision already superseded; read the current equipment calendar")
    return json.loads(row[0])


def equipment_data(value):
    data = dict(_object(value, ("equipment_key", "name", "kind", "status", "source"), "equipment"))
    for key in ("equipment_key", "name", "source"):
        data[key] = text(key, data[key]).strip()
    data["equipment_key"] = data["equipment_key"].lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", data["equipment_key"]):
        raise ValueError("equipment_key requires 1–80 ASCII letters, numbers, dots, underscores or hyphens")
    choice("equipment kind", data["kind"], KINDS)
    choice("equipment status", data["status"], ("available", "unavailable"))
    return data


def _ids(value):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or not v for v in value):
        raise ValueError("equipment IDs must be a list of at most 100 current revision IDs")
    if len(set(value)) != len(value):
        raise ValueError("equipment IDs must be distinct within each work interval")
    return sorted(value)


def _timed_plan(conn, farm, plan_id):
    plan = _read(conn, farm["id"], plan_id)
    _snapshot(conn, farm, plan["inputs"])
    if _review(conn, plan)["needs_review"]:
        raise ValueError("plan evidence changed; save and check a reviewed scenario before booking equipment")
    if "timing_inputs" not in plan or not check_timing(plan["timing_inputs"], plan["inputs"], plan["result"])["declared_checks_pass"]:
        raise ValueError("equipment booking requires a saved plan with passing timing checks")
    if "schedule_result" in plan and plan["schedule_result"]["status"] != "feasible_schedule_found":
        raise ValueError("the plan has no feasible saved schedule")
    pumps = {r["id"] for r in plan["inputs"]["resources"] if r["kind"] == "pump_time"}
    work = [dict(id=w["id"], job_id=w["job_id"], start=w["start"], end=w["end"],
                 pump_count=sum(w["concurrent"][key] for key in pumps)) for w in plan["timing_inputs"]["work"]]
    return plan, work


def _interval(start, end, zone):
    start, end = _instant(start, zone), _instant(end, zone)
    if start >= end:
        raise ValueError("equipment interval must have positive duration")
    if end <= datetime.now(timezone.utc):
        raise ValueError("cannot book an equipment interval that has already ended")
    return start.isoformat(), end.isoformat()


def _conflicts(intervals, active):
    occupied = {}
    for booking in active:
        for row in booking["intervals"]:
            occupied.setdefault(row["equipment_root_id"], []).append((row, booking["id"]))
    for row in intervals:
        for other, booking_id in occupied.get(row["equipment_root_id"], []):
            # Stored instants have a single UTC format, so lexical ordering is exact.
            if row["start"] < other["end"] and other["start"] < row["end"]:
                raise ValueError(f"equipment conflict for {row['equipment_key']} with {booking_id}: {other['start']} to {other['end']}")
        occupied.setdefault(row["equipment_root_id"], []).append((row, "another interval in this request"))


class EquipmentCalendar:
    def __init__(self, db):
        self.db = db

    def save_equipment(self, owner, farm_id, data, *, request_id, supersedes_id=None, reason=None):
        data = equipment_data(data)
        if reason is not None:
            reason = text("reason", reason).strip()
        operation = dict(kind="save_equipment", data=data, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[0], farm_id, request_id, operation):
                return previous
            prior = _latest(conn, TABLES[0], farm_id, supersedes_id) if supersedes_id is not None else None
            if prior:
                if reason is None:
                    raise ValueError("equipment corrections require a reason")
                if any(data[k] != prior["data"][k] for k in ("equipment_key", "kind")):
                    raise ValueError("equipment corrections must preserve identity and kind")
            elif any(r["data"]["equipment_key"] == data["equipment_key"] for r in _history(conn, TABLES[0], farm_id)):
                raise ValueError("equipment key already exists; correct its current revision")
            return _insert(conn, TABLES[0], owner, farm_id, request_id, operation, supersedes_id,
                           root_id=prior["root_id"] if prior else None, data=data, reason=reason)

    def equipment_inputs(self, owner, farm_id, plan_id):
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            plan, work = _timed_plan(conn, farm, plan_id)
            return dict(plan_id=plan_id, title=plan["inputs"]["title"], period=plan["inputs"]["period"], work=work,
                        suggested_assignments=plan.get("equipment_schedule", {}).get("inputs", {}).get("assignments", {}),
                        all_requested_blocks_allocated=plan["result"]["all_requested_blocks_allocated"], note=NOTE)

    def book_equipment(self, owner, farm_id, plan_id, assignments, source, accept_partial, *, request_id, supersedes_id=None, reason=None):
        if not isinstance(assignments, dict) or len(assignments) > 1000 or any(not isinstance(k, str) for k in assignments):
            raise ValueError("assignments must map every timing work ID to a list of equipment revision IDs")
        assignments = {key: _ids(value) for key, value in assignments.items()}
        if type(accept_partial) is not bool:
            raise ValueError("accept_partial must be an explicit boolean")
        source = text("source", source).strip()
        if reason is not None:
            reason = text("reason", reason).strip()
        operation = dict(kind="book_equipment", plan_id=plan_id, assignments=assignments, source=source,
                         accept_partial=accept_partial, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[1], farm_id, request_id, operation):
                return previous
            plan, work = _timed_plan(conn, farm, plan_id)
            if not plan["result"]["all_requested_blocks_allocated"] and not accept_partial:
                raise ValueError("explicitly accept_partial to book only the allocated work")
            if set(assignments) != {w["id"] for w in work}:
                raise ValueError("assignments must include every timing work ID exactly once; use [] for no equipment")
            snapshots, intervals = {}, []
            for w in work:
                assets = [_latest(conn, TABLES[0], farm_id, key) for key in assignments[w["id"]]]
                if sum(a["data"]["kind"] == "pump" for a in assets) != w["pump_count"]:
                    raise ValueError("assign distinct physical pumps for exactly the concurrent pump count of each work interval")
                start, end = _interval(w["start"], w["end"], ZoneInfo(farm["timezone"]))
                for asset in assets:
                    if asset["data"]["status"] != "available":
                        raise ValueError("equipment is unavailable for work; review its current status")
                    snapshots[asset["root_id"]] = asset
                    intervals.append(dict(equipment_root_id=asset["root_id"], equipment_key=asset["data"]["equipment_key"],
                        work_id=w["id"], job_id=w["job_id"], start=start, end=end))
            return self._book(conn, owner, farm, request_id, operation, supersedes_id, reason, source,
                              snapshots, intervals, plan, plan["inputs"]["title"])

    def reserve_equipment(self, owner, farm_id, data, *, request_id, supersedes_id=None, reason=None):
        data = dict(_object(data, ("equipment_ids", "start", "end", "title", "source"), "equipment calendar block"))
        data["equipment_ids"] = _ids(data["equipment_ids"])
        for key in ("title", "source"):
            data[key] = text(key, data[key]).strip()
        if reason is not None:
            reason = text("reason", reason).strip()
        operation = dict(kind="reserve_equipment", data=data, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[1], farm_id, request_id, operation):
                return previous
            if not farm["timezone"]:
                raise ValueError("a known farm timezone is required for equipment bookings")
            start, end = _interval(data["start"], data["end"], ZoneInfo(farm["timezone"]))
            assets = [_latest(conn, TABLES[0], farm_id, key) for key in data["equipment_ids"]]
            snapshots = {a["root_id"]: a for a in assets}
            intervals = [dict(equipment_root_id=a["root_id"], equipment_key=a["data"]["equipment_key"],
                              work_id=None, job_id=None, start=start, end=end) for a in assets]
            return self._book(conn, owner, farm, request_id, operation, supersedes_id, reason, data["source"],
                              snapshots, intervals, None, data["title"])

    def _book(self, conn, owner, farm, request_id, operation, supersedes_id, reason, source, snapshots, intervals, plan, title):
        if not intervals:
            raise ValueError("select at least one physical item of equipment to book")
        prior = _latest(conn, TABLES[1], farm["id"], supersedes_id) if supersedes_id is not None else None
        if prior and reason is None:
            raise ValueError("replacement bookings require a reason")
        active = [r for r in _history(conn, TABLES[1], farm["id"]) if not r["superseded"] and r["status"] == "active"
                  and (not prior or r["root_id"] != prior["root_id"])]
        family = _family(conn, farm["id"], plan) if plan else None
        if family and any(r["plan_family_id"] == family for r in active):
            raise ValueError("this plan family already has an equipment booking; replace its current booking")
        _conflicts(intervals, active)
        return _insert(conn, TABLES[1], owner, farm["id"], request_id, operation, supersedes_id,
            root_id=prior["root_id"] if prior else None, status="active", reason=reason, source=source,
            title=title, timezone=farm["timezone"], plan_id=plan["id"] if plan else None, plan_family_id=family,
            plan_snapshot=plan, equipment_snapshots=snapshots, intervals=intervals)

    def release_equipment(self, owner, farm_id, booking_id, source, reason, *, request_id):
        source, reason = text("source", source).strip(), text("reason", reason).strip()
        operation = dict(kind="release_equipment", booking_id=booking_id, source=source, reason=reason)
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[1], farm_id, request_id, operation):
                return previous
            prior = _latest(conn, TABLES[1], farm_id, booking_id)
            if prior["status"] != "active":
                raise ValueError("only an active equipment booking can be released")
            payload = {k: prior[k] for k in ("root_id", "title", "timezone", "plan_id", "plan_family_id", "plan_snapshot", "equipment_snapshots", "intervals")}
            return _insert(conn, TABLES[1], owner, farm_id, request_id, operation, booking_id,
                           **payload, status="released", source=source, reason=reason)

    def equipment_calendar(self, owner, farm_id):
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            return calendar_snapshot(conn, farm)


def calendar_snapshot(conn, farm):
    """Read the equipment view within an already scoped transaction."""
    farm_id = farm["id"]
    history = _history(conn, TABLES[0], farm_id)
    current = {r["root_id"]: r for r in history if not r["superseded"]}
    bookings = _history(conn, TABLES[1], farm_id)
    for row in bookings:
        changed = [root for root, old in row["equipment_snapshots"].items()
                   if root not in current or current[root]["id"] != old["id"]]
        unavailable = [root for root in row["equipment_snapshots"] if root in current and current[root]["data"]["status"] != "available"]
        plan_review = _review(conn, row["plan_snapshot"]) if row["plan_snapshot"] else None
        row["review"] = dict(equipment_changed=changed, unavailable_equipment=unavailable,
            timezone_changed=farm["timezone"] != row["timezone"], plan=plan_review,
            needs_review=bool(changed or unavailable or farm["timezone"] != row["timezone"] or (plan_review and plan_review["needs_review"])))
    return dict(equipment=list(current.values()), equipment_history=history, bookings=bookings,
                active=[r for r in bookings if not r["superseded"] and r["status"] == "active"], note=NOTE)

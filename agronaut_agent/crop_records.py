"""Owned crop journals with immutable corrections and explicit entry identities."""

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from agro_model.farm import OBSERVATION_UNITS, Observation, ProductionCycle
from agro_model.validation import choice, number, text

from .cash_records import cash_data, cash_totals
from .crop_calendar import (
    calendar_summary,
    completion_evidence,
    evidence_reference,
    task_plan,
    task_window,
)
from .crop_establishment import ensure_planting_consistency, planting_state
from .cycle_lifecycle import ensure_cycle_day, latest_actual_day, lifecycle
from .farm_store import _owned_unit, record_observation_in_transaction
from .fertilizer_records import application_data, application_totals
from .field_io import _date, _mapping, json_ready
from .harvest_reviews import NOTE as HARVEST_REVIEW_NOTE
from .harvest_reviews import HarvestReviews, harvest_review_summary
from .nutrition_io import nutrition_input_schema
from .observation_quality import ObservationReviews, reviewed_observation

SCHEMA = (
    """CREATE TABLE crop_records (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        cycle_id TEXT NOT NULL REFERENCES production_cycles(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, kind TEXT NOT NULL, batch TEXT,
        supersedes_id TEXT UNIQUE REFERENCES crop_records(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(unit_id,request_id))""",
    "CREATE INDEX idx_crop_records_cycle ON crop_records(unit_id,cycle_id,batch)",
)


def migrate_crop_records(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _scope(conn, owner, unit_id, cycle_id=None):
    text("owner", owner)
    text("unit_id", unit_id)
    unit = _owned_unit(conn, owner, unit_id)
    choice("production system", unit["production_system"], ("soil", "hydroponics", "aquaponics"))
    tz = conn.execute("SELECT timezone FROM farms WHERE id=?", (unit["farm_id"],)).fetchone()[0]
    if not tz:
        raise ValueError("crop records require a farm timezone")
    cycle = None
    if cycle_id is not None:
        text("cycle_id", cycle_id)
        row = conn.execute("SELECT * FROM production_cycles WHERE id=? AND unit_id=?", (cycle_id, unit_id)).fetchone()
        if row is None:
            raise ValueError("crop cycle does not belong to the selected unit")
        cycle = dict(row)
    return tz, cycle


def _retry(conn, unit_id, request_id, operation):
    text("request_id", request_id)
    row = conn.execute("SELECT payload FROM crop_records WHERE unit_id=? AND request_id=?", (unit_id, request_id)).fetchone()
    if row:
        saved = json.loads(row[0])
        if _json(saved["operation"]) != _json(operation):
            raise ValueError("request_id already used with different data")
        return saved


def _read(conn, unit_id, record_id):
    text("record_id", record_id)
    row = conn.execute("SELECT payload FROM crop_records WHERE id=? AND unit_id=?", (record_id, unit_id)).fetchone()
    if row is None:
        raise ValueError("unknown crop record for the selected unit")
    return json.loads(row[0])


def _latest(conn, prior, kinds=("harvest",)):
    if conn.execute("SELECT 1 FROM crop_records WHERE supersedes_id=?", (prior["id"],)).fetchone():
        raise ValueError("record already superseded; use its latest revision")
    if prior["kind"] not in kinds:
        raise ValueError(f"record kind must be one of {', '.join(kinds)}")


def _save(conn, owner, unit_id, cycle_id, request_id, kind, data, operation,
          *, supersedes_id=None, voided=False, reason=None, accounting=None, previous_cycle=None):
    saved = dict(id=uuid.uuid4().hex, owner_id=owner, unit_id=unit_id, cycle_id=cycle_id,
                 request_id=request_id, kind=kind, data=data, operation=operation,
                 supersedes_id=supersedes_id, voided=voided, reason=reason,
                 created_at=datetime.now(timezone.utc).isoformat())
    if previous_cycle is not None:
        saved["previous_cycle"] = previous_cycle
    if accounting is not None:
        saved["accounting"] = accounting
    conn.execute("INSERT INTO crop_records VALUES(?,?,?,?,?,?,?,?,?)",
                 (saved["id"], unit_id, cycle_id, request_id, kind, data.get("batch", data.get("entry")),
                  supersedes_id, saved["created_at"], _json(saved)))
    return saved


def _totals(records):
    groups = {}
    factors = {"kg": Decimal(1), "g": Decimal("0.001"), "t": Decimal(1000), "count": Decimal(1)}
    for record in records:
        if record["kind"] != "harvest" or record["voided"] or record["superseded"]:
            continue
        data = record["data"]
        unit = "count" if data["unit"] == "count" else "kg"
        # Never combine fresh/dry mass, products, grades or measured/estimated amounts.
        key = (data["product"], data["basis"], data["grade"], data["provenance"], unit)
        amount, batches = groups.get(key, (Decimal(0), 0))
        groups[key] = (amount + Decimal(str(data["quantity"]))*factors[data["unit"]], batches+1)
    return [dict(zip(("product", "basis", "grade", "provenance", "unit"), key),
                 quantity=format(amount, "f"), batches=batches)
            for key, (amount, batches) in sorted(groups.items())]


def record_crop_observation(conn, owner, unit_id, cycle_id, kind, data, request_id, supersedes_id=None, *, intake=None):
    """Validate and append one journal observation inside the caller's transaction."""
    text("cycle_id", cycle_id)
    fields = {"stage": ("stage",), "scouting": ("symptoms", "inspected_count", "affected_count"),
              "transplant": ("origin", "plant_count")}[kind]
    data = _mapping(data, ("entry", "day", "location", "source", "provenance", *fields), kind)
    data = json.loads(_json(data))
    for key in ("entry", "location", "source", {"stage": "stage", "scouting": "symptoms", "transplant": "origin"}[kind]):
        data[key] = text(key, data.get(key)).strip()
    day = _date(data.get("day"))
    choice("provenance", data.get("provenance"), ("measured", "reported", "estimated") if kind == "transplant" else ("observed", "reported"))
    if kind == "transplant":
        if "plant_count" not in data:
            raise ValueError("plant_count is required; use null when unknown")
        count = data["plant_count"]
        if count is not None and (type(count) is not int or not 1 <= count <= 1_000_000_000):
            raise ValueError("plant_count must be a positive integer up to one billion or null")
    if kind == "scouting":
        for key in ("inspected_count", "affected_count"):
            if key not in data:
                raise ValueError(f"{key} is required; use null when counts are unknown")
            value = data[key]
            if value is not None and (type(value) is not int or value < 0 or value > 1_000_000_000):
                raise ValueError(f"{key} must be a nonnegative integer or null")
        inspected, affected = data["inspected_count"], data["affected_count"]
        if (inspected is None) != (affected is None):
            raise ValueError("provide both sample counts or leave both null")
        if inspected is not None and (inspected == 0 or affected > inspected):
            raise ValueError("inspected_count must be positive and affected_count cannot exceed it")
    operation = dict(kind=f"record_{kind}", cycle_id=cycle_id, data=data, supersedes_id=supersedes_id)
    if intake is not None:
        operation["intake"] = intake
    tz, cycle = _scope(conn, owner, unit_id, cycle_id)
    existing = _retry(conn, unit_id, request_id, operation)
    if existing:
        return existing
    if day < _date(cycle["planted_on"]) or day > datetime.now(ZoneInfo(tz)).date():
        raise ValueError("observation date must fall between planting and today in the farm timezone")
    ensure_cycle_day(conn, unit_id, cycle_id, data["day"])
    if supersedes_id is not None:
        prior = _read(conn, unit_id, supersedes_id)
        _latest(conn, prior, (kind,))
        if prior["cycle_id"] != cycle_id or prior["data"]["entry"] != data["entry"]:
            raise ValueError("correction must preserve the cycle and entry ID; void a wrongly assigned entry instead")
    elif conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind=? AND batch=?",
                      (unit_id, cycle_id, kind, data["entry"])).fetchone():
        raise ValueError("entry already exists; correct its latest revision with supersedes_id")
    return _save(conn, owner, unit_id, cycle_id, request_id, kind, data, operation, supersedes_id=supersedes_id)


def create_planting(conn, owner, unit_id, crop, planted_on, source, request_id, *, intake=None):
    """Create a cycle and its source record in the caller's transaction."""
    data = dict(crop=text("crop", crop).strip(), planted_on=planted_on, source=text("source", source).strip())
    day = _date(planted_on, "planted_on")
    operation = dict(kind="create", data=data)
    if intake is not None:
        operation["intake"] = intake
    tz, _ = _scope(conn, owner, unit_id)
    existing = _retry(conn, unit_id, request_id, operation)
    if existing:
        _scope(conn, owner, unit_id, existing["cycle_id"])
        original = dict(id=existing["cycle_id"], unit_id=unit_id,
                        crop=existing["data"]["crop"], planted_on=existing["data"]["planted_on"])
        return dict(cycle=original, record=existing)
    if day > datetime.now(ZoneInfo(tz)).date():
        raise ValueError("planted_on records actual planting; future plans are not supported here")
    cycle = ProductionCycle(uuid.uuid4().hex, unit_id, data["crop"], planted_on)
    conn.execute("INSERT INTO production_cycles VALUES(?,?,?,?)", (cycle.id, unit_id, cycle.crop, planted_on))
    record = _save(conn, owner, unit_id, cycle.id, request_id, "planting", data, operation)
    return dict(cycle=asdict(cycle), record=record)


def record_measurement(conn, owner, unit_id, data, *, request_id, cycle_id=None, validate_only=False):
    """Validate an immutable reading and optionally write it inside the caller's transaction."""
    text("request_id", request_id)
    data = _mapping(data, ("quantity", "value", "unit", "observed_at", "source", "provenance"), "measurement")
    text("observed_at", data.get("observed_at"))
    # Namespaced deterministic IDs give retries their original recording timestamp,
    # while the shared immutable observation store rejects conflicting payloads.
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["agronaut-measurement", unit_id, request_id])).hex
    _scope(conn, owner, unit_id, cycle_id)
    prior = conn.execute("SELECT payload FROM field_observations WHERE id=? AND unit_id=?",
                         (identifier, unit_id)).fetchone()
    recorded_at = datetime.fromisoformat(json.loads(prior[0])["recorded_at"]) if prior else datetime.now(timezone.utc)
    original = Observation(identifier, owner, unit_id, cycle_id, data.get("quantity"), data.get("value"),
        data.get("unit"), datetime.fromisoformat(data["observed_at"]), recorded_at,
        data.get("provenance"), data.get("source"), "unverified")
    record_observation_in_transaction(conn, owner, original, validate_only=validate_only)
    return json_ready(asdict(original))


def record_harvest(conn, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None, intake=None):
    text("cycle_id", cycle_id)
    data = _mapping(data, ("batch", "day", "quantity", "unit", "product", "basis", "grade", "source", "provenance"), "harvest")
    data = json.loads(_json(data))
    for key in ("batch", "product", "grade", "source"):
        data[key] = text(key, data.get(key)).strip()
    day = _date(data.get("day"))
    amount = number("quantity", data.get("quantity"), high=1e12, positive=True)
    choice("unit", data.get("unit"), ("kg", "g", "t", "count"))
    choice("basis", data.get("basis"), ("fresh", "dry", "as_harvested"))
    choice("provenance", data.get("provenance"), ("measured", "reported", "estimated"))
    if data["unit"] == "count" and not amount.is_integer():
        raise ValueError("count quantity must be a whole number")
    operation = dict(kind="record_harvest", cycle_id=cycle_id, data=data, supersedes_id=supersedes_id)
    if intake is not None:
        operation["intake"] = intake
    tz, cycle = _scope(conn, owner, unit_id, cycle_id)
    existing = _retry(conn, unit_id, request_id, operation)
    if existing:
        return existing
    if day < _date(cycle["planted_on"]) or day > datetime.now(ZoneInfo(tz)).date():
        raise ValueError("harvest date must fall between planting and today in the farm timezone")
    ensure_cycle_day(conn, unit_id, cycle_id, data["day"])
    if supersedes_id is not None:
        prior = _read(conn, unit_id, supersedes_id)
        _latest(conn, prior)
        if prior["cycle_id"] != cycle_id or prior["data"]["batch"] != data["batch"]:
            raise ValueError("correction must preserve the cycle and batch; void a wrongly assigned batch instead")
    elif conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND batch=? AND kind='harvest'",
                      (unit_id, cycle_id, data["batch"])).fetchone():
        raise ValueError("batch already exists; correct its latest revision with supersedes_id")
    return _save(conn, owner, unit_id, cycle_id, request_id, "harvest", data, operation, supersedes_id=supersedes_id)


def plan_task(conn, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None, intake=None):
    from . import task_triggers
    text("cycle_id", cycle_id)
    plan = task_plan(data)
    operation = dict(kind="plan_task", cycle_id=cycle_id, data=plan, supersedes_id=supersedes_id)
    if intake is not None:
        operation["intake"] = intake
    _, cycle = _scope(conn, owner, unit_id, cycle_id)
    existing = _retry(conn, unit_id, request_id, operation)
    if existing:
        return existing
    if supersedes_id is not None:
        prior = _read(conn, unit_id, supersedes_id)
        _latest(conn, prior, ("calendar",))
        if prior["cycle_id"] != cycle_id or prior["data"]["entry"] != plan["entry"]:
            raise ValueError("task correction must preserve the cycle and entry ID; cancel a wrongly assigned task instead")
        if prior["data"]["status"] != "planned":
            raise ValueError("reopen a completed or cancelled task before changing its plan")
        if prior['data'].get('trigger') is not None and 'trigger' not in plan:
            raise ValueError('Task revision must explicitly retain/replace trigger or set trigger to null to remove it.')
    elif conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind='calendar' AND batch=?",
                      (unit_id, cycle_id, plan["entry"])).fetchone():
        raise ValueError("task entry already exists; use its latest revision")
    data = dict(plan, status="planned", planned_window=task_window(plan["due"], cycle), cycle_snapshot=cycle, resolution=None)
    if plan.get('trigger') is not None:
        data['trigger_snapshot'] = task_triggers.adopt(conn, unit_id, cycle_id, plan['trigger'])
    return _save(conn, owner, unit_id, cycle_id, request_id, "calendar", data, operation, supersedes_id=supersedes_id)



def resolve_task(conn, owner, unit_id, record_id, status, reason, source, *, request_id, completed_on=None, completion_record=None, intake=None):
    choice("task status", status, ("planned", "completed", "cancelled"))
    reason, source = text("reason", reason).strip(), text("source", source).strip()
    reference = evidence_reference(completion_record)
    if status == "completed":
        _date(completed_on, "completed_on")
    elif completed_on is not None or reference is not None:
        raise ValueError("only completed tasks may supply completed_on or completion_record")
    operation = dict(kind="resolve_task", record_id=record_id, status=status, reason=reason, source=source,
                     completed_on=completed_on, completion_record=reference)
    if intake is not None:
        operation["intake"] = intake
    _scope(conn, owner, unit_id)
    existing = _retry(conn, unit_id, request_id, operation)
    if existing:
        return existing
    prior = _read(conn, unit_id, record_id)
    _latest(conn, prior, ("calendar",))
    tz, _ = _scope(conn, owner, unit_id, prior["cycle_id"])
    if status == "planned" and prior["data"]["status"] == "planned":
        raise ValueError("task is already planned; use plan_task to revise its details")
    if status == "completed" and _date(completed_on) > datetime.now(ZoneInfo(tz)).date():
        raise ValueError("task completion must be on or before today in the farm timezone")
    evidence = completion_evidence(conn, unit_id, prior["cycle_id"], prior["data"]["activity"], completed_on, reference)
    resolution = dict(reason=reason, source=source, completed_on=completed_on, completion_record=evidence)
    data = dict(prior["data"], status=status, resolution=resolution)
    return _save(conn, owner, unit_id, prior["cycle_id"], request_id, "calendar", data, operation,
                 supersedes_id=record_id, reason=reason)



class CropRecords:
    def __init__(self, db):
        self.db = db

    def create(self, owner, unit_id, crop, planted_on, source, *, request_id):
        with self.db.transaction() as conn:
            return create_planting(conn, owner, unit_id, crop, planted_on, source, request_id)

    def correct_planting(self, owner, unit_id, cycle_id, crop, planted_on, source, reason, *, request_id, expected_revision):
        text("cycle_id", cycle_id)
        if expected_revision is not None:
            text("expected_revision", expected_revision)
        day = _date(planted_on, "planted_on")
        data = dict(crop=text("crop", crop).strip(), planted_on=planted_on, source=text("source", source).strip())
        reason = text("reason", reason).strip()
        operation = dict(kind="correct_planting", cycle_id=cycle_id, data=data, reason=reason, expected_revision=expected_revision)
        with self.db.transaction() as conn:
            tz, cycle = _scope(conn, owner, unit_id, cycle_id)
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            current = planting_state(conn, unit_id, cycle_id)
            if expected_revision != current["revision_id"]:
                raise ValueError("planting changed; read get.planting.revision_id and use it as expected_revision")
            if day > datetime.now(ZoneInfo(tz)).date():
                raise ValueError("planting corrections record actual planting, not future plans")
            ensure_planting_consistency(conn, unit_id, cycle_id, planted_on)
            record = _save(conn, owner, unit_id, cycle_id, request_id, "planting", data, operation,
                           supersedes_id=expected_revision, reason=reason, previous_cycle=cycle)
            conn.execute("UPDATE production_cycles SET crop=?,planted_on=? WHERE id=? AND unit_id=?",
                         (data["crop"], planted_on, cycle_id, unit_id))
            return record

    def list(self, owner, unit_id):
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            return dict(cycles=[dict(r, lifecycle=lifecycle(conn, unit_id, r["id"])) for r in conn.execute(
                "SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id", (unit_id,))])

    def get(self, owner, unit_id, cycle_id):
        text("cycle_id", cycle_id)
        with self.db.transaction() as conn:
            tz, cycle = _scope(conn, owner, unit_id, cycle_id)
            return self._journal(conn, unit_id, cycle, datetime.now(ZoneInfo(tz)).date().isoformat())

    def _journal(self, conn, unit_id, cycle, today):
        records = [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM crop_records WHERE unit_id=? AND cycle_id=? ORDER BY rowid", (unit_id, cycle["id"]))]
        superseded = {r["supersedes_id"] for r in records if r["supersedes_id"]}
        records = [dict(r, superseded=r["id"] in superseded) for r in records]
        stages = [r for r in records if r["kind"] == "stage" and not r["voided"] and not r["superseded"]]
        latest_day = max((r["data"]["day"] for r in stages), default=None)
        state = lifecycle(conn, unit_id, cycle["id"])
        tz = conn.execute("SELECT f.timezone FROM farms f JOIN production_units u ON u.farm_id=f.id WHERE u.id=?", (unit_id,)).fetchone()[0]
        return dict(cycle=cycle, planting=planting_state(conn, unit_id, cycle["id"]),
                    lifecycle=state, records=records, harvest_totals=_totals(records),
                    harvest_reviews=harvest_review_summary(records, cycle, tz, state),
                    measurements=self._measurements(conn, unit_id, cycle["id"]),
                    fertilizer=application_totals(records), cash=cash_totals(records),
                    calendar=calendar_summary(conn, unit_id, cycle, records, today),
                    latest_stage_reports=[r for r in stages if r["data"]["day"] == latest_day])

    def season_review(self, owner, unit_id, cycle_id, calculation_id=None):
        from .season_review import build_season_review

        text("cycle_id", cycle_id)
        if calculation_id is not None:
            text("calculation_id", calculation_id)
        with self.db.transaction() as conn:
            tz, cycle = _scope(conn, owner, unit_id, cycle_id)
            now = datetime.now(ZoneInfo(tz))
            journal = self._journal(conn, unit_id, cycle, now.date().isoformat())
            return build_season_review(conn, unit_id, journal, tz, calculation_id, now)

    def compare_seasons(self, owner, unit_id, cycle_ids):
        from .season_review import build_season_review, compare_reviews

        if not isinstance(cycle_ids, list) or not 2 <= len(cycle_ids) <= 10:
            raise ValueError("select between 2 and 10 crop cycle IDs")
        for cycle_id in cycle_ids:
            text("cycle_id", cycle_id)
        if len(set(cycle_ids)) != len(cycle_ids):
            raise ValueError("select distinct crop cycle IDs")
        with self.db.transaction() as conn:
            tz, _ = _scope(conn, owner, unit_id)
            cycles = [_scope(conn, owner, unit_id, cycle_id)[1] for cycle_id in cycle_ids]
            now = datetime.now(ZoneInfo(tz))
            reviews = [build_season_review(conn, unit_id, self._journal(conn, unit_id, cycle, now.date().isoformat()), tz, None, now) for cycle in cycles]
            return compare_reviews(unit_id, reviews, now)

    def plan_task(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        with self.db.transaction() as conn:
            return plan_task(conn, owner, unit_id, cycle_id, data, request_id=request_id, supersedes_id=supersedes_id)

    def resolve_task(self, owner, unit_id, record_id, status, reason, source, *, request_id,
                     completed_on=None, completion_record=None):
        with self.db.transaction() as conn:
            return resolve_task(conn, owner, unit_id, record_id, status, reason, source, request_id=request_id, completed_on=completed_on, completion_record=completion_record)

    def record_cash(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        text("cycle_id", cycle_id)
        data = cash_data(data)
        operation = dict(kind="record_cash", cycle_id=cycle_id, data=data, supersedes_id=supersedes_id)
        with self.db.transaction() as conn:
            tz, _ = _scope(conn, owner, unit_id, cycle_id)
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            if _date(data["day"]) > datetime.now(ZoneInfo(tz)).date():
                raise ValueError("cash entries record completed payments or receipts through today in the farm timezone")
            # A crop can incur preplant payments and receive proceeds after it has ended.
            if supersedes_id is not None:
                prior = _read(conn, unit_id, supersedes_id)
                _latest(conn, prior, ("cash",))
                if prior["cycle_id"] != cycle_id or prior["data"]["entry"] != data["entry"]:
                    raise ValueError("correction must preserve the cycle and entry ID; void a wrongly assigned entry instead")
            elif conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind='cash' AND batch=?",
                              (unit_id, cycle_id, data["entry"])).fetchone():
                raise ValueError("entry already exists; correct its latest revision with supersedes_id")
            return _save(conn, owner, unit_id, cycle_id, request_id, "cash", data, operation, supersedes_id=supersedes_id)

    def void_cash(self, owner, unit_id, record_id, reason, *, request_id):
        return self._void(owner, unit_id, record_id, reason, request_id, "void_cash", ("cash",))

    @staticmethod
    def _measurements(conn, unit_id, cycle_id=None):
        rows = conn.execute("SELECT payload FROM field_observations WHERE unit_id=? "
                            "AND (? IS NULL OR cycle_id=?) ORDER BY julianday(observed_at),id", (unit_id, cycle_id, cycle_id))
        return [reviewed_observation(conn, json.loads(row[0])) for row in rows]

    def list_measurements(self, owner, unit_id, cycle_id=None):
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id, cycle_id)
            return dict(measurements=self._measurements(conn, unit_id, cycle_id))

    def review_measurement(self, owner, unit_id, observation_id, quality, reason, source, *, request_id, expected_revision):
        return ObservationReviews(self.db).review(owner, unit_id, observation_id, quality, reason, source,
                                                 request_id=request_id, expected_revision=expected_revision)

    def record_measurement(self, owner, unit_id, data, *, request_id, cycle_id=None):
        with self.db.transaction() as conn:
            return record_measurement(conn, owner, unit_id, data, request_id=request_id, cycle_id=cycle_id)

    def record_harvest(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        with self.db.transaction() as conn:
            return record_harvest(conn, owner, unit_id, cycle_id, data,
                                  request_id=request_id, supersedes_id=supersedes_id)

    def record_fertilizer(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        text("cycle_id", cycle_id)
        data, accounting = application_data(data)
        operation = dict(kind="record_fertilizer", cycle_id=cycle_id, data=data, supersedes_id=supersedes_id)
        with self.db.transaction() as conn:
            tz, cycle = _scope(conn, owner, unit_id, cycle_id)
            if _owned_unit(conn, owner, unit_id)["production_system"] != "soil":
                raise ValueError("mineral fertilizer application records currently support soil units only")
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            day, planted = _date(data["day"]), _date(cycle["planted_on"])
            if day > datetime.now(ZoneInfo(tz)).date():
                raise ValueError("application date cannot be after today in the farm timezone")
            if data["phase"] == "preplant" and day > planted:
                raise ValueError("preplant application must be on or before planting")
            if data["phase"] == "in_crop" and day < planted:
                raise ValueError("in_crop application must be on or after planting")
            ensure_cycle_day(conn, unit_id, cycle_id, data["day"])
            if data.get("calculation_id") is not None:
                plan = conn.execute("SELECT payload FROM field_calculations WHERE id=? AND unit_id=? AND cycle_id=?",
                                    (data["calculation_id"], unit_id, cycle_id)).fetchone()
                if plan is None or json.loads(plan[0])["task"] != "nutrient_budget":
                    raise ValueError("calculation_id must reference a nutrient_budget for this unit and cycle")
            if supersedes_id is not None:
                prior = _read(conn, unit_id, supersedes_id)
                _latest(conn, prior, ("fertilizer",))
                if prior["cycle_id"] != cycle_id or prior["data"]["entry"] != data["entry"]:
                    raise ValueError("correction must preserve the cycle and entry ID; void a wrongly assigned entry instead")
            elif conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind='fertilizer' AND batch=?",
                              (unit_id, cycle_id, data["entry"])).fetchone():
                raise ValueError("entry already exists; correct its latest revision with supersedes_id")
            return _save(conn, owner, unit_id, cycle_id, request_id, "fertilizer", data, operation,
                         supersedes_id=supersedes_id, accounting=accounting)

    def void_fertilizer(self, owner, unit_id, record_id, reason, *, request_id):
        return self._void(owner, unit_id, record_id, reason, request_id, "void_fertilizer", ("fertilizer",))

    def record_transplant(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        return self._observation(owner, unit_id, cycle_id, "transplant", data, request_id, supersedes_id)

    def void_transplant(self, owner, unit_id, record_id, reason, *, request_id):
        return self._void(owner, unit_id, record_id, reason, request_id, "void_transplant", ("transplant",))

    def record_stage(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        return self._observation(owner, unit_id, cycle_id, "stage", data, request_id, supersedes_id)

    def record_scouting(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None):
        return self._observation(owner, unit_id, cycle_id, "scouting", data, request_id, supersedes_id)

    def _observation(self, owner, unit_id, cycle_id, kind, data, request_id, supersedes_id):
        with self.db.transaction() as conn:
            return record_crop_observation(conn, owner, unit_id, cycle_id, kind, data, request_id, supersedes_id)

    def void_observation(self, owner, unit_id, record_id, reason, *, request_id):
        return self._void(owner, unit_id, record_id, reason, request_id, "void_observation", ("stage", "scouting"))

    def void_harvest(self, owner, unit_id, record_id, reason, *, request_id):
        return self._void(owner, unit_id, record_id, reason, request_id, "void_harvest", ("harvest",))

    def _void(self, owner, unit_id, record_id, reason, request_id, operation_name, kinds):
        text("reason", reason)
        operation = dict(kind=operation_name, record_id=record_id, reason=reason)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            prior = _read(conn, unit_id, record_id)
            _latest(conn, prior, kinds)
            if prior["voided"]:
                raise ValueError("record is already voided")
            return _save(conn, owner, unit_id, prior["cycle_id"], request_id, prior["kind"], prior["data"], operation,
                         supersedes_id=record_id, voided=True, reason=reason, accounting=prior.get("accounting"))

    def close_cycle(self, owner, unit_id, cycle_id, ended_on, reason, source, *, request_id, expected_revision):
        _date(ended_on, "ended_on")
        return self._set_lifecycle(owner, unit_id, cycle_id, "closed", ended_on, reason, source, request_id, expected_revision)

    def reopen_cycle(self, owner, unit_id, cycle_id, reason, source, *, request_id, expected_revision):
        return self._set_lifecycle(owner, unit_id, cycle_id, "open", None, reason, source, request_id, expected_revision)

    def _set_lifecycle(self, owner, unit_id, cycle_id, status, ended_on, reason, source, request_id, expected_revision):
        text("cycle_id", cycle_id)
        if expected_revision is not None:
            text("expected_revision", expected_revision)
        data = dict(status=status, ended_on=ended_on, reason=text("reason", reason).strip(), source=text("source", source).strip())
        operation = dict(kind="close_cycle" if status == "closed" else "reopen_cycle",
                         cycle_id=cycle_id, data=data, expected_revision=expected_revision)
        with self.db.transaction() as conn:
            tz, cycle = _scope(conn, owner, unit_id, cycle_id)
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            current = lifecycle(conn, unit_id, cycle_id)
            if expected_revision != current["revision_id"]:
                raise ValueError("cycle lifecycle changed; read get and use its latest revision_id as expected_revision")
            if status == "open" and current["status"] != "closed":
                raise ValueError("cycle is already open")
            if status == "closed":
                if _date(ended_on) < _date(cycle["planted_on"]) or _date(ended_on) > datetime.now(ZoneInfo(tz)).date():
                    raise ValueError("cycle end must fall between planting and today in the farm timezone")
                latest = latest_actual_day(conn, unit_id, cycle_id, tz)
                if latest is not None and ended_on < latest:
                    raise ValueError(f"cycle end precedes a current actual record on {latest}; correct or void that record first")
            return _save(conn, owner, unit_id, cycle_id, request_id, "lifecycle", data, operation,
                         supersedes_id=current["revision_id"])

    def describe(self, owner, unit_id):
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
        return dict(
            operations=dict(create="{crop,planted_on:YYYY-MM-DD,source,request_id}", list="{}", get="{cycle_id}",
                start_task="{language?}", get_task="{}", answer_task="{answer,expected_revision,back?}",
                save_task="{draft_id,expected_revision}", cancel_task="{expected_revision}",
                plan_task="{cycle_id,request_id,supersedes_id?,data:{entry,title,activity,due,location,notes,source,priority,trigger?}}",
                resolve_task="{record_id,status,reason,source,request_id,completed_on?,completion_record?}",
                record_cash="{cycle_id,request_id,supersedes_id?,data:{entry,day,direction,amount,currency,category,description,allocation_note,source,provenance}}",
                void_cash="{record_id,reason,request_id}", compare_seasons="{cycle_ids:[2–10 distinct IDs in the selected unit]}",
                correct_planting="{cycle_id,crop,planted_on:YYYY-MM-DD,source,reason,request_id,expected_revision}",
                record_transplant="{cycle_id,request_id,supersedes_id?,data:{entry,day,location,origin,plant_count,source,provenance}}",
                void_transplant="{record_id,reason,request_id}",
                record_harvest="{cycle_id,request_id,supersedes_id?,data:{batch,day,quantity,unit,product,basis,grade,source,provenance}}",
                record_harvest_review="{cycle_id,request_id,supersedes_id?,reason?,data:{entry,start,end,area_m2,location,area_source,coverage,scope_note,source,harvest_record_ids}}",
                void_harvest_review="{record_id,reason,request_id}",
                record_fertilizer="{cycle_id,request_id,supersedes_id?,data:{entry,day,phase,area_m2,location,placement,source,provenance,products,calculation_id?}}",
                void_fertilizer="{record_id,reason,request_id}",
                record_stage="{cycle_id,request_id,supersedes_id?,data:{entry,day,location,stage,source,provenance}}",
                start_scouting="{language?}; en, fr or zh-Hant; collect or resume an observation, or change draft language",
                start_measurement="{language?}; en, fr or zh-Hant; guided raw readings, explicit review before saving",
                get_measurement="{}", answer_measurement="{answer,expected_revision,back?}",
                save_measurement="{draft_id,expected_revision}", cancel_measurement="{expected_revision}",
                start_harvest="{language?}; en, fr or zh-Hant; collect an actual harvest batch",
                get_harvest="{}", answer_harvest="{answer,expected_revision,back?}",
                save_harvest="{draft_id,expected_revision}; only after explicit user review and saving request",
                cancel_harvest="{expected_revision}",
                start_planting="{language?}; en, fr or zh-Hant; collect an actual planting for a new cycle",
                get_planting="{}", answer_planting="{answer,expected_revision,back?}",
                save_planting="{draft_id,expected_revision}; only after explicit user review and saving request",
                cancel_planting="{expected_revision}",
                get_scouting="{}", answer_scouting="{answer,expected_revision,back?}",
                save_scouting="{draft_id,expected_revision}; only after the user reviews the ready preview and requests saving",
                cancel_scouting="{expected_revision}; discard only the draft",
                record_scouting="{cycle_id,request_id,supersedes_id?,data:{entry,day,location,symptoms,inspected_count,affected_count,source,provenance}}",
                void_harvest="{record_id,reason,request_id}", void_observation="{record_id,reason,request_id}",
                close_cycle="{cycle_id,ended_on:YYYY-MM-DD,reason,source,request_id,expected_revision}",
                reopen_cycle="{cycle_id,reason,source,request_id,expected_revision}",
                season_review="{cycle_id,calculation_id?} (optional selected root_zone_budget for input comparison)",
                list_measurements="{cycle_id?} (omitted/null means all selected-unit observations, including unassigned)",
                record_measurement="{cycle_id?,request_id,data:{quantity,value,unit,observed_at:aware ISO timestamp,source,provenance}}",
                preview_measurements="{rows:[{request_id,cycle_id?,data:measurement}]}: validate 1–1000 rows without saving; inspect entries and preview_token",
                import_measurements="{rows,preview_token}: explicitly save the exact reviewed rows atomically; no automatic quality review or daily water totals",
                review_measurement="{observation_id,quality,reason,source,request_id,expected_revision}"),
            measurement_units=OBSERVATION_UNITS,
            harvest_review=HARVEST_REVIEW_NOTE + " Soil units only. area_m2: positive decimal STRING, at most 12 integer/6 fractional digits. "
                "start/end: inclusive YYYY-MM-DD within planting, today and closure. coverage: complete/partial/unknown. "
                "Select 1–1000 distinct current non-voided harvest record IDs in this cycle and date window. "
                "scope_note explains why these whole batches belong to this footprint and the stated coverage; area_source and source required. "
                "Corrections require latest supersedes_id and reason. get.harvest_reviews retains history/current reports and live change flags. "
                "Saved results use exact fractions, with six-place half-even displays; changes do not rewrite snapshots.",
            measurement_entry="Record explicitly supplied readings only. provenance: measured/reported/estimated/forecast. "
                "New readings are unverified; review separately. A future timestamp requires forecast. "
                "Never infer a midnight timestamp or a whole-root average from a point sensor. Reuse request_id on retries; "
                "corrections are new readings, with the old reading rejected through review_measurement when appropriate.",
            calendar="Plans are distinct from actual quantity records. plan_task creates or replaces a planned task, preserving cycle and entry ID. "
                "activity: scouting/stage_check/irrigation/fertilizer/transplant/harvest/maintenance/other; priority low/normal/high. "
                "due: {basis:date,start:YYYY-MM-DD,end:YYYY-MM-DD} or {basis:planting,start_offset_days:int,end_offset_days:int}. "
                "Offsets -365..3650; start <= end. Source and notes are explicit; never infer agronomic schedules. "
                "Optional trigger {kind:crop_record|measurement|water_day,id,reason} retains the current same-unit/cycle source snapshot. "
                "Measurement triggers also require review_revision_id (null only if unreviewed). Use get.calendar.trigger_candidates. "
                "Later source changes require review, including resolved tasks; a trigger does not establish cause or completion. "
                "Revisions of linked plans must explicitly retain/replace trigger or set it to null to remove the link. "
                "get.calendar shows farm-local upcoming/due/overdue tasks and changed crop/date assumptions requiring review. "
                "Relative pending dates follow planting corrections; original windows persist. Revise the plan explicitly to accept new assumptions. "
                "resolve_task uses the latest record_id; status completed/cancelled/planned (reopen). Reason/source required. "
                "Completed requires actual completed_on through today; optional completion_record {kind:crop_record|water_day,id} must be current, "
                "same cycle/unit, match the activity and date. Irrigation evidence needs positive net_irrigation_mm. "
                "Missing links mean operator-reported completion only; resolving never creates quantities or controls equipment. "
                "Later evidence changes are flagged, never silently relinked. Reopen before changing a resolved plan. "
                "Tasks can cover preplant preparation or postcrop cleanup, do not constrain closure and send no notifications. "
                "Pending crop activity windows beyond cycle end are flagged for review; maintenance/other work may follow closure.",
            cash="Actual paid expenses or received income, not quotes, unpaid invoices or forecasts. "
                "amount is a positive plain decimal STRING, up to 15 integer/6 fractional digits; direction expense/income. "
                "currency is three uppercase letters, retained as supplied without exchange conversion. "
                "provenance documented/reported/estimated; documented is a user-supplied document reference, not an audit. "
                "category and description are explicit text; allocation_note explains the amount assigned to this cycle. "
                "Payment/receipt day is through today in farm timezone and may precede planting or follow closure. "
                "Amounts remain separate by currency/provenance; net recorded cash is not profit. No assumed missing costs or receipts.",
            comparison="Read-only summaries of explicitly selected cycles in one owned unit, from one consistent database snapshot. "
                "Keeps dates, crop, lifecycle, harvest basis/grade/provenance, water coverage, fertilizer and cash groups, and evidence IDs. "
                "Explicit harvested-area reviews retain their scope, coverage and change flags. "
                "No cross-cycle aggregation, currency conversion, inferred area, causal attribution or ranking. Missing history stays unknown.",
            planting="Correct only on explicit request. Read get.planting.revision_id as expected_revision; null for cycles without a planting record. "
                "A correction preserves the cycle ID/unit and history, with a reason, source and previous cycle snapshot. "
                "The new date cannot conflict with current stage/scouting/transplant/harvest/water records, fertilizer phases or closure. "
                "Typed measurements may predate planting and retain their timestamps. Saved calculations are not rewritten; "
                "season_review flags crop/date conflicts. Use a new cycle for a new planting, not a correction.",
            transplant="Actual transfers into this cycle only, dated from planting through today and cycle end. "
                "Origin and location are explicit text; plant_count is a positive integer <= 1000000000 or null when unknown. "
                "Provenance: measured/reported/estimated. Separate entries may be replacements or partial transfers, so counts are not summed. "
                "Transplants do not change planting dates, transfer another unit's inventory, infer survival or update crop coefficients.",
            season_review="Read-only snapshot of this cycle's journal, water-log coverage/totals and calculation history. "
                "Assigned irrigation outcome reviews retain corrections, alternatives and current evidence flags separately from net-water totals. "
                "Missing days are unknown; totals cover recorded days and stay separate by provenance/quality. "
                "Select a calculation explicitly to compare its inputs with current actual water records. Replayed actual inputs are labelled. "
                "Differences are not water savings, causal yield effects or proof of recommendation accuracy.",
            measurement_review="Review typed field observations, not scouting entries or daily water totals. "
                "quality: unverified/checked/rejected. Read list_measurements and use quality_review.id (null initially) as expected_revision. "
                "Reviews require explicit operator instruction, reason and source; checked means operator-reviewed, not independently validated. "
                "Original quality, values, dates and provenance stay intact; effective_quality follows the latest review. "
                "Rejected actuals do not constrain closure. Restore with checked/unverified; later actuals require reopening first. "
                "Forecasts remain forecasts. get.measurements and list_measurements include full quality_history.",
            lifecycle="Read get.lifecycle.revision_id and send it as expected_revision (null initially). "
                "close_cycle also corrects an existing closure. Reopening removes the end-date restriction, preserving history. "
                "Open means not marked ended, not verified growing. Closing is not proof of yield or success. "
                "Close only on explicit request; never infer from a harvest. Crop/field actuals must fall on/before ended_on; cash dates are independent. "
                "Late records and corrections within that date remain allowed. Calculations remain available for retrospective analysis.",
            fertilizer=dict(phase=["preplant", "in_crop"], provenance=["measured", "reported", "estimated"],
                products="1–100 mineral products using the soil.nutrient_budget product schema (included below). "
                    "Explicit actual quantities in kg or L, N/P2O5/K2O mass percentages (null if unknown), analysis source. "
                    "L requires density and source for known mass; unknown density stays unknown. No organic amendments.",
                product_schema=dict(nutrition_input_schema()["properties"]["products"], minItems=1),
                scope="Soil units only. area_m2 is treated area, location identifies extent, placement describes actual application. "
                    "Preplant day <= planting; in_crop day >= planting; all dates <= farm-local today and cycle end. "
                    "calculation_id optionally links a nutrient_budget owned by this unit and cycle; never copies amounts. "
                    "Record only explicitly completed applications. Saving a plan does not log fertilizer. "
                    "Accounting snapshots persist with revisions; totals separate phase/provenance and never sum areas or kg/ha."),
            harvest=dict(unit=["kg", "g", "t", "count"], basis=["fresh", "dry", "as_harvested"],
                         provenance=["measured", "reported", "estimated"],
                         quantity="Positive number; count must be whole. Specify product and grade (ungraded if applicable)."),
            observations=dict(provenance=["observed", "reported"],
                stage="Free-text observed stage; no automatic crop-coefficient or treatment changes.",
                symptoms="Describe observed symptoms or explicitly state none observed; never infer a diagnosis.",
                counts="Both integer plant counts (inspected > 0, 0 <= affected <= inspected) or both null. Each sample stays separate.",
                location="Sample location or extent within this crop cycle; use whole unit only if actually observed."),
            rules=["Actual record operations require events through today in farm timezone; future work belongs in plan_task. Preplant fertilizer and cash may predate planting; cash can also follow closure.",
                "Nonempty entry ID unique per cycle and kind (batch ID for harvest). Source required for every write.",
                "Corrections replace all data using latest supersedes_id; preserve kind, cycle and entry/batch ID.",
                "Voids retain history and reason; correcting the latest voided revision restores it. Planting corrections use correct_planting; plantings cannot be voided.",
                "Reuse request_id for retries; new intended writes need new IDs.",
                "get returns all revisions, separated harvest totals, and all active stage reports on the latest reported day."])


def execute_crop_records(db, owner, unit_id, operation, data):
    from .harvest_consultation import HarvestConsultation
    from .measurement_import import MeasurementImports
    from .planting_consultation import PlantingConsultation
    from .scouting_consultation import ScoutingConsultation
    from .task_consultation import TaskConsultation
    task = TaskConsultation(db)
    service = CropRecords(db)
    intake = ScoutingConsultation(db)
    from .measurement_consultation import MeasurementConsultation
    measurement = MeasurementConsultation(db)
    planting = PlantingConsultation(db)
    imports = MeasurementImports(db)
    harvest = HarvestConsultation(db)
    allowed = dict(start_task=("language",), get_task=(), answer_task=("answer", "expected_revision", "back"),
                   save_task=("draft_id", "expected_revision"), cancel_task=("expected_revision",), start_harvest=("language",), get_harvest=(),
                   answer_harvest=("answer", "expected_revision", "back"),
                   save_harvest=("draft_id", "expected_revision"), cancel_harvest=("expected_revision",),
                   start_measurement=("language",), get_measurement=(),
                   answer_measurement=("answer", "expected_revision", "back"),
                   save_measurement=("draft_id", "expected_revision"), cancel_measurement=("expected_revision",), start_planting=("language",), get_planting=(), answer_planting=("answer", "expected_revision", "back"),
                   save_planting=("draft_id", "expected_revision"), cancel_planting=("expected_revision",),
                   start_scouting=("language",), get_scouting=(), answer_scouting=("answer", "expected_revision", "back"),
                   save_scouting=("draft_id", "expected_revision"), cancel_scouting=("expected_revision",), create=("crop", "planted_on", "source", "request_id"), list=(), get=("cycle_id",), describe=(),
                   plan_task=("cycle_id", "data", "request_id", "supersedes_id"),
                   resolve_task=("record_id", "status", "reason", "source", "request_id", "completed_on", "completion_record"),
                   record_cash=("cycle_id", "data", "request_id", "supersedes_id"),
                   void_cash=("record_id", "reason", "request_id"), compare_seasons=("cycle_ids",),
                   correct_planting=("cycle_id", "crop", "planted_on", "source", "reason", "request_id", "expected_revision"),
                   record_transplant=("cycle_id", "data", "request_id", "supersedes_id"),
                   void_transplant=("record_id", "reason", "request_id"),
                   record_harvest=("cycle_id", "data", "request_id", "supersedes_id"),
                   record_harvest_review=("cycle_id", "data", "request_id", "supersedes_id", "reason"),
                   void_harvest_review=("record_id", "reason", "request_id"),
                   record_fertilizer=("cycle_id", "data", "request_id", "supersedes_id"),
                   void_fertilizer=("record_id", "reason", "request_id"),
                   record_stage=("cycle_id", "data", "request_id", "supersedes_id"),
                   record_scouting=("cycle_id", "data", "request_id", "supersedes_id"),
                   void_harvest=("record_id", "reason", "request_id"),
                   void_observation=("record_id", "reason", "request_id"),
                   close_cycle=("cycle_id", "ended_on", "reason", "source", "request_id", "expected_revision"),
                   reopen_cycle=("cycle_id", "reason", "source", "request_id", "expected_revision"),
                   list_measurements=("cycle_id",), season_review=("cycle_id", "calculation_id"),
                   record_measurement=("cycle_id", "data", "request_id"),
                   preview_measurements=("rows",), import_measurements=("rows", "preview_token"),
                   review_measurement=("observation_id", "quality", "reason", "source", "request_id", "expected_revision"))
    choice("operation", operation, allowed)
    args = _mapping(data, allowed[operation], "crop operation")
    handlers = dict(start_task=task.start, get_task=task.get, answer_task=task.answer, save_task=task.save, cancel_task=task.cancel,
                   start_harvest=harvest.start, get_harvest=harvest.get, answer_harvest=harvest.answer,
                    save_harvest=harvest.save, cancel_harvest=harvest.cancel, start_measurement=measurement.start, get_measurement=measurement.get,
                    answer_measurement=measurement.answer, save_measurement=measurement.save, cancel_measurement=measurement.cancel, start_planting=planting.start, get_planting=planting.get, answer_planting=planting.answer,
                    save_planting=planting.save, cancel_planting=planting.cancel,
                    start_scouting=intake.start, get_scouting=intake.get, answer_scouting=intake.answer,
                    save_scouting=intake.save, cancel_scouting=intake.cancel, create=service.create, list=service.list, get=service.get,
                    plan_task=service.plan_task, resolve_task=service.resolve_task,
                    record_cash=service.record_cash, void_cash=service.void_cash, compare_seasons=service.compare_seasons,
                    correct_planting=service.correct_planting,
                    record_transplant=service.record_transplant, void_transplant=service.void_transplant,
                    record_harvest=service.record_harvest, void_harvest=service.void_harvest,
                    record_harvest_review=HarvestReviews(db).save, void_harvest_review=HarvestReviews(db).void,
                    record_fertilizer=service.record_fertilizer, void_fertilizer=service.void_fertilizer,
                    record_stage=service.record_stage, record_scouting=service.record_scouting,
                    void_observation=service.void_observation, describe=service.describe,
                    close_cycle=service.close_cycle, reopen_cycle=service.reopen_cycle,
                    list_measurements=service.list_measurements, review_measurement=service.review_measurement,
                    record_measurement=service.record_measurement,
                    preview_measurements=imports.preview, import_measurements=imports.save,
                    season_review=service.season_review)
    try:
        return handlers[operation](owner, unit_id, **args)
    except TypeError as exc:
        raise ValueError(f"invalid {operation} request: {exc}") from exc

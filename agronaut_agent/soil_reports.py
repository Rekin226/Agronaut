"""Owned laboratory-report transcriptions; no implicit agronomic interpretation."""

import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from agro_model.resources import _object
from agro_model.validation import choice, number, text

from .field_io import _date

QUALIFIERS = ("eq", "lt", "le", "gt", "ge", "not_reported", "not_detected", "text")
NOTE = ("Operator-transcribed laboratory results, not independently verified tests or fertilizer recommendations. "
        "Methods, units, sampling depths and dates may differ. Qualifiers and unknowns remain as entered; no conversion, averaging, "
        "soil-test interpretation, nutrient credit or rate recommendation is inferred. Dates may precede planting or follow closure. "
        "Only reports explicitly assigned to this cycle are included; no reports means unknown history.")
_NUMERIC = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z", re.ASCII)


def migrate_soil_reports(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS soil_test_reports (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        cycle_id TEXT REFERENCES production_cycles(id) ON DELETE CASCADE,
        entry TEXT NOT NULL, request_id TEXT NOT NULL,
        supersedes_id TEXT UNIQUE REFERENCES soil_test_reports(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(unit_id,request_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_soil_reports_unit ON soil_test_reports(unit_id,cycle_id,entry)")


def soil_report_data(data):
    data = dict(_object(data, ("entry", "sampled_on", "reported_on", "lab_name", "lab_report_id", "sample_id", "location",
        "depth_top_cm", "depth_bottom_cm", "subsample_count", "sampling_source", "report_source", "results"), "soil report"))
    for key in ("entry", "lab_name", "lab_report_id", "sample_id", "location", "sampling_source", "report_source"):
        data[key] = text(key, data[key]).strip()
    sampled, reported = _date(data["sampled_on"]), _date(data["reported_on"])
    if reported < sampled:
        raise ValueError("reported_on cannot precede sampled_on")
    top, bottom = data["depth_top_cm"], data["depth_bottom_cm"]
    if (top is None) != (bottom is None):
        raise ValueError("supply both sampling depths in cm, or null for both when unknown")
    if top is not None:
        number("depth_top_cm", top, low=0, high=1000)
        number("depth_bottom_cm", bottom, low=0, high=1000)
        if bottom <= top:
            raise ValueError("sampling bottom depth must exceed top depth")
    count = data["subsample_count"]
    if count is not None and (type(count) is not int or not 1 <= count <= 1000000):
        raise ValueError("subsample_count must be a positive integer or null when unknown")
    if not isinstance(data["results"], list) or not 1 <= len(data["results"]) <= 200:
        raise ValueError("a soil report needs 1–200 explicitly entered result rows")
    results, identifiers = [], set()
    for item in data["results"]:
        row = dict(_object(item, ("id", "analyte", "value", "qualifier", "unit", "method"), "lab result"))
        for key in ("id", "analyte"):
            row[key] = text(key, row[key]).strip()
        if row["id"] in identifiers:
            raise ValueError("lab result row IDs must be unique; separate methods may have separate rows")
        identifiers.add(row["id"])
        choice("qualifier", row["qualifier"], QUALIFIERS)
        for key in ("unit", "method"):
            if row[key] is not None:
                row[key] = text(key, row[key]).strip()
        if row["qualifier"] in ("not_reported", "not_detected"):
            if row["value"] is not None:
                raise ValueError("not_reported/not_detected require null value; use lt with a numeric reporting limit when supplied")
        elif row["qualifier"] == "text":
            text("textual lab result", row["value"])
            if len(row["value"]) > 500:
                raise ValueError("textual lab results must be at most 500 characters")
        else:
            value = row["value"]
            if not isinstance(value, str) or not 1 <= len(value) <= 64 or not _NUMERIC.fullmatch(value):
                raise ValueError("numeric lab values must be decimal strings, with a separate qualifier")
            try:
                numeric = Decimal(value)
                if not numeric.is_finite() or abs(numeric.adjusted()) > 100:
                    raise ValueError("lab value must be finite with decimal magnitude within supported bounds")
            except InvalidOperation as exc:
                raise ValueError("invalid numeric lab value") from exc
        results.append(row)
    return dict(data, results=results)


def _json(data):
    return json.dumps(data, sort_keys=True, allow_nan=False)


def soil_report_history(conn, unit_id, cycle_id):
    """Caller owns the unit/cycle and holds the surrounding snapshot transaction."""
    rows = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM soil_test_reports WHERE unit_id=? AND cycle_id IS ? ORDER BY rowid", (unit_id, cycle_id))]
    superseded = {r["supersedes_id"] for r in rows if r["supersedes_id"]}
    rows = [dict(r, superseded=r["id"] in superseded) for r in rows]
    current = [r for r in rows if not r["superseded"] and not r["voided"]]
    return dict(history=rows, current=current, note=NOTE)


def _read(conn, unit_id, identifier):
    text("report_id", identifier)
    row = conn.execute("SELECT payload FROM soil_test_reports WHERE id=? AND unit_id=?", (identifier, unit_id)).fetchone()
    if row is None:
        raise ValueError("unknown soil report for the selected unit")
    return json.loads(row[0])


def _retry(conn, unit_id, request_id, operation):
    text("request_id", request_id)
    row = conn.execute("SELECT payload FROM soil_test_reports WHERE unit_id=? AND request_id=?", (unit_id, request_id)).fetchone()
    if row:
        saved = json.loads(row[0])
        if saved["operation"] != operation:
            raise ValueError("request_id already used with different soil-report data")
        return saved


def _latest(conn, prior):
    if conn.execute("SELECT 1 FROM soil_test_reports WHERE supersedes_id=?", (prior["id"],)).fetchone():
        raise ValueError("soil report already superseded; use its latest revision")


def _insert(conn, owner, unit_id, cycle_id, data, operation, request_id, supersedes_id, reason, voided=False):
    saved = dict(id=uuid.uuid4().hex, owner_id=owner, unit_id=unit_id, cycle_id=cycle_id, data=data,
        request_id=request_id, supersedes_id=supersedes_id, reason=reason, voided=voided, operation=operation,
        evidence_status="operator_transcribed", created_at=datetime.now(timezone.utc).isoformat())
    conn.execute("INSERT INTO soil_test_reports VALUES(?,?,?,?,?,?,?,?)",
        (saved["id"], unit_id, cycle_id, data["entry"], request_id, supersedes_id, saved["created_at"], _json(saved)))
    return saved


class SoilReports:
    def __init__(self, db):
        self.db = db

    def save(self, owner, unit_id, data, *, request_id, cycle_id=None, supersedes_id=None, reason=None):
        from .field_workflow import _scope
        data = soil_report_data(json.loads(_json(data)))
        if reason is not None:
            reason = text("reason", reason).strip()
        operation = dict(kind="save", data=data, cycle_id=cycle_id, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            tz, _ = _scope(conn, owner, unit_id, cycle_id)
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            if _date(data["reported_on"]) > datetime.now(ZoneInfo(tz)).date():
                raise ValueError("soil reports record actual sampling and reporting; dates cannot be future")
            if supersedes_id is not None:
                prior = _read(conn, unit_id, supersedes_id)
                _latest(conn, prior)
                if prior["cycle_id"] != cycle_id or prior["data"]["entry"] != data["entry"]:
                    raise ValueError("a correction must retain the original entry and crop-cycle assignment")
                text("correction reason", reason)
            elif conn.execute("SELECT 1 FROM soil_test_reports WHERE unit_id=? AND entry=?", (unit_id, data["entry"])).fetchone():
                raise ValueError("entry already exists; correct its latest revision instead of creating a duplicate")
            return _insert(conn, owner, unit_id, cycle_id, data, operation, request_id, supersedes_id, reason)

    def void(self, owner, unit_id, report_id, reason, *, request_id):
        from .field_workflow import _scope
        reason = text("reason", reason).strip()
        operation = dict(kind="void", report_id=report_id, reason=reason)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            prior = _read(conn, unit_id, report_id)
            _latest(conn, prior)
            if prior["voided"]:
                raise ValueError("soil report is already voided")
            return _insert(conn, owner, unit_id, prior["cycle_id"], prior["data"], operation, request_id, prior["id"], reason, True)

    def get(self, owner, unit_id, report_id):
        from .field_workflow import _scope
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            return _read(conn, unit_id, report_id)

    def list(self, owner, unit_id, cycle_id=None):
        from .field_workflow import _scope
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id, cycle_id)
            return soil_report_history(conn, unit_id, cycle_id)

    def describe(self, owner, unit_id):
        from .field_workflow import _scope
        with self.db.transaction() as conn:
            tz, _ = _scope(conn, owner, unit_id)
        return dict(timezone=tz, operations=dict(
            record_soil_test="{data,request_id,cycle_id?,supersedes_id?,reason?}; corrections need latest ID and reason, preserve entry/cycle",
            list_soil_tests="{cycle_id?}; omitted/null means unassigned reports only",
            get_soil_test="{report_id}", void_soil_test="{report_id,reason,request_id}"),
            data="{entry,sampled_on:YYYY-MM-DD,reported_on:YYYY-MM-DD,lab_name,lab_report_id,sample_id,location,depth_top_cm:number|null,depth_bottom_cm:number|null,subsample_count:integer|null,sampling_source,report_source,results:[result]}",
            result="{id,analyte,value:string|null,qualifier:eq|lt|le|gt|ge|not_reported|not_detected|text,unit:string|null,method:string|null}",
            rules=["Dates must be actual, ordered and nonfuture. Sampling may predate planting; reports may follow closure.",
                "Both depths must be known with 0 <= top < bottom <= 1000 cm, or both null. Subsample count is 1–1000000 or null.",
                "1–200 result rows with unique IDs. Copy analytes, units and methods as reported; unknown units/methods are null.",
                "not_reported/not_detected require null value. text retains a nonempty categorical/descriptive result of at most 500 characters. Numeric qualifiers require finite decimal strings of at most 64 characters, adjusted exponent -100 to 100. No physical range interpretation.",
                "Re-use request_id for retries. Voiding appends history; restore by correcting the latest void revision with a reason."], note=NOTE)

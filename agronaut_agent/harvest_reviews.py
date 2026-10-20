"""Explicit harvested-area evidence and immutable, scoped harvest-per-area reviews."""

import json
import re
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from zoneinfo import ZoneInfo

from agro_model.validation import choice, text

from .cycle_lifecycle import ensure_cycle_day
from .farm_store import _owned_unit
from .field_io import _date, _mapping

NOTE = (
    "Recorded harvest per stated harvested ground area, not a yield forecast or causal comparison. "
    "The operator assigns whole batches to one footprint and states collection coverage for the selected dates. "
    "Count that footprint once across repeated pickings; no area is inferred from the production unit. "
    "Complete means operator-reported complete collection for that footprint and period, not independently verified "
    "or necessarily a full season. Partial and unknown coverage remain explicit. "
    "Products, fresh/dry/as-harvested bases, grades, provenance and mass/count units remain separate. "
    "Alternative reviews can overlap; do not sum their areas or harvests. No review means unknown area-normalized harvest."
)


def review_data(data):
    data = json.loads(json.dumps(dict(_mapping(data, (
        "entry", "start", "end", "area_m2", "location", "area_source", "coverage", "scope_note",
        "source", "harvest_record_ids"), "harvest review")), allow_nan=False))
    for key in ("entry", "location", "area_source", "scope_note", "source"):
        data[key] = text(key, data.get(key)).strip()
    if _date(data.get("start")) > _date(data.get("end")):
        raise ValueError("harvest review start must be on or before end")
    area = data.get("area_m2")
    if not isinstance(area, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?", area) or Decimal(area) <= 0:
        raise ValueError("area_m2 must be a positive plain decimal STRING, at most 12 integer and 6 fractional digits")
    choice("coverage", data.get("coverage"), ("complete", "partial", "unknown"))
    ids = data.get("harvest_record_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 1000:
        raise ValueError("select 1–1000 whole harvest batch record IDs; missing harvest is not zero")
    for key in ids:
        text("harvest record ID", key)
    if len(set(ids)) != len(ids):
        raise ValueError("harvest record IDs must be distinct")
    return data


def _amount(value):
    """Exact rational plus a clearly labelled six-place display, independent of caller context."""
    with localcontext() as ctx:
        ctx.prec = max(50, len(str(abs(value.numerator))) + len(str(value.denominator)) + 10)
        shown = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return dict(exact=str(value), display=format(shown, "f"))


def _groups(selected, area):
    groups = {}
    for record in selected:
        data = record["data"]
        unit = "count" if data["unit"] == "count" else "kg"
        key = data["product"], data["basis"], data["grade"], data["provenance"], unit
        group = groups.setdefault(key, dict(quantity=Fraction(0), record_ids=[]))
        factor = {"kg": Fraction(1), "g": Fraction(1, 1000), "t": Fraction(1000), "count": Fraction(1)}[data["unit"]]
        group["quantity"] += Fraction(str(data["quantity"])) * factor
        group["record_ids"].append(record["id"])
    return [dict(zip(("product", "basis", "grade", "provenance", "unit"), key),
        record_ids=group["record_ids"], quantity=_amount(group["quantity"]),
        per_ha=_amount(group["quantity"] * 10000 / Fraction(area)), per_ha_unit=key[-1]+"/ha")
        for key, group in sorted(groups.items())]


def _current_harvests(records):
    return {r["id"]: r for r in records if r["kind"] == "harvest" and not r["superseded"] and not r["voided"]}


def harvest_review_summary(records, cycle, timezone, state):
    """Caller supplies an owned, consistent journal snapshot. Never update a saved calculation."""
    current = _current_harvests(records)
    history = []
    for row in records:
        if row["kind"] != "harvest_review":
            continue
        data, saved = row["data"], row["accounting"]
        flags = []
        if any(key not in current for key in data["harvest_record_ids"]):
            flags.append("selected_harvest_revised_or_voided")
        available = sorted(key for key, r in current.items() if data["start"] <= r["data"]["day"] <= data["end"])
        if available != saved["available_harvest_ids"]:
            flags.append("harvest_records_in_period_changed")
        if cycle != saved["cycle"]:
            flags.append("planting_changed")
        if timezone != saved["timezone"]:
            flags.append("farm_timezone_changed")
        if state != saved["lifecycle"]:
            flags.append("cycle_lifecycle_changed")
        history.append(dict(row, review=dict(needs_review=bool(flags), flags=flags)))
    return dict(history=history, current=[r for r in history if not r["superseded"] and not r["voided"]], note=NOTE)


class HarvestReviews:
    def __init__(self, db):
        self.db = db

    def save(self, owner, unit_id, cycle_id, data, *, request_id, supersedes_id=None, reason=None):
        from .crop_records import CropRecords, _latest, _read, _retry, _save, _scope

        text("cycle_id", cycle_id)
        data = review_data(data)
        if reason is not None:
            reason = text("reason", reason).strip()
        operation = dict(kind="record_harvest_review", cycle_id=cycle_id, data=data, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            tz, cycle = _scope(conn, owner, unit_id, cycle_id)
            if _owned_unit(conn, owner, unit_id)["production_system"] != "soil":
                raise ValueError("harvested ground-area reviews currently support soil units only")
            existing = _retry(conn, unit_id, request_id, operation)
            if existing:
                return existing
            today = datetime.now(ZoneInfo(tz)).date()
            if data["start"] < cycle["planted_on"] or _date(data["end"]) > today:
                raise ValueError("review dates must fall between planting and today in the farm timezone")
            ensure_cycle_day(conn, unit_id, cycle_id, data["end"])
            if supersedes_id is not None:
                prior = _read(conn, unit_id, supersedes_id)
                _latest(conn, prior, ("harvest_review",))
                if prior["cycle_id"] != cycle_id or prior["data"]["entry"] != data["entry"]:
                    raise ValueError("correction must preserve cycle and entry ID")
                if reason is None:
                    raise ValueError("a harvest review correction requires a reason")
            elif conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind='harvest_review' AND batch=?",
                              (unit_id, cycle_id, data["entry"])).fetchone():
                raise ValueError("entry already exists; correct its latest revision with supersedes_id")
            journal = CropRecords(self.db)._journal(conn, unit_id, cycle, today.isoformat())
            current = _current_harvests(journal["records"])
            selected = []
            for key in data["harvest_record_ids"]:
                if key not in current:
                    raise ValueError("selected harvest must be a current, non-voided batch in this unit and cycle")
                row = current[key]
                if not data["start"] <= row["data"]["day"] <= data["end"]:
                    raise ValueError("selected harvest date is outside the review period")
                selected.append(row)
            accounting = dict(schema_version=1, cycle=cycle, timezone=tz, lifecycle=journal["lifecycle"],
                evidence_status="operator_reported_scope", selected_records=selected,
                available_harvest_ids=sorted(key for key, r in current.items() if data["start"] <= r["data"]["day"] <= data["end"]),
                groups=_groups(selected, data["area_m2"]), note=NOTE)
            return _save(conn, owner, unit_id, cycle_id, request_id, "harvest_review", data, operation,
                         supersedes_id=supersedes_id, reason=reason, accounting=accounting)

    def void(self, owner, unit_id, record_id, reason, *, request_id):
        from .crop_records import CropRecords

        return CropRecords(self.db)._void(owner, unit_id, record_id, reason, request_id, "void_harvest_review", ("harvest_review",))

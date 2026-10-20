"""Owned whole-day surface supplies, distinct from net-root-zone water logs."""

import json
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.resources import _object
from agro_model.soil_infiltration import SurfaceWaterSupply
from agro_model.soil_snapshots import loads_soil_snapshot
from agro_model.validation import choice, text

from .coupled_water_io import _decimal, _indexed
from .cycle_lifecycle import ensure_cycle_day
from .field_io import _date
from .field_workflow import _calculate, _json, _read, _retry, _scope


def migrate_surface_water(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS field_surface_water_days (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        cycle_id TEXT REFERENCES production_cycles(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, day TEXT NOT NULL,
        supersedes_id TEXT UNIQUE REFERENCES field_surface_water_days(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(unit_id,request_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_surface_water_unit ON field_surface_water_days(unit_id,cycle_id,day)")


def _parent(conn, owner, unit_id, calculation_id):
    _scope(conn, owner, unit_id)
    saved = _read(conn, "field_calculations", unit_id, calculation_id)
    if saved["task"] != "coupled_soil_water":
        raise ValueError("surface-water records require a coupled_soil_water calculation")
    tz, cycle = _scope(conn, owner, unit_id, saved["cycle_id"])
    if saved["inputs"]["site"]["timezone"] != tz:
        raise ValueError("calculation timezone must match the farm timezone")
    return saved, tz, cycle


def footprint(saved):
    soil = loads_soil_snapshot(saved["result"]["result"]["initial_checkpoint"], expected_kind="state").soil
    return dict(area_m2_exact=str(soil.area_m2), patches=[dict(id=p.id, area_fraction_exact=str(p.area_fraction))
                for p in sorted(soil.patches, key=lambda p: p.id)])


def _current(conn, record):
    if conn.execute("SELECT 1 FROM field_surface_water_days WHERE supersedes_id=?", (record["id"],)).fetchone():
        raise ValueError("surface-water record is superseded; select its latest revision, or replay the earlier calculation that supplied the checkpoint")
    if record["data"]["quality"] == "rejected":
        raise ValueError("rejected surface-water records cannot supply a calculation")


def validate_inherited(conn, parent):
    """A checkpoint remains historical; a new continuation must acknowledge corrections."""
    for record in parent.get("surface_water_evidence", []):
        _current(conn, record)


def history(conn, unit_id, cycle_id):
    rows = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM field_surface_water_days WHERE unit_id=? AND cycle_id IS ? ORDER BY day,rowid", (unit_id, cycle_id))]
    superseded = {r["supersedes_id"] for r in rows if r["supersedes_id"]}
    return [dict(r, superseded=r["id"] in superseded) for r in rows]


class SurfaceWaterRecords:
    def __init__(self, db):
        self.db = db

    def review(self, owner, unit_id, calculation_id):
        with self.db.transaction() as conn:
            saved, tz, _ = _parent(conn, owner, unit_id, calculation_id)
            basis = footprint(saved)
            records = history(conn, unit_id, saved["cycle_id"])
            current = {r["id"] for r in records if not r["superseded"] and r["data"]["quality"] != "rejected"}
            return dict(calculation_id=calculation_id, footprint=basis, timezone=tz,
                records=[dict(r, matches_footprint=r["footprint"] == basis and r["timezone"] == tz) for r in records],
                applied=[dict(record=r, needs_review=r["id"] not in current) for r in saved.get("surface_water_evidence", [])],
                note="Local patch depths at the soil surface after upstream losses. Rainfall runoff is supplied separately; "
                     "model overflow is calculated separately. These records are not net-root-zone irrigation or pumped volume. "
                     "Matching IDs and fractions do not verify physical patch locations; review footprint coverage explicitly.")

    def record(self, owner, unit_id, calculation_id, data, *, request_id, supersedes_id=None, reason=None):
        data = json.loads(_json(data))
        data = _object(data, ("day", "patches", "source", "representation_note", "provenance", "quality"), "surface-water record")
        day = _date(data["day"])
        for key in ("source", "representation_note"):
            text(key, data[key])
        choice("provenance", data["provenance"], ("measured", "reported", "estimated"))
        choice("quality", data["quality"], ("unverified", "checked", "rejected"))
        for row in _indexed(data["patches"], "surface-water patches"):
            row = _object(row, ("id", "rainfall_mm", "rainfall_runoff_mm", "irrigation_at_surface_mm", "source"), "surface-water patch")
            SurfaceWaterSupply(*(_decimal(row[k]) for k in ("rainfall_mm", "rainfall_runoff_mm", "irrigation_at_surface_mm")), row["source"])
        if supersedes_id is not None:
            text("correction reason", reason)
        elif reason is not None:
            raise ValueError("reason is only supplied for a correction")
        operation = dict(kind="record_surface_day", calculation_id=calculation_id, data=data, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            if existing := _retry(conn, "field_surface_water_days", unit_id, request_id, operation):
                return existing
            parent, tz, cycle = _parent(conn, owner, unit_id, calculation_id)
            basis = footprint(parent)
            if {r["id"] for r in data["patches"]} != {p["id"] for p in basis["patches"]}:
                raise ValueError("surface-water rows must cover exactly every represented patch")
            now = datetime.now(timezone.utc)
            if day >= now.astimezone(ZoneInfo(tz)).date():
                raise ValueError("surface-water totals require a completed local day")
            if data["quality"] != "rejected":
                if cycle and data["day"] < cycle["planted_on"]:
                    raise ValueError("surface-water record precedes the crop cycle")
                ensure_cycle_day(conn, unit_id, parent["cycle_id"], data["day"])
            prior = [r for r in history(conn, unit_id, parent["cycle_id"]) if r["day"] == data["day"]]
            if supersedes_id is None and prior:
                raise ValueError("surface-water totals already exist; correct them with supersedes_id")
            if supersedes_id is not None:
                old = next((r for r in prior if r["id"] == supersedes_id), None)
                if old is None or old["superseded"]:
                    raise ValueError("correction must name the latest record for the same unit, cycle and day")
                if old["timezone"] != tz:
                    raise ValueError("correction cannot reinterpret a recorded day in another timezone")
            saved = dict(id=uuid.uuid4().hex, owner_id=owner, unit_id=unit_id, cycle_id=parent["cycle_id"],
                calculation_id=calculation_id, request_id=request_id, day=data["day"], timezone=tz,
                footprint=basis, data=data, supersedes_id=supersedes_id, reason=reason, created_at=now.isoformat(), operation=operation)
            conn.execute("INSERT INTO field_surface_water_days VALUES(?,?,?,?,?,?,?,?)", (saved["id"], unit_id,
                parent["cycle_id"], request_id, data["day"], supersedes_id, saved["created_at"], _json(saved)))
            return saved

    def replay(self, owner, unit_id, calculation_id, record_ids, *, request_id, weather_policy="observed_only"):
        choice("weather_policy", weather_policy, ("observed_only", "allow_historical"))
        if not isinstance(record_ids, list) or not 1 <= len(record_ids) <= 366:
            raise ValueError("select 1–366 surface-water record IDs")
        for identifier in record_ids:
            text("surface-water record ID", identifier)
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("duplicate surface-water record IDs")
        operation = dict(kind="replay_surface_water", calculation_id=calculation_id, record_ids=sorted(record_ids), weather_policy=weather_policy)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            if existing := _retry(conn, "field_calculations", unit_id, request_id, operation):
                return existing
            parent, tz, cycle = _parent(conn, owner, unit_id, calculation_id)
            if parent.get("coupled_state_revision"):
                raise ValueError("withdraw the ending-state revision before surface-water replay, then review and reapply measurements to the recomputed prediction")
            from .coupled_state_workflow import validate_state_evidence
            validate_state_evidence(conn, parent)
            if cycle:
                from .crop_establishment import calculation_cycle_review
                from .cycle_lifecycle import lifecycle
                review = calculation_cycle_review(parent, dict(cycle), tz, lifecycle(conn, unit_id, parent["cycle_id"]))
                if review["changed_fields"] or review["conflicts"]:
                    raise ValueError("parent crop-cycle context changed; review initialization before replay")
            basis = footprint(parent)
            inputs = parent["inputs"]
            days, weather = {d["day"]: d for d in inputs["days"]}, {w["day"]: w for w in inputs["weather"]}
            selected = {r["day"]: r for r in parent.get("surface_water_evidence", [])}
            applied_days = set()
            for identifier in record_ids:
                record = _read(conn, "field_surface_water_days", unit_id, identifier)
                if record["day"] not in days:
                    raise ValueError("surface-water record falls outside this replay window; revise the ancestor supplying the checkpoint if needed")
                if record["day"] in applied_days:
                    raise ValueError("select only one revision per surface-water day")
                applied_days.add(record["day"])
                selected[record["day"]] = record
            for day, record in selected.items():
                _current(conn, record)
                if record["cycle_id"] != parent["cycle_id"] or record["timezone"] != tz or record["footprint"] != basis:
                    raise ValueError("surface-water record must match the calculation cycle, timezone and patch footprint")
                if day not in days:
                    continue  # Evidence conditioning the inherited initial checkpoint.
                kind = weather[day]["kind"]
                if kind != "observed" and not (kind == "historical" and weather_policy == "allow_historical"):
                    raise ValueError("surface-water replay requires observed weather or explicit allow_historical; forecasts/scenarios are not observations")
                patches = {p["id"]: p for p in record["data"]["patches"]}
                for patch in days[day]["patches"]:
                    row = patches[patch["id"]]
                    patch["water"] = {k: row[k] for k in ("rainfall_mm", "rainfall_runoff_mm", "irrigation_at_surface_mm", "source")}
            return _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"], inputs,
                operation, parent_id=calculation_id, surface_evidence=[selected[d] for d in sorted(selected)],
                coupled_state_evidence=parent.get("coupled_state_evidence", []))

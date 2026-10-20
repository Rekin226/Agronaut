"""Owned, immutable field calculations and explicit daily-water replay.

No calculator writes here implicitly. Saving a plan and recording completed work
are distinct operations. Record corrections append a revision; replay selects IDs
and replaces whole daily totals rather than adding them to an existing scenario.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import choice, number, text

from .cycle_lifecycle import ensure_cycle_day
from .domains import run_capability
from .farm_store import _owned_unit
from .field_io import _date, _mapping, parse_crop_coefficients, parse_weather_records

SCHEMA = (
    """CREATE TABLE field_calculations (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        cycle_id TEXT REFERENCES production_cycles(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, parent_id TEXT REFERENCES field_calculations(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(unit_id,request_id))""",
    """CREATE TABLE field_water_days (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        cycle_id TEXT REFERENCES production_cycles(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, day TEXT NOT NULL,
        supersedes_id TEXT UNIQUE REFERENCES field_water_days(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(unit_id,request_id))""",
    "CREATE INDEX idx_field_calculations_unit ON field_calculations(unit_id,created_at,id)",
    "CREATE INDEX idx_field_water_days_unit ON field_water_days(unit_id,day)",
)


def migrate_field_workflow(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _scope(conn, owner, unit_id, cycle_id=None):
    text("owner", owner)
    text("unit_id", unit_id)
    unit = _owned_unit(conn, owner, unit_id)
    if unit["production_system"] != "soil":
        raise ValueError("field workflows require a soil production unit")
    farm = conn.execute("SELECT timezone FROM farms WHERE id=?", (unit["farm_id"],)).fetchone()
    if not farm[0]:
        raise ValueError("field workflows require a farm timezone")
    cycle = None
    if cycle_id is not None:
        text("cycle_id", cycle_id)
        cycle = conn.execute("SELECT * FROM production_cycles WHERE id=? AND unit_id=?",
                             (cycle_id, unit_id)).fetchone()
        if cycle is None:
            raise ValueError("cycle does not belong to the selected unit")
    return farm[0], cycle


def _read(conn, table, unit_id, identifier):
    # table is a code-owned constant at every call site.
    text("record ID", identifier)
    row = conn.execute(f"SELECT payload FROM {table} WHERE id=? AND unit_id=?",
                       (identifier, unit_id)).fetchone()
    if row is None:
        raise ValueError("unknown record for the selected unit")
    return json.loads(row[0])


def _retry(conn, table, unit_id, request_id, operation):
    text("request_id", request_id)
    row = conn.execute(f"SELECT payload FROM {table} WHERE unit_id=? AND request_id=?",
                       (unit_id, request_id)).fetchone()
    if row:
        existing = json.loads(row[0])
        if _json(existing["operation"]) != _json(operation):
            raise ValueError("request_id already used with different data")
        return existing
    return None


def _rainfall_input(record):
    data = record["data"]
    return dict(day=record["day"], rainfall_mm=data["rainfall_mm"], source=data["source"],
                provenance=data["provenance"], quality=data["quality"], water_record_id=record["id"])


def _apply_water_record(inputs, record):
    """Replace daily totals without changing weather values, labels or source snapshots."""
    overrides = {entry["day"]: entry for entry in inputs.get("rainfall_overrides", [])}
    overrides[record["day"]] = _rainfall_input(record)
    inputs["rainfall_overrides"] = [overrides[day] for day in sorted(overrides)]
    data = record["data"]
    evidence = f"Daily water record {record['id']}: {data['provenance']}, {data['quality']}; {data['source']}"
    for event in inputs.get("events", inputs.get("days", [])):
        if event["day"] == record["day"]:
            event.update(net_irrigation_mm=data["net_irrigation_mm"], runoff_mm=data["runoff_mm"])
            # Dual-demand daily rows also contain agronomic coefficients. Retain
            # their source independently from the adopted irrigation/runoff log.
            event["source" if "events" in inputs else "water_source"] = evidence
            return
    raise ValueError("daily water record has no matching calculation event")


def _validate_applied_water(conn, unit_id, cycle_id, inputs, applied_records):
    """Saved record IDs are owned evidence, not labels an import can forge."""
    referenced = {r["water_record_id"]: r for r in inputs.get("rainfall_overrides", []) if r.get("water_record_id")}
    if len(referenced) != sum(bool(r.get("water_record_id")) for r in inputs.get("rainfall_overrides", [])):
        raise ValueError("a daily water record cannot override more than one day")
    if set(referenced) != set(applied_records):
        raise ValueError("water record references require explicit replay of the matching owned records")
    if not applied_records:
        return
    events = {e["day"]: e for e in inputs.get("events", inputs.get("days", []))}
    weather = {w["day"]: w for w in inputs.get("weather", [])}
    for identifier in applied_records:
        record = _read(conn, "field_water_days", unit_id, identifier)
        if record["cycle_id"] != cycle_id or referenced[identifier] != _rainfall_input(record):
            raise ValueError("rainfall override must match the exact daily water record and crop cycle")
        data = record["data"]
        event = events.get(record["day"], {})
        if data["quality"] == "rejected" or any(event.get(k) != data[k] for k in ("net_irrigation_mm", "runoff_mm")):
            raise ValueError("applied daily water must retain accepted recorded irrigation and runoff totals")
        if weather[record["day"]]["kind"] not in ("observed", "historical"):
            raise ValueError("applied daily water requires observed or historical weather; forecasts and scenarios are not accepted")


def _calculate(conn, owner, unit_id, cycle_id, request_id, task, inputs, operation,
               parent_id=None, applied_records=(), stage_evidence=(), soil_evidence=(), surface_evidence=(), coupled_state_evidence=()):
    tz, cycle = _scope(conn, owner, unit_id, cycle_id)
    result = run_capability("soil", task, inputs)
    if task in ("crop_water", "root_zone_budget"):
        _validate_applied_water(conn, unit_id, cycle_id, inputs, applied_records)
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        if cycle:
            if inputs["crop"]["name"].casefold() != cycle["crop"].casefold():
                raise ValueError("calculation crop must match the selected cycle")
            if inputs["weather"][0]["day"] < cycle["planted_on"]:
                raise ValueError("calculation weather starts before the crop cycle")
            periods = inputs["crop"].get("coefficient_schedule", [])
            if periods and periods[0]["start"] < cycle["planted_on"]:
                raise ValueError("coefficient schedule starts before the crop cycle")
    if task in ("surface_advance", "drip_lateral", "drip_zone", "drip_manifold", "sprinkler_lateral") and inputs["site"]["timezone"] != tz:
        raise ValueError("calculation timezone must match the farm timezone")
    if task == "surface_evaluation":
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        started = datetime.fromisoformat(inputs["event"]["started_at"])
        if cycle and started.astimezone(ZoneInfo(tz)).date().isoformat() < cycle["planted_on"]:
            raise ValueError("surface event precedes the crop cycle")
        if inputs["event"]["kind"] == "observed":
            ended = datetime.fromisoformat(result["result"]["event_ended_at"])
            if ended > datetime.now(timezone.utc):
                raise ValueError("observed surface evaluation requires a completed event")
    if task == "nutrient_budget":
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        if cycle and inputs["crop"]["name"].casefold() != cycle["crop"].casefold():
            raise ValueError("calculation crop must match the selected cycle")
    if task in ("sprinkler_catch_test", "drip_emitter_test"):
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        if cycle:
            started = datetime.fromisoformat(inputs["event"]["started_at"]).astimezone(ZoneInfo(tz)).date().isoformat()
            ended = datetime.fromisoformat(result["result"]["event_ended_at"]).date().isoformat()
            if started < cycle["planted_on"]:
                raise ValueError("irrigation test precedes the crop cycle")
            ensure_cycle_day(conn, unit_id, cycle_id, started)
            ensure_cycle_day(conn, unit_id, cycle_id, ended)
    if task == "soil_profile":
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        if cycle:
            day = datetime.fromisoformat(result["result"]["observed_at_local"]).date().isoformat()
            if day < cycle["planted_on"]:
                raise ValueError("soil profile precedes the crop cycle; save preplant assessments without a cycle")
            ensure_cycle_day(conn, unit_id, cycle_id, day)
    if task == "dual_crop_water":
        _validate_applied_water(conn, unit_id, cycle_id, inputs, applied_records)
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        if cycle:
            if inputs["crop"]["name"].casefold() != cycle["crop"].casefold():
                raise ValueError("calculation crop must match the selected cycle")
            if inputs["weather"][0]["day"] < cycle["planted_on"]:
                raise ValueError("dual crop-water days precede the crop cycle")
            ensure_cycle_day(conn, unit_id, cycle_id, inputs["weather"][-1]["day"])
    if task == "coupled_soil_water":
        if inputs["site"]["timezone"] != tz:
            raise ValueError("calculation timezone must match the farm timezone")
        if cycle:
            if inputs["crop"]["name"].casefold() != cycle["crop"].casefold():
                raise ValueError("calculation crop must match the selected cycle")
            if result["result"]["period_start"] < cycle["planted_on"]:
                raise ValueError("coupled water days precede the crop cycle")
            ensure_cycle_day(conn, unit_id, cycle_id, result["result"]["period_end"])
    if task not in ("crop_water", "root_zone_budget", "drip_design", "drip_lateral", "drip_zone", "drip_manifold", "sprinkler_lateral", "sprinkler_design", "surface_evaluation", "surface_advance", "nutrient_budget", "sprinkler_catch_test", "drip_emitter_test", "soil_profile", "dual_crop_water", "coupled_soil_water"):
        raise ValueError("this task cannot be saved in the field workflow")
    saved = dict(id=uuid.uuid4().hex, owner_id=owner, unit_id=unit_id, cycle_id=cycle_id,
                 request_id=request_id, parent_id=parent_id, created_at=datetime.now(timezone.utc).isoformat(),
                 task=task, operation=operation, inputs=inputs, result=result,
                 cycle_snapshot=dict(cycle) if cycle is not None else None,
                 applied_water_record_ids=list(applied_records))
    if stage_evidence:
        saved["crop_stage_evidence"] = list(stage_evidence)
    if soil_evidence:
        saved["soil_state_evidence"] = list(soil_evidence)
    if surface_evidence:
        saved["surface_water_evidence"] = list(surface_evidence)
    if coupled_state_evidence:
        saved["coupled_state_evidence"] = list(coupled_state_evidence)
    from .irrigation_zones import zone_snapshot
    if zone := zone_snapshot(conn, unit_id):
        saved["zone_snapshot"] = zone
    conn.execute("INSERT INTO field_calculations VALUES(?,?,?,?,?,?,?)",
                 (saved["id"], unit_id, cycle_id, request_id, parent_id, saved["created_at"], _json(saved)))
    return saved


def record_water_day(conn, owner, unit_id, data, *, request_id, cycle_id=None, supersedes_id=None, intake=None):
    """Append daily totals in the caller's transaction, retaining the public record contract."""
    data = _mapping(data, ("day", "rainfall_mm", "net_irrigation_mm", "runoff_mm", "source",
                           "provenance", "quality"), "daily water record")
    day = _date(data.get("day"))
    for key in ("rainfall_mm", "net_irrigation_mm", "runoff_mm"):
        number(key, data.get(key), 0)
    number("runoff_mm", data["runoff_mm"], 0, data["rainfall_mm"])
    text("source", data.get("source"))
    choice("provenance", data.get("provenance"), ("measured", "reported", "estimated"))
    choice("quality", data.get("quality"), ("unverified", "checked", "rejected"))
    operation = dict(kind="record_day", data=data, cycle_id=cycle_id, supersedes_id=supersedes_id)
    if intake is not None:
        operation["intake"] = intake
    tz, cycle = _scope(conn, owner, unit_id, cycle_id)
    existing = _retry(conn, "field_water_days", unit_id, request_id, operation)
    if existing:
        return existing
    now = datetime.now(timezone.utc)
    if day >= now.astimezone(ZoneInfo(tz)).date():
        raise ValueError("daily totals require a completed local calendar day")
    if cycle and data["day"] < cycle["planted_on"]:
        raise ValueError("daily record precedes the crop cycle")
    if data["quality"] != "rejected":
        ensure_cycle_day(conn, unit_id, cycle_id, data["day"])
    prior = conn.execute(
        "SELECT id FROM field_water_days WHERE unit_id=? AND cycle_id IS ? AND day=?",
        (unit_id, cycle_id, data["day"])).fetchall()
    if supersedes_id is None and prior:
        raise ValueError("daily totals already exist; correct them with supersedes_id")
    if supersedes_id is not None:
        if supersedes_id not in {r[0] for r in prior}:
            raise ValueError("correction must supersede a record for the same unit, cycle and day")
        if conn.execute("SELECT 1 FROM field_water_days WHERE supersedes_id=?",
                        (supersedes_id,)).fetchone():
            raise ValueError("record already superseded; correct its latest revision")
    saved = dict(id=uuid.uuid4().hex, owner_id=owner, unit_id=unit_id, cycle_id=cycle_id,
                 request_id=request_id, timezone=tz, day=data["day"], supersedes_id=supersedes_id,
                 created_at=now.isoformat(), data=data, operation=operation)
    conn.execute("INSERT INTO field_water_days VALUES(?,?,?,?,?,?,?,?)",
                 (saved["id"], unit_id, cycle_id, request_id, data["day"], supersedes_id,
                  saved["created_at"], _json(saved)))
    return saved


class FieldWorkflow:
    def __init__(self, db):
        self.db = db

    def fetch_weather(self, owner: str, unit_id: str, latitude: float, longitude: float,
                      start: str, end: str) -> dict:
        from .weather_provider import fetch_weather
        with self.db.transaction() as conn:
            tz, _ = _scope(conn, owner, unit_id)
        # Release the database transaction before network I/O. Fetching is read-only.
        return fetch_weather(dict(latitude=latitude, longitude=longitude, start=start, end=end, timezone=tz))

    def save(self, owner: str, unit_id: str, task: str, inputs: dict, *, request_id: str,
             cycle_id: str | None = None) -> dict:
        # JSON snapshot detaches caller-owned mutable values and rejects non-finite input.
        inputs = json.loads(_json(inputs))
        operation = dict(kind="save", task=task, inputs=inputs, cycle_id=cycle_id)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id, cycle_id)
            existing = _retry(conn, "field_calculations", unit_id, request_id, operation)
            return existing or _calculate(conn, owner, unit_id, cycle_id, request_id, task, inputs, operation)

    def get(self, owner: str, unit_id: str, calculation_id: str) -> dict:
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            return _read(conn, "field_calculations", unit_id, calculation_id)

    def continue_coupled_water(self, owner, unit_id, calculation_id, weather, days, reason, *, request_id, weather_sources=None):
        """Save a child calculation from the exact ending state of an owned replay."""
        text("continuation reason", reason)
        operation = json.loads(_json(dict(kind="continue_coupled_water", calculation_id=calculation_id,
            weather=weather, days=days, reason=reason, weather_sources=weather_sources)))
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            if existing := _retry(conn, "field_calculations", unit_id, request_id, operation):
                return existing
            parent = _read(conn, "field_calculations", unit_id, calculation_id)
            if parent["task"] != "coupled_soil_water":
                raise ValueError("continuation requires a coupled soil-water calculation")
            from .coupled_state_workflow import (
                continuation_checkpoint,
                validate_state_evidence,
            )
            from .surface_water_records import validate_inherited
            validate_inherited(conn, parent)
            validate_state_evidence(conn, parent)
            tz, cycle = _scope(conn, owner, unit_id, parent["cycle_id"])
            if cycle:
                from .crop_establishment import calculation_cycle_review
                from .cycle_lifecycle import lifecycle
                review = calculation_cycle_review(parent, dict(cycle), tz, lifecycle(conn, unit_id, parent["cycle_id"]))
                if review["changed_fields"] or review["conflicts"]:
                    raise ValueError("parent crop-cycle context changed; review the initialization before continuing")
            inputs = json.loads(_json(parent["inputs"]))
            inputs["initial_state"] = dict(checkpoint=continuation_checkpoint(parent),
                                           source=f"Continuation of owned calculation {calculation_id}: {reason}")
            inputs["weather"], inputs["days"] = operation["weather"], operation["days"]
            inputs.pop("weather_sources", None)
            if weather_sources is not None:
                inputs["weather_sources"] = operation["weather_sources"]
            return _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"],
                              inputs, operation, parent_id=calculation_id, surface_evidence=parent.get("surface_water_evidence", []),
                              coupled_state_evidence=parent.get("coupled_state_evidence", []))

    def review_soil(self, owner, unit_id, **data):
        from .soil_workflow import review_soil
        return review_soil(self, owner, unit_id, **data)

    def compare_soil(self, owner, unit_id, **data):
        from .soil_evaluation import compare_soil
        return compare_soil(self, owner, unit_id, **data)

    def compare_surface_advance(self, owner, unit_id, **data):
        from .surface_diagnostics import compare_surface_advance
        return compare_surface_advance(self, owner, unit_id, **data)

    def compare_drip_lateral(self, owner, unit_id, **data):
        from .drip_diagnostics import compare_drip_lateral
        return compare_drip_lateral(self, owner, unit_id, **data)

    def compare_drip_network(self, owner, unit_id, **data):
        from .drip_diagnostics import compare_drip_network
        return compare_drip_network(self, owner, unit_id, **data)

    def describe_sprinkler_comparison(self, owner, unit_id):
        from .sprinkler_diagnostics import describe_sprinkler_comparison
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            return describe_sprinkler_comparison()

    def review_sprinkler_measurements(self, owner, unit_id, **data):
        from .sprinkler_diagnostics import review_sprinkler_measurements
        return review_sprinkler_measurements(self, owner, unit_id, **data)

    def compare_sprinkler_lateral(self, owner, unit_id, **data):
        from .sprinkler_diagnostics import compare_sprinkler_lateral
        return compare_sprinkler_lateral(self, owner, unit_id, **data)

    def review_target(self, owner, unit_id, calculation_id):
        from .equipment_targets import review
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            saved = _read(conn, "field_calculations", unit_id, calculation_id)
            if saved["task"] not in ("drip_design", "sprinkler_design"):
                raise ValueError("water-target review requires a drip or sprinkler equipment calculation")
            return review(conn, owner, unit_id, saved)

    def apply_soil_state(self, owner, unit_id, **data):
        from .soil_workflow import apply_soil_state
        return apply_soil_state(self, owner, unit_id, **data)

    def remove_soil_state(self, owner, unit_id, **data):
        from .soil_workflow import remove_soil_state
        return remove_soil_state(self, owner, unit_id, **data)

    def revise_roots(self, owner, unit_id, **data):
        from .soil_workflow import revise_roots
        return revise_roots(self, owner, unit_id, **data)

    def review_crop(self, owner: str, unit_id: str, calculation_id: str) -> dict:
        """Current journal status beside immutable coefficient assumptions and linked evidence."""
        from .crop_establishment import calculation_cycle_review
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            saved = _read(conn, "field_calculations", unit_id, calculation_id)
            if saved["task"] not in ("crop_water", "root_zone_budget"):
                raise ValueError("crop coefficient review requires crop_water or root_zone_budget")
            tz, cycle = _scope(conn, owner, unit_id, saved["cycle_id"])
            records = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM crop_records WHERE unit_id=? AND cycle_id=? AND kind='stage' ORDER BY rowid",
                (unit_id, saved["cycle_id"]))] if cycle else []
            successors = {r["supersedes_id"]: r for r in records if r["supersedes_id"]}
            linked = []
            for record in saved.get("crop_stage_evidence", []):
                next_record = successors.get(record["id"])
                linked.append(dict(snapshot=record, superseded=next_record is not None,
                                   replacement_id=next_record["id"] if next_record else None))
            return dict(calculation_id=calculation_id, crop=saved["inputs"]["crop"],
                        linked_stage_reports=linked,
                        current_stage_reports=[r for r in records if r["kind"] == "stage"
                                               and not r["voided"] and r["id"] not in successors],
                        cycle_review=calculation_cycle_review(saved, dict(cycle), tz) if cycle else None,
                        needs_stage_review=any(r["superseded"] for r in linked),
                        note="Stage reports may cover only part of the crop. Linking them does not establish Kc values "
                             "or phase dates. Corrections do not automatically change saved coefficients or root depth.")

    def revise_crop(self, owner: str, unit_id: str, calculation_id: str, coefficients: dict, reason: str, *,
                    request_id: str, stage_record_ids: list[str] | None = None) -> dict:
        coefficients = json.loads(_json(_mapping(coefficients, ("coefficient", "coefficient_schedule"), "coefficients")))
        text("reason", reason)
        parse_crop_coefficients(dict(name="validation", **coefficients))
        identifiers = [] if stage_record_ids is None else stage_record_ids
        if not isinstance(identifiers, list):
            raise ValueError("stage_record_ids must be an array")
        for identifier in identifiers:
            text("stage record ID", identifier)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate stage record ID")
        operation = dict(kind="revise_crop", calculation_id=calculation_id, coefficients=coefficients,
                         reason=reason, stage_record_ids=sorted(identifiers))
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            existing = _retry(conn, "field_calculations", unit_id, request_id, operation)
            if existing:
                return existing
            parent = _read(conn, "field_calculations", unit_id, calculation_id)
            if parent["task"] not in ("crop_water", "root_zone_budget"):
                raise ValueError("crop revisions require crop_water or root_zone_budget")
            evidence = []
            for identifier in sorted(identifiers):
                record = _read(conn, "crop_records", unit_id, identifier)
                if record["kind"] != "stage" or record["cycle_id"] != parent["cycle_id"] or record["voided"]:
                    raise ValueError("select stage reports for this calculation's crop cycle")
                if conn.execute("SELECT 1 FROM crop_records WHERE supersedes_id=?", (identifier,)).fetchone():
                    raise ValueError("stage report is superseded; select its current revision")
                evidence.append(record)
            inputs = parent["inputs"]
            inputs["crop"] = dict(name=inputs["crop"]["name"], **coefficients)
            for identifier in parent["applied_water_record_ids"]:
                _apply_water_record(inputs, _read(conn, "field_water_days", unit_id, identifier))
            return _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"], inputs,
                              operation, parent_id=calculation_id, applied_records=parent["applied_water_record_ids"],
                              stage_evidence=evidence, soil_evidence=parent.get("soil_state_evidence", []))

    def list(self, owner: str, unit_id: str) -> dict:
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            plans = []
            for row in conn.execute("SELECT payload FROM field_calculations WHERE unit_id=? ORDER BY rowid",
                                    (unit_id,)):
                plan = json.loads(row[0])
                plans.append({key: plan[key] for key in ("id", "task", "cycle_id", "parent_id", "created_at")})
            records = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM field_water_days WHERE unit_id=? ORDER BY day,rowid", (unit_id,))]
            superseded = {r["supersedes_id"] for r in records if r["supersedes_id"]}
            return dict(calculations=plans, water_days=[dict(r, superseded=r["id"] in superseded) for r in records])

    def record_day(self, owner: str, unit_id: str, data: dict, *, request_id: str,
                   cycle_id: str | None = None, supersedes_id: str | None = None) -> dict:
        with self.db.transaction() as conn:
            return record_water_day(conn, owner, unit_id, data, request_id=request_id,
                                    cycle_id=cycle_id, supersedes_id=supersedes_id)

    def replay(self, owner: str, unit_id: str, calculation_id: str, water_record_ids: list[str], *,
               request_id: str, weather_policy: str = "observed_only") -> dict:
        choice("weather_policy", weather_policy, ("observed_only", "allow_historical"))
        if not isinstance(water_record_ids, list) or not water_record_ids:
            raise ValueError("select a nonempty list of daily water record IDs")
        for record_id in water_record_ids:
            text("water record ID", record_id)
        if len(set(water_record_ids)) != len(water_record_ids):
            raise ValueError("duplicate daily water record ID")
        operation = dict(kind="replay", calculation_id=calculation_id, water_record_ids=sorted(water_record_ids))
        if weather_policy != "observed_only":
            operation["weather_policy"] = weather_policy
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            existing = _retry(conn, "field_calculations", unit_id, request_id, operation)
            if existing:
                return existing
            parent = _read(conn, "field_calculations", unit_id, calculation_id)
            if parent["task"] not in ("root_zone_budget", "dual_crop_water"):
                raise ValueError("daily water replay requires a root_zone_budget or dual_crop_water calculation")
            inputs = parent["inputs"]  # decoded snapshot, never modifies the saved parent
            weather = {w["day"]: w for w in inputs["weather"]}
            applied_days = set()
            selected = {}
            # Inherited applied records remain visible, even when only one day is corrected.
            for record_id in parent["applied_water_record_ids"]:
                record = _read(conn, "field_water_days", unit_id, record_id)
                selected[record["day"]] = record_id
            for record_id in water_record_ids:
                record = _read(conn, "field_water_days", unit_id, record_id)
                data = record["data"]
                day = record["day"]
                if record["cycle_id"] != parent["cycle_id"]:
                    raise ValueError("daily water record belongs to a different cycle")
                if day in applied_days:
                    raise ValueError("select only one revision of a daily total")
                if day not in weather:
                    raise ValueError("daily water record falls outside the calculation weather window")
                if data["quality"] == "rejected":
                    raise ValueError("rejected daily water records cannot update a calculation")
                kind = weather[day]["kind"]
                if kind != "observed" and not (kind == "historical" and weather_policy == "allow_historical"):
                    raise ValueError("daily water replay requires observed meteorology, or explicit allow_historical "
                                     "for historical estimates; forecast and scenario weather are not accepted")
                applied_days.add(day)
                selected[day] = record_id
            applied = [selected[day] for day in sorted(selected)]
            for record_id in applied:
                _apply_water_record(inputs, _read(conn, "field_water_days", unit_id, record_id))
            return _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"],
                              inputs, operation, parent_id=calculation_id, applied_records=applied,
                              stage_evidence=parent.get("crop_stage_evidence", []),
                              soil_evidence=parent.get("soil_state_evidence", []))

    def revise_weather(self, owner: str, unit_id: str, calculation_id: str, weather: list[dict], *,
                       request_id: str, events: list[dict] | None = None,
                       weather_sources: list[dict] | None = None, root_zone_days: list[dict] | None = None,
                       dual_days: list[dict] | None = None) -> dict:
        """Replace complete dated weather records or append days; preserve saved water actions.

        Existing events and the initial soil state are immutable within this operation.
        Appended budget days require explicit event assumptions. Applied daily-water
        revisions retain precedence over rainfall supplied by a weather provider.
        Dual crop demand retains its coefficients, wetting fractions and irrigation;
        appended dates require complete dual_days with explicit sourced assumptions.
        """
        weather = json.loads(_json(weather))
        events = json.loads(_json(events)) if events is not None else []
        if not isinstance(weather, list) or not weather:
            raise ValueError("weather revision requires a nonempty array of complete weather records")
        if not isinstance(events, list):
            raise ValueError("events must be an array for appended budget days")
        # Patches may cover nonadjacent days. Validate each complete record before
        # merging, then let the calculator validate the entire consecutive window.
        patches = {}
        for raw in weather:
            try:
                parsed = parse_weather_records([raw])[0]
            except OverflowError as exc:
                raise ValueError("weather revision dates fall outside supported calendar boundaries") from exc
            if raw["day"] in patches:
                raise ValueError("duplicate weather revision day")
            patches[raw["day"]] = parsed
        operation = dict(kind="revise_weather", calculation_id=calculation_id, weather=weather, events=events)
        if dual_days is not None:
            dual_days = json.loads(_json(dual_days))
            if not isinstance(dual_days, list):
                raise ValueError("dual_days must be an array for appended days")
            operation["dual_days"] = dual_days
        if root_zone_days is not None:
            root_zone_days = json.loads(_json(root_zone_days))
            if not isinstance(root_zone_days, list):
                raise ValueError("root_zone_days must be an array for appended days")
            operation["root_zone_days"] = root_zone_days
        if weather_sources is not None:
            weather_sources = json.loads(_json(weather_sources))
            if not isinstance(weather_sources, list):
                raise ValueError("weather_sources must be an array")
            operation["weather_sources"] = weather_sources
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            existing = _retry(conn, "field_calculations", unit_id, request_id, operation)
            if existing:
                return existing
            parent = _read(conn, "field_calculations", unit_id, calculation_id)
            if parent["task"] not in ("crop_water", "root_zone_budget", "dual_crop_water"):
                raise ValueError("weather revisions require crop_water, root_zone_budget or dual_crop_water")
            inputs = parent["inputs"]
            previous = {w["day"]: w for w in inputs["weather"]}
            appended = set(patches) - set(previous)
            if any(day < inputs["weather"][0]["day"] for day in appended):
                raise ValueError("weather revisions cannot prepend days or change the initial soil state")
            for raw in weather:
                prior = previous.get(raw["day"])
                updated = patches[raw["day"]]
                if prior:
                    if prior["kind"] == "observed" and updated.kind != "observed":
                        raise ValueError("observed meteorology cannot be replaced by forecast, historical or scenario data")
                    if prior["kind"] == updated.kind == "forecast":
                        basis = "issued_at" if prior.get("issued_at") and updated.issued_at else "retrieved_at"
                        if basis == "retrieved_at" and (prior.get("issued_at") or updated.issued_at):
                            raise ValueError("forecast revisions must use the same timestamp basis")
                        stamp = getattr(updated, basis, None)
                        if not prior.get(basis) or stamp is None or stamp <= datetime.fromisoformat(prior[basis]):
                            raise ValueError(f"replacement forecast must have a newer {basis}")
                previous[raw["day"]] = dict(raw)
            inputs["weather"] = [previous[day] for day in sorted(previous)]
            if weather_sources is not None or "weather_sources" in inputs:
                snapshots = {}
                for source in [*inputs.get("weather_sources", []), *(weather_sources or [])]:
                    if not isinstance(source, dict) or not isinstance(source.get("id"), str):
                        raise ValueError("invalid weather source snapshot")
                    if source["id"] in snapshots and snapshots[source["id"]] != source:
                        raise ValueError("conflicting weather source snapshots")
                    snapshots[source["id"]] = source
                used = {w.get("source_snapshot_id") for w in inputs["weather"]}
                inputs["weather_sources"] = [s for key, s in snapshots.items() if key in used]
            if parent["task"] == "root_zone_budget":
                additions = {}
                for raw in events:
                    event = _mapping(raw, ("day", "net_irrigation_mm", "runoff_mm", "source"), "event")
                    _date(event.get("day"))
                    if event["day"] in additions:
                        raise ValueError("duplicate appended event day")
                    additions[event["day"]] = event
                if set(additions) != appended:
                    raise ValueError("events must cover exactly appended weather days; existing events are preserved")
                inputs["events"].extend(additions[day] for day in sorted(additions))
            elif events:
                raise ValueError(f"{parent['task']} weather revisions do not accept events")

            if parent["task"] == "dual_crop_water":
                additions = {}
                for raw in dual_days or []:
                    day = _date(raw.get("day") if isinstance(raw, dict) else None).isoformat()
                    if day in additions:
                        raise ValueError("duplicate appended dual day")
                    additions[day] = raw
                if set(additions) != appended:
                    raise ValueError("dual_days must cover exactly appended weather days; existing coefficients and irrigation are preserved")
                inputs["days"].extend(additions[day] for day in sorted(additions))
            elif dual_days:
                raise ValueError("dual_days requires a dual_crop_water calculation")

            if inputs.get("root_zone_schedule"):
                root_additions = {}
                for raw in root_zone_days or []:
                    day = _date(raw.get("day") if isinstance(raw, dict) else None)
                    if day.isoformat() in root_additions:
                        raise ValueError("duplicate appended root-zone day")
                    root_additions[day.isoformat()] = raw
                if set(root_additions) != appended:
                    raise ValueError("root_zone_days must cover exactly appended weather days; existing root depths are preserved")
                inputs["root_zone_schedule"].extend(root_additions[day] for day in sorted(root_additions))
            elif root_zone_days:
                raise ValueError("use revise_roots to establish a root-zone schedule before appending root_zone_days")

            # Use the exact applied revisions, even if newer log corrections now
            # exist. Adopting a correction remains an explicit replay operation.
            for record_id in parent["applied_water_record_ids"]:
                record = _read(conn, "field_water_days", unit_id, record_id)
                _apply_water_record(inputs, record)
            return _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"],
                              inputs, operation, parent_id=calculation_id,
                              applied_records=parent["applied_water_record_ids"],
                              stage_evidence=parent.get("crop_stage_evidence", []),
                              soil_evidence=parent.get("soil_state_evidence", []))


def execute_field_workflow(db, owner: str, unit_id: str, operation: str, data: dict) -> dict:
    from .coupled_state_workflow import CoupledStateWorkflow
    from .equipment_revisions import EquipmentRevisions
    from .field_consultation import FieldConsultation
    from .irrigation_zones import IrrigationZones
    from .soil_reports import SoilReports
    from .surface_water_records import SurfaceWaterRecords
    from .water_log_consultation import WaterLogConsultation
    from .weather_review import WeatherReview
    water_log = WaterLogConsultation(db)
    intake = FieldConsultation(db)
    soils = SoilReports(db)
    zones = IrrigationZones(db)
    surface = SurfaceWaterRecords(db)
    coupled_state = CoupledStateWorkflow(db)
    equipment = EquipmentRevisions(db)
    """Shared interface contract. Identity/unit scope never come from the data object."""
    workflow = FieldWorkflow(db)
    allowed = {
        "preview_equipment_revision": ("calculation_id", "patch", "reason", "target_policy"),
        "revise_equipment": ("calculation_id", "patch", "reason", "target_policy", "preview_token", "request_id"),
        "start_water_log": ("language",), "get_water_log": (),
        "answer_water_log": ("answer", "expected_revision", "back"),
        "save_water_log": ("draft_id", "expected_revision"), "cancel_water_log": ("expected_revision",),
        "review_coupled_state": ("calculation_id",),
        "apply_coupled_state": ("calculation_id", "readings", "evaporation_memories", "dataset_kind", "reason", "request_id"),
        "remove_coupled_state": ("calculation_id", "reason", "request_id"),
        "record_surface_day": ("calculation_id", "data", "request_id", "supersedes_id", "reason"),
        "review_surface_water": ("calculation_id",),
        "replay_surface_water": ("calculation_id", "record_ids", "request_id", "weather_policy"),
        "describe_zones": (), "list_zones": (), "create_zone": ("data", "request_id"),
        "save": ("task", "inputs", "request_id", "cycle_id"),
        "get": ("calculation_id",), "list": (),
        "describe_soil_tests": (), "list_soil_tests": ("cycle_id",), "get_soil_test": ("report_id",),
        "record_soil_test": ("data", "request_id", "cycle_id", "supersedes_id", "reason"),
        "void_soil_test": ("report_id", "reason", "request_id"),
        "fetch_weather": ("latitude", "longitude", "start", "end"),
        "review_weather": ("calculation_id", "max_age_hours", "as_of"),
        "review_crop": ("calculation_id",),
        "review_soil": ("calculation_id",),
        "compare_soil": ("calculation_id", "comparisons", "dataset_kind", "selection_note"),
        "compare_surface_advance": ("calculation_id", "comparisons", "dataset_kind", "selection_note"),
        "compare_drip_network": ("calculation_id", "comparisons", "dataset_kind", "selection_note"),
        "compare_drip_lateral": ("calculation_id", "comparisons", "dataset_kind", "selection_note"),
        "describe_sprinkler_comparison": (),
        "review_sprinkler_measurements": ("calculation_id",),
        "compare_sprinkler_lateral": ("calculation_id", "comparisons", "dataset_kind", "selection_note"),
        "review_target": ("calculation_id",),
        "apply_soil_state": ("calculation_id", "observation_id", "as_of", "represented_depth_m", "representation_note",
                             "depletion_adjustment_mm", "adjustment_source", "reason", "expected_review", "request_id"),
        "remove_soil_state": ("calculation_id", "as_of", "reason", "request_id"),
        "revise_roots": ("calculation_id", "root_zone_schedule", "reason", "request_id"),
        "start_intake": ("language",), "get_intake": (),
        "answer_intake": ("answer", "expected_revision", "back"),
        "cancel_intake": ("expected_revision",), "save_intake": ("draft_id", "expected_revision"),
        "revise_crop": ("calculation_id", "coefficients", "reason", "request_id", "stage_record_ids"),
        "record_day": ("data", "request_id", "cycle_id", "supersedes_id"),
        "replay": ("calculation_id", "water_record_ids", "request_id", "weather_policy"),
        "continue_coupled_water": ("calculation_id", "weather", "days", "reason", "request_id", "weather_sources"),
        "revise_weather": ("calculation_id", "weather", "events", "weather_sources", "root_zone_days", "dual_days", "request_id"),
    }
    choice("operation", operation, allowed)
    args = _mapping(data, allowed[operation], "workflow operation")
    handlers = {"start_water_log": water_log.start, "get_water_log": water_log.get,
                "preview_equipment_revision": equipment.preview, "revise_equipment": equipment.save,
                "answer_water_log": water_log.answer, "save_water_log": water_log.save, "cancel_water_log": water_log.cancel,
                "save": workflow.save, "get": workflow.get, "list": workflow.list,
                "review_coupled_state": coupled_state.review, "apply_coupled_state": coupled_state.apply, "remove_coupled_state": coupled_state.remove,
                "record_surface_day": surface.record, "review_surface_water": surface.review, "replay_surface_water": surface.replay,
                "describe_zones": zones.describe, "list_zones": zones.list, "create_zone": zones.create,
                "describe_soil_tests": soils.describe, "list_soil_tests": soils.list, "get_soil_test": soils.get,
                "record_soil_test": soils.save, "void_soil_test": soils.void,
                "record_day": workflow.record_day, "replay": workflow.replay,
                "continue_coupled_water": workflow.continue_coupled_water,
                "revise_weather": workflow.revise_weather, "fetch_weather": workflow.fetch_weather,
                "review_weather": WeatherReview(db).review,
                "review_crop": workflow.review_crop, "revise_crop": workflow.revise_crop,
                "review_soil": workflow.review_soil, "apply_soil_state": workflow.apply_soil_state,
                "compare_soil": workflow.compare_soil,
                "compare_surface_advance": workflow.compare_surface_advance,
                "compare_drip_lateral": workflow.compare_drip_lateral,
                "compare_drip_network": workflow.compare_drip_network,
                "describe_sprinkler_comparison": workflow.describe_sprinkler_comparison,
                "review_sprinkler_measurements": workflow.review_sprinkler_measurements,
                "compare_sprinkler_lateral": workflow.compare_sprinkler_lateral,
                "review_target": workflow.review_target,
                "remove_soil_state": workflow.remove_soil_state, "revise_roots": workflow.revise_roots,
                "start_intake": intake.start, "get_intake": intake.get, "answer_intake": intake.answer,
                "cancel_intake": intake.cancel, "save_intake": intake.save}
    try:
        return handlers[operation](owner, unit_id, **args)
    except TypeError as exc:
        raise ValueError(f"invalid {operation} request: {exc}") from exc

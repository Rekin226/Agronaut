"""Owner-scoped farm records over Agronaut's SQLite database.

Legacy facts remain intact. Their migration copy has an explicit legacy unit, while
ambiguous conversation history stays at user scope. Unit selection in chat is a
separate integration: this store never silently redirects the old memory APIs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from zoneinfo import ZoneInfo

from agro_model.farm import Farm, Observation, ProductionCycle, ProductionUnit

from .cycle_lifecycle import ensure_cycle_day, lifecycle
from .field_io import json_ready
from .observation_quality import reviewed_observation

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS farms (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
        timezone TEXT, legacy INTEGER NOT NULL DEFAULT 0)""",
    """CREATE INDEX IF NOT EXISTS idx_farms_owner ON farms(owner_id)""",
    """CREATE TABLE IF NOT EXISTS production_units (
        id TEXT PRIMARY KEY, farm_id TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
        name TEXT NOT NULL, production_system TEXT, medium TEXT, environment TEXT,
        water_strategy TEXT, irrigation_method TEXT, legacy INTEGER NOT NULL DEFAULT 0)""",
    """CREATE INDEX IF NOT EXISTS idx_units_farm ON production_units(farm_id)""",
    """CREATE TABLE IF NOT EXISTS production_cycles (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        crop TEXT NOT NULL, planted_on TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS unit_facts (
        unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        key TEXT NOT NULL, value TEXT NOT NULL, source TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY(unit_id,key))""",
    """CREATE TABLE IF NOT EXISTS field_observations (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        cycle_id TEXT REFERENCES production_cycles(id) ON DELETE CASCADE,
        observed_at TEXT NOT NULL, payload TEXT NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS idx_field_observations_unit
        ON field_observations(unit_id,observed_at,id)""",
)


def migrate_farms(conn):
    """Run inside the caller's transaction; no commits or side effects outside SQLite."""
    for statement in SCHEMA:
        conn.execute(statement)
    owners = conn.execute("SELECT user_id FROM users UNION SELECT user_id FROM user_facts").fetchall()
    for row in owners:
        owner = row[0]
        facts = dict(conn.execute("SELECT key,value FROM user_facts WHERE user_id=?", (owner,)))
        domain = facts.get("production_system")
        if domain not in ("soil", "hydroponics", "aquaponics"):
            domain = None
        fish = str(facts.get("fish_species", "")).strip().lower()
        if domain is None and fish not in ("", "none", "null", "unknown", "n/a"):
            domain = "aquaponics"
        fid = str(uuid.uuid5(uuid.NAMESPACE_URL, "agronaut:legacy-farm:" + owner))
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "agronaut:legacy-unit:" + owner))
        conn.execute("INSERT OR IGNORE INTO farms(id,owner_id,name,legacy) VALUES(?,?,?,1)",
                     (fid, owner, "Legacy farm"))
        # No inferred timezone, climate, medium or environment: those are still unknown.
        conn.execute("INSERT OR IGNORE INTO production_units"
                     "(id,farm_id,name,production_system,legacy) VALUES(?,?,?,?,1)",
                     (uid, fid, "Legacy system", domain))
        conn.execute("INSERT OR IGNORE INTO unit_facts(unit_id,key,value,source,updated_at) "
                     "SELECT ?,key,value,source,updated_at FROM user_facts WHERE user_id=?",
                     (uid, owner))


def _owned_unit(conn, owner_id, unit_id):
    row = conn.execute("SELECT u.* FROM production_units u JOIN farms f ON f.id=u.farm_id "
                       "WHERE u.id=? AND f.owner_id=?", (unit_id, owner_id)).fetchone()
    if row is None:
        raise ValueError("unknown production unit for this owner")
    return row


def _validate_parent(conn, owner_id, unit):
    if unit.parent_unit_id is None:
        return
    parent = _owned_unit(conn, owner_id, unit.parent_unit_id)
    if parent["farm_id"] != unit.farm_id or parent["production_system"] != "soil" or parent["parent_unit_id"] is not None:
        raise ValueError("a zone requires a top-level soil production unit in the same farm")
    if parent["environment"] != unit.environment:
        raise ValueError("zone environment must match its parent field")


def record_observation_in_transaction(conn, owner_id, observation, *, validate_only=False):
    if observation.owner_id != owner_id:
        raise ValueError("observation owner does not match the current owner")
    payload = json.dumps(json_ready(asdict(observation)), sort_keys=True, allow_nan=False)
    _owned_unit(conn, owner_id, observation.unit_id)
    if observation.cycle_id is not None and conn.execute(
        "SELECT 1 FROM production_cycles WHERE id=? AND unit_id=?",
        (observation.cycle_id, observation.unit_id),
    ).fetchone() is None:
        raise ValueError("cycle does not belong to the observation's unit")
    existing = conn.execute("SELECT payload FROM field_observations WHERE id=?",
                            (observation.id,)).fetchone()
    if existing:
        if existing[0] == payload:
            return False
        raise ValueError("observation ID already exists with different data; record a new ID")
    if observation.cycle_id is not None and observation.provenance != "forecast" and observation.quality != "rejected":
        state = lifecycle(conn, observation.unit_id, observation.cycle_id)
        if state["status"] == "closed":
            tz = conn.execute("SELECT f.timezone FROM farms f JOIN production_units u ON u.farm_id=f.id "
                              "WHERE u.id=?", (observation.unit_id,)).fetchone()[0]
            ensure_cycle_day(conn, observation.unit_id, observation.cycle_id,
                             observation.observed_at.astimezone(ZoneInfo(tz)).date().isoformat())
    if validate_only:
        return True
    conn.execute("INSERT INTO field_observations(id,unit_id,cycle_id,observed_at,payload) "
                 "VALUES(?,?,?,?,?)", (observation.id, observation.unit_id,
                                       observation.cycle_id,
                                       observation.observed_at.isoformat(), payload))
    return True


class FarmStore:
    def __init__(self, db):
        self.db = db

    def create_farm(self, farm: Farm):
        self.db.execute("INSERT INTO farms(id,owner_id,name,timezone) VALUES(?,?,?,?)",
                        (farm.id, farm.owner_id, farm.name, farm.timezone))

    def list_farms(self, owner_id: str):
        return [dict(row) for row in self.db.query(
            "SELECT * FROM farms WHERE owner_id=? ORDER BY id", (owner_id,))]

    def create_unit(self, owner_id: str, unit: ProductionUnit):
        if unit.parent_unit_id is not None:
            raise ValueError("use IrrigationZones.create so the zone identity and boundary source are saved atomically")
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM farms WHERE id=? AND owner_id=?",
                            (unit.farm_id, owner_id)).fetchone() is None:
                raise ValueError("unknown farm for this owner")
            _validate_parent(conn, owner_id, unit)
            conn.execute("INSERT INTO production_units(id,farm_id,name,production_system,medium,"
                         "environment,water_strategy,irrigation_method,parent_unit_id) VALUES(?,?,?,?,?,?,?,?,?)",
                         (unit.id, unit.farm_id, unit.name, unit.production_system, unit.medium,
                          unit.environment, unit.water_strategy, unit.irrigation_method, unit.parent_unit_id))

    def list_units(self, owner_id: str) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT u.*,p.name AS parent_name FROM production_units u JOIN farms f ON f.id=u.farm_id "
            "LEFT JOIN production_units p ON p.id=u.parent_unit_id "
            "WHERE f.owner_id=? ORDER BY u.id", (owner_id,))]

    def resolve_domain(self, owner_id: str, unit_id: str, production_system: str):
        if production_system not in ("soil", "aquaponics", "hydroponics"):
            raise ValueError("production system must be soil, aquaponics or hydroponics")
        with self.db.transaction() as conn:
            unit = _owned_unit(conn, owner_id, unit_id)
            if unit["production_system"] not in (None, production_system):
                raise ValueError("this unit already has a different production system; create a separate unit")
            conn.execute("UPDATE production_units SET production_system=? WHERE id=?",
                         (production_system, unit_id))

    def create_cycle(self, owner_id: str, cycle: ProductionCycle):
        with self.db.transaction() as conn:
            _owned_unit(conn, owner_id, cycle.unit_id)
            conn.execute("INSERT INTO production_cycles(id,unit_id,crop,planted_on) VALUES(?,?,?,?)",
                         (cycle.id, cycle.unit_id, cycle.crop, cycle.planted_on))

    def list_cycles(self, owner_id: str, unit_id: str) -> list[dict]:
        with self.db.transaction() as conn:
            _owned_unit(conn, owner_id, unit_id)
            return [dict(row, lifecycle=lifecycle(conn, unit_id, row["id"])) for row in conn.execute(
                "SELECT * FROM production_cycles WHERE unit_id=? ORDER BY planted_on,id", (unit_id,))]

    def record_observation(self, owner_id: str, observation: Observation) -> bool:
        with self.db.transaction() as conn:
            return record_observation_in_transaction(conn, owner_id, observation)

    def observations(self, owner_id: str, unit_id: str) -> list[dict]:
        with self.db.transaction() as conn:
            _owned_unit(conn, owner_id, unit_id)
            rows = conn.execute("SELECT payload FROM field_observations WHERE unit_id=? "
                                "ORDER BY julianday(observed_at),id", (unit_id,)).fetchall()
            return [reviewed_observation(conn, json.loads(row[0])) for row in rows]

    def export(self, owner_id: str) -> dict:
        # Single snapshot so an export cannot join rows from different concurrent edits.
        with self.db.transaction() as conn:
            farms = [dict(r) for r in conn.execute("SELECT * FROM farms WHERE owner_id=? ORDER BY id",
                                                  (owner_id,))]
            units = [dict(r) for r in conn.execute(
                "SELECT u.* FROM production_units u JOIN farms f ON f.id=u.farm_id "
                "WHERE f.owner_id=? ORDER BY u.id", (owner_id,))]
            result = {"farms": farms, "units": units}
            result["irrigation_zones"] = [json.loads(r[0]) for r in conn.execute(
                "SELECT z.payload FROM irrigation_zones z JOIN production_units u ON u.id=z.zone_unit_id "
                "JOIN farms f ON f.id=u.farm_id WHERE f.owner_id=? ORDER BY z.rowid", (owner_id,))]
            for key, table in (("resource_budgets", "farm_resource_budgets"), ("resource_commitments", "farm_resource_commitments"),
                               ("equipment", "farm_equipment"), ("equipment_bookings", "farm_equipment_bookings")):
                result[key] = [json.loads(r[0]) for r in conn.execute(
                    f"SELECT p.payload FROM {table} p JOIN farms f ON f.id=p.farm_id WHERE f.owner_id=? ORDER BY p.rowid", (owner_id,))]
            result["resource_plans"] = [json.loads(r[0]) for r in conn.execute(
                "SELECT p.payload FROM farm_resource_plans p JOIN farms f ON f.id=p.farm_id WHERE f.owner_id=? ORDER BY p.rowid",
                (owner_id,))]
            result["resource_outcomes"] = [json.loads(r[0]) for r in conn.execute(
                "SELECT r.payload FROM resource_outcome_reviews r JOIN farms f ON f.id=r.farm_id WHERE f.owner_id=? ORDER BY r.rowid",
                (owner_id,))]
            for key, table in (("cycles", "production_cycles"), ("unit_facts", "unit_facts"),
                               ("observations", "field_observations"), ("field_intakes", "field_intake_drafts"),
                               ("scouting_intakes", "scouting_intake_drafts"),
                               ("harvest_intakes", "harvest_intake_drafts"),
                               ("task_intakes", "task_intake_drafts"),
                               ("planting_intakes", "planting_intake_drafts"),
                               ("water_log_intakes", "water_log_drafts"),
                               ("measurement_intakes", "measurement_intake_drafts"),
                               ("measurement_intake_receipts", "measurement_intake_receipts"),
                               ("calculations", "field_calculations"), ("water_days", "field_water_days"),
                               ("surface_water_days", "field_surface_water_days"),
                               ("soil_tests", "soil_test_reports"),
                               ("crop_records", "crop_records"), ("observation_reviews", "observation_reviews")):
                # table names are code-owned, never supplied by the caller.
                rows = conn.execute(f"SELECT t.* FROM {table} t "
                                    "JOIN production_units u ON u.id=t.unit_id "
                                    "JOIN farms f ON f.id=u.farm_id WHERE f.owner_id=?",
                                    (owner_id,)).fetchall()
                result[key] = [json.loads(r["payload"]) if key in {"task_intakes", "observations", "field_intakes", "scouting_intakes", "harvest_intakes", "planting_intakes", "water_log_intakes", "measurement_intakes", "measurement_intake_receipts", "calculations", "water_days", "surface_water_days", "crop_records", "observation_reviews", "soil_tests"} else dict(r)
                               for r in rows]
            from .unit_context import export_contexts

            result["contexts"] = export_contexts(conn, owner_id)
            result["setup_drafts"] = [dict(r, payload=json.loads(r["payload"])) for r in conn.execute(
                "SELECT * FROM unit_setup_drafts WHERE owner_id=?", (owner_id,))]
            return result

    def purge(self, owner_id: str):
        from .unit_context import purge_contexts

        with self.db.transaction() as conn:
            # Existing stores do not have foreign keys to units. Erase their context
            # records before the farm cascade removes the ownership mapping.
            purge_contexts(conn, owner_id)
            conn.execute("DELETE FROM unit_setup_drafts WHERE owner_id=?", (owner_id,))
            conn.execute("DELETE FROM farms WHERE owner_id=?", (owner_id,))

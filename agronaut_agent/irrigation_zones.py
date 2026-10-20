"""Immutable zone identities with independently scoped records beneath a field."""

import json
import re
from contextlib import nullcontext
from datetime import datetime, timezone

from agro_model.resources import _amount, _display, _object
from agro_model.validation import choice, text

from .farm_setup import create_unit
from .farm_store import _owned_unit

NOTE = (
    "Each zone has its own production-unit ID, crop cycles, measurements, water logs, calculations and conversation context. "
    "Only the parent farm and growing environment are shared at creation. Parent/sibling facts, crop cycles and observations "
    "are not inherited or combined. Zone boundaries and area are operator supplied; Agronaut does not survey them or check overlap. "
    "Do not add whole-field and zone quantities as if they were disjoint. A zone definition is immutable; create a new zone "
    "identity when boundaries change or the definition is wrong, and retain old records under their original identity. "
    "Declaring a zone does not calculate crop demand, resize equipment, reserve resources or operate irrigation."
)


def migrate_irrigation_zones(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(production_units)")}
    if "parent_unit_id" not in columns:
        conn.execute("ALTER TABLE production_units ADD COLUMN parent_unit_id TEXT REFERENCES production_units(id) ON DELETE CASCADE")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_units_parent ON production_units(parent_unit_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS irrigation_zones (
        zone_unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
        parent_unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        zone_key TEXT NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(parent_unit_id,zone_key), UNIQUE(parent_unit_id,request_id))""")


def zone_snapshot(conn, unit_id):
    row = conn.execute("SELECT payload FROM irrigation_zones WHERE zone_unit_id=?", (unit_id,)).fetchone()
    return json.loads(row[0]) if row else None


def zone_data(value):
    data = dict(_object(value, ("zone_key", "name", "water_strategy", "irrigation_method", "ground_area_m2", "boundary_note", "source"), "irrigation zone"))
    for key in ("zone_key", "name", "boundary_note", "source"):
        data[key] = text(key, data[key]).strip()
    data["zone_key"] = data["zone_key"].lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", data["zone_key"]):
        raise ValueError("zone_key requires 1–80 ASCII letters, numbers, dots, underscores or hyphens")
    choice("water_strategy", data["water_strategy"], ("supplemental", "fully_irrigated"))
    choice("irrigation_method", data["irrigation_method"], ("drip", "sprinkler", "surface"))
    if data["ground_area_m2"] is not None:
        amount = _amount(data["ground_area_m2"])
        if amount == 0:
            raise ValueError("zone ground area must be positive or null for unknown")
        data["ground_area_m2"] = _display(amount)
    return data


class IrrigationZones:
    def __init__(self, db):
        self.db = db

    def create(self, owner, parent_unit_id, data, *, request_id, connection=None):
        data = zone_data(data)
        text("request_id", request_id)
        operation = dict(data=data, parent_unit_id=parent_unit_id)
        with (self.db.transaction() if connection is None else nullcontext(connection)) as conn:
            parent = _owned_unit(conn, owner, parent_unit_id)
            if parent["production_system"] != "soil" or parent["parent_unit_id"] is not None:
                raise ValueError("create irrigation zones beneath a top-level soil production unit")
            existing = conn.execute("SELECT payload FROM irrigation_zones WHERE parent_unit_id=? AND request_id=?", (parent_unit_id, request_id)).fetchone()
            if existing:
                existing = json.loads(existing[0])
                if existing["operation"] != operation:
                    raise ValueError("request_id already used with different data")
                return existing
            if conn.execute("SELECT 1 FROM irrigation_zones WHERE parent_unit_id=? AND zone_key=?", (parent_unit_id, data["zone_key"])).fetchone():
                raise ValueError("zone key already exists beneath this field; use its existing identity or a distinct key")
            unit = create_unit(self.db, owner, dict(farm=dict(id=parent["farm_id"]), unit=dict(name=data["name"],
                production_system="soil", medium="soil", environment=parent["environment"], water_strategy=data["water_strategy"],
                irrigation_method=data["irrigation_method"])), connection=conn, parent_unit_id=parent_unit_id)
            saved = dict(id=unit["id"], owner_id=owner, parent_unit_id=parent_unit_id, request_id=request_id,
                created_at=datetime.now(timezone.utc).isoformat(), data=data, unit_snapshot=unit,
                parent_snapshot=dict(parent), operation=operation, note=NOTE)
            conn.execute("INSERT INTO irrigation_zones VALUES(?,?,?,?,?,?)", (unit["id"], parent_unit_id, data["zone_key"], request_id,
                saved["created_at"], json.dumps(saved, sort_keys=True, allow_nan=False)))
            return saved

    def list(self, owner, unit_id):
        with self.db.transaction() as conn:
            unit = _owned_unit(conn, owner, unit_id)
            return dict(unit_id=unit_id, parent_unit_id=unit["parent_unit_id"], zone=zone_snapshot(conn, unit_id),
                zones=[json.loads(row[0]) for row in conn.execute("SELECT payload FROM irrigation_zones WHERE parent_unit_id=? ORDER BY rowid", (unit_id,))], note=NOTE)

    def describe(self, owner, unit_id):
        listing = self.list(owner, unit_id)
        return dict(**listing, operations=dict(list_zones="{}", describe_zones="{}", create_zone="{data:zone,request_id}"),
            zone_schema="{zone_key:stable_local_key,name,water_strategy:supplemental|fully_irrigated,irrigation_method:drip|sprinkler|surface,ground_area_m2:positive_decimal_string|null,boundary_note,source}",
            rules=["Select a top-level soil unit with a known growing environment before creation. Zones share its farm and environment; nested zones and aquatic parents are not supported.",
                   "Keys are unique beneath a field: 1–80 lowercase ASCII letters, digits, dots, underscores or hyphens. The zone gets a new independent unit ID. Identical request retries return the original record; conflicting IDs or duplicate keys are rejected.",
                   "Area is declared ground footprint, not wetted fraction, planted area or application depth. Null is unknown. Decimal strings allow up to 15 integer and 6 fractional digits. No boundary geometry, overlap or summed field area is inferred.",
                   "Creating through field_records preserves the selected parent. Select the returned zone ID explicitly with /unit before logging its crop, water or measurements. Farm CLI and the native Create and use zone action select it explicitly.",
                   "No parent/sibling history, facts, measurements, cycles or calculations are copied. Supply zone-specific inputs and sources. Use shared farm budgets and equipment to coordinate zones; do not duplicate whole-field quantities."])

"""Validated, atomic production-unit setup shared by interfaces."""

import uuid
from contextlib import nullcontext
from dataclasses import asdict

from agro_model.farm import Farm, ProductionUnit
from agro_model.validation import text

from .farm_store import _validate_parent
from .field_io import _construct, _mapping


def create_unit(db, owner_id: str, request: dict, *, connection=None, parent_unit_id=None) -> dict:
    """Create atomically; a supplied connection must belong to the caller's transaction."""
    text("owner_id", owner_id)
    request = _mapping(request, ("farm", "unit"), "unit setup")
    farm_data = _mapping(request.get("farm"), ("id", "name", "timezone"), "farm")
    unit_data = _mapping(request.get("unit"), ("name", "production_system", "medium", "environment",
                                             "water_strategy", "irrigation_method"), "unit")
    farm = None
    if "id" in farm_data:
        if set(farm_data) != {"id"}:
            raise ValueError("use either an existing farm id or a new farm name and timezone")
        farm_id = text("farm id", farm_data["id"])
    else:
        farm_id = uuid.uuid4().hex
        farm = _construct(Farm, {"id": farm_id, "owner_id": owner_id, **farm_data})
    unit = _construct(ProductionUnit, {"id": uuid.uuid4().hex, "farm_id": farm_id, "parent_unit_id": parent_unit_id, **unit_data})
    with (db.transaction() if connection is None else nullcontext(connection)) as conn:
        if farm is None:
            if conn.execute("SELECT 1 FROM farms WHERE id=? AND owner_id=?", (farm_id, owner_id)).fetchone() is None:
                raise ValueError("unknown farm for this owner")
        else:
            conn.execute("INSERT INTO farms(id,owner_id,name,timezone) VALUES(?,?,?,?)",
                         (farm.id, farm.owner_id, farm.name, farm.timezone))
        _validate_parent(conn, owner_id, unit)
        conn.execute("INSERT INTO production_units(id,farm_id,name,production_system,medium,environment,"
                     "water_strategy,irrigation_method,parent_unit_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (unit.id, unit.farm_id, unit.name, unit.production_system, unit.medium,
                      unit.environment, unit.water_strategy, unit.irrigation_method, unit.parent_unit_id))
    return asdict(unit)

"""Local terminal farm setup; no language model and no remote account mutations."""

import json
import sqlite3
from pathlib import Path

from .farm_setup import create_unit
from .farm_store import FarmStore
from .field_cli import _reject_constant, _unique_keys
from .unit_context import UnitContexts


def run(args):
    from .store import ConversationStore, _Db

    db = None
    try:
        payload = None
        if args.operation in ("create", "create-zone"):
            payload = json.loads(Path(args.input).read_text(), parse_constant=_reject_constant,
                                 object_pairs_hook=_unique_keys)
        db = _Db(args.db)
        owner = ConversationStore(db).get_or_create_user("cli", args.user)
        contexts = UnitContexts(db)
        if args.operation == "overview":
            from .farm_overview import FarmOverview
            result = FarmOverview(db).get(owner, args.farm)
        elif args.operation == "create":
            unit = create_unit(db, owner, payload)
            contexts.select(owner, unit["id"])
            result = {"created": unit, "active": contexts.describe_active(owner)}
        elif args.operation == "create-zone":
            from .field_io import _mapping
            from .irrigation_zones import IrrigationZones
            payload = _mapping(payload, ("data", "request_id"), "zone creation")
            with db.transaction() as conn:
                saved = IrrigationZones(db).create(owner, args.unit, **payload, connection=conn)
                contexts.select(owner, saved["id"], connection=conn)
            result = {"created": saved, "active": contexts.describe_active(owner)}
        elif args.operation == "zones":
            from .irrigation_zones import IrrigationZones
            result = IrrigationZones(db).list(owner, args.unit)
        elif args.operation == "select":
            if args.production_system is not None:
                FarmStore(db).resolve_domain(owner, args.unit, args.production_system)
            contexts.select(owner, args.unit)
            result = {"active": contexts.describe_active(owner)}
        else:
            result = {"units": FarmStore(db).list_units(owner), "active": contexts.describe_active(owner)}
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "VALIDATION_FAILED", "error": str(exc)}))
        return 2
    finally:
        if db is not None:
            db._conn.close()


def add_farm_commands(subparsers):
    parser = subparsers.add_parser("farm", help="create and select local production units (JSON output)")
    parser.add_argument("--db", help="SQLite file; defaults to the configured Agronaut database")
    parser.add_argument("--user", default="local", help="local CLI identity, matching the REPL default")
    operations = parser.add_subparsers(dest="operation", required=True)
    create = operations.add_parser("create", help="create a unit from JSON and make it active")
    create.add_argument("--input", required=True)
    zone = operations.add_parser("create-zone", help="create an irrigation zone under a soil unit and select it")
    zone.add_argument("--unit", required=True, help="owned parent field ID")
    zone.add_argument("--input", required=True)
    zones = operations.add_parser("zones", help="list a field's zones or inspect a selected zone")
    zones.add_argument("--unit", required=True)
    select = operations.add_parser("select", help="select an owned unit")
    select.add_argument("--unit", required=True)
    select.add_argument("--production-system", choices=("soil", "hydroponics", "aquaponics"),
                        help="resolve a legacy unit whose production system was unknown")
    operations.add_parser("list", help="list your units and current selection")
    overview = operations.add_parser("overview", help="read crop cycles and planned work across owned units")
    overview.add_argument("--farm", help="optional owned farm ID; defaults to all your farms")
    parser.set_defaults(func=run)

"""Persistent field workflows for the local terminal identity."""

import json
import sqlite3
from pathlib import Path

from .field_cli import _reject_constant, _unique_keys
from .field_workflow import execute_field_workflow
from .unit_context import UnitContexts


def run(args):
    from .store import ConversationStore, _Db

    db = None
    try:
        data = (json.loads(Path(args.input).read_text(), parse_constant=_reject_constant,
                           object_pairs_hook=_unique_keys) if args.input else {})
        db = _Db(args.db)
        owner = ConversationStore(db).get_or_create_user("cli", args.user)
        unit = UnitContexts(db).active(owner)
        unit_id = args.unit or (unit.unit_id if unit else None)
        if unit_id is None:
            raise ValueError("select a production unit first or supply --unit")
        result = execute_field_workflow(db, owner, unit_id, args.operation, data)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "VALIDATION_FAILED", "error": str(exc)}))
        return 2
    finally:
        if db is not None:
            db._conn.close()


def add_workflow_commands(subparsers):
    parser = subparsers.add_parser("field", help="save field calculations, revise weather and log actual water (JSON)")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--user", default="local", help="local CLI identity")
    parser.add_argument("--unit", help="owned unit ID; defaults to your active unit")
    parser.add_argument("operation", choices=("preview_equipment_revision", "revise_equipment", "start_water_log", "get_water_log", "answer_water_log", "save_water_log", "cancel_water_log", "describe_zones", "list_zones", "create_zone", "save", "get", "list", "record_day", "replay", "continue_coupled_water", "revise_weather", "fetch_weather",
                                               "describe_soil_tests", "record_soil_test", "get_soil_test", "list_soil_tests", "void_soil_test",
                                               "record_surface_day", "review_surface_water", "replay_surface_water",
                                               "review_coupled_state", "apply_coupled_state", "remove_coupled_state",
                                               "review_weather", "review_target", "review_crop", "revise_crop", "review_soil", "compare_soil", "apply_soil_state", "remove_soil_state", "revise_roots",
                                               "compare_surface_advance", "compare_drip_lateral", "compare_drip_network",
                                               "describe_sprinkler_comparison", "review_sprinkler_measurements", "compare_sprinkler_lateral",
                                               "start_intake", "get_intake", "answer_intake", "cancel_intake", "save_intake"))
    parser.add_argument("--input", help="operation data JSON; omitted for list")
    parser.set_defaults(func=run)

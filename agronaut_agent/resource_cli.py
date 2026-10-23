"""Standard-library CLI for owned farm-wide resource scenarios."""

import json
import sqlite3
from pathlib import Path

from .farm_resources import execute_farm_resources
from .field_cli import _reject_constant, _unique_keys


def run(args):
    from .store import ConversationStore, _Db

    db = None
    try:
        data = json.loads(Path(args.input).read_text(), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_keys) if args.input else {}
        db = _Db(args.db)
        owner = ConversationStore(db).get_or_create_user("cli", args.user)
        result = execute_farm_resources(db, owner, args.farm, args.operation, data)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "VALIDATION_FAILED", "error": str(exc)}))
        return 2
    finally:
        if db is not None:
            db._conn.close()


def add_resource_commands(subparsers):
    parser = subparsers.add_parser("resources", help="evaluate and save farm-wide resource allocation scenarios")
    parser.add_argument("--db", help="SQLite file")
    parser.add_argument("--user", default="local", help="local CLI identity")
    parser.add_argument("--farm", required=True, help="explicit owned farm ID")
    parser.add_argument("operation", choices=("equipment_schedule_inputs", "propose_equipment_schedule", "save_equipment_schedule", "equipment_calendar", "equipment_inputs", "save_equipment", "book_equipment", "reserve_equipment", "release_equipment", "save_new_work", "settlement_inputs", "settle_commitment", "ledger", "save_budget", "commit_plan", "release_commitment", "describe", "design_job", "evaluate", "save", "evaluate_timing", "save_timed", "propose_schedule", "save_schedule",
                                             "outcome_inputs", "evaluate_outcome", "save_outcome", "list_outcomes", "list", "get", "compare"))
    parser.add_argument("--input", help="operation JSON; omitted for list/describe")
    parser.set_defaults(func=run)

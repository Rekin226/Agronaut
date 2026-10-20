"""Local crop-cycle, observation and harvest journal CLI, without a model or network."""

import json
import sqlite3
from pathlib import Path

from .crop_records import execute_crop_records
from .field_cli import _reject_constant, _unique_keys
from .unit_context import UnitContexts


def run(args):
    from .store import ConversationStore, _Db

    db = None
    try:
        from .measurement_import import MAX_CSV_BYTES, csv_rows

        csv_path = getattr(args, "csv", None)
        preview_token = getattr(args, "preview_token", None)
        if (csv_path or preview_token) and args.operation not in ("preview_measurements", "import_measurements"):
            raise ValueError("--csv and --preview-token are only for measurement imports")
        if preview_token and (not csv_path or args.operation != "import_measurements"):
            raise ValueError("--preview-token requires import_measurements --csv")
        data = json.loads(Path(args.input).read_text(), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_keys) if args.input else {}
        if csv_path:
            with Path(csv_path).open("rb") as stream:
                content = stream.read(MAX_CSV_BYTES + 1)
            if len(content) > MAX_CSV_BYTES:
                raise ValueError("measurement CSV must be at most 2 MB")
            data = {"rows": csv_rows(content.decode("utf-8-sig"))}
            if args.operation == "import_measurements":
                data["preview_token"] = preview_token
        db = _Db(args.db)
        owner = ConversationStore(db).get_or_create_user("cli", args.user)
        active = UnitContexts(db).active(owner)
        unit_id = args.unit or (active.unit_id if active else None)
        if unit_id is None:
            raise ValueError("select a production unit first or supply --unit")
        print(json.dumps(execute_crop_records(db, owner, unit_id, args.operation, data), indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "VALIDATION_FAILED", "error": str(exc)}))
        return 2
    finally:
        if db is not None:
            db._conn.close()


def add_crop_commands(subparsers):
    parser = subparsers.add_parser("crop", help="record plantings, transplants, stages, scouting, fertilizer, harvests and cash entries; compare seasons (JSON)")
    parser.add_argument("--db", help="SQLite file")
    parser.add_argument("--user", default="local", help="local CLI identity")
    parser.add_argument("--unit", help="owned unit ID; defaults to your active unit")
    parser.add_argument("operation", choices=("start_task", "get_task", "answer_task", "save_task", "cancel_task", "start_harvest", "get_harvest", "answer_harvest", "save_harvest", "cancel_harvest", "preview_measurements", "import_measurements", "start_measurement", "get_measurement", "answer_measurement", "save_measurement", "cancel_measurement", "start_planting", "get_planting", "answer_planting", "save_planting", "cancel_planting",
                                            "start_scouting", "get_scouting", "answer_scouting", "save_scouting", "cancel_scouting", "record_harvest_review", "void_harvest_review", "plan_task", "resolve_task", "record_cash", "void_cash", "compare_seasons", "create", "correct_planting", "record_transplant", "void_transplant", "list", "get", "describe", "record_harvest", "void_harvest",
                                            "record_stage", "record_scouting", "record_fertilizer", "void_fertilizer", "void_observation", "close_cycle", "reopen_cycle",
                                            "list_measurements", "record_measurement", "review_measurement", "season_review"))
    files = parser.add_mutually_exclusive_group()
    files.add_argument("--input", help="operation JSON; omitted for list/describe")
    files.add_argument("--csv", help="UTF-8 measurement template for preview_measurements/import_measurements")
    parser.add_argument("--preview-token", help="token from the reviewed preview, for import_measurements --csv")
    parser.set_defaults(func=run)

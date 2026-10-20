"""Explicitly mapped measurement batches; preview without writes, atomic reviewed save."""

import csv
import hashlib
import io
import json
import math
import re
from decimal import Decimal, InvalidOperation

from agro_model.validation import text

from .field_io import _mapping

MAX_ROWS = 1000
MAX_CSV_BYTES = 2_000_000
COLUMNS = ("request_id", "cycle_id", "quantity", "value", "unit", "observed_at", "source", "provenance")


def csv_rows(content):
    """Parse the documented UTF-8 template, without guessing units, dates or missing data."""
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("measurement CSV must be UTF-8 text up to 2 MB")
    reader = csv.reader(io.StringIO(content.removeprefix("\ufeff"), newline=""), strict=True)
    rows = []
    try:
        header = next(reader, [])
        if len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            raise ValueError("CSV requires each column exactly once: " + ",".join(COLUMNS))
        for index, values in enumerate(reader, 1):
            if index > MAX_ROWS:
                raise ValueError(f"measurement import supports at most {MAX_ROWS} rows")
            if len(values) != len(header):
                raise ValueError(f"CSV record {index}: wrong number of columns")
            row = dict(zip(header, values))
            raw = row.pop("value")
            if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", raw):
                raise ValueError(f"CSV record {index}: value must be a decimal number in the supplied unit")
            value = float(raw)
            try:
                underflow = value == 0 and Decimal(raw) != 0
            except InvalidOperation as exc:
                raise ValueError(f"CSV record {index}: value is outside the supported numeric range") from exc
            if not math.isfinite(value) or underflow:
                raise ValueError(f"CSV record {index}: value is outside the supported numeric range")
            rows.append(dict(request_id=row.pop("request_id"), cycle_id=row.pop("cycle_id") or None,
                             data=dict(row, value=value)))
    except csv.Error as exc:
        raise ValueError(f"invalid CSV near line {reader.line_num}: {exc}") from exc
    if not rows:
        raise ValueError("measurement import requires at least one row")
    return rows


def _rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError(f"rows must be a list of 1–{MAX_ROWS} measurements")
    normalized, seen = [], set()
    for index, item in enumerate(rows, 1):
        row = dict(_mapping(item, ("request_id", "cycle_id", "data"), f"row {index}"))
        request = text(f"row {index} request_id", row.get("request_id"))
        if request in seen:
            raise ValueError(f"row {index}: duplicate request_id within import")
        seen.add(request)
        row.setdefault("cycle_id", None)
        normalized.append(row)
    # Freeze caller data, reject non-JSON/non-finite values before any write.
    return json.loads(json.dumps(normalized, sort_keys=True, allow_nan=False))


def _token(owner, unit_id, rows):
    payload = json.dumps(["agronaut-measurement-import-v1", owner, unit_id, rows],
                         sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MeasurementImports:
    def __init__(self, db):
        self.db = db

    def preview(self, owner, unit_id, rows):
        return self._run(owner, unit_id, rows, save=False)

    def save(self, owner, unit_id, rows, preview_token):
        text("preview_token", preview_token)
        return self._run(owner, unit_id, rows, save=True, preview_token=preview_token)

    def _run(self, owner, unit_id, rows, *, save, preview_token=None):
        from .crop_records import _scope, record_measurement
        from .observation_quality import reviewed_observation

        rows = _rows(rows)
        token = _token(owner, unit_id, rows)
        entries = []
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            if save and preview_token != token:
                raise ValueError("import differs from the preview; preview and review the current rows again")
            for index, row in enumerate(rows, 1):
                try:
                    measurement = record_measurement(conn, owner, unit_id, **row, validate_only=True)
                    exists = conn.execute("SELECT 1 FROM field_observations WHERE id=?", (measurement["id"],)).fetchone()
                    if save:
                        measurement = record_measurement(conn, owner, unit_id, **row)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ValueError(f"row {index}: {exc}") from exc
                entries.append(dict(row=index, request_id=row["request_id"], action="existing" if exists else "new",
                                    measurement=reviewed_observation(conn, measurement)))
        return dict(status="saved" if save else "preview", preview_token=token, entries=entries,
                    new_count=sum(e["action"] == "new" for e in entries),
                    existing_count=sum(e["action"] == "existing" for e in entries),
                    note="New readings remain unverified. No daily totals, crop outcomes or equipment actions are inferred.")

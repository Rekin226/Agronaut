"""Append-only operator reviews of typed field observations; originals remain intact."""

import json
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agro_model.validation import choice, text

SCHEMA = (
    """CREATE TABLE observation_reviews (
        id TEXT PRIMARY KEY,
        unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        observation_id TEXT NOT NULL REFERENCES field_observations(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL,
        supersedes_id TEXT UNIQUE REFERENCES observation_reviews(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(unit_id,request_id))""",
    "CREATE INDEX idx_observation_reviews_observation ON observation_reviews(observation_id)",
)


def migrate_observation_reviews(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def review_history(conn, unit_id, observation_id):
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM observation_reviews WHERE unit_id=? AND observation_id=? ORDER BY rowid", (unit_id, observation_id))]


def reviewed_observation(conn, original):
    """Caller must check ownership. Expose effective quality separately from original quality."""
    history = review_history(conn, original["unit_id"], original["id"])
    latest = history[-1] if history else None
    return dict(original, effective_quality=latest["quality"] if latest else original["quality"],
                quality_review=latest, quality_history=history)


class ObservationReviews:
    def __init__(self, db):
        self.db = db

    def review(self, owner, unit_id, observation_id, quality, reason, source, *, request_id, expected_revision):
        from .cycle_lifecycle import ensure_cycle_day, lifecycle
        from .farm_store import _owned_unit

        for key, value in (("owner", owner), ("unit_id", unit_id), ("observation_id", observation_id), ("request_id", request_id)):
            text(key, value)
        if expected_revision is not None:
            text("expected_revision", expected_revision)
        choice("quality", quality, ("unverified", "checked", "rejected"))
        reason, source = text("reason", reason).strip(), text("source", source).strip()
        operation = dict(observation_id=observation_id, quality=quality, reason=reason, source=source, expected_revision=expected_revision)
        with self.db.transaction() as conn:
            unit = _owned_unit(conn, owner, unit_id)
            row = conn.execute("SELECT payload FROM field_observations WHERE id=? AND unit_id=?", (observation_id, unit_id)).fetchone()
            if row is None:
                raise ValueError("unknown field observation for the selected unit")
            original = json.loads(row[0])
            previous_request = conn.execute("SELECT payload FROM observation_reviews WHERE unit_id=? AND request_id=?",
                                            (unit_id, request_id)).fetchone()
            if previous_request:
                saved = json.loads(previous_request[0])
                if saved["operation"] != operation:
                    raise ValueError("request_id already used with different data")
                return saved
            current = reviewed_observation(conn, original)["quality_review"]
            if expected_revision != (current["id"] if current else None):
                raise ValueError("observation review changed; read measurements and use the latest quality_review.id as expected_revision")
            if quality != "rejected" and original["provenance"] != "forecast" and original["cycle_id"] is not None:
                state = lifecycle(conn, unit_id, original["cycle_id"])
                if state["status"] == "closed":
                    tz = conn.execute("SELECT timezone FROM farms WHERE id=?", (unit["farm_id"],)).fetchone()[0]
                    day = datetime.fromisoformat(original["observed_at"]).astimezone(ZoneInfo(tz)).date().isoformat()
                    ensure_cycle_day(conn, unit_id, original["cycle_id"], day)
            saved = dict(id=uuid.uuid4().hex, owner_id=owner, unit_id=unit_id, observation_id=observation_id,
                         quality=quality, reason=reason, source=source, request_id=request_id,
                         supersedes_id=expected_revision, created_at=datetime.now(timezone.utc).isoformat(), operation=operation)
            conn.execute("INSERT INTO observation_reviews VALUES(?,?,?,?,?,?,?)",
                         (saved["id"], unit_id, observation_id, request_id, expected_revision,
                          saved["created_at"], json.dumps(saved, sort_keys=True, allow_nan=False)))
            return saved

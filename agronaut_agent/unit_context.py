"""Owned production-unit contexts over the existing per-context stores.

Legacy APIs call their partition key `user_id`. A unit receives a separate opaque key
so history, semantic recall, summaries, calibration, follow-ups and approvals all use
the same isolation boundary. This key is never a channel delivery address. Identity
stays at owner scope, and ambiguous legacy conversations are never copied into a unit.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass

from .farm_store import _owned_unit

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS unit_contexts (
        unit_id TEXT PRIMARY KEY REFERENCES production_units(id) ON DELETE CASCADE,
        context_id TEXT NOT NULL UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS active_units (
        owner_id TEXT PRIMARY KEY,
        unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE)""",
)

# Each existing store that can hold unit information must participate in export/erasure.
# Table and column identifiers are code-owned, never supplied by a caller.
_OWNED = (
    ("facts", "user_facts", "user_id"),
    ("messages", "messages", "user_id"),
    ("memories", "memories", "user_id"),
    ("summaries", "session_summary", "user_id"),
    ("measurements", "measurements", "user_id"),
    ("readings", "twin_readings", "user_id"),
    ("proposal_items", "proposal_items", "user_id"),
    ("proposals", "proposals", "user_id"),
    ("followups", "followups", "user_id"),
    ("community_insights", "community_insights", "source_user_id"),
)


def migrate_contexts(conn):
    for statement in SCHEMA:
        conn.execute(statement)
    # A delayed model/summary job may finish after erasure. It must not recreate an
    # orphaned unit partition after its ownership mapping has been deleted.
    for _, table, column in _OWNED:
        for event, clause in (("insert", "INSERT"), ("update", f"UPDATE OF {column}")):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS unit_context_{table}_{event}
                BEFORE {clause} ON {table}
                WHEN NEW.{column} LIKE '@unit/%' AND NOT EXISTS (
                    SELECT 1 FROM unit_contexts WHERE context_id=NEW.{column})
                BEGIN SELECT RAISE(ABORT, 'unknown or deleted unit context'); END""")


@dataclass(frozen=True)
class UnitContext:
    owner_id: str
    unit_id: str
    context_id: str
    production_system: str | None
    name: str
    parent_unit_id: str | None = None


def _contexts(conn, owner_id):
    return conn.execute(
        "SELECT f.owner_id,u.id AS unit_id,c.context_id,u.production_system,u.name,u.parent_unit_id "
        "FROM unit_contexts c JOIN production_units u ON u.id=c.unit_id "
        "JOIN farms f ON f.id=u.farm_id WHERE f.owner_id=? ORDER BY u.id", (owner_id,)).fetchall()


def export_contexts(conn, owner_id):
    result = []
    for row in _contexts(conn, owner_id):
        context = dict(row)
        for key, table, column in _OWNED:
            context[key] = [dict(r) for r in conn.execute(
                f"SELECT * FROM {table} WHERE {column}=? ORDER BY rowid", (row["context_id"],))]
        result.append(context)
    return result


def purge_contexts(conn, owner_id):
    """Caller owns the transaction; erase data before deleting the ownership map."""
    for row in _contexts(conn, owner_id):
        context_id = row["context_id"]
        conn.execute("DELETE FROM memory_embeddings WHERE memory_id IN "
                     "(SELECT id FROM memories WHERE user_id=?)", (context_id,))
        for _, table, column in _OWNED:
            conn.execute(f"DELETE FROM {table} WHERE {column}=?", (context_id,))
        conn.execute("DELETE FROM unit_contexts WHERE unit_id=?", (row["unit_id"],))
    conn.execute("DELETE FROM active_units WHERE owner_id=?", (owner_id,))


class UnitContexts:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def owned_tables():
        return tuple((table, column) for _, table, column in _OWNED)

    def select(self, owner_id: str, unit_id: str, *, connection=None) -> UnitContext:
        """Select atomically, optionally within an already-open caller transaction."""
        with (self.db.transaction() if connection is None else nullcontext(connection)) as conn:
            unit = _owned_unit(conn, owner_id, unit_id)
            row = conn.execute("SELECT context_id FROM unit_contexts WHERE unit_id=?",
                               (unit_id,)).fetchone()
            if row is None:
                context_id = "@unit/" + uuid.uuid4().hex
                conn.execute("INSERT INTO unit_contexts VALUES(?,?)", (unit_id, context_id))
                # An operator may have continued using the legacy profile after migration.
                # Carry its current facts/twin state on the FIRST explicit selection, not
                # the stale migration snapshot. Ambiguous history/actions stay at owner scope.
                if unit["legacy"]:
                    conn.execute("INSERT INTO user_facts(user_id,key,value,source,updated_at) "
                                 "SELECT ?,key,value,source,updated_at FROM user_facts WHERE user_id=?",
                                 (context_id, owner_id))
                else:
                    conn.execute("INSERT INTO user_facts(user_id,key,value,source,updated_at) "
                                 "SELECT ?,key,value,source,updated_at FROM unit_facts WHERE unit_id=?",
                                 (context_id, unit_id))
            else:
                context_id = row["context_id"]
            conn.execute("INSERT INTO active_units(owner_id,unit_id) VALUES(?,?) "
                         "ON CONFLICT(owner_id) DO UPDATE SET unit_id=excluded.unit_id",
                         (owner_id, unit_id))
            return UnitContext(owner_id, unit_id, context_id, unit["production_system"], unit["name"], unit["parent_unit_id"])

    def active(self, owner_id: str) -> UnitContext | None:
        rows = self.db.query(
            "SELECT f.owner_id,u.id AS unit_id,c.context_id,u.production_system,u.name,u.parent_unit_id "
            "FROM active_units a JOIN production_units u ON u.id=a.unit_id "
            "JOIN farms f ON f.id=u.farm_id JOIN unit_contexts c ON c.unit_id=u.id "
            "WHERE a.owner_id=? AND f.owner_id=a.owner_id", (owner_id,))
        return UnitContext(**dict(rows[0])) if rows else None

    def clear_selection(self, owner_id: str):
        self.db.execute("DELETE FROM active_units WHERE owner_id=?", (owner_id,))

    def for_context(self, context_id: str) -> UnitContext | None:
        rows = self.db.query(
            "SELECT f.owner_id,u.id AS unit_id,c.context_id,u.production_system,u.name,u.parent_unit_id "
            "FROM unit_contexts c JOIN production_units u ON u.id=c.unit_id "
            "JOIN farms f ON f.id=u.farm_id WHERE c.context_id=?", (context_id,))
        return UnitContext(**dict(rows[0])) if rows else None

    def export(self, owner_id: str) -> list[dict]:
        with self.db.transaction() as conn:
            return export_contexts(conn, owner_id)

    def describe_active(self, owner_id: str) -> dict | None:
        context = self.active(owner_id)
        return asdict(context) if context else None

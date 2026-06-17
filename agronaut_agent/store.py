"""Per-user persistence (SQLite) — replaces the old single global ThreadState.

Two stores over one SQLite file:
  ConversationStore — identity (channel + native id -> stable user_id) and message history.
  MemoryStore       — long-term facts about the user's actual system (tank, species, location).

Concurrency: one laptop / one bot process. WAL mode + a per-connection lock keep the
single-process event loop safe. Stdlib only — no ORM, no heavy deps.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "agronaut.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    channel_user TEXT NOT NULL,
    display_name TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE(channel, channel_user)
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    tool_name  TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
CREATE TABLE IF NOT EXISTS user_facts (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    source     TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
-- Agent-curated long-term memory: free-form notes the assistant chooses to keep about a
-- user's system and history (Hermes-style self-curated memory). Categories let context
-- assembly prioritise (e.g. surface 'event' and 'learning' notes for troubleshooting).
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    category   TEXT NOT NULL,   -- profile | event | preference | learning
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, id);
-- Rolling cross-session summary: older conversation turns folded into a compact recap so
-- context survives beyond the recent-message window (OpenHuman-style summary recall).
CREATE TABLE IF NOT EXISTS session_summary (
    user_id    TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Db:
    """Shared connection + lock. One file, opened once per path."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else Path(os.getenv("AGRONAUT_DB", _DEFAULT_DB))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()


def user_id_for(channel: str, channel_user: str) -> str:
    """Stable, namespaced id, e.g. 'telegram:123456789'."""
    return f"{channel}:{channel_user}"


class ConversationStore:
    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def get_or_create_user(self, channel: str, channel_user: str, display_name: str | None = None) -> str:
        uid = user_id_for(channel, channel_user)
        self.db.execute(
            "INSERT OR IGNORE INTO users(user_id, channel, channel_user, display_name, created_at) "
            "VALUES (?,?,?,?,?)",
            (uid, channel, str(channel_user), display_name, _now()),
        )
        return uid

    def append_message(self, user_id: str, role: str, content: str, tool_name: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO messages(user_id, role, content, tool_name, created_at) VALUES (?,?,?,?,?)",
            (user_id, role, content, tool_name, _now()),
        )

    def recent_messages(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.query(
            "SELECT role, content, tool_name FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def reset_conversation(self, user_id: str) -> None:
        self.db.execute("DELETE FROM messages WHERE user_id=?", (user_id,))


class MemoryStore:
    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def get_facts(self, user_id: str) -> dict[str, str]:
        rows = self.db.query("SELECT key, value FROM user_facts WHERE user_id=?", (user_id,))
        return {r["key"]: r["value"] for r in rows}

    def set_fact(self, user_id: str, key: str, value: str, source: str = "user_stated") -> None:
        self.db.execute(
            "INSERT INTO user_facts(user_id, key, value, source, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, source=excluded.source, "
            "updated_at=excluded.updated_at",
            (user_id, key, str(value), source, _now()),
        )

    def set_facts(self, user_id: str, facts: dict, source: str = "parsed") -> None:
        for k, v in facts.items():
            if v is not None:
                self.set_fact(user_id, k, str(v), source)

    def forget(self, user_id: str) -> None:
        self.db.execute("DELETE FROM user_facts WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM session_summary WHERE user_id=?", (user_id,))

    # --- agent-curated memories ------------------------------------------
    _MEMORY_CATEGORIES = ("profile", "event", "preference", "learning")

    def add_memory(self, user_id: str, content: str, category: str = "profile") -> bool:
        """Store a durable note. Returns False if it duplicates an existing note (case-
        insensitive) for this user, so the agent can call freely without bloating memory."""
        content = (content or "").strip()
        if not content:
            return False
        category = category if category in self._MEMORY_CATEGORIES else "profile"
        existing = self.db.query(
            "SELECT 1 FROM memories WHERE user_id=? AND lower(content)=lower(?) LIMIT 1",
            (user_id, content),
        )
        if existing:
            return False
        self.db.execute(
            "INSERT INTO memories(user_id, category, content, created_at) VALUES (?,?,?,?)",
            (user_id, category, content, _now()),
        )
        return True

    def get_memories(self, user_id: str, limit: int = 12) -> list[dict]:
        """Most-recent memories first (capped), returned oldest->newest for readable context."""
        rows = self.db.query(
            "SELECT category, content FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def memory_count(self, user_id: str) -> int:
        rows = self.db.query("SELECT COUNT(*) AS n FROM memories WHERE user_id=?", (user_id,))
        return rows[0]["n"] if rows else 0

    # --- rolling cross-session summary -----------------------------------
    def get_summary(self, user_id: str) -> str | None:
        rows = self.db.query("SELECT summary FROM session_summary WHERE user_id=?", (user_id,))
        return rows[0]["summary"] if rows else None

    def set_summary(self, user_id: str, summary: str) -> None:
        self.db.execute(
            "INSERT INTO session_summary(user_id, summary, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
            (user_id, summary.strip(), _now()),
        )

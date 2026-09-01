"""Per-user persistence (SQLite) — replaces the old single global ThreadState.

Two stores over one SQLite file:
  ConversationStore — identity (channel + native id -> stable user_id) and message history.
  MemoryStore       — long-term facts about the user's actual system (tank, species, location).

Concurrency: one laptop / one bot process. WAL mode + a per-connection lock keep the
single-process event loop safe. Stdlib only — no ORM, no heavy deps.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import paths as _paths

_DEFAULT_DB = _paths.data_dir() / "agronaut.sqlite3"

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
-- Proactive/passive outcome follow-ups (self-learning): a scheduled check-in the bot
-- sends later to learn whether its advice worked. Delivered by the channel poller.
CREATE TABLE IF NOT EXISTS followups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    channel      TEXT NOT NULL,
    channel_user TEXT NOT NULL,
    question     TEXT NOT NULL,
    about        TEXT,
    due_at       TEXT NOT NULL,
    status       TEXT NOT NULL,   -- pending | sent | answered | cancelled | failed
    attempts     INTEGER NOT NULL DEFAULT 0,
    outcome      TEXT,
    created_at   TEXT NOT NULL,
    sent_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_followups_channel ON followups(channel, status, due_at);
CREATE INDEX IF NOT EXISTS idx_followups_user ON followups(user_id, status);
-- Cross-user learning: generalized, PII-stripped insights nominated from per-user learnings,
-- human-approved before they can surface to other operators. Only `insight`/`topic` are ever
-- shared; `source_user_id`/`original` are for the owner's local review only.
CREATE TABLE IF NOT EXISTS community_insights (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_user_id TEXT NOT NULL,
    original       TEXT,
    insight        TEXT NOT NULL,
    topic          TEXT,
    status         TEXT NOT NULL,   -- pending | approved | rejected
    created_at     TEXT NOT NULL,
    reviewed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_community_status ON community_insights(status);
-- Per-operator coefficient calibration: real measured outcomes (keyed by the aqua_model
-- calibration key, e.g. 'tilapia.fcr'). Aggregated into bounded overrides at sizing time;
-- seeds are never mutated.
-- Embedding vectors for semantic recall over `memories` (float32 bytes). Populated on
-- write when an embedder is available, backfilled lazily on first search otherwise.
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY,
    dim       INTEGER NOT NULL,
    vector    BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    coefficient TEXT NOT NULL,
    value       REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_measurements_user ON measurements(user_id, coefficient);
-- Append-only history of readings a user logged into their LIVE twin, paired with what
-- the twin predicted at that moment. The twin state itself keeps only "today" (each /log
-- overwrites the snapshot), so without this the drift report is computed, spoken once and
-- lost — and "is the model getting closer to MY pond?" becomes unanswerable.
CREATE TABLE IF NOT EXISTS twin_readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    greenhouse  TEXT NOT NULL,
    observed    TEXT NOT NULL,
    modelled    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_twin_readings_user ON twin_readings(user_id, id);
-- The approval gate. A proposal from `aqua_model.advisory` is inert until a human decides
-- on it, and this table IS that decision: one row per proposal, one row per item, with the
-- state the twin was in when it proposed. Nothing in Agronaut may act on a recommendation
-- that does not have an `approved` row here — today that rule costs nothing, because there
-- is no actuation path at all; it exists so that when one is built the gate is already the
-- only door, rather than something bolted on afterwards.
--
-- It is also the record that makes the advice measurable: an approved action with a date
-- beside the readings that followed it is the only way to ever answer "did taking this
-- system's advice help?", which is the question a literature-seeded model most needs asked.
CREATE TABLE IF NOT EXISTS proposals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    as_of        TEXT NOT NULL,
    context      TEXT NOT NULL,
    payload      TEXT NOT NULL,   -- advisory.to_dict(), the proposal exactly as rendered
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposals_user ON proposals(user_id, id);
CREATE TABLE IF NOT EXISTS proposal_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id  INTEGER NOT NULL,
    user_id      TEXT NOT NULL,
    position     INTEGER NOT NULL,  -- the number the operator sees and types
    action       TEXT NOT NULL,
    confidence   REAL NOT NULL,
    evidence     TEXT NOT NULL,
    status       TEXT NOT NULL,     -- proposed | approved | rejected
    decided_at   TEXT,
    UNIQUE(proposal_id, position)
);
CREATE INDEX IF NOT EXISTS idx_proposal_items_user ON proposal_items(user_id, status);
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

    def recent_context_messages(self, user_id: str, limit: int = 20) -> list[dict]:
        """Like recent_messages, but `limit` budgets only user/assistant rows; tool rows
        inside that window ride along without consuming it — a tool-heavy turn must not
        evict real conversation from the model's context."""
        convo = self.db.query(
            "SELECT id FROM messages WHERE user_id=? AND role IN ('user','assistant') "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        if not convo:
            return []
        cutoff = convo[-1]["id"]
        rows = self.db.query(
            "SELECT role, content, tool_name FROM messages WHERE user_id=? AND id>=? ORDER BY id",
            (user_id, cutoff),
        )
        return [dict(r) for r in rows]

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
        # also drop any semantic-recall vectors for this user's memories (by memory_id)
        self.db.execute(
            "DELETE FROM memory_embeddings WHERE memory_id IN "
            "(SELECT id FROM memories WHERE user_id=?)", (user_id,))
        self.db.execute("DELETE FROM user_facts WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM session_summary WHERE user_id=?", (user_id,))

    def all_memories(self, user_id: str) -> list[dict]:
        """Every memory (not the recency-capped view) — for a full data export."""
        rows = self.db.query(
            "SELECT category, content, created_at FROM memories WHERE user_id=? ORDER BY id",
            (user_id,))
        return [dict(r) for r in rows]

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


class FollowupStore:
    """Scheduled outcome check-ins. One open (pending/sent) follow-up per user; delivered
    by the channel poller; terminal states are answered/cancelled/failed."""

    _OPEN = ("pending", "sent")

    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def schedule(self, user_id: str, channel: str, channel_user: str, question: str,
                 about: str, due_at: str) -> bool:
        open_rows = self.db.query(
            "SELECT 1 FROM followups WHERE user_id=? AND status IN ('pending','sent') LIMIT 1",
            (user_id,),
        )
        if open_rows:
            return False
        self.db.execute(
            "INSERT INTO followups(user_id, channel, channel_user, question, about, due_at, "
            "status, attempts, created_at) VALUES (?,?,?,?,?,?,'pending',0,?)",
            (user_id, channel, str(channel_user), question, about, due_at, _now()),
        )
        return True

    def due(self, channel: str, now: str) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM followups WHERE channel=? AND status='pending' AND due_at<=? "
            "ORDER BY due_at ASC",
            (channel, now),
        )
        return [dict(r) for r in rows]

    def open_for(self, user_id: str) -> dict | None:
        rows = self.db.query(
            "SELECT * FROM followups WHERE user_id=? AND status IN ('pending','sent') "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return dict(rows[0]) if rows else None

    def mark_sent(self, fid: int) -> None:
        self.db.execute("UPDATE followups SET status='sent', sent_at=? WHERE id=?", (_now(), fid))

    def bump_attempt(self, fid: int) -> int:
        self.db.execute("UPDATE followups SET attempts=attempts+1 WHERE id=?", (fid,))
        rows = self.db.query("SELECT attempts FROM followups WHERE id=?", (fid,))
        return rows[0]["attempts"] if rows else 0

    def mark_failed(self, fid: int) -> None:
        self.db.execute("UPDATE followups SET status='failed' WHERE id=?", (fid,))

    def mark_answered(self, fid: int) -> None:
        self.db.execute("UPDATE followups SET status='answered' WHERE id=?", (fid,))

    def cancel(self, fid: int) -> None:
        self.db.execute("UPDATE followups SET status='cancelled' WHERE id=?", (fid,))

    def record_outcome(self, fid: int, outcome: str) -> None:
        self.db.execute("UPDATE followups SET outcome=? WHERE id=?", (outcome, fid))


class CommunityStore:
    """Shared community insights: per-user learnings, generalized + owner-approved, that can
    surface to other operators. Only `insight`/`topic` ever leave via search_approved."""

    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def nominate(self, source_user_id: str, original: str, insight: str, topic: str) -> bool:
        insight = (insight or "").strip()
        if not insight:
            return False
        dup = self.db.query(
            "SELECT 1 FROM community_insights WHERE lower(insight)=? "
            "AND status IN ('pending','approved') LIMIT 1",
            (insight.lower(),),
        )
        if dup:
            return False
        self.db.execute(
            "INSERT INTO community_insights(source_user_id, original, insight, topic, status, "
            "created_at) VALUES (?,?,?,?,'pending',?)",
            (source_user_id, original or "", insight, (topic or "").strip(), _now()),
        )
        return True

    def pending(self) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM community_insights WHERE status='pending' ORDER BY id ASC"
        )
        return [dict(r) for r in rows]

    def approve(self, cid: int) -> None:
        self.db.execute(
            "UPDATE community_insights SET status='approved', reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (_now(), cid),
        )

    def reject(self, cid: int) -> None:
        self.db.execute(
            "UPDATE community_insights SET status='rejected', reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (_now(), cid),
        )

    def search_approved(self, query: str) -> list[dict]:
        like = f"%{(query or '').strip().lower()}%"
        rows = self.db.query(
            "SELECT insight, topic FROM community_insights WHERE status='approved' "
            "AND (lower(insight) LIKE ? OR lower(topic) LIKE ?) ORDER BY id DESC LIMIT 5",
            (like, like),
        )
        return [dict(r) for r in rows]


class CalibrationStore:
    """Per-operator coefficient measurements -> bounded overrides. A coefficient is applied only
    with >=2 measurements whose mean is within the published empirical range (aqua_model
    calibration); seeds are never touched."""

    _MIN_OBS = 2

    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def record(self, user_id: str, coefficient: str, value: float) -> None:
        self.db.execute(
            "INSERT INTO measurements(user_id, coefficient, value, recorded_at) VALUES (?,?,?,?)",
            (user_id, coefficient, float(value), _now()),
        )

    def export(self, user_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT coefficient, value, recorded_at FROM measurements WHERE user_id=? ORDER BY id",
            (user_id,))
        return [dict(r) for r in rows]

    def purge(self, user_id: str) -> None:
        self.db.execute("DELETE FROM measurements WHERE user_id=?", (user_id,))

    def _by_coefficient(self, user_id: str) -> dict[str, list[float]]:
        rows = self.db.query(
            "SELECT coefficient, value FROM measurements WHERE user_id=?", (user_id,)
        )
        out: dict[str, list[float]] = {}
        for r in rows:
            out.setdefault(r["coefficient"], []).append(r["value"])
        return out

    def overrides_for(self, user_id: str) -> dict:
        from aqua_model import calibration
        out: dict[str, float] = {}
        for key, vals in self._by_coefficient(user_id).items():
            if len(vals) < self._MIN_OBS:
                continue
            try:
                cal = calibration.get(key)
            except KeyError:
                continue
            mean = sum(vals) / len(vals)
            if cal.emp_low <= mean <= cal.emp_high:
                out[key] = round(mean, 4)
        return out

    def calibration_report(self, user_id: str) -> list[dict]:
        from aqua_model import calibration
        report = []
        for key, vals in self._by_coefficient(user_id).items():
            mean_exact = sum(vals) / len(vals)
            mean = round(mean_exact, 4)
            try:
                cal = calibration.get(key)
            except KeyError:
                report.append({"coefficient": key, "n": len(vals), "mean": mean,
                               "applied": False, "seed": None, "emp_low": None,
                               "emp_high": None, "in_range": None})
                continue
            in_range = cal.emp_low <= mean_exact <= cal.emp_high   # full-precision, not rounded
            report.append({
                "coefficient": key, "n": len(vals), "mean": mean,
                "applied": len(vals) >= self._MIN_OBS and in_range,
                "seed": cal.seed, "emp_low": cal.emp_low, "emp_high": cal.emp_high,
                "in_range": in_range,
            })
        return report


class ReadingStore:
    """Append-only log of what the farmer measured vs what the twin thought at the time.

    The live twin is a single overwritten snapshot by design — it models TODAY. This table
    is the memory that snapshot cannot keep: every logged reading with the model's value
    beside it, so drift can be charted over weeks instead of narrated once and forgotten.
    """

    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def record(self, user_id: str, observed: dict, modelled: dict,
               greenhouse: str = "poly", recorded_at: str | None = None) -> None:
        """Store one logged reading. `observed` is what the user measured, `modelled` what
        the twin held for those same fields — keys absent from either are simply absent."""
        self.db.execute(
            "INSERT INTO twin_readings(user_id, recorded_at, greenhouse, observed, modelled)"
            " VALUES (?,?,?,?,?)",
            (user_id, recorded_at or _now(), str(greenhouse),
             json.dumps(_clean_readings(observed)), json.dumps(_clean_readings(modelled))),
        )

    def history(self, user_id: str) -> list[dict]:
        """Every reading for this user, oldest first."""
        rows = self.db.query(
            "SELECT recorded_at, greenhouse, observed, modelled FROM twin_readings"
            " WHERE user_id=? ORDER BY id", (user_id,))
        return [{"recorded_at": r["recorded_at"], "greenhouse": r["greenhouse"],
                 "observed": json.loads(r["observed"]), "modelled": json.loads(r["modelled"])}
                for r in rows]

    def purge(self, user_id: str) -> None:
        self.db.execute("DELETE FROM twin_readings WHERE user_id=?", (user_id,))


def _clean_readings(d: dict | None) -> dict:
    """Drop unset fields and coerce to plain floats — the caller passes a dict with None
    for every reading the user did not take."""
    return {k: float(v) for k, v in (d or {}).items() if v is not None}


class ProposalStore:
    """The approval gate: proposals recorded, decisions recorded, nothing acted on.

    Two properties matter and both are enforced here rather than by convention.

    *Only the latest proposal is decidable.* An operator typing `/approve 2` means item 2 of
    what they are looking at now. If a stale proposal could still take decisions, the same
    two keystrokes would mean different things depending on how far the chat had scrolled,
    which is precisely the class of mistake an approval gate exists to prevent.

    *A decision is final.* Re-approving or flipping an already-decided item is refused, not
    silently overwritten, because the row is evidence about what a person chose at a moment.
    Changing their mind means asking for a fresh proposal, which is honest: the system has
    moved on by then anyway.
    """

    OPEN, APPROVED, REJECTED = "proposed", "approved", "rejected"

    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def record(self, user_id: str, payload: dict) -> int:
        """Store a freshly computed proposal and return its id. `payload` is
        `advisory.to_dict(proposal)`; positions follow the order it renders in."""
        now = _now()
        cur = self.db.execute(
            "INSERT INTO proposals(user_id, as_of, context, payload, created_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, str(payload.get("as_of") or now[:10]), str(payload.get("context", "")),
             json.dumps(payload), now))
        pid = int(cur.lastrowid)
        for i, r in enumerate(payload.get("recommendations", []), 1):
            self.db.execute(
                "INSERT INTO proposal_items(proposal_id, user_id, position, action,"
                " confidence, evidence, status) VALUES (?,?,?,?,?,?,?)",
                (pid, user_id, i, str(r["action"]), float(r["confidence"]),
                 str(r["evidence"]), self.OPEN))
        return pid

    def latest(self, user_id: str) -> dict | None:
        """The most recent proposal with its items, or None if this user has never had one."""
        rows = self.db.query(
            "SELECT id, as_of, context, payload, created_at FROM proposals"
            " WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": int(r["id"]), "as_of": r["as_of"], "context": r["context"],
                "payload": json.loads(r["payload"]), "created_at": r["created_at"],
                "items": self._items(int(r["id"]))}

    def _items(self, proposal_id: int) -> list[dict]:
        rows = self.db.query(
            "SELECT position, action, confidence, evidence, status, decided_at"
            " FROM proposal_items WHERE proposal_id=? ORDER BY position", (proposal_id,))
        return [{"position": int(x["position"]), "action": x["action"],
                 "confidence": float(x["confidence"]), "evidence": x["evidence"],
                 "status": x["status"], "decided_at": x["decided_at"]} for x in rows]

    def decide(self, user_id: str, positions, approve: bool) -> dict:
        """Record the operator's decision on items of their LATEST proposal.

        Returns what happened, per position, rather than raising: a farmer typing a number
        that is off by one should get a sentence, not a stack trace. Keys: `decided`,
        `already` (position -> prior status), `unknown`, plus `proposal_id` and `action_for`.
        """
        latest = self.latest(user_id)
        out: dict = {"decided": [], "already": {}, "unknown": [], "proposal_id": None,
                     "action_for": {}}
        if latest is None:
            return out
        out["proposal_id"] = latest["id"]
        by_pos = {i["position"]: i for i in latest["items"]}
        out["action_for"] = {i["position"]: i["action"] for i in latest["items"]}
        status = self.APPROVED if approve else self.REJECTED
        now = _now()
        for pos in positions:
            item = by_pos.get(int(pos))
            if item is None:
                out["unknown"].append(int(pos))
                continue
            if item["status"] != self.OPEN:
                out["already"][int(pos)] = item["status"]
                continue
            self.db.execute(
                "UPDATE proposal_items SET status=?, decided_at=? WHERE proposal_id=?"
                " AND position=? AND status=?",
                (status, now, latest["id"], int(pos), self.OPEN))
            out["decided"].append(int(pos))
        return out

    def approved_history(self, user_id: str, limit: int = 20) -> list[dict]:
        """Actions this operator approved, newest first — the record that lets a later
        question ("did following the advice help?") be answered against the readings."""
        rows = self.db.query(
            "SELECT i.action, i.confidence, i.evidence, i.decided_at, p.as_of, p.context"
            " FROM proposal_items i JOIN proposals p ON p.id = i.proposal_id"
            " WHERE i.user_id=? AND i.status=? ORDER BY i.id DESC LIMIT ?",
            (user_id, self.APPROVED, int(limit)))
        return [{"action": r["action"], "confidence": float(r["confidence"]),
                 "evidence": r["evidence"], "decided_at": r["decided_at"],
                 "as_of": r["as_of"], "context": r["context"]} for r in rows]

    def purge(self, user_id: str) -> None:
        self.db.execute("DELETE FROM proposal_items WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM proposals WHERE user_id=?", (user_id,))

"""The database says which layout it holds, so an upgrade cannot read it wrong.

Every statement in `_SCHEMA` is `CREATE TABLE IF NOT EXISTS`, which means an old database
opens without complaint and then fails on the first missing column, deep inside a turn, on
someone else's machine. The stamp exists so that the failure happens at open time and says
something useful instead.

The case that matters most is the middle one: a database written by 1.0.0, before any stamp
existed, must keep its data and be adopted rather than rejected or rebuilt.
"""

import sqlite3

import pytest

from agronaut_agent.store import (
    SCHEMA_VERSION,
    ConversationStore,
    SchemaTooNewError,
    _Db,
)


def _user_version(path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_a_fresh_database_is_stamped(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    _Db(path)
    assert _user_version(path) == SCHEMA_VERSION


def test_a_pre_stamp_database_keeps_its_history_and_is_adopted(tmp_path):
    """A 1.0.0 database carries user_version 0 and the v1 layout. That IS v1."""
    path = tmp_path / "old.sqlite3"
    db = _Db(path)
    convo = ConversationStore(db)
    user = convo.get_or_create_user("telegram", "77", "Rekin")
    convo.append_message(user, "user", "how big a tank for 50 tilapia?")

    # Rewind the stamp to simulate a file written before this version existed.
    raw = sqlite3.connect(str(path))
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()
    assert _user_version(path) == 0

    reopened = ConversationStore(_Db(path))
    assert _user_version(path) == SCHEMA_VERSION
    kept = reopened.recent_messages(user)
    assert any("50 tilapia" in m["content"] for m in kept), "history was lost on adoption"


def test_a_database_from_the_future_is_refused_not_guessed_at(tmp_path):
    path = tmp_path / "future.sqlite3"
    _Db(path)
    raw = sqlite3.connect(str(path))
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaTooNewError) as err:
        _Db(path)
    # The message has to tell an operator what to do, not just that something is wrong.
    assert "pip install -U agronaut" in str(err.value)


def test_reopening_an_current_database_leaves_the_stamp_alone(tmp_path):
    path = tmp_path / "steady.sqlite3"
    _Db(path)
    for _ in range(3):
        _Db(path)
    assert _user_version(path) == SCHEMA_VERSION

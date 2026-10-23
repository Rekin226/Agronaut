import json
import sqlite3
from datetime import datetime, timezone

import pytest

from agro_model.farm import Farm, Observation, ProductionCycle, ProductionUnit
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import SCHEMA_VERSION, _Db


def legacy_database(path):
    # Deliberately build the old layout, rather than calling current migration code.
    conn = sqlite3.connect(path)
    conn.executescript("""
    PRAGMA user_version = 1;
    CREATE TABLE users (user_id TEXT PRIMARY KEY, channel TEXT, channel_user TEXT,
                        display_name TEXT, created_at TEXT, UNIQUE(channel, channel_user));
    CREATE TABLE user_facts (user_id TEXT, key TEXT, value TEXT, source TEXT,
                            updated_at TEXT, PRIMARY KEY(user_id,key));
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
                           role TEXT, content TEXT, tool_name TEXT, created_at TEXT);
    INSERT INTO users VALUES ('u1','telegram','1','One','2026-01-01');
    INSERT INTO users VALUES ('u2','telegram','2','Two','2026-01-01');
    INSERT INTO user_facts VALUES ('u1','fish_species','tilapia','user_stated','2026-01-02');
    INSERT INTO user_facts VALUES ('u2','crop','lettuce','parsed','2026-01-03');
    INSERT INTO messages(user_id,role,content) VALUES ('u1','user','My old notes');
    """)
    conn.close()


def seed(store, owner="owner", suffix=""):
    farm = Farm("farm"+suffix, owner, "Farm", "Asia/Taipei")
    store.create_farm(farm)
    unit = ProductionUnit("unit"+suffix, farm.id, "Tomatoes", "soil", "soil",
                          "open_field", "fully_irrigated", "drip")
    store.create_unit(owner, unit)
    store.create_cycle(owner, ProductionCycle("cycle"+suffix, unit.id, "tomato", "2026-07-01"))
    return unit


def test_migration_preserves_legacy_facts_and_creates_restorable_backup(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    db = _Db(path)
    store = FarmStore(db)
    one = store.list_units("u1")
    two = store.list_units("u2")
    assert len(one) == len(two) == 1
    assert one[0]["production_system"] == "aquaponics"
    assert two[0]["production_system"] is None
    assert store.export("u1")["unit_facts"][0]["source"] == "user_stated"
    assert db.query("SELECT content FROM messages")[0]["content"] == "My old notes"
    backups = list(tmp_path.glob(f"legacy.sqlite3.pre-v{SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    assert backup.execute("SELECT count(*) FROM user_facts").fetchone()[0] == 2
    backup.close()
    reopened = FarmStore(_Db(path))
    assert len(reopened.list_units("u1")) == 1
    assert len(list(tmp_path.glob("*.bak"))) == 1
    assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION


def test_failed_upgrade_rolls_back_schema_and_stamp(tmp_path, monkeypatch):
    from agronaut_agent import farm_store
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    original = farm_store.migrate_farms

    def fail(conn):
        original(conn)
        raise RuntimeError("simulated interrupted migration")

    monkeypatch.setattr(farm_store, "migrate_farms", fail)
    with pytest.raises(RuntimeError):
        _Db(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='farms'").fetchone() is None
    assert conn.execute("SELECT count(*) FROM user_facts").fetchone()[0] == 2
    conn.close()


def test_scope_integrity_and_idempotent_observations(tmp_path):
    store = FarmStore(_Db(tmp_path / "farms.sqlite3"))
    unit = seed(store)
    other = seed(store, "other", "2")
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    observation = Observation("obs", "owner", unit.id, "cycle", "rainfall", 5, "mm",
                              now, now, "measured", "gauge")
    assert store.record_observation("owner", observation) is True
    assert store.record_observation("owner", observation) is False
    assert len(store.observations("owner", unit.id)) == 1
    with pytest.raises(ValueError):
        store.record_observation("other", observation)
    with pytest.raises(ValueError):
        store.observations("other", unit.id)
    with pytest.raises(ValueError):
        store.create_cycle("owner", ProductionCycle("bad", other.id, "onion", "2026-07-01"))
    bad = Observation("wrong-cycle", "owner", unit.id, "cycle2", "rainfall", 5, "mm",
                      now, now, "measured", "gauge")
    with pytest.raises(ValueError):
        store.record_observation("owner", bad)


def test_conflicting_event_id_never_overwrites_an_observation(tmp_path):
    from dataclasses import replace
    store = FarmStore(_Db(tmp_path / "farm.sqlite3"))
    seed(store)
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    first = Observation("obs", "owner", "unit", "cycle", "rainfall", 5, "mm",
                        now, now, "reported", "operator")
    store.record_observation("owner", first)
    with pytest.raises(ValueError, match="already"):
        store.record_observation("owner", replace(first, value=20))
    assert store.observations("owner", "unit")[0]["value"] == 5


def test_export_and_purge_are_owner_scoped(tmp_path):
    store = FarmStore(_Db(tmp_path / "farm.sqlite3"))
    seed(store)
    seed(store, "other", "2")
    exported = store.export("owner")
    assert len(exported["farms"]) == len(exported["units"]) == len(exported["cycles"]) == 1
    json.dumps(exported)
    store.purge("owner")
    assert all(not rows for rows in store.export("owner").values())
    assert len(store.list_units("other")) == 1


def test_rejected_duplicate_farm_does_not_poison_the_connection(tmp_path):
    store = FarmStore(_Db(tmp_path / "farm.sqlite3"))
    farm = Farm("farm", "owner", "Home", "Asia/Taipei")
    store.create_farm(farm)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_farm(farm)
    store.create_unit("owner", ProductionUnit(
        "unit", "farm", "Field", "soil", "soil", "open_field", "rainfed"))
    assert len(store.list_units("owner")) == 1

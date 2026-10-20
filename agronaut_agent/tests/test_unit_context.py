"""Unit context isolation, including state beyond simple profile facts."""

import sqlite3

import pytest

from agro_model.farm import Farm, ProductionUnit
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import (
    SCHEMA_VERSION,
    ConversationStore,
    FollowupStore,
    MemoryStore,
    _Db,
)
from agronaut_agent.unit_context import UnitContexts


def setup_units(tmp_path):
    db = _Db(tmp_path / "farm.sqlite3")
    farms = FarmStore(db)
    for owner in ("cli:one", "cli:two"):
        farms.create_farm(Farm(owner, owner, "Home", "UTC"))
        for name in ("tomatoes", "onions"):
            farms.create_unit(owner, ProductionUnit(
                owner + name, owner, name, "soil", "soil", "open_field", "fully_irrigated", "drip"))
    return db, farms, UnitContexts(db)


def test_selecting_unit_is_owner_checked_and_durable(tmp_path):
    db, farms, contexts = setup_units(tmp_path)
    assert contexts.active("cli:one") is None
    with pytest.raises(ValueError, match="unknown production unit"):
        contexts.select("cli:one", "cli:twotomatoes")
    first = contexts.select("cli:one", "cli:onetomatoes")
    assert first.unit_id == "cli:onetomatoes"
    assert first.owner_id == "cli:one"
    assert first.production_system == "soil"
    reopened = UnitContexts(_Db(db.path))
    assert reopened.active("cli:one") == first
    assert contexts.active("cli:two") is None
    contexts.clear_selection("cli:one")
    assert contexts.active("cli:one") is None
    assert contexts.select("cli:one", first.unit_id) == first


def test_switching_keeps_facts_history_summaries_and_followups_separate(tmp_path):
    db, farms, contexts = setup_units(tmp_path)
    mem, conv, followups = MemoryStore(db), ConversationStore(db), FollowupStore(db)
    mem.set_fact("cli:one", "fish_species", "tilapia")
    conv.append_message("cli:one", "user", "Ambiguous legacy conversation")
    first = contexts.select("cli:one", "cli:onetomatoes")
    assert mem.get_facts(first.context_id) == {}
    assert conv.recent_messages(first.context_id) == []
    mem.set_fact(first.context_id, "crop", "tomato")
    mem.add_memory(first.context_id, "Field one had runoff")
    mem.set_summary(first.context_id, "Tomatoes in the upper field")
    conv.append_message(first.context_id, "user", "Tomato crop in upper field")
    followups.schedule(first.context_id, "cli", "one", "Was the flow measured?", "flow", "2026-07-01")
    second = contexts.select("cli:one", "cli:oneonions")
    assert second.context_id != first.context_id
    assert mem.get_facts(second.context_id) == {}
    assert mem.get_memories(second.context_id) == []
    assert mem.get_summary(second.context_id) is None
    assert conv.recent_messages(second.context_id) == []
    assert followups.open_for(second.context_id) is None
    again = contexts.select("cli:one", "cli:onetomatoes")
    assert mem.get_facts(again.context_id) == {"crop": "tomato"}
    assert followups.open_for(again.context_id) is not None
    assert mem.get_facts("cli:one") == {"fish_species": "tilapia"}


def test_legacy_unit_copies_only_its_migrated_facts_on_first_selection(tmp_path):
    db, farms, contexts = setup_units(tmp_path)
    db.execute("INSERT INTO unit_facts VALUES(?,?,?,?,?)",
               ("cli:onetomatoes", "crop", "tomato", "reported", "2026-01-01"))
    selected = contexts.select("cli:one", "cli:onetomatoes")
    mem = MemoryStore(db)
    assert mem.get_facts(selected.context_id)["crop"] == "tomato"
    mem.set_fact(selected.context_id, "crop", "maize")
    contexts.select("cli:one", "cli:onetomatoes")
    assert mem.get_facts(selected.context_id)["crop"] == "maize"


def test_unit_context_export_and_purge_cover_all_scoped_state(tmp_path):
    db, farms, contexts = setup_units(tmp_path)
    mine = contexts.select("cli:one", "cli:onetomatoes")
    other = contexts.select("cli:two", "cli:twotomatoes")
    for selected in (mine, other):
        mem = MemoryStore(db)
        mem.set_fact(selected.context_id, "crop", "tomato")
        mem.add_memory(selected.context_id, "Observation")
        memory_id = db.query("SELECT id FROM memories WHERE user_id=?", (selected.context_id,))[0][0]
        db.execute("INSERT INTO memory_embeddings VALUES(?,?,?)", (memory_id, 1, b"\x00\x00\x00\x00"))
        mem.set_summary(selected.context_id, "Summary")
        ConversationStore(db).append_message(selected.context_id, "assistant", "Report")
        FollowupStore(db).schedule(selected.context_id, "cli", selected.owner_id.split(":")[1],
                                   "Follow-up", "water", "2026-07-01")
        db.execute("INSERT INTO measurements(user_id,coefficient,value,recorded_at) VALUES(?,?,?,?)",
                   (selected.context_id, "example", 1, "2026-01-01"))
        db.execute("INSERT INTO twin_readings(user_id,recorded_at,greenhouse,observed,modelled) "
                   "VALUES(?,?,?,?,?)", (selected.context_id, "2026-01-01", "open", "{}", "{}"))
        db.execute("INSERT INTO proposals(user_id,as_of,context,payload,created_at) VALUES(?,?,?,?,?)",
                   (selected.context_id, "2026-01-01", "{}", "{}", "2026-01-01"))
        pid = db.query("SELECT id FROM proposals WHERE user_id=?", (selected.context_id,))[0][0]
        db.execute("INSERT INTO proposal_items(proposal_id,user_id,position,action,confidence,evidence,status) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, selected.context_id, 1, "check", 0.5, "{}", "proposed"))
        db.execute("INSERT INTO community_insights(source_user_id,original,insight,status,created_at) "
                   "VALUES(?,?,?,?,?)", (selected.context_id, "Original", "Insight", "pending", "2026-01-01"))
    before = contexts.export("cli:two")
    exported = contexts.export("cli:one")
    assert len(exported) == 1
    for key in ("facts", "messages", "memories", "summaries", "measurements", "readings",
                "proposals", "proposal_items", "followups", "community_insights"):
        assert len(exported[0][key]) == 1
    # FarmStore deletion must not strand any of the existing stores' context rows.
    farms.purge("cli:one")
    assert contexts.active("cli:one") is None
    assert contexts.export("cli:one") == []
    for table, column in contexts.owned_tables():
        assert db.query(f"SELECT * FROM {table} WHERE {column}=?", (mine.context_id,)) == []
    assert contexts.export("cli:two") == before
    assert len(db.query("SELECT * FROM memory_embeddings")) == 1
    assert db.query("SELECT * FROM memory_embeddings e JOIN memories m ON m.id=e.memory_id "
                    "WHERE m.user_id=?", (other.context_id,))


def test_approval_ids_and_calibration_cannot_cross_unit_contexts(tmp_path):
    from agronaut_agent.store import CalibrationStore, ProposalStore

    db, farms, contexts = setup_units(tmp_path)
    first = contexts.select("cli:one", "cli:onetomatoes")
    second = contexts.select("cli:one", "cli:oneonions")
    proposals, calibration = ProposalStore(db), CalibrationStore(db)
    proposals.record(first.context_id, {"as_of": "2026-07-01", "context": {},
                                      "recommendations": [{"action": "measure flow", "confidence": 0.5,
                                                           "evidence": []}]})
    calibration.record(first.context_id, "tilapia.fcr", 1.5)
    assert proposals.latest(second.context_id) is None
    assert calibration.export(second.context_id) == []
    # Position 1 in one field never approves position 1 in another field.
    proposals.decide(second.context_id, [1], True)
    assert proposals.approved_history(first.context_id) == []


def test_delayed_write_cannot_recreate_an_erased_unit_profile(tmp_path):
    db, farms, contexts = setup_units(tmp_path)
    selected = contexts.select("cli:one", "cli:onetomatoes")
    mem = MemoryStore(db)
    farms.purge("cli:one")
    with pytest.raises(sqlite3.IntegrityError, match="deleted unit context"):
        mem.set_summary(selected.context_id, "Late background result")
    with pytest.raises(sqlite3.IntegrityError, match="deleted unit context"):
        mem.set_fact(selected.context_id, "crop", "tomato")
    assert mem.get_facts(selected.context_id) == {}
    assert mem.get_summary(selected.context_id) is None


def test_upgrade_from_v2_keeps_farm_records_and_creates_context_tables(tmp_path):
    path = tmp_path / "v2.sqlite3"
    db = _Db(path)
    FarmStore(db).create_farm(Farm("farm", "cli:one", "Home", "UTC"))
    db._conn.close()
    with sqlite3.connect(path) as raw:
        raw.execute("DROP TABLE farm_resource_plans")
        raw.execute("DROP TABLE active_units")
        raw.execute("DROP TABLE unit_contexts")
        raw.execute("DROP TABLE field_calculations")
        raw.execute("DROP TABLE field_water_days")
        raw.execute("DROP TABLE unit_setup_drafts")
        raw.execute("DROP TABLE observation_reviews")
        raw.execute("DROP TABLE crop_records")
        raw.execute("PRAGMA user_version=2")
    reopened = _Db(path)
    assert FarmStore(reopened).export("cli:one")["farms"][0]["id"] == "farm"
    assert UnitContexts(reopened).active("cli:one") is None
    backups = list(tmp_path.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 2

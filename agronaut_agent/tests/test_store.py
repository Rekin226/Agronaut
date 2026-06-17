"""Per-user persistence: identity, history, memory — concurrency-safe by construction."""

import pytest

from agronaut_agent.store import _Db, ConversationStore, MemoryStore, user_id_for
from agronaut_agent import memory_extract


@pytest.fixture
def stores(tmp_path):
    db = _Db(tmp_path / "t.sqlite3")
    return ConversationStore(db), MemoryStore(db)


def test_user_id_namespacing():
    assert user_id_for("telegram", "123") == "telegram:123"
    assert user_id_for("discord", "123") != user_id_for("telegram", "123")


def test_get_or_create_is_idempotent(stores):
    conv, _ = stores
    a = conv.get_or_create_user("telegram", "123", "Rachid")
    b = conv.get_or_create_user("telegram", "123", "Rachid")
    assert a == b == "telegram:123"


def test_message_history_ordered_and_resettable(stores):
    conv, _ = stores
    uid = conv.get_or_create_user("telegram", "123")
    conv.append_message(uid, "user", "hi")
    conv.append_message(uid, "assistant", "hello")
    assert [m["role"] for m in conv.recent_messages(uid)] == ["user", "assistant"]
    conv.reset_conversation(uid)
    assert conv.recent_messages(uid) == []


def test_facts_upsert_and_survive_conversation_reset(stores):
    conv, mem = stores
    uid = conv.get_or_create_user("telegram", "123")
    mem.set_facts(uid, {"temperature_c": "28", "fish_species": "tilapia"})
    mem.set_fact(uid, "temperature_c", "30", source="user_stated")  # overwrite
    assert mem.get_facts(uid) == {"temperature_c": "30", "fish_species": "tilapia"}
    conv.reset_conversation(uid)
    assert mem.get_facts(uid) == {"temperature_c": "30", "fish_species": "tilapia"}


def test_extract_facts_from_free_text():
    facts = memory_extract.extract_facts("water is 26C and pH 7.2, I keep tilapia")
    assert facts["temperature_c"] == "26.0"
    assert facts["ph"] == "7.2"
    assert facts["fish_species"].lower() == "tilapia"


def test_extract_facts_does_not_fabricate_ph_from_bare_numbers():
    # "10 m2" must NOT be read as pH 10 — only an explicit pH cue counts (honesty ethos).
    facts = memory_extract.extract_facts("Size a 10 m2 tilapia and lettuce system at 26C with 250 L/day")
    assert "ph" not in facts
    assert facts["temperature_c"] == "26.0"


def test_memories_add_dedup_and_order(stores):
    _, mem = stores
    uid = "telegram:7"
    assert mem.add_memory(uid, "Runs a 3000 L IBC system", "profile") is True
    assert mem.add_memory(uid, "runs a 3000 l ibc system", "profile") is False  # case-insensitive dup
    assert mem.add_memory(uid, "Had an ammonia spike in June", "event") is True
    assert mem.memory_count(uid) == 2
    mems = mem.get_memories(uid)
    assert [m["content"] for m in mems][-1] == "Had an ammonia spike in June"  # newest last
    assert {m["category"] for m in mems} == {"profile", "event"}


def test_memory_category_falls_back_to_profile(stores):
    _, mem = stores
    mem.add_memory("u", "note", "nonsense-category")
    assert mem.get_memories("u")[0]["category"] == "profile"


def test_summary_upsert(stores):
    _, mem = stores
    assert mem.get_summary("u") is None
    mem.set_summary("u", "Operator in Burkina, 3000L system, recurring pH drift.")
    mem.set_summary("u", "Updated recap.")
    assert mem.get_summary("u") == "Updated recap."


def test_forget_wipes_memory_and_summary(stores):
    conv, mem = stores
    uid = conv.get_or_create_user("telegram", "7")
    mem.add_memory(uid, "note", "profile")
    mem.set_summary(uid, "recap")
    mem.set_fact(uid, "temperature_c", "27")
    mem.forget(uid)
    assert mem.memory_count(uid) == 0
    assert mem.get_summary(uid) is None
    assert mem.get_facts(uid) == {}

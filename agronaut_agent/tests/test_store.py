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


from agronaut_agent.store import _Db, FollowupStore, _now


def _fs():
    return FollowupStore(_Db(":memory:"))


def test_schedule_one_open_per_user():
    fs = _fs()
    assert fs.schedule("telegram:1", "telegram", "1", "did it work?", "ammonia", "2000-01-01T00:00:00+00:00")
    # a second while one is still open is refused
    assert fs.schedule("telegram:1", "telegram", "1", "again?", "ph", "2000-01-01T00:00:00+00:00") is False


def test_due_returns_only_past_due_pending_for_channel():
    fs = _fs()
    fs.schedule("telegram:1", "telegram", "1", "q1", "a", "2000-01-01T00:00:00+00:00")  # past
    fs.schedule("telegram:2", "telegram", "2", "q2", "a", "2999-01-01T00:00:00+00:00")  # future
    due = fs.due("telegram", _now())
    assert [d["question"] for d in due] == ["q1"]


def test_sent_is_not_returned_by_due_no_nagging():
    fs = _fs()
    fs.schedule("telegram:1", "telegram", "1", "q1", "a", "2000-01-01T00:00:00+00:00")
    row = fs.due("telegram", _now())[0]
    fs.mark_sent(row["id"])
    assert fs.due("telegram", _now()) == []          # never resent
    assert fs.open_for("telegram:1")["status"] == "sent"


def test_bump_attempt_and_fail():
    fs = _fs()
    fs.schedule("telegram:1", "telegram", "1", "q", "a", "2000-01-01T00:00:00+00:00")
    fid = fs.due("telegram", _now())[0]["id"]
    assert fs.bump_attempt(fid) == 1 and fs.bump_attempt(fid) == 2 and fs.bump_attempt(fid) == 3
    fs.mark_failed(fid)
    assert fs.open_for("telegram:1") is None          # failed is not "open"


def test_answer_and_cancel_free_the_slot():
    fs = _fs()
    fs.schedule("telegram:1", "telegram", "1", "q", "a", "2000-01-01T00:00:00+00:00")
    fs.mark_answered(fs.open_for("telegram:1")["id"])
    # answered frees the slot -> a new one can be scheduled
    assert fs.schedule("telegram:1", "telegram", "1", "q2", "a", "2999-01-01T00:00:00+00:00")
    fs.cancel(fs.open_for("telegram:1")["id"])
    assert fs.open_for("telegram:1") is None


from agronaut_agent.store import CommunityStore


def _cs():
    return CommunityStore(_Db(":memory:"))


def test_nominate_dedups_and_rejects_blank():
    cs = _cs()
    assert cs.nominate("telegram:1", "raw ctx", "a partial water change clears an ammonia spike", "ammonia")
    # normalized-equal (case-insensitive) duplicate is refused
    assert cs.nominate("telegram:2", "x", "A PARTIAL water change clears an ammonia spike", "ammonia") is False
    # blank insight refused
    assert cs.nominate("telegram:3", "x", "   ", "ammonia") is False


def test_pending_lists_only_pending_oldest_first():
    cs = _cs()
    cs.nominate("telegram:1", "x", "insight one", "a")
    cs.nominate("telegram:1", "x", "insight two", "b")
    pend = cs.pending()
    assert [p["insight"] for p in pend] == ["insight one", "insight two"]


def test_approve_makes_it_searchable_reject_does_not():
    cs = _cs()
    cs.nominate("telegram:1", "x", "raise KH to stabilize pH swings", "ph")
    cid = cs.pending()[0]["id"]
    cs.approve(cid)
    assert cs.pending() == []
    hits = cs.search_approved("pH")
    assert len(hits) == 1 and hits[0]["insight"] == "raise KH to stabilize pH swings"

    cs.nominate("telegram:2", "x", "add shade cloth in summer heat", "temperature")
    cs.reject(cs.pending()[0]["id"])
    assert cs.search_approved("shade") == []          # rejected never surfaces


def test_search_approved_never_leaks_identity_or_original():
    cs = _cs()
    cs.nominate("telegram:secret", "private: 3000L IBC in Burkina", "aerate at dawn to prevent DO crashes", "do")
    cs.approve(cs.pending()[0]["id"])
    hit = cs.search_approved("DO")[0]
    assert set(hit.keys()) == {"insight", "topic"}     # no source_user_id, no original
    assert "Burkina" not in str(hit)


def test_approve_only_acts_on_pending():
    cs = _cs()
    cs.nominate("telegram:1", "x", "some insight", "t")
    cid = cs.pending()[0]["id"]
    cs.approve(cid)
    cs.reject(cid)                                      # already approved -> no-op
    assert len(cs.search_approved("some")) == 1         # still approved, not rejected

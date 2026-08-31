"""Semantic recall over agent-curated memories: top-k by embedding similarity to the
current turn, with lazy backfill for pre-existing rows and a recency fallback when no
embedder is available. Tests use a deterministic bag-of-words embedder — no model download.
"""

import re

import numpy as np
from langchain_core.messages import AIMessage

from agronaut_agent.core import AgronautAgent
from agronaut_agent.semantic import SemanticMemory
from agronaut_agent.store import MemoryStore, _Db


def _stable_bucket(word: str, dim: int = 64) -> int:
    # A process-stable hash (Python's built-in hash() is salted per run via PYTHONHASHSEED,
    # which makes bucket collisions — and thus ranking — flaky across runs).
    h = 0
    for ch in word:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h % dim


def _bow_embed(texts):
    """Deterministic toy embedder: stable-hashed bag-of-words, L2-normalized."""
    out = []
    for t in texts:
        v = np.zeros(64, dtype="float32")
        for w in re.findall(r"[a-z]+", str(t).lower()):
            v[_stable_bucket(w)] += 1.0
        n = float(np.linalg.norm(v)) or 1.0
        out.append(v / n)
    return out


def _fill_memories(mem, uid, n=19):
    for i in range(n):
        mem.add_memory(uid, f"harvested {i} kg of lettuce in week {i}", "event")


def test_search_surfaces_old_related_memory_over_recent_noise(tmp_path):
    db = _Db(tmp_path / "s.sqlite3")
    mem = MemoryStore(db)
    uid = "cli:sem"
    mem.add_memory(uid, "replaced the clogged impeller on the return pump", "learning")
    _fill_memories(mem, uid)  # 19 newer, unrelated memories — recency-only recall misses it

    sem = SemanticMemory(db, embed_fn=_bow_embed)
    hits = sem.search(uid, "that pump issue from before", k=5)
    assert hits, "semantic search returned nothing"
    assert "impeller" in hits[0]["content"]
    assert hits[0]["category"] == "learning"


def test_search_backfills_embeddings_for_existing_rows(tmp_path):
    db = _Db(tmp_path / "s.sqlite3")
    mem = MemoryStore(db)
    _fill_memories(mem, "cli:bf", n=6)
    sem = SemanticMemory(db, embed_fn=_bow_embed)
    sem.search("cli:bf", "anything at all", k=3)
    n = db.query("SELECT COUNT(*) AS c FROM memory_embeddings")[0]["c"]
    assert n == 6  # every memory row got a vector on first search


def test_unavailable_embedder_reports_unavailable(tmp_path):
    sem = SemanticMemory(_Db(tmp_path / "s.sqlite3"), embed_fn=None)
    assert sem.available is False
    assert sem.search("cli:x", "query", k=3) == []


class _ChattyFake:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Hello!")


def test_agent_recall_block_uses_semantic_match_for_current_turn(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake(),
                          embed_fn=_bow_embed)
    uid = agent._conv.get_or_create_user("cli", "sem2")
    agent._mem.add_memory(uid, "replaced the clogged impeller on the return pump", "learning")
    _fill_memories(agent._mem, uid)

    block = agent._recall_block(uid, query="remind me about that pump issue")
    assert "impeller" in block  # the 20th-oldest memory surfaced because it matches the turn


def test_agent_recall_falls_back_to_recency_without_embedder(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake(),
                          embed_fn=None)
    uid = agent._conv.get_or_create_user("cli", "sem3")
    agent._mem.add_memory(uid, "replaced the clogged impeller on the return pump", "learning")
    _fill_memories(agent._mem, uid)

    block = agent._recall_block(uid, query="remind me about that pump issue")
    # recency fallback: the last 12 memories are shown, exactly as before this feature
    assert "week 18" in block
    assert "impeller" not in block

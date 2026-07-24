"""Semantic recall over agent-curated memories.

SemanticMemory ranks a user's `memories` rows by cosine similarity to the current turn,
so recall stays relevant as history grows instead of degrading to "the last 12 notes".
The embedder is pluggable and optional: tests inject a deterministic one, production
lazily loads sentence-transformers (already a dependency of the KB index), and when no
embedder is available callers fall back to recency — exactly the pre-feature behaviour.

Vectors are cached in the memory_embeddings table (float32 bytes); rows written before
an embedder existed are backfilled on first search.
"""

from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger(__name__)

_EMBED_MODEL = os.getenv("AGRONAUT_EMBED_MODEL", "sentence-transformers/all-mpnet-base-v2")


def default_embedder():
    """A lazy sentence-transformers embedder, or None when disabled/unavailable.
    The model loads on first call, not at agent startup."""
    if os.getenv("AGRONAUT_EMBEDDINGS", "").lower() in {"off", "0", "false"}:
        return None
    state = {}

    def _embed(texts):
        if "model" not in state:
            from sentence_transformers import SentenceTransformer  # heavy: import lazily
            state["model"] = SentenceTransformer(_EMBED_MODEL)
        return state["model"].encode([str(t) for t in texts], normalize_embeddings=True)

    try:
        import sentence_transformers  # noqa: F401 — cheap availability probe only
    except Exception:
        return None
    return _embed


class SemanticMemory:
    def __init__(self, db, embed_fn=None):
        self.db = db
        self._embed = embed_fn

    @property
    def available(self) -> bool:
        return self._embed is not None

    def index_memory(self, memory_id: int, content: str) -> None:
        """Embed one memory row now (called on write when an embedder exists)."""
        if self._embed is None:
            return
        try:
            vec = np.asarray(self._embed([content])[0], dtype="float32")
        except Exception:  # embedding must never break a memory write
            log.debug("embedding failed for memory %s", memory_id, exc_info=True)
            return
        self._store(memory_id, vec)

    def search(self, user_id: str, query: str, k: int = 8) -> list[dict]:
        """Top-k memories for this user by cosine similarity to `query`.
        Returns [] when no embedder is available (callers fall back to recency)."""
        if self._embed is None:
            return []
        rows = self.db.query(
            "SELECT m.id, m.category, m.content, e.vector, e.dim FROM memories m "
            "LEFT JOIN memory_embeddings e ON e.memory_id = m.id WHERE m.user_id=?",
            (user_id,),
        )
        if not rows:
            return []
        try:
            missing = [r for r in rows if r["vector"] is None]
            if missing:  # lazy backfill for rows written before the embedder existed
                vecs = self._embed([r["content"] for r in missing])
                for r, v in zip(missing, vecs):
                    self._store(r["id"], np.asarray(v, dtype="float32"))
            qv = np.asarray(self._embed([query])[0], dtype="float32")
        except Exception:  # a flaky embedder degrades to recency, never breaks the turn
            log.debug("semantic search failed", exc_info=True)
            return []

        by_id = {}
        for r in rows:
            if r["vector"] is not None:
                by_id[r["id"]] = np.frombuffer(r["vector"], dtype="float32")
        for r in missing:
            got = self.db.query(
                "SELECT vector FROM memory_embeddings WHERE memory_id=?", (r["id"],))
            if got:
                by_id[r["id"]] = np.frombuffer(got[0]["vector"], dtype="float32")

        qn = float(np.linalg.norm(qv)) or 1.0
        scored = []
        for r in rows:
            v = by_id.get(r["id"])
            if v is None:
                continue
            vn = float(np.linalg.norm(v)) or 1.0
            scored.append((float(np.dot(qv, v)) / (qn * vn), r))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [{"id": r["id"], "category": r["category"], "content": r["content"]}
                for _s, r in scored[:k]]

    def _store(self, memory_id: int, vec: np.ndarray) -> None:
        self.db.execute(
            "INSERT INTO memory_embeddings(memory_id, dim, vector) VALUES (?,?,?) "
            "ON CONFLICT(memory_id) DO UPDATE SET dim=excluded.dim, vector=excluded.vector",
            (memory_id, int(vec.shape[0]), vec.tobytes()),
        )

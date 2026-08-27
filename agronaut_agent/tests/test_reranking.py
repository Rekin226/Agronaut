"""Cross-encoder reranking of the retrieved shortlist.

A bi-encoder embeds query and passage separately, so it compares two summaries of meaning. A
cross-encoder reads the pair together and scores their actual interaction — far better, far
slower, and therefore usable only on a shortlist. The fused pool is exactly that shortlist.

These tests inject a fake model: the real one is a ~90 MB download and its judgements are not
this module's contract. What IS the contract is that reranking reorders, that it runs before the
diversity cap, and that its absence costs ranking quality and nothing else.
"""


from agronaut_agent import rag


class _FakeCE:
    """Scores by how many query words appear in the passage."""
    def predict(self, pairs):
        return [sum(w in p.lower() for w in q.lower().split()) for q, p in pairs]


class _BoomCE:
    def predict(self, pairs):
        raise RuntimeError("model exploded mid-batch")


def _by_key(items):
    return {k: {"source": s, "text": txt, "score": 0.5} for k, s, txt in items}


KEYS = [("a", "knowledge/x.md", "nothing relevant here at all"),
        ("b", "knowledge/y.md", "nitrite spike in a new aquaponic system"),
        ("c", "knowledge/z.md", "spike")]


def test_reranking_reorders_by_pair_relevance(monkeypatch):
    monkeypatch.setattr(rag, "_RERANKER", _FakeCE())
    monkeypatch.setattr(rag, "_RERANK_TRIED", True)
    bk = _by_key(KEYS)
    out = rag._rerank("nitrite spike", ["a", "b", "c"], bk)
    assert out[0] == "b"


def test_missing_model_keeps_the_fused_order(monkeypatch):
    """A reranker that will not load must cost ranking quality and nothing else."""
    monkeypatch.setattr(rag, "_RERANKER", None)
    monkeypatch.setattr(rag, "_RERANK_TRIED", True)
    assert rag._rerank("q", ["a", "b", "c"], _by_key(KEYS)) == ["a", "b", "c"]


def test_scoring_failure_keeps_the_fused_order(monkeypatch):
    """A model that loads but throws mid-batch must not lose the results already retrieved."""
    monkeypatch.setattr(rag, "_RERANKER", _BoomCE())
    monkeypatch.setattr(rag, "_RERANK_TRIED", True)
    assert rag._rerank("q", ["a", "b", "c"], _by_key(KEYS)) == ["a", "b", "c"]


def test_single_item_is_not_reranked(monkeypatch):
    """Nothing to reorder — must not pay for a model call."""
    called = {"n": 0}

    class _Counting:
        def predict(self, pairs):
            called["n"] += 1
            return [1.0] * len(pairs)

    monkeypatch.setattr(rag, "_RERANKER", _Counting())
    monkeypatch.setattr(rag, "_RERANK_TRIED", True)
    assert rag._rerank("q", ["a"], _by_key(KEYS)) == ["a"]
    assert called["n"] == 0


def test_reranking_ships_disabled(monkeypatch):
    monkeypatch.delenv("AGRONAUT_RERANK", raising=False)
    assert not rag.rerank_enabled()
    monkeypatch.setenv("AGRONAUT_RERANK", "on")
    assert rag.rerank_enabled()


def test_passage_is_truncated_before_scoring(monkeypatch):
    """Cross-encoders have a short input window; an unbounded passage silently truncates inside
    the model or errors. Bound it here where the limit is visible."""
    seen = {}

    class _Capture:
        def predict(self, pairs):
            seen["max"] = max(len(p) for _q, p in pairs)
            return [1.0] * len(pairs)

    monkeypatch.setattr(rag, "_RERANKER", _Capture())
    monkeypatch.setattr(rag, "_RERANK_TRIED", True)
    bk = {"a": {"source": "s", "text": "x" * 9000, "score": 0.1},
          "b": {"source": "t", "text": "y" * 9000, "score": 0.2}}
    rag._rerank("q", ["a", "b"], bk)
    assert seen["max"] <= 2000

# Cross-User Learning Foundation (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human-gated pipeline that turns a verified per-user learning into vetted, generalized shared knowledge: the LLM nominates a PII-stripped insight, the owner approves it in a local CLI, and approved insights surface to other operators during troubleshooting — labeled peer-reported and never fed to the trust zone.

**Architecture:** A `community_insights` table + `CommunityStore` hold nominations through a `pending → approved/rejected` lifecycle. A `nominate_shared_insight` tool enqueues the LLM's generalized text; a local `review.py` CLI is the only place approval happens (off the chat surface); a `search_community_knowledge` tool surfaces approved insights (only `insight`+`topic`, never identity). Wired through `runtime` like the Phase-3 follow-up store.

**Tech Stack:** Python 3.12, SQLite (`store.py`), LangChain `@tool`, pytest. No new dependencies.

## Global Constraints

- `search_approved` returns ONLY `insight` + `topic` — never `source_user_id` or `original` (privacy).
- The tool stores the LLM's already-generalized, PII-stripped `insight`; the raw learning is never what gets shared.
- Approval happens ONLY in the local `review.py` CLI — never via Telegram or any chat-triggered path.
- Community insights are labeled "reported by other operators (community experience, not verified science)" on surfacing; never presented as fact/coefficients; never fed to `aqua_model`/`validate_design_input`.
- Dedup: `nominate` skips a normalized-equal (case-insensitive) `insight` already `pending`/`approved`.
- `approve`/`reject` only act on a `pending` row (status-guarded UPDATE).
- All stores share one `_Db`; `AGRONAUT_TOOLS` goes from 9 to **11**; timestamps UTC ISO via `_now()`.
- Deterministic tests — no live LLM; the review CLI's interactive loop is not unit-tested (its pure command logic is).
- Work on branch `feat/cross-user-learning` (already checked out). Commit after every task.

---

### Task 1: `community_insights` table + `CommunityStore`

**Files:**
- Modify: `agronaut_agent/store.py` (add table to `_SCHEMA`; add `CommunityStore`)
- Test: `agronaut_agent/tests/test_store.py`

**Interfaces:**
- Consumes: `_Db`, `_now`.
- Produces: `CommunityStore(db)` with:
  - `nominate(source_user_id, original, insight, topic) -> bool` — insert `pending`; `False` if `insight` blank or a normalized-equal insight is already `pending`/`approved`.
  - `pending() -> list[dict]` — `pending` rows, oldest first (full rows, for the owner's review).
  - `approve(cid)` / `reject(cid)` — status-guarded (only a `pending` row transitions).
  - `search_approved(query) -> list[dict]` — up to 5 `approved` rows whose `insight` or `topic` matches `query` (case-insensitive LIKE); each dict has ONLY `insight` and `topic`.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_store.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_store.py -k "nominate or pending_lists or approve or search_approved" -v`
Expected: FAIL — `ImportError: cannot import name 'CommunityStore'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/store.py`, add the table to `_SCHEMA` (before its closing `"""`, after the `followups` block):

```python
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
```

Then add the class at the end of `store.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_store.py -v`
Expected: PASS (all, including the 5 new community tests)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/store.py agronaut_agent/tests/test_store.py
git commit -m "feat(store): community_insights table + CommunityStore (human-gated, privacy-safe)"
```

---

### Task 2: `runtime` community context + nominate/search tools

**Files:**
- Modify: `agronaut_agent/runtime.py`
- Modify: `agronaut_agent/tools.py`
- Test: `agronaut_agent/tests/test_tools.py`

**Interfaces:**
- Consumes: `CommunityStore` (Task 1); `runtime.get_current()`; `MemoryStore.get_memories`.
- Produces:
  - `runtime.set_current(memory_store, user_id, followups=None, community=None)` + `runtime.get_community()`.
  - `nominate_shared_insight(insight, topic="") -> str` and `search_community_knowledge(query) -> str` tools, appended to `AGRONAUT_TOOLS` (length → **11**).

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_tools.py`:

```python
def test_registry_includes_community_tools():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "nominate_shared_insight" in names
    assert "search_community_knowledge" in names
    assert len(AGRONAUT_TOOLS) == 11


def test_nominate_writes_pending_and_rejects_blank():
    from agronaut_agent.store import _Db, MemoryStore, CommunityStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import nominate_shared_insight

    db = _Db(":memory:")
    mem, cs = MemoryStore(db), CommunityStore(db)
    mem.add_memory("telegram:1", "30% water change fixed my ammonia", "learning")
    runtime.set_current(mem, "telegram:1", None, cs)
    try:
        out = nominate_shared_insight.invoke(
            {"insight": "a partial water change commonly clears an acute ammonia spike",
             "topic": "ammonia"})
        assert "nominated" in out.lower()
        pend = cs.pending()
        assert len(pend) == 1
        assert pend[0]["insight"].startswith("a partial water change")
        assert pend[0]["original"] == "30% water change fixed my ammonia"   # review context
        # blank is rejected
        assert "nothing" in nominate_shared_insight.invoke({"insight": "  ", "topic": "x"}).lower()
    finally:
        runtime.clear_current()


def test_search_community_knowledge_labels_and_filters():
    from agronaut_agent.store import _Db, MemoryStore, CommunityStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import search_community_knowledge

    db = _Db(":memory:")
    cs = CommunityStore(db)
    cs.nominate("telegram:9", "raw", "aerate at dawn to avoid DO crashes", "dissolved oxygen")
    cs.approve(cs.pending()[0]["id"])
    runtime.set_current(MemoryStore(db), "telegram:2", None, cs)
    try:
        out = search_community_knowledge.invoke({"query": "dissolved oxygen"})
        assert "other operators" in out.lower()          # peer-reported label
        assert "aerate at dawn" in out
        # no match -> clean fallback
        assert "no community" in search_community_knowledge.invoke({"query": "zzzzz"}).lower()
    finally:
        runtime.clear_current()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -k "community or nominate" -v`
Expected: FAIL — `ImportError: cannot import name 'nominate_shared_insight'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/runtime.py`, add the community context var and accessor. Add the var after `_followups`:

```python
_community = contextvars.ContextVar("agronaut_community", default=None)
```

Update `set_current` and `clear_current` and add `get_community`:

```python
def set_current(memory_store, user_id: str, followups=None, community=None) -> None:
    _current.set((memory_store, user_id))
    _followups.set(followups)
    _community.set(community)


def clear_current() -> None:
    _current.set(None)
    _followups.set(None)
    _community.set(None)
```

```python
def get_community():
    """Return the CommunityStore for the in-flight message, or None if unset."""
    return _community.get()
```

In `agronaut_agent/tools.py`, add the two tools (place them after `schedule_followup`, before the `AGRONAUT_TOOLS` list):

```python
@tool
def nominate_shared_insight(insight: str, topic: str = "") -> str:
    """Nominate a GENERALIZED, PII-STRIPPED lesson for the shared community knowledge pool so it
    can help OTHER operators — after the owner approves it. Call this when a learning you just
    recorded would help operators in general, not one person's specific system. Write `insight`
    as a single general sentence with NO personal or identifying details (no location, names, or
    specific tank IDs): e.g. "a partial (~30%) water change commonly clears an acute ammonia
    spike". `topic` is a short tag like "ammonia" or "dissolved oxygen". The owner reviews and
    approves before anything is ever shared."""
    cur = runtime.get_current()
    cs = runtime.get_community()
    if cur is None or cs is None:
        return "Can't nominate a shared insight right now."
    mem, user_id = cur
    text = (insight or "").strip()
    if not text:
        return "Nothing to nominate."
    if len(text) > 500:
        return "That insight is too long to share — summarize it in one sentence."
    learnings = [m["content"] for m in mem.get_memories(user_id) if m["category"] == "learning"]
    original = learnings[-1] if learnings else ""
    ok = cs.nominate(user_id, original, text, topic or "")
    return ("Thanks — I've nominated that for the shared knowledge base (pending the owner's "
            "review)." if ok else "That insight is already in the shared queue.")


@tool
def search_community_knowledge(query: str) -> str:
    """Search practical insights other operators contributed and the owner approved. Use during
    troubleshooting for real-world tips. These are COMMUNITY EXPERIENCE, not verified science —
    always present them as "reported by other operators", never as fact or coefficients, and
    never for sizing numbers."""
    cs = runtime.get_community()
    if cs is None:
        return "Community knowledge unavailable right now."
    hits = cs.search_approved(query)
    if not hits:
        return "No community insights yet for that — answer from your own knowledge."
    lines = "\n".join(
        f"- {h['insight']}" + (f" ({h['topic']})" if h.get("topic") else "") for h in hits
    )
    return "Reported by other operators (community experience, not verified science):\n" + lines
```

Append both to `AGRONAUT_TOOLS`:

```python
    schedule_followup,
    nominate_shared_insight,
    search_community_knowledge,
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -v`
Expected: PASS (all, registry count now 11)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/runtime.py agronaut_agent/tools.py agronaut_agent/tests/test_tools.py
git commit -m "feat(tools): nominate_shared_insight + search_community_knowledge + runtime context"
```

---

### Task 3: Agent wiring + system prompt

**Files:**
- Modify: `agronaut_agent/core.py`
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `CommunityStore` (Task 1); `runtime.set_current(..., community=...)` (Task 2).
- Produces: `self._community: CommunityStore`; `handle_message` passes it into `runtime.set_current`; `SYSTEM_PROMPT` gains a nomination bullet and a community-knowledge troubleshooting instruction.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
class _NominateFake:
    """Turn 1 -> nominate a shared insight; then -> final text."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Shared for review.")
        return AIMessage(content="", tool_calls=[{
            "name": "nominate_shared_insight", "id": "n1",
            "args": {"insight": "a partial water change commonly clears an acute ammonia spike",
                     "topic": "ammonia"}}])


def test_nomination_reaches_the_community_store(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_NominateFake())
    agent.handle_message("telegram", "1", "the 30% water change fixed my ammonia")
    pend = agent._community.pending()
    assert len(pend) == 1
    assert pend[0]["insight"].startswith("a partial water change")


def test_system_prompt_mentions_community_sharing():
    from agronaut_agent.core import SYSTEM_PROMPT
    low = SYSTEM_PROMPT.lower()
    assert "nominate_shared_insight" in low
    assert "search_community_knowledge" in low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py -k "nomination or community" -v`
Expected: FAIL — `AttributeError: 'AgronautAgent' object has no attribute '_community'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py`:

Change the store import to add `CommunityStore`:

```python
from .store import _Db, ConversationStore, MemoryStore, FollowupStore, CommunityStore, _now
```

In `__init__`, next to `self._followups`:

```python
        self._followups = FollowupStore(db)
        self._community = CommunityStore(db)
```

In `handle_message`, pass the community store into runtime (extend the existing `set_current` call):

```python
        runtime.set_current(self._mem, user_id, self._followups, self._community)
```

In `SYSTEM_PROMPT`, add a nomination bullet right after the Phase-3 learning bullet (the line ending `so it improves your future advice.`):

```
- If a learning you saved would help other operators in general (not tied to one person's
  system), also call nominate_shared_insight with a generalized, PII-stripped one-sentence
  version — no locations, names, or personal details. The owner approves before anything is shared.
```

And extend the qualitative-troubleshooting HARD RULE (the line `you are reasoning from general knowledge.`) by appending:

```
  Also check search_community_knowledge for real-world operator tips, and present anything it
  returns as "reported by other operators", never as verified fact or a number.
```

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all — existing tests unaffected; the 4th `set_current` arg is optional)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): wire CommunityStore + nomination/community prompt instructions"
```

---

### Task 4: Local review CLI

**Files:**
- Create: `agronaut_agent/review.py`
- Test: `agronaut_agent/tests/test_review.py`

**Interfaces:**
- Consumes: `CommunityStore` (Task 1), `_Db`.
- Produces:
  - `format_candidate(c: dict) -> str` — renders one pending candidate (id, topic, source, original, the SHARE-AS insight).
  - `apply_command(store, cmd: str) -> str` — parse+apply one review command over a `CommunityStore`; returns a user-facing message (`"__quit__"` sentinel to exit). Pure over the store; the interactive `main()` is not unit-tested.

- [ ] **Step 1: Write the failing test**

Create `agronaut_agent/tests/test_review.py`:

```python
"""The community-insight review CLI — the local, off-chat approval gate."""

from agronaut_agent.store import _Db, CommunityStore
from agronaut_agent.review import apply_command, format_candidate


def _store_with_one():
    cs = CommunityStore(_Db(":memory:"))
    cs.nominate("telegram:1", "private ctx", "aerate at dawn to avoid DO crashes", "do")
    return cs


def test_apply_command_approves_pending():
    cs = _store_with_one()
    cid = cs.pending()[0]["id"]
    msg = apply_command(cs, f"approve {cid}")
    assert "approv" in msg.lower()
    assert cs.pending() == [] and len(cs.search_approved("aerate")) == 1


def test_apply_command_rejects_pending():
    cs = _store_with_one()
    cid = cs.pending()[0]["id"]
    apply_command(cs, f"reject {cid}")
    assert cs.pending() == [] and cs.search_approved("aerate") == []


def test_apply_command_unknown_id_and_quit_and_help():
    cs = _store_with_one()
    assert "no pending candidate" in apply_command(cs, "approve 999").lower()
    assert apply_command(cs, "quit") == "__quit__"
    assert "commands" in apply_command(cs, "wat").lower()


def test_format_candidate_shows_insight_and_review_context():
    cs = _store_with_one()
    text = format_candidate(cs.pending()[0])
    assert "aerate at dawn" in text            # the SHARE-AS insight
    assert "private ctx" in text               # original context, for the owner's eyes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_review.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agronaut_agent.review'`

- [ ] **Step 3: Write minimal implementation**

Create `agronaut_agent/review.py`:

```python
"""Local admin CLI to review community-insight nominations — the ONLY place the privileged
"promote to everyone" action happens. Off the chat surface: no Telegram message can trigger it.

Run:  python -m agronaut_agent.review
"""

from __future__ import annotations

from .store import _Db, CommunityStore


def format_candidate(c: dict) -> str:
    """Render one pending candidate for the owner's review (shows the private context)."""
    return (
        f"[{c['id']}] topic: {c.get('topic') or '—'}\n"
        f"    from:     {c['source_user_id']}\n"
        f"    original: {c.get('original') or '—'}\n"
        f"    SHARE AS: {c['insight']}"
    )


def apply_command(store: CommunityStore, cmd: str) -> str:
    """Parse and apply one review command over `store`. Returns a message; '__quit__' to exit."""
    parts = (cmd or "").strip().split()
    if not parts:
        return ""
    verb = parts[0].lower()
    if verb in ("quit", "exit", "q"):
        return "__quit__"
    if verb in ("approve", "reject") and len(parts) == 2 and parts[1].isdigit():
        cid = int(parts[1])
        if cid not in {c["id"] for c in store.pending()}:
            return f"No pending candidate with id {cid}."
        (store.approve if verb == "approve" else store.reject)(cid)
        return f"{verb.capitalize()}d #{cid}."
    return "Commands: approve <id> | reject <id> | quit"


def main() -> None:  # pragma: no cover (interactive loop)
    import agent  # loads .env (AGRONAUT_DB path)
    store = CommunityStore(_Db())
    print("Community insight review — approve <id> / reject <id> / quit")
    while True:
        pending = store.pending()
        if not pending:
            print("No pending candidates. Done.")
            return
        print(f"\n{len(pending)} pending:\n")
        for c in pending:
            print(format_candidate(c) + "\n")
        try:
            cmd = input("review> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        msg = apply_command(store, cmd)
        if msg == "__quit__":
            return
        if msg:
            print(msg)


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_review.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/review.py agronaut_agent/tests/test_review.py
git commit -m "feat(review): local CLI approval gate for community insights"
```

---

### Task 5: Live smoke test + README

**Files:**
- Modify: `README.md`
- No test file (live verification + docs).

**Interfaces:**
- Consumes: the whole feature; a configured NVIDIA provider (already in `.env`).

- [ ] **Step 1: Drive the pipeline end-to-end (nominate → approve → surface)**

Run:

```bash
PYTHONPATH=/home/rekin226/Desktop/code_space/Agronaut .venv/bin/python -c "
import agent
from agronaut_agent.core import AgronautAgent
from agronaut_agent.review import apply_command
a = AgronautAgent(db_path='/tmp/community_smoke.sqlite3')
# seed a per-user learning + nominate directly through the store (skip the LLM's judgement)
a._mem.add_memory('telegram:1','30% water change fixed my ammonia in my 3000L IBC','learning')
a._community.nominate('telegram:1','30% water change fixed my ammonia in my 3000L IBC',
                      'a partial (~30%) water change commonly clears an acute ammonia spike','ammonia')
print('PENDING:', [p['insight'] for p in a._community.pending()])
cid = a._community.pending()[0]['id']
print('REVIEW:', apply_command(a._community, f'approve {cid}'))
print('SEARCH (other user sees):', a._community.search_approved('ammonia'))
"
```
Expected: the insight is pending; approve moves it to approved; `search_approved` returns it as `{insight, topic}` only (no `source_user_id`/`original`, no "3000L IBC").

- [ ] **Step 2: Add the README note**

Under the "### Consultative agent" subsection in `README.md`, append:

```markdown
Lessons can also become shared knowledge: a generalized, PII-stripped version of a verified
fix is nominated, the owner approves it in a local review CLI (`python -m agronaut_agent.review`),
and approved insights then help other operators — labeled as community experience, never as
verified science.
```

- [ ] **Step 3: Run the full suite one last time**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the community-learning pipeline"
```

---

## Notes for the implementer

- **Run tests with the venv:** always `.venv/bin/python -m pytest ...`.
- **`:memory:` SQLite** is fine for store/tool/review tests; core tests use `tmp_path`.
- **All stores share one `_Db`** — the agent builds `db = _Db(db_path)` once and passes it to `ConversationStore`, `MemoryStore`, `FollowupStore`, and `CommunityStore`.
- **Privacy is structural:** `search_approved` selects only `insight, topic`. Never widen that SELECT.
- **Approval is CLI-only** — do not add any Telegram command or chat path that calls `approve`/`reject`.
- **Trust boundary unchanged** — community insights are qualitative text; they never reach `aqua_model`/`validate_design_input`.

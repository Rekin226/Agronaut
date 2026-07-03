# Outcome Loop (Self-Learning Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agronaut learn what actually worked — the LLM schedules proactive follow-ups ("did the water change fix the ammonia?"), a poller delivers them, and outcomes (proactive or user-volunteered) are saved as `learning` memories that feed future advice.

**Architecture:** A new `followups` table + `FollowupStore` holds scheduled check-ins. A `schedule_followup` tool lets the LLM arm one. The agent owns the store and exposes a channel-agnostic delivery API; a plain-asyncio poller in the Telegram adapter delivers due follow-ups and survives restarts. Outcomes are captured on the user's reply and stored as `learning` memories (existing category), so they flow into advice through the Phase-2 recall unchanged.

**Tech Stack:** Python 3.12, SQLite (`store.py`), LangChain `@tool`, python-telegram-bot v21 (`Application.post_init`, `bot.send_message`), asyncio, pytest. No new dependencies.

## Global Constraints

- Goals are exactly `design`, `optimize`, `troubleshoot` (`profile.GOALS`).
- **One open follow-up per user** — `FollowupStore.schedule` refuses a new one while a `pending` OR `sent` follow-up exists for that user.
- **No nagging** — once `sent`, a follow-up is never resent.
- **Delay clamped to 1–336 hours** (14 days); the tool rejects out-of-range.
- Outcomes are stored as `learning` memories (existing `memories` category) — NOT a new memory type.
- **Agent schedules; adapter delivers.** The adapter never touches `FollowupStore` directly — it calls the agent's delivery API (`due_followups`/`mark_followup_sent`/`followup_send_failed`).
- The poller is a **plain asyncio task** — no PTB JobQueue extra, no new dependency.
- All stores share one `_Db` (one SQLite file); timestamps are UTC ISO via `store._now()`.
- Deterministic tests only — no live LLM, no real Telegram. The poller's *selection* logic is tested in the store; the Telegram send is not unit-tested.
- Work on branch `feat/outcome-loop` (already checked out). Commit after every task.

---

### Task 1: `followups` table + `FollowupStore`

**Files:**
- Modify: `agronaut_agent/store.py` (add table to `_SCHEMA`; add `FollowupStore` class)
- Test: `agronaut_agent/tests/test_store.py`

**Interfaces:**
- Consumes: `_Db`, `_now` (already in `store.py`).
- Produces: `FollowupStore(db)` with:
  - `schedule(user_id, channel, channel_user, question, about, due_at) -> bool` — insert `pending`; returns `False` (no insert) if a `pending`/`sent` follow-up already exists for `user_id`.
  - `due(channel, now) -> list[dict]` — `pending` rows with `due_at <= now` for `channel`, oldest first.
  - `mark_sent(id)` / `bump_attempt(id) -> int` / `mark_failed(id)` / `mark_answered(id)` / `cancel(id)` / `record_outcome(id, outcome)`.
  - `open_for(user_id) -> dict | None` — the user's latest `pending`/`sent` follow-up.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_store.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_store.py -k "followup or due or schedule or bump or answer" -v`
Expected: FAIL — `ImportError: cannot import name 'FollowupStore'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/store.py`, add the table to `_SCHEMA` (immediately before the closing `"""` at line 65, after the `session_summary` block):

```python
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
```

Then add the class at the end of `store.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_store.py -v`
Expected: PASS (all, including the 5 new followup tests)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/store.py agronaut_agent/tests/test_store.py
git commit -m "feat(store): followups table + FollowupStore (one-open-per-user, no nagging)"
```

---

### Task 2: `runtime` follow-up context + `schedule_followup` tool

**Files:**
- Modify: `agronaut_agent/runtime.py`
- Modify: `agronaut_agent/tools.py`
- Test: `agronaut_agent/tests/test_tools.py`

**Interfaces:**
- Consumes: `FollowupStore` (Task 1); `runtime.get_current()`.
- Produces:
  - `runtime.set_current(memory_store, user_id, followups=None)` (optional 3rd arg) + `runtime.get_followups()`.
  - `schedule_followup(question: str, hours: float, about: str = "") -> str` tool, appended to `AGRONAUT_TOOLS` (length → **9**). Validates `hours` ∈ [1, 336]; derives `channel`/`channel_user` from `user_id`; schedules via `runtime.get_followups()`.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_tools.py`:

```python
def test_registry_includes_schedule_followup():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "schedule_followup" in names
    assert len(AGRONAUT_TOOLS) == 9


def test_schedule_followup_writes_a_row_and_guards_duplicates():
    from agronaut_agent.store import _Db, MemoryStore, FollowupStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import schedule_followup

    db = _Db(":memory:")
    mem, fs = MemoryStore(db), FollowupStore(db)
    runtime.set_current(mem, "telegram:7", fs)
    try:
        out = schedule_followup.invoke({"question": "did the water change help?",
                                        "hours": 24, "about": "ammonia spike"})
        assert "check back" in out.lower()
        assert fs.open_for("telegram:7")["question"] == "did the water change help?"
        # second while one is open is refused
        again = schedule_followup.invoke({"question": "still ok?", "hours": 24, "about": "x"})
        assert "pending" in again.lower()
    finally:
        runtime.clear_current()


def test_schedule_followup_rejects_out_of_range_hours():
    from agronaut_agent.store import _Db, MemoryStore, FollowupStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import schedule_followup

    db = _Db(":memory:")
    runtime.set_current(MemoryStore(db), "telegram:8", FollowupStore(db))
    try:
        assert "between" in schedule_followup.invoke(
            {"question": "q", "hours": 0.5, "about": "x"}).lower()
        assert "between" in schedule_followup.invoke(
            {"question": "q", "hours": 999, "about": "x"}).lower()
    finally:
        runtime.clear_current()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -k "schedule_followup or registry" -v`
Expected: FAIL — `ImportError: cannot import name 'schedule_followup'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/runtime.py`, replace the body with the followups-aware version:

```python
_current = contextvars.ContextVar("agronaut_current", default=None)
_followups = contextvars.ContextVar("agronaut_followups", default=None)


def set_current(memory_store, user_id: str, followups=None) -> None:
    _current.set((memory_store, user_id))
    _followups.set(followups)


def clear_current() -> None:
    _current.set(None)
    _followups.set(None)


def get_current():
    """Return (memory_store, user_id) for the in-flight message, or None outside a turn."""
    return _current.get()


def get_followups():
    """Return the FollowupStore for the in-flight message, or None if unset."""
    return _followups.get()
```

(Keep the module docstring and `import contextvars` at the top.)

In `agronaut_agent/tools.py`, add the tool next to `remember_about_user`:

```python
@tool
def schedule_followup(question: str, hours: float, about: str = "") -> str:
    """Schedule a proactive check-in with the user to learn whether your advice worked.
    Use ONLY after giving an actionable fix (e.g. a water change, a pH adjustment) — not for
    plans or trivia. `question` is what you'll ask them later (e.g. "did the 30% water change
    bring the ammonia down?"). `hours` is when to check back — pick it to match the fix (a
    water change ~24h, cycling ~a week); must be between 1 and 336 (14 days). `about` is a
    short label of the issue. Only one check-in can be pending per user."""
    cur = runtime.get_current()
    fs = runtime.get_followups()
    if cur is None or fs is None:
        return "Can't schedule a follow-up right now."
    _mem, user_id = cur
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "Follow-up delay must be a number of hours between 1 and 336."
    if not (1.0 <= h <= 336.0):
        return "Follow-up delay must be between 1 hour and 14 days (336 hours)."
    from datetime import datetime, timedelta, timezone
    channel, _, channel_user = user_id.partition(":")
    due_at = (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()
    ok = fs.schedule(user_id, channel, channel_user, question, about or "", due_at)
    return ("Got it — I'll check back on that." if ok
            else "I already have a check-in pending with you; I'll follow up on that first.")
```

Append `schedule_followup` to the `AGRONAUT_TOOLS` list.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -v`
Expected: PASS (all, registry count now 9)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/runtime.py agronaut_agent/tools.py agronaut_agent/tests/test_tools.py
git commit -m "feat(tools): schedule_followup + runtime follow-up context"
```

---

### Task 3: Agent constructs the store + delivery API

**Files:**
- Modify: `agronaut_agent/core.py`
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `FollowupStore` (Task 1); `runtime.set_current(mem, uid, followups)` (Task 2); `store._now`.
- Produces on `AgronautAgent`:
  - `self._followups: FollowupStore` (over the same `_Db`).
  - `due_followups(channel) -> list[dict]`, `mark_followup_sent(fid)`, `followup_send_failed(fid)` (bumps attempt; marks `failed` at 3).
  - `handle_message` now passes `self._followups` into `runtime.set_current`.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
def test_agent_exposes_followup_delivery_api(tmp_path):
    from agronaut_agent.store import _now
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    # schedule a past-due follow-up directly via the agent's store
    agent._followups.schedule("telegram:5", "telegram", "5", "did it work?", "x",
                              "2000-01-01T00:00:00+00:00")
    due = agent.due_followups("telegram")
    assert len(due) == 1 and due[0]["question"] == "did it work?"
    fid = due[0]["id"]
    agent.mark_followup_sent(fid)
    assert agent.due_followups("telegram") == []          # sent -> not due again

    # send-failure path: 3 strikes -> failed
    agent._followups.schedule("telegram:6", "telegram", "6", "q", "x",
                              "2000-01-01T00:00:00+00:00")
    fid2 = agent.due_followups("telegram")[0]["id"]
    agent.followup_send_failed(fid2)
    agent.followup_send_failed(fid2)
    agent.followup_send_failed(fid2)
    assert agent._followups.open_for("telegram:6") is None  # failed after 3 attempts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py::test_agent_exposes_followup_delivery_api -v`
Expected: FAIL — `AttributeError: 'AgronautAgent' object has no attribute '_followups'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py`, extend the imports and `__init__`, and add the delivery API.

Change the store import line (currently `from .store import _Db, ConversationStore, MemoryStore`):

```python
from .store import _Db, ConversationStore, MemoryStore, FollowupStore, _now
```

In `__init__`, where `self._conv` and `self._mem` are built, add the follow-up store (they share `db`):

```python
        self._conv = ConversationStore(db)
        self._mem = MemoryStore(db)
        self._followups = FollowupStore(db)
```

In `handle_message`, pass the follow-up store into runtime (change the `set_current` line):

```python
        runtime.set_current(self._mem, user_id, self._followups)  # tools reach this user
```

Add the delivery API methods (place them after `forget_everything`, before `set_goal`):

```python
    # --- follow-up delivery API (called by a channel poller) --------------
    def due_followups(self, channel: str) -> list:
        """Follow-ups due for delivery on `channel` right now."""
        return self._followups.due(channel, _now())

    def mark_followup_sent(self, followup_id: int) -> None:
        self._followups.mark_sent(followup_id)

    def followup_send_failed(self, followup_id: int) -> None:
        """A delivery attempt failed; retry next tick, but give up after 3."""
        if self._followups.bump_attempt(followup_id) >= 3:
            self._followups.mark_failed(followup_id)
```

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all — existing tests unaffected; `set_current` still works, extra arg is optional)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): construct FollowupStore + channel-agnostic delivery API"
```

---

### Task 4: Outcome capture, cancel-on-reply, and prompt

**Files:**
- Modify: `agronaut_agent/core.py` (`handle_message` and `SYSTEM_PROMPT`)
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `self._followups.open_for/record_outcome/mark_answered/cancel` (Task 1); `SystemMessage` (already imported).
- Produces: `handle_message` — before building context, if the user has an open follow-up: a `sent` one is marked `answered` and a capture note is injected into this turn; a `pending` one (not yet delivered) is `cancelled`. `SYSTEM_PROMPT` gains proactive+passive learning instructions.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
class _LearningFake:
    """Turn 1 -> save a learning memory; then -> final text. Mimics the model capturing
    an outcome when it sees the capture note."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Glad it worked!")
        return AIMessage(content="", tool_calls=[{
            "name": "remember_about_user", "id": "c1",
            "args": {"note": "30% water change fixed the ammonia spike", "category": "learning"}}])


def test_sent_followup_is_answered_on_next_reply(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_LearningFake())
    # a follow-up was delivered and is awaiting the user's answer
    agent._followups.schedule("telegram:9", "telegram", "9", "did it work?", "ammonia",
                              "2000-01-01T00:00:00+00:00")
    agent._followups.mark_sent(agent._followups.open_for("telegram:9")["id"])
    agent.handle_message("telegram", "9", "yes it worked great")
    assert agent._followups.open_for("telegram:9") is None        # answered -> slot freed
    assert agent._mem.memory_count("telegram:9") == 1             # outcome saved as learning


def test_pending_followup_cancelled_when_user_messages_first(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    agent._followups.schedule("telegram:10", "telegram", "10", "did it work?", "x",
                              "2999-01-01T00:00:00+00:00")  # not yet due/sent
    agent.handle_message("telegram", "10", "hey, new question")
    assert agent._followups.open_for("telegram:10") is None       # cancelled, not asked


def test_system_prompt_mentions_followups_and_outcomes():
    from agronaut_agent.core import SYSTEM_PROMPT
    low = SYSTEM_PROMPT.lower()
    assert "schedule_followup" in low
    assert "worked" in low  # capture outcomes as learnings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py -k "followup or outcomes" -v`
Expected: FAIL — `test_sent_followup_is_answered_on_next_reply` (the follow-up stays `sent`) and `test_system_prompt_mentions_followups_and_outcomes` (`schedule_followup` absent).

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py` `handle_message`, insert the capture/cancel logic after `append_message(user_id, "user", text)` and before `runtime.set_current(...)`, and inject the note into the context:

```python
    def handle_message(self, channel: str, channel_user: str, text: str, display_name: str | None = None) -> str:
        user_id = self._conv.get_or_create_user(channel, channel_user, display_name)
        self._mem.set_facts(user_id, memory_extract.extract_facts(text), source="parsed")
        self._conv.append_message(user_id, "user", text)

        # Outcome loop: a delivered follow-up is being answered now; a not-yet-sent one is
        # superseded by the user messaging first.
        capture_note = None
        open_fu = self._followups.open_for(user_id)
        if open_fu and open_fu["status"] == "sent":
            self._followups.record_outcome(open_fu["id"], text)  # audit: what they answered
            self._followups.mark_answered(open_fu["id"])
            capture_note = (
                f'You earlier asked this user: "{open_fu["question"]}". They are replying now. '
                f"If they report whether it worked, save the result with "
                f"remember_about_user(category='learning')."
            )
        elif open_fu and open_fu["status"] == "pending":
            self._followups.cancel(open_fu["id"])

        runtime.set_current(self._mem, user_id, self._followups)  # tools reach this user
        try:
            messages = self._build_context(user_id)
            if capture_note:
                messages.append(SystemMessage(content=capture_note))
            reply = self._run_tool_loop(messages, user_id)
        finally:
            runtime.clear_current()
        self._conv.append_message(user_id, "assistant", reply)
        self._schedule_summary(user_id)
        return reply
```

In `SYSTEM_PROMPT`, extend the "REMEMBER AS YOU GO:" block — add two bullets right after the `remember_about_user (category event / learning / preference). Honour "forget that".` line:

```
- After you give an ACTIONABLE fix (a water change, a pH/temperature adjustment, a dosing
  change), call schedule_followup to check back later whether it worked — pick the delay to
  match how long the fix takes to show. Don't schedule for plans, sizing, or trivia.
- When the user reports whether something worked (now or in answer to a check-in), save it
  with remember_about_user(category='learning') so it improves your future advice.
```

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all, including the three new outcome tests)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): capture follow-up outcomes as learnings; cancel-on-reply; prompt"
```

---

### Task 5: Telegram delivery poller

**Files:**
- Modify: `agronaut_agent/channels/telegram_adapter.py`
- Test: `agronaut_agent/tests/test_telegram_adapter.py`

**Interfaces:**
- Consumes: `agent.due_followups(channel)` / `mark_followup_sent(id)` / `followup_send_failed(id)` (Task 3).
- Produces: `TelegramAdapter._followup_loop(app)` (async) and `POLL_SECONDS`; the loop is started from `_post_init` via `asyncio.create_task`.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_telegram_adapter.py`:

```python
def test_adapter_has_followup_poller():
    a = _adapter()
    assert hasattr(a, "_followup_loop")
    import inspect
    assert inspect.iscoroutinefunction(a._followup_loop)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_telegram_adapter.py -k followup -v`
Expected: FAIL — `AssertionError` (no `_followup_loop`)

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/channels/telegram_adapter.py`, add `import asyncio` if not present (it is). Add a module constant near the top (after `log = ...`):

```python
POLL_SECONDS = 60
```

Add the poller method (place it before `_command_specs`):

```python
    async def _followup_loop(self, app: Application) -> None:
        """Deliver due outcome follow-ups. Runs for the app's lifetime; best-effort — a
        failed poll or send never affects live message handling."""
        while True:
            try:
                due = await asyncio.to_thread(self.agent.due_followups, self.channel_name)
                for fu in due:
                    try:
                        await app.bot.send_message(chat_id=int(fu["channel_user"]),
                                                   text=fu["question"])
                        await asyncio.to_thread(self.agent.mark_followup_sent, fu["id"])
                    except Exception:
                        log.warning("follow-up send failed for %s", fu["id"], exc_info=True)
                        await asyncio.to_thread(self.agent.followup_send_failed, fu["id"])
            except Exception:  # never let the poller die
                log.debug("follow-up poll failed", exc_info=True)
            await asyncio.sleep(POLL_SECONDS)
```

In `_post_init`, start the poller after registering commands:

```python
    async def _post_init(self, app: Application) -> None:
        """Register the / command menu and start the follow-up poller. Non-fatal on failure."""
        commands = [BotCommand(c, desc) for c, _h, desc in self._command_specs()]
        try:
            await app.bot.set_my_commands(commands)
        except Exception:  # transient network etc. — commands still work by typing
            log.warning("set_my_commands failed; commands still work by typing", exc_info=True)
        app.create_task(self._followup_loop(app))
```

(`app.create_task` is PTB's supervised task creator — it keeps a reference so the task isn't garbage-collected and logs exceptions; equivalent to `asyncio.create_task` but lifecycle-managed by the Application.)

- [ ] **Step 4: Run the new test + full suite**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_telegram_adapter.py -v`
Expected: PASS

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all — no regressions)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/channels/telegram_adapter.py agronaut_agent/tests/test_telegram_adapter.py
git commit -m "feat(telegram): background poller delivers due outcome follow-ups"
```

---

### Task 6: Live smoke test + README

**Files:**
- Modify: `README.md`
- No test file (live verification + docs).

**Interfaces:**
- Consumes: the whole feature; a configured NVIDIA provider (already in `.env`).

- [ ] **Step 1: Drive the loop end-to-end through the agent (no waiting for the poller)**

Run:

```bash
PYTHONPATH=/home/rekin226/Desktop/code_space/Agronaut .venv/bin/python -c "
import agent
from agronaut_agent.core import AgronautAgent
from agronaut_agent.store import _now
a = AgronautAgent(db_path='/tmp/followup_smoke.sqlite3')
# simulate a scheduled + delivered follow-up, then the user's answer
a._followups.schedule('telegram:1','telegram','1','did the water change fix the ammonia?','ammonia','2000-01-01T00:00:00+00:00')
due = a.due_followups('telegram'); print('due:', [d['question'] for d in due])
a.mark_followup_sent(due[0]['id'])
print('reply:', a.handle_message('telegram','1','yes the ammonia dropped to 0 after the water change'))
print('followup status:', a._followups.open_for('telegram:1'))  # should be None (answered)
print('learnings:', a._mem.get_memories('telegram:1'))
"
```
Expected: `due` lists the question; after the reply, the follow-up is answered (`None` open) and a `learning` memory about the water change appears.

- [ ] **Step 2: Add the README note**

Under the "### Consultative agent" subsection in `README.md`, append:

```markdown
Agronaut also learns from outcomes: after suggesting a fix it can check back later
("did the water change fix the ammonia?"), and whatever worked is remembered and shapes
its future advice.
```

- [ ] **Step 3: Run the full suite one last time**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the outcome-learning loop"
```

---

## Notes for the implementer

- **Run tests with the venv:** always `.venv/bin/python -m pytest ...`.
- **`:memory:` SQLite** is fine for store/tool tests (one connection); core/agent tests use `tmp_path` files, matching the existing suite.
- **Don't run the bot in tests** — `run_polling()` blocks; the poller is verified structurally + by the live smoke test.
- **All stores share one `_Db`** — the agent builds `db = _Db(db_path)` once and passes it to `ConversationStore`, `MemoryStore`, and `FollowupStore`. Don't create a second `_Db` on the same file.
- **Trust boundary unchanged** — follow-ups never touch `aqua_model`; they only schedule/deliver text and store `learning` memories.

# Agronaut — Outcome Loop / Self-Learning Phase 3 (Design)

**Date:** 2026-07-03
**Status:** Approved for planning
**Scope:** The outcome loop — the bot learns what actually worked, both passively (user
volunteers a result) and proactively (the bot follows up), and feeds it into future advice.

---

## 1. Problem

Agronaut *remembers* a user's system (Phase 1+2) but does not *learn* from what happens
next. When it advises a fix ("do a 30% water change for the ammonia spike"), it never finds
out whether that worked, so its advice can't improve over time. The owner wants it to be
self-learning.

This is Phase 3 of the roadmap and the prerequisite for Phases 4 (cross-user) and 5
(calibration): both need the outcome data this phase collects.

### What already exists (reuse, don't rebuild)

- `memories` table with a `learning` category, agent-curated via `remember_about_user`.
- Recall block (`_recall_block`) already surfaces `learning`/`event` memories first in
  troubleshoot mode (Phase 2) — so **the feedback path into advice already exists**; this
  phase just fills it with real outcome data.
- `runtime.get_current()` gives tools the current `(MemoryStore, user_id)`.
- No scheduler exists (PTB installed without the job-queue extra); no outcome tracking.

---

## 2. The outcome loop

Passive and proactive share the whole back half — only the trigger differs.

```
                 ┌─────────────── PROACTIVE ───────────────┐
troubleshoot advice → LLM calls schedule_followup(question, hours, about)
   │                        ↓
   │              followups row (pending, due_at)
   │                        ↓  (adapter poller, survives restarts)
   │              bot sends "did the 30% water change fix the ammonia?"
   │                        ↓
   └────► user replies ─────┤
              ↑             ↓
        PASSIVE: user       outcome saved as a `learning` memory
        volunteers          ("30% water change fixed the June ammonia spike")
        "it worked!"        ↓
                     surfaces in future advice via existing recall
```

The captured outcome is stored as a `learning` memory (existing category), NOT a new type —
so it automatically flows into future advice through the Phase-2 recall block.

---

## 3. Data model — the `followups` table

Added to `store.py`'s `_SCHEMA`:

| column | purpose |
|--------|---------|
| `id` | PK |
| `user_id` | owner (e.g. `telegram:12345`) |
| `channel`, `channel_user` | how/where to deliver (the poller sends to `channel_user`) |
| `question` | the check-in message to send |
| `about` | short issue label, for the outcome record |
| `due_at` | when to send (UTC ISO) |
| `status` | `pending` → `sent` → `answered`; also `cancelled` / `failed` |
| `attempts` | send-retry guard (give up at 3) |
| `created_at`, `sent_at`, `outcome` | audit + captured result |

---

## 4. Components

### 4.1 `schedule_followup` tool (`tools.py`) — the LLM trigger
- Args: `question: str` (what to ask later), `hours: float` (delay the model picks per fix,
  **clamped/validated to 1–336h = 14 days**), `about: str` (short issue label).
- Reaches the `FollowupStore` via `runtime.get_followups()` and the `user_id` via
  `runtime.get_current()`; derives `channel`/`channel_user` from `user_id`
  (`user_id.split(":", 1)`).
- **Conservative guard lives here:** if a pending follow-up already exists for the user, it
  does NOT add another — returns "already have a check-in pending".
- Registry grows to 9 tools.

### 4.2 `FollowupStore` (`store.py`) — persistence + pure logic
- `schedule(user_id, channel, channel_user, question, about, due_at) -> bool` — insert
  pending; enforces one-pending-per-user (returns False if one already pending).
- `due(channel, now) -> list[dict]` — pending rows with `due_at <= now` for that channel.
- `mark_sent(id)` / `bump_attempt(id) -> int` (returns new count; caller gives up at 3 →
  `mark_failed`) / `mark_failed(id)`.
- `open_for(user_id) -> dict | None` — the user's non-terminal follow-up (for capture/cancel).
- `record_outcome(id, outcome)` + `mark_answered(id)` / `cancel(id)`.

### 4.3 Delivery poller (`telegram_adapter.py`)
- A background asyncio task started at boot (in `post_init`), looping every ~60s:
  fetch due follow-ups → `await bot.send_message(chat_id, question)` → mark sent. On failure:
  bump attempt, retry next tick, give up at 3.
- **The adapter does not touch `FollowupStore` directly** — the agent owns the DB and exposes
  a tiny channel-agnostic delivery API the poller calls: `due_followups(channel) -> list`,
  `mark_followup_sent(id)`, `followup_send_failed(id)` (bumps attempt, marks `failed` at 3).
  This keeps scheduling logic in the agent and only the Telegram send in the adapter.
- Plain asyncio task — **no new dependency** (not PTB's JobQueue extra).
- Store access wrapped in `asyncio.to_thread` (the store is sync). No nagging: once `sent`,
  never resent.

### 4.4 Outcome capture (`core.py handle_message`)
- If the user has a `sent` follow-up open, inject a turn-scoped system note: *"You earlier
  asked '<question>'. The user is replying now; if they report the result, save it with
  remember_about_user(category='learning')."* Mark that follow-up `answered` deterministically
  on this reply; the worked/didn't classification is the LLM's via the learning memory.
- If the user has a `pending` (not-yet-sent) follow-up and messages first, `cancel` it — don't
  ask about something they've already moved past.

### 4.5 System prompt (`core.py`) — two small additions
- Proactive: when giving an actionable fix, call `schedule_followup` to check back.
- Passive: whenever the user reports whether something worked, save it as a `learning`.

---

## 5. Guardrails (conservative — enforced in code, not just the prompt)

- One pending follow-up per user (`FollowupStore.schedule`).
- No nagging — once `sent`, never resent.
- `hours` clamped to 1–336; out-of-range rejected by the tool.
- Follow-ups only when the LLM judges the advice actionable.

---

## 6. Error handling / edge cases

- **Send fails** (network / bad chat): `bump_attempt`; retry next tick; give up at 3
  (`failed`, logged) — no infinite loop.
- **Restart mid-wait:** state is in SQLite; the poller resumes and sends anything now due.
- **User answers before the follow-up fires:** the still-`pending` follow-up is `cancelled`
  on the next user message.
- **Poller vs. live-turn concurrency:** `FollowupStore` uses the store's existing lock
  (WAL + per-connection), same as every other write.
- **Delivery is best-effort:** a failed poll never affects live message handling.

---

## 7. Testing (deterministic — no live LLM, no real Telegram)

- **Store:** `schedule` enforces one-pending; `due()` returns only past-due pending rows for
  the channel; `bump_attempt` → give-up at 3; `record_outcome`; cancel-on-reply.
- **Tool:** `schedule_followup` writes a row via `runtime`; declines when one is pending;
  clamps/rejects bad `hours`; registry length 9.
- **Agent:** with a fake model, a `sent` follow-up is marked `answered` on the next turn and a
  `learning` memory is stored; a `pending` follow-up is `cancelled` when the user messages
  first.
- The async poller stays thin — its selection logic is tested in the store; the Telegram send
  itself is not unit-tested (consistent with the existing suite).

---

## 8. Files touched

| File | Change |
|------|--------|
| `agronaut_agent/store.py` | `followups` table in `_SCHEMA`; `FollowupStore` |
| `agronaut_agent/tools.py` | `schedule_followup` tool (registry → 9) |
| `agronaut_agent/core.py` | construct `FollowupStore`; outcome-capture + cancel wiring in `handle_message`; delivery API (`due_followups`/`mark_followup_sent`/`followup_send_failed`); 2 system-prompt additions |
| `agronaut_agent/channels/telegram_adapter.py` | background poller task + delivery |
| `agronaut_agent/runtime.py` | carry the `FollowupStore` in request context — optional 3rd arg on `set_current`, new `get_followups()` (existing `(mem, user_id)` unpacking unchanged) |
| `agronaut_agent/tests/` | store, tools, core cases above |

---

## 9. Out of scope (YAGNI)

- Cross-user learning (Phase 4) — one operator's outcome improving others' advice.
- Coefficient calibration from reported yields (Phase 5).
- Any nagging / re-engagement beyond a single follow-up.
- Follow-ups on non-troubleshoot advice (design/optimize plans aren't "fixes" to verify).
- LLM-based outcome classification beyond what the `learning` memory naturally captures.

# Agronaut — Cross-User Learning Foundation / Phase 4 (Design)

**Date:** 2026-07-03
**Status:** Approved for planning
**Scope:** A safe, human-gated promotion pipeline that turns a verified per-user learning
into vetted, generalized *shared* knowledge — the foundation for operators learning from
each other. Foundation-first: the machinery ships now; it compounds once operators are added.

---

## 1. Problem

Phase 3 makes Agronaut learn what worked *for one user* (`learning` memories). Phase 4 lets a
verified fix from one operator improve the advice given to *others* — without leaking anyone's
private system details or propagating bad/unverified advice.

This is the roadmap's highest-risk phase (bad data reaching everyone; privacy). The design is
built around a **human review gate** and **generalization before sharing**.

### What already exists (reuse, don't rebuild)

- Per-user `learning` memories (Phase 3), surfaced in troubleshoot recall.
- `runtime.get_current()` / per-request context vars for tools (Phase 3 added a follow-up store
  the same way).
- The RAG `search_knowledge_base` tool over curated docs — community insights sit *alongside*
  it, distinctly labeled.
- Shared `_Db` (WAL + lock); `AGRONAUT_TOOLS` currently has **9** tools.

---

## 2. The promotion pipeline (two gates protect everyone)

```
per-user `learning` memory (Phase 3)
        │  LLM judges it broadly useful AND writes a generalized, PII-stripped version
        ▼
nominate_shared_insight(insight, topic)  ──►  community_insights row (status: pending)
        │
        ▼   [ LOCAL CLI review: you see the original context + the proposed generalized text ]
   approve ──► status: approved        reject ──► status: rejected (private learning untouched)
        │
        ▼
approved insights surfaced to OTHER users during troubleshooting via search_community_knowledge,
labeled "reported by other operators" — never cited as fact, never fed to aqua_model
```

**Gate 1 — LLM generalizes at nomination.** It writes *"a partial (~30%) water change commonly
clears an acute ammonia spike"*, never *"my 3000 L IBC in Burkina."* PII stripping happens
before anything is queued.

**Gate 2 — human review.** You approve the exact generalized wording in a local CLI before it
can ever reach another user. `source_user_id` and `original` are for your review only.

---

## 3. Data model — the `community_insights` table

Added to `store.py`'s `_SCHEMA`:

| column | purpose |
|--------|---------|
| `id` | PK |
| `source_user_id` | who it came from — **never exposed to other users** |
| `original` | raw learning + context — **for your review only** |
| `insight` | the generalized, PII-stripped text that may be shared |
| `topic` | short tag (e.g. "ammonia", "DO") for retrieval |
| `status` | `pending` → `approved` / `rejected` |
| `created_at`, `reviewed_at` | audit |

One table: `pending` = review queue; `approved` = live shared knowledge; `rejected` = archived.
Only `insight` (+ `topic`) is ever returned to other users.

---

## 4. Components

### 4.1 `nominate_shared_insight` tool (`tools.py`)
- Args: `insight: str` (the **generalized, PII-stripped** text the model writes), `topic: str`.
- Reaches `source_user_id` + the memory store via `runtime.get_current()` and the
  `CommunityStore` via a new `runtime.get_community()`. `original` is derived deterministically
  as the user's most recent `learning` memory (via the memory store) for your review context —
  the model does not have to re-supply it; `""` if none exists.
- **Dedup guard:** skip if a normalized-equal `insight` is already `pending`/`approved`.
- Rejects blank or absurdly long insight text.

### 4.2 `CommunityStore` (`store.py`)
- `nominate(source_user_id, original, insight, topic) -> bool` (dedup → False on duplicate).
- `pending() -> list[dict]` (review queue), `approve(id)`, `reject(id)`.
- `search_approved(query) -> list[dict]` — `LIKE` match over `status='approved'` insight/topic
  (no embeddings at this scale; dynamic, no index rebuild).

### 4.3 Review CLI (`agronaut_agent/review.py`, run `python -m agronaut_agent.review`)
- Local-only human gate. Lists each pending candidate: source, `original` context, proposed
  `insight`. You type `approve <id>` / `reject <id>` / `quit`.
- The privileged "promote to everyone" action lives here — **off the chat surface**; no Telegram
  message can trigger it.
- Pure `list/approve/reject` logic is testable; the input loop stays thin.

### 4.4 `search_community_knowledge` tool (`tools.py`)
- During troubleshooting the LLM calls it; returns approved insights **clearly labeled**:
  *"Reported by other operators (community experience, not verified science)."*
- Returns a clean "no community insights yet" when empty (agent falls back to its own knowledge).

`AGRONAUT_TOOLS` grows from 9 to **11** (nominate + search).

### 4.5 System prompt (`core.py`) — two additions
- When you record a broadly-useful learning, also nominate a generalized version.
- When troubleshooting, consult community knowledge and **caveat it as peer-reported, never as
  fact or coefficients**.

### 4.6 Wiring (`core.py`, `runtime.py`)
- The agent constructs `CommunityStore(db)` (shared `_Db`) and passes it into `runtime.set_current`
  alongside the memory + follow-up stores; `runtime.get_community()` exposes it to the tools.

---

## 5. Privacy & trust guardrails (structural, not just prompted)

- `source_user_id` and `original` are never returned by `search_approved` — only `insight`/`topic`.
- The tool stores the LLM's already-generalized `insight`; the raw learning is never shared.
- Community insights always carry the peer-reported caveat on surfacing; the prompt forbids
  presenting them as fact/coefficients.
- Trust gate untouched — community insights are qualitative and never reach `aqua_model`/
  `validate_design_input`.

---

## 6. Error handling / edge cases

- **Dedup:** `nominate` skips a normalized-equal pending/approved insight → no queue spam.
- **Blank/oversized insight:** the tool rejects it with a clear message.
- **Nothing to review:** the CLI prints "no pending candidates" and exits cleanly.
- **Bad/at-rest id at review:** `approve`/`reject` on an unknown or already-reviewed id → a clear
  message, no crash.
- **No approved insights:** `search_community_knowledge` returns a clean empty note.
- **Concurrency:** `CommunityStore` uses the shared `_Db` lock like every other store.

---

## 7. Testing (deterministic — no live LLM)

- **Store:** `nominate` dedups; `pending` lists only pending; `approve`/`reject` transitions;
  `search_approved` returns only approved matches (empty for a non-match); never returns
  `source_user_id`/`original`.
- **Tools:** `nominate_shared_insight` writes a pending row via `runtime` (rejects blank);
  `search_community_knowledge` returns approved matches with the caveat label; registry == 11.
- **Review CLI:** the pure `list/approve/reject` functions tested directly; the input loop not
  unit-tested.
- **Agent:** with a fake model, a nomination reaches the store; a private learning is never
  auto-shared.

---

## 8. Files touched

| File | Change |
|------|--------|
| `agronaut_agent/store.py` | `community_insights` table + `CommunityStore` |
| `agronaut_agent/tools.py` | `nominate_shared_insight` + `search_community_knowledge` (→ 11) |
| `agronaut_agent/core.py` | construct `CommunityStore`; pass into `runtime.set_current`; 2 prompt additions |
| `agronaut_agent/runtime.py` | carry `CommunityStore` in request context + `get_community()` |
| `agronaut_agent/review.py` *(new)* | local admin review CLI |
| `agronaut_agent/tests/` | store, tools, review, agent cases above |

---

## 9. Out of scope (YAGNI)

- Corroboration thresholds (multi-contributor counting) — not needed for a single-owner gate.
- Embedding-based retrieval for community search (keyword `LIKE` suffices at this scale).
- Auto-approval; editing insights during review (reject keeps it a private learning).
- Any Telegram-side promotion/approval control (privileged action stays in the local CLI).
- Phase 5 coefficient calibration.

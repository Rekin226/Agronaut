# Agronaut — Consultative Core + Memory Upgrade (Design)

**Date:** 2026-06-26
**Status:** Approved for planning
**Scope:** Phase 1 (Consultative core) + Phase 2 (Memory & recall), built together.

---

## 1. Problem

Agronaut's Telegram agent works but feels shallow. Four concrete complaints, all
confirmed by the owner:

1. **Answers too fast** — a vague user message produces an answer/numbers instead of
   first understanding the situation.
2. **No goal discovery** — it never establishes *why* the user is here (start a system?
   fix dying fish? maximize protein?), so advice isn't tailored.
3. **Weak recommendations** — even with context, advice is generic.
4. **Forgets / shallow recall** — cross-session memory feels thin and inconsistent.

Root cause: the agent treats every message as an isolated Q&A. It has no notion of an
ongoing **consultation** with a *goal* and a *system state*. Recall today dumps raw facts
+ the last 12 free-form notes into context and hopes the model uses them.

### What already exists (do not rebuild)

- Tool-calling brain (llama-3.3-70b via NVIDIA) with a deterministic trust zone
  (`aqua_model`) for all sizing math.
- `user_facts` table — key/value with `source` tracking; regex auto-extraction of
  temperature / pH / fish species per message (`memory_extract.py`).
- `memories` table — agent-curated free-form notes, categorized
  (profile/event/preference/learning), with dedup.
- `session_summary` — rolling cross-session summary, refreshed in a background thread.
- FAISS RAG over `knowledge/*.md` + cited sources.

This design **extends** that foundation; it does not replace it.

---

## 2. Approach

Chosen: **Approach C — a structured System Profile** (over prompt-only A and a full
state-machine B). One typed per-user object serves as *both* the consultation
slot-tracker (Phase 1) *and* the reliable memory primitive (Phase 2). The LLM keeps
control of conversational flow; a deterministic helper steers it by computing what's
still missing.

The profile reuses the existing `user_facts` table — we standardize a **canonical key
set** rather than adding a new table.

**Consultation rhythm:** *essentials, then a first cut.* Ask 2–4 key questions to pin the
goal + critical facts, deliver a useful first recommendation fast, then refine through
follow-ups. (Not a one-question-at-a-time form; not act-immediately.)

---

## 3. Data model — the System Profile

Canonical keys stored in `user_facts` (typed, lenient values):

| Field | Example | Filled by |
|-------|---------|-----------|
| `system_stage` | planning / building / running | LLM |
| `fish_species` | tilapia | regex + LLM |
| `crop` | lettuce | LLM |
| `grow_area_m2` | 10 | LLM |
| `temperature_c` | 26 | regex + LLM |
| `water_budget_lpd` | 200 | LLM |
| `ph` | 7.2 | regex + LLM |
| `tank_volume_l` | 1000 | LLM (actual tank on a running system) |
| `dissolved_oxygen_mgl` | 5.5 | regex + LLM |
| `ammonia_mgl` | 0.5 | regex + LLM |
| `water_source` | "borehole, slightly saline" | LLM |
| `location` | Niamey | LLM |
| `goal` | design / optimize / troubleshoot | LLM |
| `goal_detail` | "maximize protein on a budget" | LLM |
| `objective` | food / protein / water_efficiency | LLM |
| `experience_level` | beginner / intermediate / expert | LLM |

**Two memory tiers, clear split:**

- **Profile** = durable structured *state* → drives slot-filling + recommendations,
  surfaced every turn.
- **`memories`** (existing) = *episodic* notes → the story over time. Untouched.

`tank_volume_l` matters because the sizing model normally *outputs* tank volume for a
greenfield design; for a *running* system (optimize/troubleshoot) the user's real tank
is an input fact.

**Trust boundary preserved.** `update_profile` writes are lenient. The real guard stays
at `validate_design_input`, which still rejects bad numbers the moment a model tool is
actually called. The profile never bypasses the trust gate.

---

## 4. Consultative flow

### Goal → essentials map (declarative, in code)

| Goal | Essentials before a first cut |
|------|-------------------------------|
| `design` | fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd |
| `optimize` | grow_area_m2, temperature_c, water_budget_lpd, objective |
| `troubleshoot` | symptom (free text) + relevant water params (temp, pH, DO, ammonia) — softer, judgment-based |

### One turn

```
user message
   ↓
regex auto-extract (temp / pH / DO / ammonia / species) → profile
   ↓
LLM sees: system prompt + rendered profile + "still missing for <goal>: [...]"
   ↓
┌─ goal unknown?       → ask what they're trying to do
├─ essentials missing? → ask for the 2–4 missing ones (batched, conversational)
└─ essentials present? → call the model tool → first cut + cite + offer to refine
   ↓
update_profile(...) writes any new structured facts the LLM gleaned
```

### The deterministic nudge

`missing_essentials(goal, profile) -> list[str]` computes what's still needed and injects
it into context every turn (e.g. *"Still need for sizing: crop, water_budget"*). The LLM
runs the conversation naturally but is steered by a fact, not hope — so it reliably stops
rushing *and* stops re-asking what it already knows. Both UX failure modes handled by one
mechanism.

### New tool

`update_profile(updates: dict)` — the LLM's typed pen for writing profile fields as they
surface. Lenient validation; **canonical keys only** (non-canonical keys dropped + logged).

### Prompt rewrite

The system prompt shifts from "answer directly, don't re-run tools" to a consultative
posture: detect the goal → fill essentials → give an essentials-then-first-cut
recommendation **anchored explicitly to the user's stated goal and constraints**. Anchoring
advice to profile + goal is what fixes "weak/generic recommendations." All existing HARD
RULES (never invent numbers; surface caveats; honor the trust gate) are retained.

---

## 5. Recall rendering (Phase-2 memory win)

`_recall_block` stops dumping raw facts and renders a clean, scannable, **goal-aware**
block. Troubleshooting surfaces water params + `event`/`learning` notes first;
design/optimize surfaces the system spec first.

```
YOUR SYSTEM (what I remember)
• stage: running · location: Niamey
• tilapia + lettuce · 10 m² grow · 1000 L tank
• temp 26°C · pH 7.2 · DO 5.5 · ammonia 0.5
• goal: optimize (maximize protein)
Still need for optimize: water_budget

RECENT HISTORY
• (event) ammonia spike in June — fixed with 30% water change
• (learning) prefers metric, terse answers

PAST SUMMARY: <rolling summary>
```

Typed, prioritized, always present — this is most of the "shallow recall" fix.

---

## 6. Error handling / edge cases

- **Conflicting facts** (10 m² → later 20 m²): latest wins, `source` tracked. No merge
  logic.
- **Bad numbers**: profile stays lenient; `validate_design_input` rejects at tool-call
  time. Trust gate unchanged.
- **Unknown / ambiguous goal**: agent asks rather than guessing — no silent default.
- **`update_profile` with junk keys**: non-canonical keys dropped, logged, not stored.
- **Memory unavailable** (runtime not set): tool returns a soft message, turn continues —
  same pattern as today's `remember_about_user`.

---

## 7. Testing

Extends the existing deterministic, no-live-LLM suite:

- **Unit:** `missing_essentials()` per goal; `update_profile` writes + key validation;
  `render_profile()` output; goal-aware ordering.
- **Dry-run** (`test_core_dryrun.py` fake model): vague input → agent asks for essentials
  → fills via `update_profile` → first cut. Proves the loop without a live model.

---

## 8. Files touched

| File | Change |
|------|--------|
| `agronaut_agent/profile.py` *(new)* | canonical keys, goal→essentials map, `missing_essentials()`, `render_profile()` |
| `agronaut_agent/tools.py` | add `update_profile` tool |
| `agronaut_agent/core.py` | consultative `SYSTEM_PROMPT`; `_recall_block` → profile rendering + missing-essentials injection |
| `agronaut_agent/memory_extract.py` | add DO + ammonia regex extraction |
| `agronaut_agent/store.py` | minor: profile helpers if needed (reuses `user_facts`) |
| `agronaut_agent/tests/` | new unit + dry-run cases |

---

## 9. Out of scope (later phases)

- Proactive follow-ups / outcome loop (Phase 3) — bot initiates "did the fix work?"
- Cross-user learning (Phase 4) — one operator's verified fix improves everyone's advice.
- Coefficient calibration from real yields (Phase 5) — touches the trust zone.
- Semantic / embedding recall over memories — only when memory actually grows large.

These depend on the outcome data Phase 3 collects, so they are sequenced after this work.

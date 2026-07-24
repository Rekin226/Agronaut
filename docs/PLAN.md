# Agronaut Gap-Closure Plan (2026 H2)

Source: the 2026-07-23 gap analysis (Agronaut vs `NousResearch/hermes-agent`) + NGO funding
research. Strategy in one line: **protect the moat (deterministic, cited, calibratable core),
close the agent-infrastructure gap around it, then earn users and the DPG/FAO funding ladder.**

How to use this file: one task = one branch = one PR. Work top-to-bottom within a phase;
phases 1–2 can interleave once Phase 0 lands. Tick the box in the PR that ships the task.
Sizes: **S** ≤ half a day, **M** 1–3 days, **L** a week-plus of gradual work.

---

## Phase 0 — Stop the bleeding (correctness of the promise)

The two defects that undermine "consultative agent with memory" for every user, every day.

- [x] **0.1 Fix cross-turn context: stop dropping tool results.** (M)
  - **What:** `_build_context` (`agronaut_agent/core.py:120-126`) discards stored `tool` rows,
    so computed sizings vanish on the next turn; the prompt even tells the model to "answer
    from earlier tool results" that aren't there. Also `recent_messages(limit=20)`
    (`agronaut_agent/store.py:164-169`) counts tool rows in the query window, shrinking
    effective history on tool-heavy turns.
  - **Fix shape:** re-materialize a compact form of tool results into context (e.g. a
    one-line summary row per tool call, or replay the last N tool outputs verbatim), and
    count only user/assistant rows toward the history limit.
  - **Accept:** integration test — turn 1 sizes a system, turn 2 asks "what tank volume did
    you give me?" with tools disabled; the agent answers from context. No re-run, no guess.

- [x] **0.2 One brain: point Streamlit chat at the real agent.** (L)
  - **What:** `app.py:125-141` "Assistant (chat)" drives legacy `srcs/chatbot.py` — a
    module-global `ThreadState()` singleton (`srcs/chatbot.py:310`) shared by ALL concurrent
    web users, with no deterministic tools, no System Profile, no calibration. The most
    polished surface demos the weakest brain.
  - **Fix shape:** route Streamlit chat through `agronaut_agent.core` with a per-session
    store key; keep the legacy module only for what the agent still imports (regex parsers,
    FAISS builder — `agronaut_agent/memory_extract.py:44`, `agronaut_agent/rag.py:23`), then
    schedule its retirement (M3 "RAG demotion" in TODOS.md).
  - **Accept:** two simultaneous browser sessions hold independent conversations with
    independent profiles; a web user can run a full design consultation with tools.

- [x] **0.3 Surface silent-calibration no-ops.** (S)
  - **What:** `record_measurement` accepts measurements for species/crops that have no
    `calibration.py` row; `overrides_for` (`agronaut_agent/store.py:395-398`) silently skips
    them — the operator's data is inert with no signal.
  - **Accept:** recording a measurement with no calibration coverage returns an honest
    "stored, but can't calibrate <key> yet — no published range on file" in the tool reply.

---

## Phase 1 — Field senses (what a farmer actually sends)

- [x] **1.1 Photo input on Telegram.** (L)
  - **What:** `telegram_adapter.py:182` handles only text. A photo of a sick fish / yellowing
    leaf / algae bloom is the most natural troubleshooting input that exists.
  - **Fix shape:** photo handler → vision-capable model (NVIDIA hosts open VLMs; keep the
    provider pluggable like `agent/llm.py`) → structured observation ("chlorosis on older
    leaves") fed into the normal agent turn as a user fact. The VLM observes; diagnosis stays
    with the agent + KB so citations still apply. Never let a VLM guess numbers into tools.
  - **Accept:** sending a leaf photo with "what's wrong?" yields a cited diagnosis flow;
    non-image documents get a graceful "can't read that yet."

- [x] **1.2 Semantic recall over memories.** (M)
  - **What:** recall today is SQL `LIKE` + last-12-by-recency (`store.py:223-229`, `354-361`);
    it gets worse as history grows. FAISS + `all-mpnet-base-v2` already exist for the KB.
  - **Fix shape:** embed `memories` (and community insights) on write; recall = top-k by
    similarity to the current turn, merged with the typed System Profile (which stays exact).
  - **Accept:** a user with 50+ memories asks about "that pump issue from before" and the
    relevant memory surfaces without keyword overlap.

- [x] **1.3 Enforced citations in RAG.** (M)
  - **What:** `rag.search` (`agronaut_agent/rag.py:33-41`) returns concatenated passages with
    no source attached — "cited advice" is currently prompt hope, not mechanics.
  - **Fix shape:** carry doc/URL metadata through the index; tool returns `[{passage, source}]`;
    system prompt requires the source in the answer; dedupe `urls.txt` (two entries duplicated).
  - **Accept:** every KB-derived claim in a reply names its source; a test asserts the tool
    output schema includes `source` for all passages.

- [x] **1.4 Voice notes in, one non-English language out.** (L) — voice-input shipped; artifact localization deferred to a named pilot partner (see PR #45).
  - **What:** funders' last-mile bar (AIEP playbook): text-English-Telegram reaches trained
    extension agents, not farmers. Voice + local language is the gate every program checks.
  - **Fix shape:** Telegram voice handler → open ASR (e.g. Whisper-class, pluggable) → normal
    turn. Pick ONE target language with a real deployment story (partner-driven, not
    speculative) and localize the structured artifacts too — report + BOM (`aqua_model/report.py`)
    and the Telegram command menu (`telegram_adapter.py:153-165`), not just chat prose.
  - **Accept:** a voice note in the target language gets a correct-language reply; the design
    report renders fully localized.

---

## Phase 2 — Reach & compliance (DPG-shaped work)

Every task here is simultaneously product hardening AND a Digital Public Goods
certification requirement (the funding ladder's first rung).

- [x] **2.1 Channel abstraction.** (M)
  - **What:** Telegram specifics are woven into the adapter + followup delivery. DPG indicator
    4 (platform independence) requires proprietary channels be swappable.
  - **Fix shape:** a small `Channel` interface (send, receive, media, followup delivery);
    Telegram becomes instance #1; the REPL becomes instance #2 (proof of independence).
    Fix the group-chat identity bug while in there: memory keyed on `effective_chat.id` but
    allowlist on `effective_user.id` (`telegram_adapter.py:42-46,117`).
  - **Accept:** `agronaut_agent` imports nothing from `python-telegram-bot`; a group chat
    doesn't collapse all members into one profile.

- [x] **2.2 WhatsApp adapter.** (M) — built + unit-tested; live test needs owner Meta creds. **Depends:** 2.1.
  - **Accept:** full consultation (incl. follow-ups) over WhatsApp against a test number.

- [x] **2.3 Documented open-weights path for the tool-calling brain.** (M)
  - **What:** Ollama backend can't bind tools (`agent/llm.py:174-189`), so fully-offline today
    means the degraded legacy path — a platform-independence hole (hosted NVIDIA is the only
    real brain).
  - **Fix shape:** support one self-hostable OpenAI-compatible server with tool calling
    (vLLM/llama.cpp-server class) as a first-class documented provider; CI-test the binding
    with a mocked endpoint.
  - **Accept:** README documents a zero-proprietary-API deployment that passes the tool-loop
    smoke test.

- [x] **2.4 DPG compliance pack.** (M)
  - **What:** privacy/do-no-harm policy (what's collected, retention, deletion, consent —
    Telegram chat is personal data), a `/export` + `/delete_me` data path (non-proprietary
    JSON/CSV export per indicator 6), AI-transparency note (RAG over cited public sources; no
    training on user data), SDG mapping (2/6/12).
  - **Accept:** `docs/dpg/` contains the 9-indicator evidence map; export/delete work from chat.

- [x] **2.5 Docker one-liner + compose.** (S)
  - **Accept:** `docker compose up` → working Streamlit + bot (given env vars); README quick
    start shrinks to three lines.

- [x] **2.6 Submit DPG application.** (S) — application DRAFTED (docs/dpg/APPLICATION_DRAFT.md); actual submission left to owner (outward-facing). **Depends:** 2.1, 2.3, 2.4.

---

## Phase 3 — Domain expansion (toward "the god of agriculture")

The generalizable asset is the pattern: **cited parametric engine + validation gate +
calibration loop + LLM explainer.** Each new engine widens agronomy → agriculture.

- [x] **3.1 Hydroponics mode in `aqua_model`.** (L)
  - **Why:** doubles the addressable audience and unlocks the humanitarian door (WFP H2Grow,
    Azraq, Zaatari are hydroponics — fish are complexity camps avoid). Largely a subset of
    existing machinery: nutrient-solution sizing instead of fish-feed nitrogen source; water
    balance, crops, optimizer, report all reuse.
  - **Accept:** `size_system(source="hydroponic")`-class API with cited coefficient seeds, its
    own "not modeled" list, tests at parity with aquaponics; agent can consult on both and
    say which fits the user's constraints.

- [x] **3.2 Deterministic SVG system schematic.** (M)
  - **What:** generate a labeled 2-D schematic (tanks, beds, biofilter, plumbing, flows,
    dimensions) from `size_system` output — pure code, no ML — attached to reports and sent
    as an image in chat. First step of the "graphical design" ambition; the M4 digital twin
    adds time-series charts later.
  - **Accept:** every design produces an SVG/PNG that matches its BOM numbers; snapshot test.

- [x] **3.3 Package aqua_model tools as agentskills.io skills.** (M)
  - **What:** distribution hack — publish "aquaponics-engineer" as a standard skill usable
    from hermes-agent/OpenClaw (219k-star ecosystem) wrapping the same validated tools. Their
    users get math that's real; Agronaut keeps the field/NGO deployments where safety is ours.
  - **Accept:** skill published + smoke-tested against hermes-agent; README cross-links.

- [x] **3.4 Tier-3 KB docs** (economics, feasibility, food safety, regulations). (M) — the
    open gap from the 2026-07 KB audit; required before funder conversations get serious.

---

## Phase 4 — Evidence (what actually opens funding doors)

No funder moves on features. They move on: a named LMIC deployment partner, 100–1,000 real
users with analytics, an outcome survey, one local language, a documented advice-safety
evaluation, a privacy policy. Phases 0–2 make this phase possible.

- [x] **4.1 Advice-safety golden set.** (M) — 100+ Q&A pairs (design, troubleshoot, edge
  cases, out-of-scope traps) scored per release; the AIEP-recommended artifact funders ask
  for first. Wire into CI as a report, not a gate.
- [x] **4.2 Usage analytics (privacy-preserving).** (S) — counts and funnels, no message
  content; consistent with 2.4.
- [ ] **4.3 Pilot partner search.** (ongoing) — target list in order: CGIAR Asia Digital Hub
  @ WorldFish (aquatic foods + DPG incubation mandate — best single match), GIZ FAIR
  Forward/i4Ag ecosystem, WFP Innovation Accelerator (with 3.1 shipped), university/NGO
  aquaponics programs. The pilot-proposal generator already in TODOS.md becomes real here.
- [ ] **4.4 60dB-style outcome survey of pilot users.** (M) — **Depends:** 4.3 live.

---

## Sequencing at a glance

```
0.1 → 0.2 → 0.3            (unblock everything; ~1 PR each)
1.1 ─┐
1.2  ├─ interleave with →  2.1 → 2.2
1.3  │                     2.3 → 2.4 → 2.5 → 2.6 (DPG submitted)
1.4 ─┘                          ↓
3.1 → 3.2 → 3.3 → 3.4      4.1 → 4.2 → 4.3 → 4.4 (funding conversations)
```

Standing constraint (unchanged from the roadmap): profile/memory/vision output must NEVER
feed numbers into `aqua_model` bypassing `validate_design_input`, and inputs the gate
rejected are never persisted.

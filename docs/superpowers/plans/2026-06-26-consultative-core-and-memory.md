# Consultative Core + Memory Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Agronaut's one-shot Q&A bot into a consultative agent that discovers the user's goal, gathers the essentials, gives an essentials-then-first-cut recommendation, and reliably recalls each user's system across sessions.

**Architecture:** A typed **System Profile** (canonical keys layered on the existing `user_facts` table) is the single primitive. It is both the consultation slot-tracker and the memory record. A deterministic `missing_essentials()` helper steers the LLM (which keeps conversational control); a new `update_profile` tool is the LLM's typed pen; a rewritten system prompt makes the agent consult instead of answer-dump; and `_recall_block` renders the profile cleanly every turn.

**Tech Stack:** Python 3.12, LangChain tool-calling (`@tool`), SQLite (`user_facts`/`memories`/`session_summary`), pytest. No new dependencies.

## Global Constraints

- **Trust boundary is sacred:** `update_profile` is lenient; the only validation gate for model inputs stays at `validate_design_input`. The profile must never feed numbers into `aqua_model` without passing that gate. (verbatim from spec §3)
- **No new tables:** the System Profile reuses the existing `user_facts` table (key/value + `source`). (spec §3)
- **Two memory tiers stay separate:** Profile = durable structured state; `memories` = episodic notes, untouched. (spec §3)
- **Deterministic tests only:** no live LLM in the test suite; use fake chat models, matching the existing `test_core_dryrun.py` pattern. (spec §7)
- **Goals are exactly:** `design`, `optimize`, `troubleshoot`. (spec §4)
- **Consultation rhythm:** essentials (2–4 questions) then a first cut, then refine. (spec §2)
- Work on branch `feat/consultative-core-memory` (already checked out). Commit after every task.

---

### Task 1: Profile core — canonical keys, goal→essentials map, `missing_essentials()`

**Files:**
- Create: `agronaut_agent/profile.py`
- Test: `agronaut_agent/tests/test_profile.py`

**Interfaces:**
- Consumes: nothing (leaf module, stdlib only).
- Produces:
  - `PROFILE_KEYS: tuple[str, ...]` — the 16 canonical keys.
  - `GOALS: tuple[str, ...]` = `("design", "optimize", "troubleshoot")`.
  - `GOAL_ESSENTIALS: dict[str, tuple[str, ...]]`.
  - `missing_essentials(goal: str | None, profile: dict) -> list[str]` — essential keys still blank for `goal`; `[]` when goal is unknown or has no hard essentials.

- [ ] **Step 1: Write the failing test**

Create `agronaut_agent/tests/test_profile.py`:

```python
"""The System Profile primitive: canonical keys, goal essentials, and the
deterministic 'what's still missing' helper that steers the consultation."""

from agronaut_agent import profile


def test_profile_keys_include_new_water_fields():
    for key in ("tank_volume_l", "dissolved_oxygen_mgl", "ammonia_mgl",
                "goal", "objective", "experience_level"):
        assert key in profile.PROFILE_KEYS


def test_missing_essentials_for_design_lists_blanks():
    have = {"goal": "design", "fish_species": "tilapia"}
    missing = profile.missing_essentials("design", have)
    assert missing == ["crop", "grow_area_m2", "temperature_c", "water_budget_lpd"]


def test_missing_essentials_empty_when_all_present():
    have = {"grow_area_m2": "10", "temperature_c": "26",
            "water_budget_lpd": "200", "objective": "protein"}
    assert profile.missing_essentials("optimize", have) == []


def test_missing_essentials_blank_string_counts_as_missing():
    have = {"grow_area_m2": "  ", "temperature_c": "26",
            "water_budget_lpd": "200", "objective": "protein"}
    assert profile.missing_essentials("optimize", have) == ["grow_area_m2"]


def test_missing_essentials_unknown_goal_is_empty():
    assert profile.missing_essentials(None, {}) == []
    assert profile.missing_essentials("troubleshoot", {}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agronaut_agent.profile'`

- [ ] **Step 3: Write minimal implementation**

Create `agronaut_agent/profile.py`:

```python
"""The System Profile — a typed view over the user's aquaponics system and current
consultation goal. Stored as canonical keys in the existing `user_facts` table; this
module owns the vocabulary, the goal->essentials map, the 'what's still missing'
steering helper, and the recall rendering.
"""

from __future__ import annotations

# Canonical profile fields. update_profile accepts only these keys.
PROFILE_KEYS: tuple[str, ...] = (
    "system_stage",          # planning | building | running
    "fish_species",
    "crop",
    "grow_area_m2",
    "temperature_c",
    "water_budget_lpd",
    "ph",
    "tank_volume_l",         # actual tank on a running system (input, not computed)
    "dissolved_oxygen_mgl",
    "ammonia_mgl",
    "water_source",
    "location",
    "goal",                  # design | optimize | troubleshoot
    "goal_detail",
    "objective",             # food | protein | water_efficiency
    "experience_level",      # beginner | intermediate | expert
)

GOALS: tuple[str, ...] = ("design", "optimize", "troubleshoot")

# Essentials required before a first-cut recommendation, per goal. troubleshoot is
# judgment-based (no hard slots) — the prompt drives it from symptoms + water params.
GOAL_ESSENTIALS: dict[str, tuple[str, ...]] = {
    "design": ("fish_species", "crop", "grow_area_m2", "temperature_c", "water_budget_lpd"),
    "optimize": ("grow_area_m2", "temperature_c", "water_budget_lpd", "objective"),
    "troubleshoot": (),
}


def missing_essentials(goal: str | None, profile: dict) -> list[str]:
    """Essential keys for `goal` that are still blank in `profile`. Empty list when the
    goal is unknown or has no hard essentials (e.g. troubleshoot)."""
    essentials = GOAL_ESSENTIALS.get((goal or "").strip().lower(), ())
    return [k for k in essentials if not str(profile.get(k, "")).strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/profile.py agronaut_agent/tests/test_profile.py
git commit -m "feat(profile): canonical keys + goal essentials + missing_essentials()"
```

---

### Task 2: Profile rendering — `render_profile()`

**Files:**
- Modify: `agronaut_agent/profile.py`
- Test: `agronaut_agent/tests/test_profile.py`

**Interfaces:**
- Consumes: `PROFILE_KEYS` (Task 1).
- Produces: `render_profile(profile: dict, goal: str | None = None) -> str` — a compact, goal-aware "YOUR SYSTEM" block; `""` when no known fields are set. For `goal == "troubleshoot"`, water-quality params are ordered before the system spec.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_profile.py`:

```python
def test_render_profile_empty_is_blank():
    assert profile.render_profile({}) == ""


def test_render_profile_shows_known_fields_with_labels():
    text = profile.render_profile(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": "10",
         "temperature_c": "26", "goal": "design"},
        goal="design",
    )
    assert "YOUR SYSTEM" in text
    assert "tilapia" in text and "lettuce" in text
    assert "10" in text and "26" in text


def test_render_profile_troubleshoot_puts_water_params_first():
    text = profile.render_profile(
        {"grow_area_m2": "10", "dissolved_oxygen_mgl": "4.0", "ammonia_mgl": "2.0"},
        goal="troubleshoot",
    )
    # water params surface before the system spec when troubleshooting
    assert text.index("DO") < text.index("grow area")
    assert text.index("ammonia") < text.index("grow area")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -k render -v`
Expected: FAIL — `AttributeError: module 'agronaut_agent.profile' has no attribute 'render_profile'`

- [ ] **Step 3: Write minimal implementation**

Append to `agronaut_agent/profile.py`:

```python
# Friendly labels (with units) for recall rendering.
_LABELS: dict[str, str] = {
    "system_stage": "stage",
    "location": "location",
    "fish_species": "fish",
    "crop": "crop",
    "grow_area_m2": "grow area",
    "tank_volume_l": "tank (L)",
    "temperature_c": "temp (°C)",
    "ph": "pH",
    "dissolved_oxygen_mgl": "DO (mg/L)",
    "ammonia_mgl": "ammonia (mg/L)",
    "water_budget_lpd": "water budget (L/day)",
    "water_source": "water source",
    "goal": "goal",
    "goal_detail": "goal detail",
    "objective": "objective",
    "experience_level": "experience",
}

# Default display order: system spec first, then water params, then goal.
_SYSTEM_ORDER = ("system_stage", "location", "fish_species", "crop", "grow_area_m2",
                 "tank_volume_l", "water_budget_lpd", "water_source")
_WATER_ORDER = ("temperature_c", "ph", "dissolved_oxygen_mgl", "ammonia_mgl")
_GOAL_ORDER = ("goal", "goal_detail", "objective", "experience_level")


def render_profile(profile: dict, goal: str | None = None) -> str:
    """Compact, goal-aware recall block. Empty string when nothing is known."""
    if (goal or "").strip().lower() == "troubleshoot":
        order = _WATER_ORDER + _SYSTEM_ORDER + _GOAL_ORDER
    else:
        order = _SYSTEM_ORDER + _WATER_ORDER + _GOAL_ORDER

    lines = []
    for key in order:
        val = str(profile.get(key, "")).strip()
        if val:
            lines.append(f"• {_LABELS[key]}: {val}")
    if not lines:
        return ""
    return "YOUR SYSTEM (what I remember)\n" + "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/profile.py agronaut_agent/tests/test_profile.py
git commit -m "feat(profile): goal-aware render_profile() recall block"
```

---

### Task 3: Deterministic DO + ammonia extraction

**Files:**
- Modify: `agronaut_agent/memory_extract.py`
- Test: `agronaut_agent/tests/test_memory_extract.py`

**Interfaces:**
- Consumes: existing `extract_facts(text: str) -> dict[str, str]`.
- Produces: `extract_facts` additionally returns `dissolved_oxygen_mgl` and `ammonia_mgl` when present. DO requires a `mg/L`/`ppm` unit to avoid matching the English word "do"; ammonia is matched by its (unambiguous) name.

- [ ] **Step 1: Write the failing test**

Create `agronaut_agent/tests/test_memory_extract.py`:

```python
"""Deterministic fact extraction from free text (water-quality readings)."""

from agronaut_agent.memory_extract import extract_facts


def test_extracts_dissolved_oxygen_with_unit():
    assert extract_facts("DO is 5.5 mg/L this morning")["dissolved_oxygen_mgl"] == "5.5"
    assert extract_facts("dissolved oxygen 4 ppm")["dissolved_oxygen_mgl"] == "4"


def test_does_not_match_the_word_do_without_a_unit():
    assert "dissolved_oxygen_mgl" not in extract_facts("what do I do at 26C?")


def test_extracts_ammonia():
    assert extract_facts("ammonia 0.5")["ammonia_mgl"] == "0.5"
    assert extract_facts("ammonia spiked to 2 ppm")["ammonia_mgl"] == "2"


def test_still_extracts_temperature():
    assert extract_facts("water is 27C")["temperature_c"] == "27.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_memory_extract.py -v`
Expected: FAIL — `KeyError: 'dissolved_oxygen_mgl'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/memory_extract.py`, add the two patterns after the `_PH_CUE` definition:

```python
# DO needs a unit (mg/L or ppm) so the English word "do" can't fabricate a reading.
_DO_RE = re.compile(
    r"(?:dissolved\s+oxygen|\bDO\b)\D{0,8}(\d+(?:\.\d+)?)\s*(?:mg/?\s*l|ppm)",
    re.IGNORECASE,
)
# "ammonia" is unambiguous — no unit required.
_AMMONIA_RE = re.compile(r"ammonia\D{0,8}(\d+(?:\.\d+)?)", re.IGNORECASE)
```

Then inside `extract_facts`, before `return facts`, add:

```python
    do = _DO_RE.search(text)
    if do:
        facts["dissolved_oxygen_mgl"] = do.group(1)
    amm = _AMMONIA_RE.search(text)
    if amm:
        facts["ammonia_mgl"] = amm.group(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_memory_extract.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/memory_extract.py agronaut_agent/tests/test_memory_extract.py
git commit -m "feat(memory): extract dissolved oxygen + ammonia from messages"
```

---

### Task 4: The `update_profile` tool

**Files:**
- Modify: `agronaut_agent/tools.py`
- Test: `agronaut_agent/tests/test_tools.py:11-17` (registry count), plus new tool tests

**Interfaces:**
- Consumes: `profile.PROFILE_KEYS` (Task 1); `runtime.get_current()` → `(MemoryStore, user_id)`; `MemoryStore.set_facts(user_id, dict, source=...)`.
- Produces: `update_profile` tool appended to `AGRONAUT_TOOLS` (length becomes **8**). Accepts a `dict`, keeps only canonical keys with non-empty values, writes them with `source="user_stated"`, ignores unknown keys.

- [ ] **Step 1: Write the failing test**

In `agronaut_agent/tests/test_tools.py`, update the registry test and add tool tests. Replace the body of `test_tool_registry` count assertion and add new tests at the end of the file:

```python
def test_registry_includes_update_profile():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "update_profile" in names
    assert len(AGRONAUT_TOOLS) == 8


def test_update_profile_writes_canonical_drops_unknown():
    from agronaut_agent.store import _Db, MemoryStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import update_profile

    mem = MemoryStore(_Db(":memory:"))
    runtime.set_current(mem, "cli:p")
    try:
        out = update_profile.invoke({"updates": {
            "goal": "optimize", "objective": "protein", "grow_area_m2": 10,
            "bogus_key": "x", "ph": "",
        }})
    finally:
        runtime.clear_current()

    facts = mem.get_facts("cli:p")
    assert facts["goal"] == "optimize"
    assert facts["objective"] == "protein"
    assert facts["grow_area_m2"] == "10"
    assert "bogus_key" not in facts   # unknown key ignored
    assert "ph" not in facts          # empty value skipped
    assert "optimize" in out
```

Also change the existing `test_tool_registry` assertion `assert len(AGRONAUT_TOOLS) == 7` to `== 8`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -k "update_profile or registry" -v`
Expected: FAIL — `ImportError: cannot import name 'update_profile'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/tools.py`: add logging + profile imports near the top (after the existing `from . import rag, runtime, serialize`):

```python
import logging

from . import profile as profile_mod

log = logging.getLogger(__name__)
```

Add the tool (place it next to `remember_about_user`):

```python
@tool
def update_profile(updates: dict) -> str:
    """Save typed facts about THIS user's system to their profile so you recall and reuse
    them across the conversation and future sessions. Pass a dict of canonical fields you
    have learned, e.g. {"goal": "optimize", "objective": "protein", "grow_area_m2": 10,
    "tank_volume_l": 1000, "dissolved_oxygen_mgl": 5.5}. Canonical keys: system_stage,
    fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd, ph, tank_volume_l,
    dissolved_oxygen_mgl, ammonia_mgl, water_source, location, goal, goal_detail,
    objective, experience_level. Unknown keys are ignored. Call this whenever the user
    reveals a durable fact — do not wait for the end of the conversation."""
    cur = runtime.get_current()
    if cur is None:
        return "Profile unavailable right now."
    mem, user_id = cur
    updates = updates or {}
    accepted = {k: v for k, v in updates.items()
                if k in profile_mod.PROFILE_KEYS and str(v).strip() not in ("", "None")}
    rejected = [k for k in updates if k not in profile_mod.PROFILE_KEYS]
    if rejected:
        log.debug("update_profile dropped unknown keys: %s", rejected)
    if not accepted:
        return "No recognized profile fields to save."
    mem.set_facts(user_id, accepted, source="user_stated")
    return "Saved to your profile: " + ", ".join(f"{k}={v}" for k, v in accepted.items())
```

Append `update_profile` to the `AGRONAUT_TOOLS` list:

```python
AGRONAUT_TOOLS = [
    size_aquaponics_system,
    optimize_fish_crop_ratio,
    list_supported_species_and_crops,
    design_envelope_reality_check,
    render_design_report,
    search_knowledge_base,
    remember_about_user,
    update_profile,
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -v`
Expected: PASS (all, including updated count == 8)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/tools.py agronaut_agent/tests/test_tools.py
git commit -m "feat(tools): update_profile — the LLM's typed pen for the System Profile"
```

---

### Task 5: Wire profile rendering + missing-essentials into recall

**Files:**
- Modify: `agronaut_agent/core.py:90-104` (`_recall_block`), plus the import line at `agronaut_agent/core.py:19`
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `profile.render_profile`, `profile.missing_essentials` (Tasks 1–2); existing `MemoryStore.get_facts`, `get_summary`, `get_memories`.
- Produces: `_recall_block(user_id)` now renders the goal-aware profile, appends a `Still need for <goal>: ...` line when essentials are missing, orders `event`/`learning` memories first when `goal == "troubleshoot"`, then the rolling summary. Signature unchanged.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
def test_recall_renders_profile_and_missing_essentials(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    uid = agent._conv.get_or_create_user("cli", "recall")
    agent._mem.set_facts(uid, {"goal": "design", "fish_species": "tilapia"})

    block = agent._recall_block(uid)
    assert "YOUR SYSTEM" in block
    assert "tilapia" in block
    # the deterministic nudge lists exactly the still-blank design essentials
    assert "Still need for design:" in block
    for key in ("crop", "grow_area_m2", "temperature_c", "water_budget_lpd"):
        assert key in block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py::test_recall_renders_profile_and_missing_essentials -v`
Expected: FAIL — assertion error: `"YOUR SYSTEM" not in block` (old code emits "Known system facts:")

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py`, change the import at line 19 from:

```python
from . import memory_extract, runtime
```

to:

```python
from . import memory_extract, runtime, profile
```

Replace the `_recall_block` method (currently lines 90–104) with:

```python
    def _recall_block(self, user_id: str) -> str:
        """Assemble cross-session recall: goal-aware profile + missing essentials,
        episodic memories, and the rolling summary."""
        parts: list[str] = []
        facts = self._mem.get_facts(user_id)
        goal = facts.get("goal")

        rendered = profile.render_profile(facts, goal=goal)
        if rendered:
            parts.append(rendered)
        missing = profile.missing_essentials(goal, facts)
        if missing:
            parts.append(f"Still need for {goal}: " + ", ".join(missing))

        memories = self._mem.get_memories(user_id)
        if memories:
            if (goal or "").strip().lower() == "troubleshoot":
                # surface what happened / what worked first when diagnosing
                memories = sorted(
                    memories,
                    key=lambda m: 0 if m["category"] in ("event", "learning") else 1,
                )
            parts.append("RECENT HISTORY\n" + "\n".join(
                f"- ({m['category']}) {m['content']}" for m in memories
            ))

        summary = self._mem.get_summary(user_id)
        if summary:
            parts.append("PAST SUMMARY: " + summary)
        return "\n\n".join(parts)
```

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -v`
Expected: PASS — including the existing `test_agent_curates_and_recalls_memory` (still finds `"3000 L IBC"` in the recall block, now under `RECENT HISTORY`).

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): goal-aware profile recall + missing-essentials nudge"
```

---

### Task 6: Consultative system prompt + consultation dry-run

**Files:**
- Modify: `agronaut_agent/core.py:23-58` (`SYSTEM_PROMPT`)
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: the `update_profile` tool (Task 4); the recall block (Task 5).
- Produces: a rewritten `SYSTEM_PROMPT` that makes the agent discover the goal, ask for missing essentials (batched 2–4), give an essentials-then-first-cut recommendation anchored to the user's goal/profile, and call `update_profile` as facts surface. All existing HARD RULES retained. A dry-run test proves the agent persists profile facts via `update_profile` within a normal turn.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
class _ConsultFake:
    """Turn 1 -> call update_profile with what the user revealed; then -> a question."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Got it. What's your daily water budget?")
        return AIMessage(content="", tool_calls=[{
            "name": "update_profile", "id": "u1",
            "args": {"updates": {"goal": "design", "fish_species": "tilapia",
                                 "crop": "lettuce"}}}])


def test_consultation_persists_profile_via_tool(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ConsultFake())
    reply = agent.handle_message("telegram", "c1", "I want to set up tilapia and lettuce")
    assert "water budget" in reply
    facts = agent._mem.get_facts("telegram:c1")
    assert facts["goal"] == "design"
    assert facts["fish_species"] == "tilapia"
    assert facts["crop"] == "lettuce"


def test_system_prompt_is_consultative():
    from agronaut_agent.core import SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    assert "goal" in lowered
    assert "update_profile" in lowered
    assert "essential" in lowered
    # the old answer-dump instruction is gone
    assert "answer directly" not in lowered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py -k "consultation or consultative" -v`
Expected: FAIL — `test_system_prompt_is_consultative` fails (`"update_profile" not in lowered`, and `"answer directly"` still present).

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py`, replace the entire `SYSTEM_PROMPT` string (lines 23–58) with:

```python
SYSTEM_PROMPT = """You are Agronaut, a personal aquaponics design and troubleshooting assistant.

You speak with operators and farmers — be concrete, warm, and brief. Reply in the user's language.
Keep replies short and scannable for a phone: lead with the point, use short bullets for numbers or steps.

YOU RUN A CONSULTATION, NOT A Q&A. Your job is to understand the person before you advise them.

1. FIND THE GOAL. Every conversation has one of three goals — figure out which:
   - design: size a new system from scratch.
   - optimize: find the best fish/crop ratio for an existing or planned system.
   - troubleshoot: diagnose a problem (sick fish, bad water, failing plants).
   If the goal is unclear, ask — briefly — what they're trying to do. Do not guess.

2. GATHER THE ESSENTIALS, THEN GIVE A FIRST CUT. Each goal needs a few facts before you
   can help well:
   - design needs: fish species, crop, grow area (m²), water temperature, daily water budget.
   - optimize needs: grow area (m²), water temperature, daily water budget, objective
     (food / protein / water_efficiency).
   - troubleshoot needs: the symptom, plus relevant water readings (temperature, pH,
     dissolved oxygen, ammonia).
   The system note above tells you what is still missing ("Still need for ..."). Ask for the
   missing essentials — at most 2–4 at once, conversationally, never as a long form. Once you
   have them, ACT: call the right tool and give a useful first recommendation. Then offer to refine.
   Do NOT re-ask anything already in YOUR SYSTEM above.

3. ANCHOR EVERY RECOMMENDATION to their stated goal and their system. Generic advice is a
   failure — tie the answer to what they told you (their species, area, budget, constraints).

REMEMBER AS YOU GO:
- The moment the user reveals a durable structured fact (species, area, temperature, tank
  volume, water readings, location, their goal/objective, experience level), call
  update_profile to save it. Do not wait until the end.
- For episodic things that happened or fixes that worked, call remember_about_user
  (category event / learning / preference). Honour "forget that".

HARD RULES (these are your credibility):
- NEVER state a sizing number, bill-of-materials quantity, or coefficient that did not come
  from a tool result. For any sizing/optimization question, CALL the tool — do not estimate.
- When a tool returns coefficients and "not modeled" caveats, surface them: cite the source of
  key numbers and remind the user these are calibration seeds, not guarantees.
- If the trust gate rejects an input (VALIDATION_FAILED), ask the user for a corrected value.
  Never guess or work around it.
- For qualitative troubleshooting, use the knowledge tool and your general knowledge; say when
  you are reasoning from general knowledge.

ANSWERING FOLLOW-UPS: use the conversation, YOUR SYSTEM, and earlier tool results first. If a
number was already computed or a fact already known, answer from it directly — don't re-run a
tool. To judge whether a value is safe (temperature, pH, DO), read the operating_envelope from
the prior sizing result; don't search the knowledge base for it."""
```

The wording "answer from it directly" is deliberate: the bare phrase "answer directly" must NOT
appear anywhere in the prompt, because `test_system_prompt_is_consultative` asserts
`"answer directly" not in lowered` (it marks the removal of the old answer-dump instruction).

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -v`
Expected: PASS (all tests, including the two new consultation tests and the unchanged tool-loop/memory tests).

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): consultative system prompt (goal discovery, essentials-then-first-cut)"
```

---

### Task 7: Manual smoke test against the live model + README note

**Files:**
- Modify: `README.md` (short "Consultative agent" note under the agent section)
- No test file (this task is a live smoke check + docs).

**Interfaces:**
- Consumes: the whole feature; a configured NVIDIA provider (already in `.env`).
- Produces: a verified end-to-end consultation transcript and a one-paragraph README update.

- [ ] **Step 1: Run the REPL and drive a vague consultation**

Run: `.venv/bin/python -m agronaut_agent.core`
Then type: `I want to start growing fish and vegetables`
Expected: the agent asks what they're trying to do / for a couple of essentials (species, crop, grow area, temp, water budget) — it does NOT dump numbers. Provide them across two messages; expect a first-cut sizing with cited coefficients. Type `quit` to exit.

- [ ] **Step 2: Verify the profile persisted**

Run: `.venv/bin/python -c "from agronaut_agent.store import _Db, MemoryStore; print(MemoryStore(_Db()).get_facts('cli:local'))"`
Expected: a dict containing `goal`, `fish_species`, `crop`, and any numbers you gave.

- [ ] **Step 3: Add the README note**

Under the agent section of `README.md`, add:

```markdown
### Consultative agent

Agronaut runs a consultation, not a one-shot Q&A. It identifies your goal (design a
system, optimize a ratio, or troubleshoot a problem), asks for the few essentials that
goal needs, then gives a first-cut recommendation tied to *your* system — and remembers
it (a typed System Profile + episodic notes) across sessions.
```

- [ ] **Step 4: Run the full suite one last time**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: describe the consultative agent behavior"
```

---

### Task 8: Deterministic profile capture from validated tool calls

**Why (added after live verification):** the live model runs the consultation well but
does not reliably call `update_profile` — it jumps straight to the sizing tool and answers,
so structured memory never persists. The robust fix is deterministic: when a sizing/optimize
tool *succeeds*, its arguments ARE the user's trust-gated system facts — capture them into
the profile automatically, regardless of whether the model called `update_profile`. The
`update_profile` tool remains the complement for facts that surface in plain chat (location,
tank volume, DO/ammonia readings).

**Files:**
- Modify: `agronaut_agent/profile.py` (add `profile_updates_from_tool`)
- Modify: `agronaut_agent/core.py` — `_run_tool_loop`, persist captured facts after each tool result
- Test: `agronaut_agent/tests/test_profile.py`, `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `PROFILE_KEYS` (Task 1); `MemoryStore.set_facts(user_id, dict, source=...)`.
- Produces: `profile.profile_updates_from_tool(name: str, args: dict, result: str) -> dict`
  — returns the profile-fact subset of a tool's args, but only for fact-carrying tools and
  only when `result` shows no failure marker; `{}` otherwise. `_run_tool_loop` writes these
  with `source="tool_call"`.

- [ ] **Step 1: Write the failing unit test**

Append to `agronaut_agent/tests/test_profile.py`:

```python
def test_profile_updates_from_size_success():
    args = {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
            "temperature_c": 27, "water_budget_lpd": 300}
    updates = profile.profile_updates_from_tool("size_aquaponics_system", args, "FEASIBLE ...")
    assert updates == {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                       "temperature_c": 27, "water_budget_lpd": 300}


def test_profile_updates_skipped_on_validation_failure():
    args = {"fish_species": "shark", "crop": "lettuce", "grow_area_m2": 12,
            "temperature_c": 27, "water_budget_lpd": 300}
    out = profile.profile_updates_from_tool("size_aquaponics_system", args,
                                            "VALIDATION_FAILED: unknown species 'shark'")
    assert out == {}


def test_profile_updates_for_optimize_includes_objective():
    args = {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000,
            "objective": "food"}
    out = profile.profile_updates_from_tool("optimize_fish_crop_ratio", args, "Best ratio: ...")
    assert out == {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000,
                   "objective": "food"}


def test_profile_updates_ignores_non_fact_tools():
    assert profile.profile_updates_from_tool("search_knowledge_base",
                                             {"query": "x"}, "some passage") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -k profile_updates -v`
Expected: FAIL — `AttributeError: module 'agronaut_agent.profile' has no attribute 'profile_updates_from_tool'`

- [ ] **Step 3: Implement `profile_updates_from_tool`**

Append to `agronaut_agent/profile.py`:

```python
# Tools whose validated arguments ARE the user's system facts. The args map 1:1 to
# canonical profile keys, so a successful call deterministically fills the profile.
_TOOL_PROFILE_ARGS: dict[str, tuple[str, ...]] = {
    "size_aquaponics_system": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                               "water_budget_lpd"),
    "render_design_report": ("fish_species", "crop", "grow_area_m2", "temperature_c",
                             "water_budget_lpd"),
    "optimize_fish_crop_ratio": ("grow_area_m2", "temperature_c", "water_budget_lpd",
                                 "objective"),
}
# Substrings that mark a tool result as a non-success — never persist args from these.
_TOOL_FAILURE_MARKERS = ("VALIDATION_FAILED", "TOOL_ERROR", "Unknown objective", "Unknown tool")


def profile_updates_from_tool(name: str, args: dict, result: str) -> dict:
    """Profile facts to persist from a successful fact-carrying tool call. Empty dict for
    non-fact tools or when the result shows a failure marker (so bad/ rejected inputs are
    never remembered)."""
    keys = _TOOL_PROFILE_ARGS.get(name)
    if not keys:
        return {}
    if any(marker in (result or "") for marker in _TOOL_FAILURE_MARKERS):
        return {}
    args = args or {}
    return {k: args[k] for k in keys
            if k in args and str(args[k]).strip() not in ("", "None")}
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -k profile_updates -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing core dry-run test**

Append to `agronaut_agent/tests/test_core_dryrun.py` (note: `_FakeChat` already calls
`size_aquaponics_system` with full args and never calls `update_profile` — the ideal
fixture to prove deterministic capture):

```python
def test_tool_args_persist_to_profile_without_update_profile(tmp_path):
    # _FakeChat sizes a system but never calls update_profile; the profile must still fill.
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_FakeChat())
    agent.handle_message("cli", "cap", "size a 12 m2 tilapia + lettuce at 27C, 300 L/day")
    facts = agent._mem.get_facts("cli:cap")
    assert facts["crop"] == "lettuce"
    assert facts["grow_area_m2"] == "12"
    assert facts["water_budget_lpd"] == "300"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py::test_tool_args_persist_to_profile_without_update_profile -v`
Expected: FAIL — `KeyError: 'crop'` (profile only has regex-extracted temperature_c/fish_species)

- [ ] **Step 7: Wire capture into the tool loop**

In `agronaut_agent/core.py`, in `_run_tool_loop`, the per-call block currently ends:

```python
                self._conv.append_message(user_id, "tool", result, tool_name=call["name"])
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
```

Insert the capture between those two lines so it runs for every tool result:

```python
                self._conv.append_message(user_id, "tool", result, tool_name=call["name"])
                captured = profile.profile_updates_from_tool(call["name"], call["args"], result)
                if captured:
                    self._mem.set_facts(user_id, captured, source="tool_call")
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
```

(`profile` is already imported in `core.py` from Task 5; `self._mem` is the `MemoryStore`.)

- [ ] **Step 8: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -v`
Expected: PASS (all, including the new capture tests and the unchanged tool-loop/memory tests).

- [ ] **Step 9: Commit**

```bash
git add agronaut_agent/profile.py agronaut_agent/core.py agronaut_agent/tests/test_profile.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): deterministically capture validated tool args into the profile"
```

---

## Notes for the implementer

- **Run tests with the venv:** always `.venv/bin/python -m pytest ...` (deps live in `.venv`).
- **`:memory:` SQLite** is fine for tool tests (Task 4) because `MemoryStore` only needs one
  connection; the core/dry-run tests use `tmp_path` files, matching the existing suite.
- **Do not touch `aqua_model` or `validate_design_input`** — the trust gate is out of scope.
- **Profile vs memories:** structured facts → `update_profile`/`user_facts`; episodic notes →
  `remember_about_user`/`memories`. Keep them separate.

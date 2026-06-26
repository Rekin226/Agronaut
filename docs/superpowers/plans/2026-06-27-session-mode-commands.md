# Session-Mode Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/design`, `/optimize`, `/troubleshoot` Telegram commands that let the user explicitly set the consultation goal, and register all commands in Telegram's `/` menu.

**Architecture:** The goal-setting logic lives in the channel-agnostic agent/profile layer (so it serves Telegram, the REPL, and any future channel and is testable without the live bot). `profile.py` gains the per-goal prompt vocabulary + an `essentials_hint` renderer; `core.py` gains `set_goal`; the Telegram adapter gains three thin handlers, a single `_command_specs()` source of truth for both handler registration and the `/` menu, and a `post_init` hook that calls `set_my_commands`.

**Tech Stack:** Python 3.12, python-telegram-bot v21 (`CommandHandler`, `BotCommand`, `Application.post_init`), pytest. No new dependencies.

## Global Constraints

- Goals are exactly: `design`, `optimize`, `troubleshoot` (the existing `profile.GOALS`).
- Setting a mode writes `profile['goal']` with `source="user_stated"`.
- Mode switch must NOT reset the conversation or wipe facts — it only refocuses the goal.
- The new commands enforce the same allowlist gate (`self._allowed`) as every other handler.
- All commands (the new three **and** existing `whoami`/`reset`/`forget`) must be registered in Telegram's `/` menu via `set_my_commands`.
- `set_my_commands` failure at startup must be non-fatal (log + continue; commands still work by typing).
- Adapter passes `str(update.effective_chat.id)` as the `channel_user` to agent methods (matches the existing handlers).
- Deterministic tests — no live LLM and no running the bot (`run_polling` is blocking; the suite does not exercise it).
- No new dependencies.
- Work on branch `feat/session-mode-commands` (already checked out). Commit after every task.

---

### Task 1: Profile prompt vocabulary + `essentials_hint`

**Files:**
- Modify: `agronaut_agent/profile.py` (append after the existing `render_profile` definitions)
- Test: `agronaut_agent/tests/test_profile.py`

**Interfaces:**
- Consumes: `GOALS`, `GOAL_ESSENTIALS`, `missing_essentials` (already in `profile.py`).
- Produces:
  - `GOAL_HEADERS: dict[str, str]` — emoji + mode name per goal.
  - `GOAL_PROMPTS: dict[str, str]` — the full "what to share" sentence per goal.
  - `essentials_hint(goal: str, facts: dict) -> str` — for `troubleshoot`, the symptom prompt; for `design`/`optimize`, the full prompt when nothing is known, a terse "Also tell me: …" list when partially known, or a "ready, say go" line when all essentials are present.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_profile.py`:

```python
def test_goal_headers_and_prompts_cover_all_goals():
    for g in profile.GOALS:
        assert g in profile.GOAL_HEADERS
        assert g in profile.GOAL_PROMPTS


def test_essentials_hint_troubleshoot_is_symptom_prompt():
    hint = profile.essentials_hint("troubleshoot", {})
    assert "symptom" in hint.lower()


def test_essentials_hint_design_empty_profile_is_full_prompt():
    hint = profile.essentials_hint("design", {})
    assert hint == profile.GOAL_PROMPTS["design"]


def test_essentials_hint_design_partial_lists_only_missing():
    # temperature known -> not re-asked; the rest are still missing
    hint = profile.essentials_hint("design", {"temperature_c": "26"})
    assert hint.startswith("Also tell me:")
    assert "water temp" not in hint          # known field omitted
    assert "fish species" in hint and "crop" in hint


def test_essentials_hint_full_profile_says_ready():
    facts = {"grow_area_m2": "10", "temperature_c": "26",
             "water_budget_lpd": "200", "objective": "protein"}
    hint = profile.essentials_hint("optimize", facts)
    assert "go" in hint.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -k "essentials_hint or goal_headers" -v`
Expected: FAIL — `AttributeError: module 'agronaut_agent.profile' has no attribute 'GOAL_HEADERS'`

- [ ] **Step 3: Write minimal implementation**

Append to `agronaut_agent/profile.py`:

```python
# --- session-mode (/design, /optimize, /troubleshoot) prompt vocabulary -------------
GOAL_HEADERS: dict[str, str] = {
    "design": "🌱 Design mode",
    "optimize": "⚖️ Optimize mode",
    "troubleshoot": "🔧 Troubleshoot mode",
}

# The full "what to share" sentence for a goal, used when nothing is known yet (and, for
# troubleshoot, always — it has no hard slots).
GOAL_PROMPTS: dict[str, str] = {
    "design": "Tell me: fish species, crop, grow area (m²), water temp, and daily water budget.",
    "optimize": "Tell me: grow area (m²), water temp, daily water budget, and objective "
                "(food / protein / water_efficiency).",
    "troubleshoot": "What's going wrong? Share the symptom plus any water readings — "
                    "temp, pH, DO, ammonia.",
}

# Short friendly labels for the still-missing essentials list (no units — this is a prompt,
# not the recall display, so it stays distinct from _LABELS above).
_ESSENTIAL_LABELS: dict[str, str] = {
    "fish_species": "fish species",
    "crop": "crop",
    "grow_area_m2": "grow area (m²)",
    "temperature_c": "water temp",
    "water_budget_lpd": "daily water budget",
    "objective": "objective (food / protein / water_efficiency)",
}


def essentials_hint(goal: str, facts: dict) -> str:
    """What to ask the user for next, given the goal and what's already known.

    troubleshoot -> the symptom prompt (no hard slots). design/optimize -> the full prompt
    when nothing is known, a terse list of only the missing slots when partially known, or a
    'ready' line when every essential is present."""
    g = (goal or "").strip().lower()
    if g == "troubleshoot":
        return GOAL_PROMPTS["troubleshoot"]
    missing = missing_essentials(g, facts)
    if not missing:
        return "I've got your system — want me to run it? Just say go."
    if len(missing) == len(GOAL_ESSENTIALS.get(g, ())):
        return GOAL_PROMPTS[g]
    return "Also tell me: " + ", ".join(_ESSENTIAL_LABELS.get(k, k) for k in missing) + "."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_profile.py -v`
Expected: PASS (all profile tests)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/profile.py agronaut_agent/tests/test_profile.py
git commit -m "feat(profile): per-goal mode headers, prompts, and essentials_hint"
```

---

### Task 2: `AgronautAgent.set_goal`

**Files:**
- Modify: `agronaut_agent/core.py` (add a method next to `reset`/`forget_everything`, ~line 195)
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `profile.GOALS`, `profile.GOAL_HEADERS`, `profile.essentials_hint` (Task 1); existing `self._conv.get_or_create_user`, `self._mem.set_fact`, `self._mem.get_facts`.
- Produces: `AgronautAgent.set_goal(channel: str, channel_user: str, goal: str) -> str` — persists `profile['goal']` (`source="user_stated"`) and returns the user-facing confirmation (`"<header>. <hint>"`). Raises `ValueError` for a goal not in `profile.GOALS`.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
def test_set_goal_persists_and_confirms(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    msg = agent.set_goal("cli", "mode", "design")
    assert "Design mode" in msg
    assert agent._mem.get_facts("cli:mode")["goal"] == "design"


def test_set_goal_does_not_reset_conversation(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    agent.handle_message("cli", "mode2", "hi")          # one turn of history
    agent.set_goal("cli", "mode2", "troubleshoot")
    # history survives a mode switch (mode only refocuses the goal)
    assert len(agent._conv.recent_messages("cli:mode2")) >= 1


def test_set_goal_rejects_unknown_goal(tmp_path):
    import pytest
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    with pytest.raises(ValueError):
        agent.set_goal("cli", "mode3", "frobnicate")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py -k set_goal -v`
Expected: FAIL — `AttributeError: 'AgronautAgent' object has no attribute 'set_goal'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py`, add this method to `AgronautAgent` (place it right after `forget_everything`):

```python
    def set_goal(self, channel: str, channel_user: str, goal: str) -> str:
        """Explicitly set the consultation goal (backs the /design, /optimize, /troubleshoot
        commands). Persists profile['goal'] and returns the user-facing confirmation. Does
        NOT touch conversation history or other facts. Raises ValueError on an unknown goal."""
        g = (goal or "").strip().lower()
        if g not in profile.GOALS:
            raise ValueError(f"unknown goal {goal!r}")
        user_id = self._conv.get_or_create_user(channel, channel_user)
        self._mem.set_fact(user_id, "goal", g, source="user_stated")
        facts = self._mem.get_facts(user_id)
        return f"{profile.GOAL_HEADERS[g]}. {profile.essentials_hint(g, facts)}"
```

(`profile` is already imported in `core.py`; `self._conv`/`self._mem` already exist.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py -k set_goal -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): set_goal — explicit consultation-mode selection"
```

---

### Task 3: Telegram command handlers, `/` menu, and help

**Files:**
- Modify: `agronaut_agent/channels/telegram_adapter.py`
- Test: `agronaut_agent/tests/test_telegram_adapter.py` *(new)*

**Interfaces:**
- Consumes: `AgronautAgent.set_goal` (Task 2).
- Produces:
  - `TelegramAdapter._command_specs() -> list[tuple[str, callable, str]]` — single source of `(command, handler, menu description)` for both handler registration and the `/` menu.
  - Handlers `_on_design`, `_on_optimize`, `_on_troubleshoot` (via the shared `_set_mode` helper).
  - `_post_init(app)` registering `set_my_commands`.

- [ ] **Step 1: Write the failing test**

Create `agronaut_agent/tests/test_telegram_adapter.py`:

```python
"""The Telegram adapter's command wiring — verified without running the bot."""

from agronaut_agent.channels.telegram_adapter import TelegramAdapter


def _adapter():
    # token bypasses env lookup; allowed_ids=[] -> open (we only inspect wiring here).
    return TelegramAdapter(agent=object(), token="x:y", allowed_ids=[])


def test_command_specs_include_mode_commands():
    names = [c for c, _h, _desc in _adapter()._command_specs()]
    for cmd in ("design", "optimize", "troubleshoot"):
        assert cmd in names


def test_command_specs_keep_existing_commands_for_menu():
    names = [c for c, _h, _desc in _adapter()._command_specs()]
    for cmd in ("start", "help", "whoami", "reset", "forget"):
        assert cmd in names


def test_every_command_spec_has_a_callable_handler_and_description():
    for cmd, handler, desc in _adapter()._command_specs():
        assert callable(handler), cmd
        assert isinstance(desc, str) and desc, cmd


def test_mode_handlers_exist():
    a = _adapter()
    for attr in ("_on_design", "_on_optimize", "_on_troubleshoot", "_set_mode", "_post_init"):
        assert hasattr(a, attr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_telegram_adapter.py -v`
Expected: FAIL — `AttributeError: 'TelegramAdapter' object has no attribute '_command_specs'`

- [ ] **Step 3: Implement the handlers, specs, and post_init**

In `agronaut_agent/channels/telegram_adapter.py`:

(a) Extend the telegram import (currently `from telegram import Update`):

```python
from telegram import Update, BotCommand
```

(b) Add the mode handlers and the shared helper. Place them right after `_on_reset` (before `_on_text`):

```python
    async def _set_mode(self, update: Update, goal: str) -> None:
        if not self._allowed(update):
            return await self._deny(update)
        msg = await asyncio.to_thread(
            self.agent.set_goal, self.channel_name, str(update.effective_chat.id), goal
        )
        await update.message.reply_text(msg)

    async def _on_design(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_mode(update, "design")

    async def _on_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_mode(update, "optimize")

    async def _on_troubleshoot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_mode(update, "troubleshoot")
```

(c) Add `_command_specs` and `_post_init` (place them just before `run`):

```python
    def _command_specs(self):
        """Single source of (command, handler, menu description) — drives both handler
        registration and the Telegram / command menu, so the two never drift."""
        return [
            ("start", self._on_start, "What Agronaut is"),
            ("help", self._on_help, "Show help"),
            ("design", self._on_design, "Mode: size a new system"),
            ("optimize", self._on_optimize, "Mode: best fish/crop ratio"),
            ("troubleshoot", self._on_troubleshoot, "Mode: diagnose a problem"),
            ("whoami", self._on_whoami, "What I remember about you"),
            ("reset", self._on_reset, "Clear this conversation"),
            ("forget", self._on_forget, "Wipe everything I know"),
        ]

    async def _post_init(self, app: Application) -> None:
        """Register the / command menu once the app is up. Non-fatal on failure."""
        commands = [BotCommand(c, desc) for c, _h, desc in self._command_specs()]
        try:
            await app.bot.set_my_commands(commands)
        except Exception:  # transient network etc. — commands still work by typing
            log.warning("set_my_commands failed; commands still work by typing", exc_info=True)
```

(d) Replace the body of `run` (currently builds the app and adds handlers one by one) with:

```python
    def run(self) -> None:
        app = Application.builder().token(self.token).post_init(self._post_init).build()
        for name, handler, _desc in self._command_specs():
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        scope = f"{len(self.allowed_ids)} allowed id(s)" if self.allowed_ids else "OPEN (no allowlist)"
        log.info("Agronaut Telegram bot starting — %s", scope)
        app.run_polling()
```

(e) Update the `/help` text. In `_on_help`, replace the `"Commands:\n"` block so it lists the modes:

```python
            "Commands:\n"
            "/design — size a new system\n"
            "/optimize — best fish/crop ratio\n"
            "/troubleshoot — diagnose a problem\n"
            "/whoami — what I remember about you\n"
            "/reset — clear this conversation (keeps long-term memory)\n"
            "/forget — wipe everything I know about you",
```

- [ ] **Step 4: Run the new test + full suite**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_telegram_adapter.py -v`
Expected: PASS (4 passed)

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all — no regressions)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/channels/telegram_adapter.py agronaut_agent/tests/test_telegram_adapter.py
git commit -m "feat(telegram): /design /optimize /troubleshoot commands + / menu registration"
```

---

### Task 4: Live smoke test + README note

**Files:**
- Modify: `README.md`
- No test file (live verification + docs).

**Interfaces:**
- Consumes: the whole feature; a configured NVIDIA provider (already in `.env`).

- [ ] **Step 1: Drive the mode command through the agent against the live model**

Run a non-interactive check (REPL has no command parsing; exercise the seam `set_goal` + a follow-up `handle_message` directly):

```bash
PYTHONPATH=/home/rekin226/Desktop/code_space/Agronaut .venv/bin/python -c "
import agent
from agronaut_agent.core import AgronautAgent
a = AgronautAgent(db_path='/tmp/mode_smoke.sqlite3')
print(a.set_goal('cli','m','optimize'))
print(a._mem.get_facts('cli:m'))
"
```
Expected: prints "⚖️ Optimize mode. Tell me: …" and a facts dict containing `'goal': 'optimize'`.

- [ ] **Step 2: Add the README note**

Under the "### Consultative agent" subsection in `README.md`, append:

```markdown
You can also set the mode explicitly with `/design`, `/optimize`, or `/troubleshoot` —
the bot then jumps straight to gathering what that goal needs. All commands appear in
Telegram's `/` menu.
```

- [ ] **Step 3: Run the full suite one last time**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the session-mode commands"
```

---

## Notes for the implementer

- **Run tests with the venv:** always `.venv/bin/python -m pytest ...`.
- **Do not run the bot in tests** — `run_polling()` blocks. The adapter is verified structurally via `_command_specs()` and the live smoke test only.
- **`TelegramAdapter(agent=object(), token="x:y", allowed_ids=[])`** constructs without env or network — `_command_specs()` only references bound methods, it does not call the agent.
- Follow existing adapter patterns (allowlist check first, `asyncio.to_thread` for agent calls, `str(update.effective_chat.id)` as the channel user).

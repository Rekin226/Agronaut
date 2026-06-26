# Agronaut — Session-Mode Commands (Design)

**Date:** 2026-06-27
**Status:** Approved for planning
**Scope:** Telegram slash commands that let the user explicitly pick the consultation goal.

---

## 1. Problem

The consultative agent infers the user's goal (design / optimize / troubleshoot) from
conversation. Users want to **set it explicitly** so the bot skips the "what are you trying
to do?" step and goes straight to gathering that goal's essentials. Today the only commands
are `/start /help /whoami /reset /forget`, and none of the bot's commands appear in
Telegram's `/` menu.

This builds directly on the existing consultative core: `profile['goal']` already drives the
consultation (via `missing_essentials`), so a command that sets it is most of the feature.

---

## 2. Behavior

Three new commands — `/design`, `/optimize`, `/troubleshoot`. Each:

1. Sets `profile['goal']` to that goal with `source="user_stated"` (an explicit choice,
   stronger provenance than the LLM's inference).
2. Replies with a confirmation that names the mode and states what to provide next, derived
   from the goal's essentials and **omitting anything already known** (reusing
   `missing_essentials`):
   - **/design** → "🌱 Design mode. Tell me: fish species, crop, grow area (m²), water temp,
     daily water budget." (minus known fields)
   - **/optimize** → grow area, water temp, daily water budget, objective (minus known).
   - **/troubleshoot** (no hard slots) → "🔧 Troubleshoot mode. What's going wrong? Share the
     symptom plus any water readings — temp, pH, DO, ammonia."
   - If all essentials are already known → "I've got your system — want me to run it? Just
     say go."

**Does NOT reset.** Switching mode refocuses the goal only; the System Profile and
conversation history persist. A clean slate is still `/reset` (conversation) or `/forget`
(everything).

**Why this is the whole feature:** because `profile['goal']` already steers the
consultation, setting it by command makes the bot skip goal-discovery and jump to essentials
— exactly the point of letting the user pick the mode.

---

## 3. Components

### Agent layer (channel-agnostic — serves Telegram, the REPL, any future channel)

`agronaut_agent/core.py`:
- New method `AgronautAgent.set_goal(channel, channel_user, goal) -> str`. Resolves/creates
  the user, writes `profile['goal']` via `MemoryStore.set_facts(..., source="user_stated")`,
  and returns the confirmation text. Goal is validated against `profile.GOALS`; an unknown
  goal raises `ValueError` (callers only ever pass the three literals, so this is a guard,
  not a user-facing path).

`agronaut_agent/profile.py` (owns canonical keys, labels, essentials — so the rendering
vocabulary stays in one place):
- `GOAL_PROMPTS: dict[str, str]` — per-goal "what to share" phrasing, including the
  troubleshoot symptom prompt.
- `essentials_hint(goal, facts) -> str` — renders the still-missing essentials with friendly
  labels (reusing the existing `_LABELS` + `missing_essentials`), or the "I've got your
  system — say go" line when nothing is missing. For `troubleshoot` (no hard essentials) it
  returns the symptom prompt from `GOAL_PROMPTS`.

### Adapter layer

`agronaut_agent/channels/telegram_adapter.py`:
- One thin helper `_set_mode(update, goal)`: allowlist check → `agent.set_goal(...)` → reply.
- Three handlers (`_on_design`, `_on_optimize`, `_on_troubleshoot`) wired with
  `CommandHandler("design"/"optimize"/"troubleshoot", …)`.
- **Telegram `/` menu:** register all commands via `bot.set_my_commands([...])` at startup
  so they appear in the app's command menu — covering the new three **and** the existing
  `/whoami /reset /forget` (currently absent from the menu). Registration runs in the
  Application `post_init` hook.
- `/help` text updated to list the three modes.

---

## 4. Error handling / edge cases

- **Unknown goal reaches `set_goal`:** raises `ValueError`. Not user-reachable (handlers pass
  fixed literals); it is a programming guard.
- **Mode set with a partially-filled profile:** confirmation lists only the missing
  essentials.
- **Mode set with a fully-known system:** confirmation is the "say go" line; the LLM handles
  the actual run on the next message.
- **`set_my_commands` fails at startup** (transient network): log and continue — the commands
  still work by typing them; only the menu listing is affected.
- **Allowlist:** the new commands enforce the same allowlist gate as every other handler.

---

## 5. Testing

Deterministic, no live LLM — matches the existing suite, which tests the agent/profile layer
(not the async Telegram plumbing).

- **Unit — `profile.essentials_hint` / `GOAL_PROMPTS`:**
  - `design` with a partial profile (e.g. temp known) lists only the missing slots and omits
    the known one.
  - `troubleshoot` returns the symptom prompt.
  - a fully-populated `design`/`optimize` profile returns the "say go" line.
- **Unit — `AgronautAgent.set_goal`:** persists `goal` to the profile (`get_facts` shows it)
  and returns text naming the mode; an unknown goal raises `ValueError`.

The adapter handlers stay thin enough that the agent-level tests cover the logic.

---

## 6. Out of scope (YAGNI — considered and cut)

- Inline tappable buttons / a `/mode` switcher command.
- Any per-mode behavior beyond setting the goal (no verbosity/persona modes).
- Conversation reset on mode switch (explicitly rejected — modes preserve state).

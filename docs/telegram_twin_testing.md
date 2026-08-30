# Testing the twin over Telegram: setup, the script, and what "working" looks like

## Before anything: pulling code does not reload a running bot

A running `python bot.py` keeps executing the code it started with. After `git pull` you
MUST restart the process, and it is worth verifying what the process will actually run:

```bash
git log --oneline -1          # should show the twin merge (PR #97 or later)
pkill -f "python bot.py"      # or stop it however you run it (systemd, tmux, ...)
python bot.py
```

`/help` in Telegram is the quickest freshness check: the new build advertises
*Simulate*, *Mirror ... LIVE*, *Estimate*, and *Show ... in 3D*. If `/help` does not
mention Simulate, the process is still running old code.

## The model matters — and the default was the problem

The twin flows are multi-step tool conversations, and two measured facts about the
NVIDIA free tier (2026-08, this repo's own benchmark) decide the experience:

| model | one LLM round-trip | tool selection on twin flows |
|---|---|---|
| `meta/llama-3.3-70b-instruct` (old default) | **~56 s** | correct |
| `mistralai/mistral-nemotron` | **~2-3 s** | correct |
| `meta/llama-3.1-8b-instruct` (the fallback) | ~1 s | shaky on complex flows |

A turn uses 2-6 round-trips, so the old default produced **3-5 minute replies**, and under
free-tier congestion (503s are routine) every failed call silently fell back to the 8B
model — which answers in text instead of calling tools. That combination reads exactly as
"the twin doesn't work, I only get text." The recommended config:

```
LLM_PROVIDER=nvidia
LLM_MODEL=mistralai/mistral-nemotron
```

Set it in the server's `.env` (it is NOT in git — pulling does not change it) and restart.

**What five full validation runs taught about the free tier, so nobody relearns it:**
model behavior is weather. The same model, same prompt, hours apart, produced: correct
structured tool calls; tool calls leaked as plain text (two dialects); "I'll log that
now" narration with no call; and once, plain confident fabrication of a cost figure.
The agent now carries guards for every *marked* failure (a fabrication tripwire, a
leaked-call rescue parser, a promise-without-action nudge — each born from a measured
incident and unit-tested), and the primary is retried before any fallback. But no guard
catches unmarked confident fiction from a small model, and no free tier promises a
response window. Hence the two-layer design:

- **Conversational layer** — best-effort, guarded, and self-auditable:
  `python scripts/validate_telegram_flows.py` runs the six flows against the live model
  and names exactly which ones it fumbles today.
- **Command layer — the guarantee.** `/log` and `/forecast` drive the live twin by
  direct tool invocation with NO LLM in the path. They work identically in every model
  weather, including a fully dead endpoint.

## The automated check, before the phone

`python scripts/validate_telegram_flows.py` runs the six-step conversation below through
the bot's exact brain against the real configured LLM and reports which tools actually
fired. Run it on the server after any pull: 6/6 means the conversational path works right
now; misses name exactly which flow the current model is fumbling. `/log` and `/forecast`
work regardless — they never touch the LLM.

## The test script — six messages, in order

Each step names the tool that should fire and what arriving proof looks like. Watch the
server log too: every tool call is logged.

1. **"I want to design a system with catfish and lettuce, about 15 m², media bed,
   in Bobo-Dioulasso. My power is sometimes unreliable and I'm still learning."**
   → after it gathers water budget/temperature: `design_full_system`. Proof: a component
   list with reasons ("Architecture: COUPLED... settling tank — ..."), a power warning,
   and a **3D HTML file delivered as a document** you can open in the phone browser.
2. **"How much will it produce over a year?"**
   → `fetch_site_climate` (first time only — it geocodes the town and pulls real NASA
   weather; no commands to run), then `simulate_season`. Proof: a season projection with
   kg fish, kg crop, the limiting factor, and honesty lines.
3. **"What would it cost to build here, and does it make money if I sell at the market?"**
   → `estimate_system_cost` / `business_case` (channel='direct'). Proof: ranged costs in
   XOF with sources, margin, payback — or a plainly stated loss.
4. **"My system is running: 2000 L tank, 60 catfish around 200 g."**
   → `update_profile` saves it. Then:
5. **"Today I measured ammonia 0.5, nitrate 40, water 27."**
   → `log_my_readings`. Proof: drift notes — "model was X% low on nitrate — pulled toward
   yours" — and a one-line snapshot. This is the LIVE twin: its state now persists.
6. **"How's my system doing? What will this week do to it?"**
   → `my_system_forecast`. Proof: "advanced N day(s) through your site's real weather" and
   a forecast over the actual coming days.

## When a step gives only text

- **Check the server log first.** A `primary LLM failed` warning means free-tier
  congestion; the retry usually rides it out, but a hammered hour is a hammered hour.
- **The model may be asking a fair question** — the consultation gathers essentials
  before acting. "Which water budget?" is the design working, not failing.
- **Rephrase toward the concrete.** Small hosted models tool-call best on specific asks
  ("simulate a season for X at Y") and worst on vague ones ("tell me about my system").
- If text-only persists across all six steps after a restart with the recommended model,
  capture the server log of one turn and open an issue — the log shows whether the model
  never called a tool or a tool errored.

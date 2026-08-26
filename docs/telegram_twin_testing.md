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
"the twin doesn't work, I only get text." Two fixes shipped with this doc: the primary is
retried once before falling back, and the recommended config is now:

```
LLM_PROVIDER=nvidia
LLM_MODEL=mistralai/mistral-nemotron
```

Set it in the server's `.env` and restart. (Any tool-capable hosted model works; this one
measured 20x faster with correct tool choice on the twin flows.)

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

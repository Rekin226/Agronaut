# 🌱 Agronaut

**A personal agronomy agent — chat with it to design, optimize, and maintain aquaponics systems.**

Agronaut is a conversational agent (in the spirit of Hermes / OpenClaw) specialized for
agriculture. Its first deep domain is **aquaponics**: it turns the trial-and-error of
designing a fish-and-plant system into a calculated, cited, honest answer — and finds the
fish/crop ratio that squeezes the most food from the least water.

> Built by a hands-on aquaponics operator to cut the pain he lived: years of reading papers
> and losing fish to figure out what the math could have told him up front.

---

## Why it's different from a chatbot

A chatbot retrieves what a paper *said*. Agronaut **computes the answer for your specific
system**. The trustworthy part is a deterministic engineering model — the LLM only collects
facts, routes to the right tool, and explains results in plain language.

```
  YOU ──▶  agent layer (LLM: collect facts, route, explain)
                 │  proposes values
                 ▼
           validation gate  ── rejects bad/uncertain input ──┐
                 │ typed, validated                           │
                 ▼                                            │
        aqua_model  (TRUST ZONE — pure, tested, cited)        │
        coefficients ▸ mass balance ▸ sizing ▸ optimizer  ◀───┘
                 │
                 ▼
        a sized system + bill of materials + operating envelope
        + cited coefficients + an explicit "what's NOT modeled" list
```

The math is verifiable on its own — you can audit every coefficient (with its source)
without trusting the model. Calibration ≠ validation: the engine ships with seed defaults
from published sources, meant to be calibrated against a real running system.

---

## Features

Three modes in the app (sidebar **Mode** switch):

- **Assistant (chat)** — troubleshoot a running system (low DO, yellow leaves, pump sizing…).
- **Design Calculator** — fixed inputs → a fully sized system: tank/system volume, fish
  count, feed/day, pump turnover, biofilter, makeup water, **bill of materials**, **operating
  envelope**, maintenance checklist, and a downloadable funder-ready report.
- **Optimize Ratio** — search fish × crop-mix combinations for the best ratio under your
  binding constraint (e.g. a fixed water budget), maximizing food, protein, or water-use
  efficiency, and showing the gain over a naive even split.

The design and optimizer modes are **fully deterministic and need no LLM at all.**

### Consultative agent

Agronaut runs a consultation, not a one-shot Q&A. It identifies your goal (design a
system, optimize a ratio, or troubleshoot a problem), asks for the few essentials that
goal needs, then gives a first-cut recommendation tied to *your* system — and remembers
it (a typed System Profile + episodic notes) across sessions.

You can also set the mode explicitly with `/design`, `/optimize`, or `/troubleshoot` —
the bot then jumps straight to gathering what that goal needs. All commands appear in
Telegram's `/` menu.

Agronaut also learns from outcomes: after suggesting a fix it can check back later
("did the water change fix the ammonia?"), and whatever worked is remembered and shapes
its future advice.

Lessons can also become shared knowledge: a generalized, PII-stripped version of a verified
fix is nominated, the owner approves it in a local review CLI (`python -m agronaut_agent.review`),
and approved insights then help other operators — labeled as community experience, never as
verified science.

And it calibrates to reality: when you report real measured outcomes (harvest weight, FCR,
crop yield), Agronaut tunes *your* future sizings toward your system — bounded to the
published empirical ranges, so a measurement can only move a coefficient within what the
literature allows, and every calibrated number is labeled.

The deterministic sizing model now covers five fish (tilapia, clarias, channel catfish, trout,
common carp) and 30+ crops — leafy greens (lettuce, kale, chard, spinach, pak choi, arugula,
watercress…), culinary herbs (basil, mint, cilantro, parsley, dill…), and fruiting crops
(tomato, cucumber, pepper, strawberry, eggplant, zucchini…) — each with cited, calibratable
seed coefficients placed within FAO 589's published feeding-rate band for its category.

### Honesty by design
Every result lists the coefficients it used (value + range + **source**: FAO 589,
UVI/Rakocy, literature) and an explicit list of what it does **not** model
(pH/alkalinity, micronutrients, salinity, solids, pests, cohort logic, per-crop ET).
A confidently-wrong design can't masquerade as complete.

---

## The engineering model (aquaponics core)

Parametric, not machine-learned — buildable today from published equations:

- **Feeding-rate ratio (FRR)** sizes the system: grams of feed per m² of plant area/day.
- **Nitrogen balance** is an independent *consistency check* (feed → fish-retained → excreted
  → plants + solids + water-exchange + denitrification), flagging disagreement with FRR
  rather than silently reconciling — this guards against over-sizing the grow beds.
- **Water balance** (evapotranspiration + evaporation + sludge − rainfall) drives the
  water-budget feasibility check.
- **Optimizer** is bounded enumeration over a small species×crop palette (no heavyweight
  solver), with the even-split baseline inside the search space so it can never do worse.

---

## Pluggable LLM backend (open models)

The chat layer is model-agnostic — pick a backend with one env var, no code change:

| Provider | `LLM_PROVIDER` | Notes |
|---|---|---|
| Ollama (local) | `ollama` | Offline, default (`llama3`). Best for low-connectivity / field use. |
| NVIDIA (hosted) | `nvidia` | OpenAI-compatible open models; free tier. Needs `NVIDIA_API_KEY`. |
| Hugging Face | `hf` | Default `Qwen/Qwen2.5-7B-Instruct` (Apache-2.0, strong at JSON). Needs `HUGGINGFACEHUB_API_TOKEN`. |

Override the model with `LLM_MODEL`. Provider libraries are imported lazily — install only
the one you use. The design/optimizer modes run with **no LLM dependency at all.**

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirement.txt
streamlit run app.py
```

Open the sidebar **Mode** switch. The **Design Calculator** and **Optimize Ratio** modes
work immediately (no model server). For **chat**, run Ollama locally or set a hosted
provider (see above).

### Run the tests
```bash
python3 -m pytest        # the aqua_model core suite is pure (no model server needed)
```

### Run the Telegram bot

The consultative agent is reachable over Telegram. Set these (in `.env` or the environment):

| Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from [@BotFather](https://t.me/BotFather) |
| `AGRONAUT_ALLOWED_IDS` | comma-separated Telegram user IDs allowed to use the bot (empty = open to anyone, discouraged) |
| `LLM_PROVIDER` / `NVIDIA_API_KEY` | the tool-calling brain — e.g. `nvidia` (free at [build.nvidia.com](https://build.nvidia.com)) |
| `LLM_MODEL` | optional, e.g. `meta/llama-3.1-70b-instruct` |

```bash
source .venv/bin/activate
python bot.py            # long-polls Telegram; Ctrl-C to stop
```

#### Keep it running (systemd)

For an always-on bot that survives crashes and reboots, run it as a **`systemd --user`
service**. Create `~/.config/systemd/user/agronaut-bot.service`:

```ini
[Unit]
Description=Agronaut Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/Agronaut
ExecStart=/path/to/Agronaut/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=default.target
```

```bash
loginctl enable-linger "$USER"          # run even when you're not logged in
systemctl --user daemon-reload
systemctl --user enable --now agronaut-bot     # start now + on boot
```

Manage it:

```bash
systemctl --user status agronaut-bot     # is it up?
systemctl --user restart agronaut-bot    # after pulling/changing code
journalctl --user -u agronaut-bot -f     # live logs
```

> Only **one** poller may run at a time — a manual `python bot.py` and the service will
> conflict on Telegram's `getUpdates`. When the service owns the bot, restart it after code
> changes (`systemctl --user restart agronaut-bot`) instead of running the script directly.

---

## Project layout

```
aqua_model/          # TRUST ZONE — pure Python, no LLM, no network, fully tested
  coefficients.py    #   cited data layer (value + range + unit + source + safety factor)
  species.py crops.py#   seed databases (calibration-flagged)
  massbalance.py     #   nitrogen consistency check, water balance, biofilter
  sizing.py          #   size_system() — FRR anchors; build-artifact output
  optimizer.py       #   optimize() — best fish/crop ratio under a constraint
  validate.py        #   the trust gate (typed input only)
  report.py          #   funder-facing design report
  logging_schema.py  #   versioned install-logging standard (the dataset moat)
agent/               # LLM-facing layer (imports aqua_model, never the reverse)
  llm.py             #   pluggable backend (ollama | nvidia | hf)
  facts.py           #   UI↔model seam
  calculator_ui.py optimizer_ui.py   # Streamlit views
app.py               # Streamlit app (chat | calculator | optimizer)
srcs/chatbot.py      # the conversational/RAG troubleshooting flow
knowledge/  urls.txt # reference content for RAG
```

---

## Roadmap

- **M1 — design calculator** ✅ deterministic sizing, cited coefficients, report, logging standard
- **M2 — ratio optimizer** ✅ fish/crop mix for max efficiency
- **M3 — agent orchestrator** — refactor chat into a tool-calling agent; RAG → citation tool
- **M4 — digital twin** — time-series simulator calibrated on real installed systems
- **M5 — field maintenance assistant** — phone-friendly, low-connectivity

**Status:** the design + optimizer core is built and tested. The model is *validated* once
it reproduces a real running system within tolerance — that calibration step is the next
milestone (`aqua_model/tests/test_calibration.py`).

---

## License

MIT — see [LICENSE](LICENSE). The code is open by design (it's built on published science);
the value is in calibrated, real-world data, not the equations. Contributions welcome.

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

# Architecture

How Agronaut is built, and the one rule that holds it together. For contributing, see [CONTRIBUTING.md](../CONTRIBUTING.md).

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

## Project layout

```
aqua_model/            # TRUST ZONE — pure Python, no LLM, no network, no I/O, fully tested
  coefficients.py      #   cited data layer (value + range + unit + source + safety factor)
  species.py crops.py  #   seed databases, every field sourced (10 species, 34 crops)
  massbalance.py       #   nitrogen consistency check, water balance, biofilter
  sizing.py            #   size_system() — FRR anchors; build-artifact output
  hydroponics.py       #   size_hydroponic_system() — plants only, no fish
  optimizer.py         #   optimize() — best fish/crop ratio under a constraint
  triage.py            #   visual symptoms -> a ranked, CITED differential (never a verdict)
  calibration.py       #   bound coefficients toward an operator's own measurements
  validate.py          #   the trust gate — the only door into the model
  report.py pilot.py   #   funder-facing design report and pilot proposal
  schematic.py         #   deterministic SVG/PNG system diagram
  layout.py hydraulics.py #   placement, grading, routed pipe runs and the real pump head
  production.py mirror.py #   the coupled season twin, and one operator's live state
  advisory.py          #   proposals with derived confidence — a human approves, nothing acts
  scene3d.py           #   the 3D scene, including the twin state the viewer renders
  logging_schema.py    #   versioned install-logging standard (the dataset moat)

agent/                 # LLM-facing layer (imports aqua_model, never the reverse)
  llm.py               #   pluggable chat backend (ollama | nvidia | hf | openai_compat)
  vision.py            #   pluggable VLM + the observation guard + EXIF stripping
  observation_features.py #   prose -> the categorical vocabulary triage.py accepts
  classifier.py        #   pluggable image classifier as a FEATURE source (no backend yet)
  transcribe.py        #   pluggable speech-to-text
  calculator_ui.py optimizer_ui.py facts.py   # Streamlit views + the UI/model seam

agronaut_agent/        # the channel-agnostic brain
  cli.py               #   the `agronaut` command — one front door, routes to the real callables
  core.py              #   handle_message / handle_image / handle_voice — the three seams
  tools.py             #   the LLM-callable tools (thin wrappers over the trust zone)
  store.py profile.py  #   SQLite memory: System Profile, notes, calibration, follow-ups
  rag.py semantic.py   #   citation-enforced retrieval (floor, hybrid, per-source cap) + recall
  channels/            #   telegram_adapter.py, whatsapp_adapter.py, base.py

scripts/               # safety_eval.py (hermetic golden set, runs in CI), vision_eval.py
                       # faithfulness_eval.py (faithfulness / relevancy / citation accuracy)
                       # corpus_report.py (what each source contributes; --candidate vets one)
                       # retrieval_eval.py (recall/precision/MRR/MAP over the retrieval golden set)
                       # retrieval_sweep.py (re-calibrates floor / cap / beta on the live corpus)
skills/                # the deterministic core as a portable agentskills.io skill + CLI
knowledge/ urls.txt    # the curated, cited knowledge base (urls.txt: CATEGORY|URL|LABEL|LICENCE)
docs/dpg/              # DPG compliance pack: privacy, AI transparency, safety eval
  CORPUS.md            #   corpus provenance + the code(MIT)/content(mixed) licence split
  retrieval_eval/      #   golden set, baselines, and every technique's measured verdict
app.py                 # Streamlit app (chat | calculator | optimizer)
pyproject.toml         # packaging + the `agronaut` console script (deps read from requirement.txt)
srcs/chatbot.py        # legacy RAG/state-machine layer, slated for retirement (#25)
```

## Use it from another agent (agentskills.io skill)

Agronaut's deterministic engine is also packaged as a portable
[agentskills.io](https://agentskills.io) skill in
[`skills/aquaponics_engineer/`](../skills/aquaponics_engineer/SKILL.md), so agents like
Hermes, OpenClaw, or Claude Code can hand users a *computed*, cited design instead of a
guess:

```bash
python -m skills.aquaponics_engineer.cli size-aquaponics \
    --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
```

Same trust zone, same citations, no LLM — a bad input is rejected at the gate.

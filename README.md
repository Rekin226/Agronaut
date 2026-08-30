# 🌱 Agronaut

[![CI](https://github.com/Rekin226/Agronaut/actions/workflows/ci.yml/badge.svg)](https://github.com/Rekin226/Agronaut/actions/workflows/ci.yml)
[![Advice-safety golden set](https://img.shields.io/badge/advice--safety-enforced%20in%20CI-brightgreen)](docs/dpg/safety_eval/golden_set.json)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](requirement.txt)
[![good first issues](https://img.shields.io/github/issues/Rekin226/Agronaut/good%20first%20issue?label=good%20first%20issues&color=7057ff)](https://github.com/Rekin226/Agronaut/labels/good%20first%20issue)

**An open-source AI agronomy agent that runs locally on your own computer or server,
connects to messaging apps like Telegram and WhatsApp, and computes aquaponics system
designs from a deterministic, source-cited engineering core instead of guessing them.**

A self-hostable agent specialized for agriculture: a domain application in the spirit of
Hermes / OpenClaw, rather than another agent framework. Its first deep domain is
**aquaponics**. Describe your water, space and species in one sentence and it returns a
buildable design, with a bill of materials, an operating envelope, a source for every
number, and an explicit list of what it does *not* model. It will also search fish × crop
mixes for the ratio that grows the most food from the least water.

> Built by a hands-on aquaponics operator to cut the pain he lived: years of reading papers
> and losing fish to figure out what the math could have told him up front.

The sizing method behind it is a granted Taiwan utility model patent (**TW M661364**).
The code is MIT, runs on open weights, and needs no proprietary API.

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

### Send it a photo

Photograph a yellowing leaf, a sick fish, or green water — on **Telegram, WhatsApp, or the
web chat** — and you get a *cited differential*, not a guess:

1. A vision model describes what it sees. It only **observes**.
2. A deterministic guard strips any measurement or prescription out of that description, so a
   fabricated `pH 6.4` or an invented `add 5 mL of salt` can never enter the conversation as
   though you had said it. A named condition is kept but flagged **unverified**.
3. A fixed, cited table (`aqua_model/triage.py`) maps the visible symptoms to a **ranked list
   of candidate causes** — each naming the knowledge document it came from, and each with the
   checks that would tell it apart from its neighbours.

It will not hand you a single confident diagnosis, because a photograph cannot support one:
iron deficiency and pH lockout look identical in an image, so you get both plus the check that
separates them. Ordering follows the knowledge base's own rules — pH before iron, water quality
before any fish pathogen. Nothing it says states a dose.

### Voice notes

Speak instead of typing, on Telegram or WhatsApp. The transcript runs through a normal turn, so
memory, tools and cited knowledge all apply.

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

The same rule governs the advice layer. Citation is enforced **in code**, not asked for in a
prompt: every retrieved passage is labelled with its source before the model ever sees it. And
retrieval is allowed to say *no* — a question the corpus cannot answer returns "no matching
passages" rather than the three closest paragraphs wearing source labels. Ask Agronaut the capital
of Canada and it will decline, not cite an aquaponics paper at you.

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

## The advice layer (retrieval), and how it was tuned

Sizing is computed. Troubleshooting advice is *retrieved*, from a corpus of 22 hand-written
operator guides plus openly licensed publications — currently **1354 chunks**, led by FAO 589.

Retrieval is measured, not assumed. `docs/dpg/retrieval_eval/golden_set.json` holds queries in
real operator voice ("my tilapia are gasping at the surface", not "dissolved oxygen") plus
off-topic controls that must be **refused**:

```bash
python -m scripts.retrieval_eval     # recall@k, precision@k, MRR, MAP@k + floor separation
python -m scripts.corpus_report      # what each declared source actually contributes
```

Eight techniques were implemented and measured. **Four ship; four lose** — and the losses are
recorded in `docs/dpg/retrieval_eval/techniques.json` with the conditions that would reverse them,
which is how hybrid search went from rejected to shipped when the corpus grew:

| | ships | why |
|---|---|---|
| Relevance floor | **on** | refuses 8/10 off-topic queries, silences 0/33 real ones |
| Hybrid BM25 + RRF | **on** (β=0.90) | lost at 362 chunks, won at 1354: recall/MAP +0.091 |
| Per-source cap | **on** (2) | one 992-chunk book was taking all 3 slots on 10 of 33 queries |
| PDF cleaning | **on** | drops contents pages; running header removed from 111 chunks → 4 |
| Header chunking · context prefix · PDF chapter labels · cross-encoder rerank | off | each measured *worse* on this corpus |

Three of the four failures share one mechanism: they add topic words to chunks in a corpus where
every document already shares a vocabulary domain, which dilutes rather than disambiguates. What
worked was structural — refusing irrelevant passages, refusing error pages, refusing to let one
source fill the whole answer.

**Corpus licensing is mixed and deliberately explicit.** The code is MIT; FAO 589 is
non-commercial-only. See [`docs/dpg/CORPUS.md`](docs/dpg/CORPUS.md) — commercial users should drop
that entry from `urls.txt` and rebuild. Vet any source before adding it:

```bash
python -m scripts.corpus_report --candidate "<url>" --label "<expected topic>"
```

It checks four things, because a source can fail in four ways: unreachable, empty, **wrong
subject** (a guessed publication ID once resolved to *"Sharks for the Aquarium"* — 28k characters
that pass every check except being about aquaponics), or not openly licensed.

---

## Pluggable LLM backend (open models)

The chat layer is model-agnostic — pick a backend with one env var, no code change:

| Provider | `LLM_PROVIDER` | Notes |
|---|---|---|
| Ollama (local) | `ollama` | Offline, default (`llama3`). Best for low-connectivity / field use. |
| NVIDIA (hosted) | `nvidia` | OpenAI-compatible open models; free tier. Needs `NVIDIA_API_KEY`. |
| Hugging Face | `hf` | Default `Qwen/Qwen2.5-7B-Instruct` (Apache-2.0, strong at JSON). Needs `HUGGINGFACEHUB_API_TOKEN`. |
| Self-hosted (OpenAI-compatible) | `openai_compat` | **Zero proprietary API** — point `OPENAI_COMPAT_BASE_URL` at your own vLLM / llama.cpp / LM Studio / TGI server. Drives the full tool-calling agent with an open-weights model you host. |

### Self-hosted, no vendor (the open-weights path)

For a deployment with no hosted API at all, serve an open-weights model with an
OpenAI-compatible server and point Agronaut at it:

```bash
# example: vLLM serving a tool-calling-capable open model
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct
# then:
export LLM_PROVIDER=openai_compat
export OPENAI_COMPAT_BASE_URL=http://localhost:8000/v1
python bot.py
```

This runs the deterministic core **and** the tool-calling assistant with no proprietary
dependency — the configuration Agronaut submits for [Digital Public Good](docs/dpg/)
platform-independence.

Override the model with `LLM_MODEL`. Provider libraries are imported lazily — install only
the one you use. The design/optimizer modes run with **no LLM dependency at all.**

---

## Quick start

### Docker (one command)

```bash
docker compose up web            # Streamlit at http://localhost:8501
docker compose --profile bot up  # web + the Telegram bot (needs .env)
```

The web app's Design Calculator and Optimizer work immediately. Chat and the bot need an
LLM provider configured in a local `.env` (see below). The SQLite memory DB persists in a
named volume, shared between web and bot.

### From source

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # installs the deps and the `agronaut` command
streamlit run app.py
```

Open the sidebar **Mode** switch. The **Design Calculator** and **Optimize Ratio** modes
work immediately (no model server). For **chat**, run Ollama locally or set a hosted
provider (see above).

(`pip install -r requirement.txt` still works if you only want the libraries — `pip install -e .`
installs the same list and adds the command below. For a **deterministic-only** install with no
chat stack, `requirements.txt` is a light manifest covering just the calculator and optimizer.)

### Deploy a hosted demo (free, ~2 min)

The deterministic modes deploy to [Streamlit Community Cloud](https://share.streamlit.io) with
zero config — the light `requirements.txt` keeps the build fast and key-free:

1. Fork this repo (or use your own).
2. Streamlit Community Cloud → **New app** → pick the repo, branch `main`, main file `app.py`.
3. Deploy. Chat mode shows a friendly "needs the chat stack" note; the calculator and
   optimizer are fully live.

See [`docs/demo.md`](docs/demo.md) for the deployment details and for recording a README GIF.

### The `agronaut` command

One front door over everything the project ships. It works from any directory once
installed; bare `agronaut` is the chat REPL, because that is what most people want.

```bash
agronaut                         # chat with the agent in the terminal
agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
agronaut size-hydro --crop lettuce --area 10 --temp 22 --water 500
agronaut optimize --area 10 --temp 28 --water 5000 --objective food
agronaut list                    # supported species, crops, objectives
agronaut web                     # the Streamlit app (trailing flags go to streamlit,
                                 #   e.g. agronaut web --server.port=9000)
agronaut bot                     # the Telegram bot
agronaut review                  # approve/reject pending community insights
agronaut analytics               # usage summary
```

**Where it keeps things.** In a checkout, the knowledge base, the fetched-page cache and the
SQLite memory DB all sit beside the source, as before. Installed non-editably, the cited
corpus is read from `<prefix>/share/agronaut` and state goes to your XDG directories rather
than into `site-packages` — override any of it with `AGRONAUT_CORPUS_DIR`,
`AGRONAUT_CACHE_DIR`, or `AGRONAUT_DATA_DIR`.

| Command | Needs an LLM? |
|---|---|
| `size` / `size-hydro` / `optimize` / `list` | **No.** Pure `aqua_model` — deterministic, offline, cited. Bad input exits non-zero at the trust gate rather than guessing. |
| `chat` (the default) / `bot` | Yes — a tool-calling provider (see above). |
| `web` | Only for the app's chat mode; the calculator and optimizer run without one. |

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
| `AGRONAUT_RELEVANCE_MAX_DISTANCE` | how far a passage may be and still be used as context (default 1.65, `off` disables). Calibrated against the golden set; **not portable** — re-read `python -m scripts.retrieval_eval` after any corpus or embedding-model change |
| `AGRONAUT_HYBRID` / `AGRONAUT_HYBRID_BETA` | keyword+semantic fusion, on by default at β=0.90 (β is the semantic weight) |
| `AGRONAUT_MAX_PER_SOURCE` | how many passages one source may contribute to a single answer (default 2; `1` favours breadth, `0` disables) |
| `AGRONAUT_INDEX_CACHE` | the built index is cached under `data/.index_cache/`, keyed by a corpus fingerprint; `off` rebuilds every time |
| `AGRONAUT_RERANK` / `AGRONAUT_MD_HEADERS` / `AGRONAUT_PDF_SECTIONS` | techniques that measured *worse* on this corpus and ship disabled — kept because the verdict is corpus-dependent (see `docs/dpg/retrieval_eval/techniques.json`) |
| `LLM_PROVIDER` / `NVIDIA_API_KEY` | the tool-calling brain — e.g. `nvidia` (free at [build.nvidia.com](https://build.nvidia.com)) |
| `LLM_MODEL` | optional, e.g. `meta/llama-3.1-70b-instruct` |
| `VLM_PROVIDER` / `VLM_MODEL` | optional photo understanding — send a picture of a sick fish or yellowing leaf and the bot describes it, then diagnoses through the same cited flow. Defaults to a hosted NVIDIA vision model; `AGRONAUT_VISION=off` disables. The vision model only *observes*: a deterministic guard strips any reading or prescription out of its description, and the diagnosis itself comes from a fixed, cited triage table (`aqua_model/triage.py`) that returns a ranked differential — never a single verdict. Photos work on Telegram, WhatsApp, and the web chat. |
| `ASR_PROVIDER` / `ASR_MODEL` | optional voice notes — a spoken message is transcribed then answered in the same language. Defaults to a **local** faster-whisper model (works offline — best for low-connectivity field use; needs `pip install faster-whisper`). Set `ASR_PROVIDER=nvidia` for a hosted endpoint; `AGRONAUT_VOICE=off` disables. |

```bash
source .venv/bin/activate
agronaut bot             # long-polls Telegram; Ctrl-C to stop (same as `python bot.py`)
```

### Run on WhatsApp (Cloud API)

Agronaut also speaks WhatsApp — the channel most smallholder-facing programs reach farmers
on. It uses Meta's WhatsApp Cloud API (webhook in, Graph API out) and needs a WhatsApp
Business account. Set:

| Var | Purpose |
|---|---|
| `WHATSAPP_TOKEN` | permanent access token |
| `WHATSAPP_PHONE_NUMBER_ID` | the sender phone-number id |
| `WHATSAPP_VERIFY_TOKEN` | any string; also entered in Meta's webhook config |
| `WHATSAPP_APP_SECRET` | app secret, used to verify inbound request signatures |

```python
from agronaut_agent.core import AgronautAgent
from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
WhatsAppAdapter(AgronautAgent()).run()   # serves the webhook + a follow-up poller
```

Point Meta's webhook at `https://your-host/` (put the process behind HTTPS — a reverse
proxy or tunnel). The same brain, memory, tools, and follow-ups as Telegram.

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
aqua_model/            # TRUST ZONE — pure Python, no LLM, no network, no I/O, fully tested
  coefficients.py      #   cited data layer (value + range + unit + source + safety factor)
  species.py crops.py  #   seed databases, every field sourced (5 species, 30 crops)
  massbalance.py       #   nitrogen consistency check, water balance, biofilter
  sizing.py            #   size_system() — FRR anchors; build-artifact output
  hydroponics.py       #   size_hydroponic_system() — plants only, no fish
  optimizer.py         #   optimize() — best fish/crop ratio under a constraint
  triage.py            #   visual symptoms -> a ranked, CITED differential (never a verdict)
  calibration.py       #   bound coefficients toward an operator's own measurements
  validate.py          #   the trust gate — the only door into the model
  report.py pilot.py   #   funder-facing design report and pilot proposal
  schematic.py         #   deterministic SVG/PNG system diagram
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
                       # corpus_report.py (what each source contributes; --candidate vets one)
                       # retrieval_eval.py (recall/precision/MRR/MAP over the retrieval golden set)
skills/                # the deterministic core as a portable agentskills.io skill + CLI
knowledge/ urls.txt    # the curated, cited knowledge base (urls.txt: CATEGORY|URL|LABEL|LICENCE)
docs/dpg/              # DPG compliance pack: privacy, AI transparency, safety eval
  CORPUS.md            #   corpus provenance + the code(MIT)/content(mixed) licence split
  retrieval_eval/      #   golden set, baselines, and every technique's measured verdict
app.py                 # Streamlit app (chat | calculator | optimizer)
pyproject.toml         # packaging + the `agronaut` console script (deps read from requirement.txt)
srcs/chatbot.py        # legacy RAG/state-machine layer, slated for retirement (#25)
```

---

## Roadmap

- **M1 — design calculator** ✅ deterministic sizing, cited coefficients, report, logging standard
- **M2 — ratio optimizer** ✅ fish/crop mix for max efficiency
- **M3 — agent orchestrator** — 🟡 the tool-calling agent is built and is what every channel
  now runs on. Retrieval is now measured end to end (golden set, recall/MRR/MAP, a relevance
  floor that refuses off-topic questions) and every technique's verdict is recorded; fully
  demoting RAG to a pure citation tool is still open ([#25](https://github.com/Rekin226/Agronaut/issues/25))
- **Field senses** ✅ photos and voice notes on Telegram, WhatsApp and the web, behind a
  code-enforced observation guard and a cited visual-triage table
- **M4 — digital twin** — time-series simulator calibrated on real installed systems ([#26](https://github.com/Rekin226/Agronaut/issues/26))
- **M5 — reach** — SMS/USSD for farmers without a smartphone ([#73](https://github.com/Rekin226/Agronaut/issues/73)), offline-first ([#79](https://github.com/Rekin226/Agronaut/issues/79))
- **Beyond aquaponics** — does the architecture generalise to irrigated field crops via
  FAO-56? ([#78](https://github.com/Rekin226/Agronaut/issues/78))

**Status, honestly.** The design, optimizer, and triage core is built, tested, and enforced in
CI (700+ tests; a hermetic advice-safety golden set that fails the build on a regression). What
is *not* done is **validation against reality**: the coefficients are literature seeds meant to
be calibrated, and the vision path has never been scored against a real photograph because
[the corpus is empty](https://github.com/Rekin226/Agronaut/issues/72). Calibrated ≠ validated,
and the model says so in every result it produces.

The advice layer has the same shape of honesty and the same gap. Retrieval is measured against a
33-query golden set, but that set was written by one person against the corpus it already had —
it cannot tell you about questions nobody thought to ask. And **corpus breadth is the live
constraint**: 22 hand-written files still answer most queries, because open-access aquaponics
*literature* is plentiful while open *operator guidance* barely exists. Widening it is
[#77](https://github.com/Rekin226/Agronaut/issues/77), and `docs/dpg/CORPUS.md` records which
sources were surveyed and why they were not added.

---

## Use it from another agent (agentskills.io skill)

Agronaut's deterministic engine is also packaged as a portable
[agentskills.io](https://agentskills.io) skill in
[`skills/aquaponics-engineer/`](skills/aquaponics_engineer/SKILL.md), so agents like
Hermes, OpenClaw, or Claude Code can hand users a *computed*, cited design instead of a
guess:

```bash
python -m skills.aquaponics_engineer.cli size-aquaponics \
    --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
```

Same trust zone, same citations, no LLM — a bad input is rejected at the gate.

## Contributing

**You don't need an API key, a GPU, or ML experience to contribute here.** The Design
Calculator, the Optimizer, the whole engineering core, and the visual-triage table are
deterministic — `pip install -r requirement.txt && pytest` and you're developing.

The three contributions this project needs most:

| | What | Why it matters |
|---|---|---|
| 🌾 | **Agronomy knowledge** — a crop, a species, a symptom rule, a correction | Every number needs a published source. Finding one *is* the work. Practitioner corrections are especially welcome. |
| 📊 | **Real system data** — your FCR, harvest weights, yields | The coefficients are literature *seeds* meant to be calibrated against reality. Your data makes the model true rather than plausible. → [#22](https://github.com/Rekin226/Agronaut/issues/22) |
| 📷 | **Photographs** — deficient leaves, sick fish, algae, root disease | The vision path is currently verified against handwritten test strings, not real photos. Run `python -m scripts.check_vision_corpus` to see what's wanted. |

Then: [**good first issues**](https://github.com/Rekin226/Agronaut/labels/good%20first%20issue)
· [**CONTRIBUTING.md**](CONTRIBUTING.md) (setup + the trust-zone rules)
· [**Code of Conduct**](CODE_OF_CONDUCT.md)

One rule worth knowing before you write code: `aqua_model/` is a **trust zone** — pure Python,
no LLM, no network, every number carrying a cited source, every output stating what it does
*not* model. CI enforces the first part by installing only `pytest pandas Pillow` and asserting
the core imports without any LLM library. Details in
[CONTRIBUTING.md](CONTRIBUTING.md#the-one-thing-to-understand-before-you-write-code).

## Citing Agronaut

If you use Agronaut in research or programme work, see [CITATION.cff](CITATION.cff).

## License

MIT — see [LICENSE](LICENSE). The code is open by design (it's built on published science);
the value is in calibrated, real-world data, not the equations. Contributions welcome.

# 🌱 Agronaut

[![PyPI](https://img.shields.io/pypi/v/agronaut?color=blue)](https://pypi.org/project/agronaut/)
[![CI](https://github.com/Rekin226/Agronaut/actions/workflows/ci.yml/badge.svg)](https://github.com/Rekin226/Agronaut/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![good first issues](https://img.shields.io/github/issues/Rekin226/Agronaut/good%20first%20issue?label=good%20first%20issues&color=7057ff)](https://github.com/Rekin226/Agronaut/labels/good%20first%20issue)

**An open-source aquaponics assistant you run on your own computer. It designs systems with
a deterministic, source-cited engineering model, and talks to you in the terminal, the
browser, Telegram or WhatsApp.**

Describe your water, space and species, and Agronaut returns a buildable design: tank and
pump sizes, fish count, feed rate, a bill of materials, the operating range to keep, a source
for every number, and a list of what it does *not* model. The language model only collects
facts and explains results. The numbers come from code you can audit.

Built by a working aquaponics operator. The sizing method is a granted Taiwan utility model
patent (TW M661364); the code is MIT.

---

## Quick start

You need **Python 3.11 or newer**.

### 1. Size a system (no API key, no account, offline)

```bash
pip install agronaut
agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
```

You get a full design in a few seconds:

```
FEASIBLE design (raft / DWC grow beds).
Sizing: feed=720 g/day, fish=96 head, biomass=48 kg, system_volume=6666.7 L, rearing_tank=2400 L, pump=6666.7 L/h against 0.52 m head (~27 W), makeup_water=58 L/day
Biofilter media: ~84.57 m2 surface
Bill of materials:
  ...
```

followed by the operating range, a nitrogen cross-check, every coefficient with its source, and
what the design does not model.

Then try:

```bash
agronaut list                                                     # species and crops it knows
agronaut optimize --area 10 --temp 28 --water 5000 --objective food   # best fish × crop ratio
```

### 2. Talk to it

```bash
agronaut setup     # pick a model and a channel; it checks each key as you paste it
agronaut           # chat in the terminal
agronaut web       # or in the browser at http://localhost:8501
```

Choose any model you like:

- **Local, free, no key:** install [Ollama](https://ollama.com), then `ollama pull qwen3.5:4b`.
- **Your own API key:** Claude (Anthropic) or NVIDIA's free tier.

Run `agronaut setup` again any time to switch models or add a channel. Your saved keys are kept.

### 3. Put it on your phone (optional)

```bash
agronaut bot          # Telegram (recommended: one token from @BotFather)
agronaut whatsapp     # WhatsApp (more setup, see docs/whatsapp_setup.md)
```

On your phone, `/log ammonia 0.5 nitrate 40 temp 27` records a reading and `/forecast` shows
the week ahead. Neither calls a model, so they work even when the model is slow or offline.

### What needs a model?

| You want | You need |
|---|---|
| `size`, `size-hydro`, `optimize`, `list`, the Design page in the web app | **Nothing.** Deterministic, offline, cited. |
| Chat, photos, voice notes | A model (local or your own key) |
| `/log`, `/forecast` on Telegram or WhatsApp | A model for setup, then nothing |

---

## What it does

- **Designs aquaponic and hydroponic systems** for 10 fish species and 34 crops, with a bill of
  materials and a downloadable report.
- **Finds the best fish-to-crop ratio** for your water budget, maximising food, protein or
  water efficiency.
- **Runs a consultation**, one question at a time, and remembers your system between sessions.
- **Reads photos** of sick fish, yellow leaves or green water and returns a ranked, cited list
  of possible causes, never a single confident verdict.
- **Simulates a season** for your site with real climate data, and shows it in an offline 3D view.
- **Checks its own replies**: every number the assistant quotes must match what the engine
  computed.

More detail: [docs/features.md](docs/features.md).

---

## How it works

```
  you ──▶ assistant (language model: collects facts, routes, explains)
                │ proposes values
                ▼
          validation gate ── rejects bad or uncertain input
                │
                ▼
          aqua_model: the engineering core (pure Python, tested, every number cited)
                │
                ▼
          a sized system + bill of materials + operating range + sources + "not modelled" list
```

The core (`aqua_model/`) imports no model and no network, and every coefficient carries a
value, a range, a unit and a published source (mostly FAO 589 and Goddek et al. 2019). The
assistant can only reach it through the validation gate. See
[docs/architecture.md](docs/architecture.md).

---

## How good is it? (measured, not claimed)

```bash
agronaut eval        # every quality measurement, its last result and its age
```

- **Advice safety:** 419 automated checks, enforced in CI on every change.
- **The season simulator** was scored against 7 real ponds on held-out data. It got the
  **direction** of change right on 5 of 7, but did not beat a simple trend baseline on the
  **level** on any of them. Use it to compare options, not to predict a number.
- **Answer faithfulness** is 0.84 to 0.90, with no fabricated citations. The automated judge
  agrees with a person only fairly, so treat this as a guide.
- **Not modelled yet:** dissolved oxygen, pH and alkalinity, solids handling, staggered
  harvests, micronutrients. Every design lists its own gaps.

Method and numbers: [docs/evaluation.md](docs/evaluation.md).

---

## When something is off

```bash
agronaut doctor       # checks install, config, model, channels; every failure comes with a fix
agronaut --version    # the version and which copy of the code is running
agronaut update       # install the latest release
```

---

## Run from source

```bash
git clone https://github.com/Rekin226/Agronaut.git
cd Agronaut
python3 -m venv .venv && source .venv/bin/activate
pip install -e . pytest

python -m pytest                   # the full test suite, no model needed
python -m scripts.safety_eval      # the advice-safety golden set
agronaut web                       # the app, from your checkout
```

Or with Docker:

```bash
docker compose up web              # the web app at http://localhost:8501
docker compose --profile bot up    # web + the Telegram bot (needs a .env)
```

---

## Documentation

| Page | What's in it |
|---|---|
| [Install and run](docs/running.md) | Every install option, all CLI commands, Docker, a hosted demo, keeping a bot running |
| [Configuration](docs/configuration.md) | Model providers, self-hosting with open weights, every environment variable |
| [WhatsApp setup](docs/whatsapp_setup.md) | Meta's dashboard, step by step |
| [Features](docs/features.md) | Photos, the 3D twin, voice, the consultation, honesty rules |
| [Architecture](docs/architecture.md) | The trust zone, the engineering model, project layout, the agent skill |
| [Evaluation](docs/evaluation.md) | Retrieval tuning, tracing, faithfulness, reply grounding |
| [Privacy](docs/dpg/PRIVACY.md) | What is recorded, where, and how to delete it |

---

## Contributing

You don't need an API key, a GPU or ML experience. The most valuable contributions are often
not code:

- 🌾 **Agronomy knowledge:** a crop, a species, a symptom rule, a correction, with its source.
- 💰 **A price book for your country**, so cost estimates are true where you live.
- 📊 **Real system data** (feed, harvest weights, water readings) to calibrate the model.
- 📷 **Photographs** of deficient leaves, sick fish or algae.

Start with [issue #27](https://github.com/Rekin226/Agronaut/issues/27), the
[good first issues](https://github.com/Rekin226/Agronaut/labels/good%20first%20issue) and
[CONTRIBUTING.md](CONTRIBUTING.md). One rule before you write code: `aqua_model/` stays pure,
and every number in it needs a published source.

## Citing Agronaut

If you use Agronaut in research or programme work, see [CITATION.cff](CITATION.cff).

## Licence

Code: MIT, see [LICENSE](LICENSE). The knowledge corpus has mixed licences (FAO 589 is
non-commercial); see [docs/dpg/CORPUS.md](docs/dpg/CORPUS.md).

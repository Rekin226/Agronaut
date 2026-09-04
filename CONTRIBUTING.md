# Contributing to Agronaut

Thanks for being here. Agronaut turns aquaponics from trial-and-error into a calculated,
cited answer — and it gets better mainly through two things outsiders can give it:
**real-world data** and **domain knowledge**. You do not need to be an ML engineer. Some of
the most valuable contributions are a CSV, a photograph, or a paragraph with a citation.

**New here? Start with [issues labelled `good first issue`](https://github.com/Rekin226/Agronaut/labels/good%20first%20issue).**

---

## The one thing to understand before you write code

Agronaut has a **trust zone** and everything else.

```
  YOU ──▶  agent layer (LLM: collect facts, route, explain)
                 │  proposes values
                 ▼
           validation gate  ── rejects bad/uncertain input ──┐
                 │ typed, validated                           │
                 ▼                                            │
        aqua_model  (TRUST ZONE — pure, tested, cited)        │
        coefficients ▸ mass balance ▸ sizing ▸ triage    ◀────┘
```

`aqua_model/` is the trust zone. Three rules hold there, and CI enforces the first one:

1. **No LLM, no network, no I/O, and no imports from `agent/` or `srcs/`.** A CI job installs
   only `pytest pandas Pillow` and asserts `import aqua_model` pulls in no LLM or UI library.
   If your change needs one, it belongs in `agent/` instead.
2. **Every number carries a source.** Magic numbers live in `aqua_model/coefficients.py` as a
   `Coefficient` with a value, a plausible range, a unit, and a `source`. Functions read the
   registry; they never hard-code a number.
3. **Every output says what it does *not* model.** This is the project's credibility, not a
   formality. A result that omits its caveats is incomplete.

The agent layer may import from `aqua_model`. **Never the reverse.**

### The other rule: nothing bypasses the gate

Values proposed by a model — from chat, from a photo, from memory — reach `aqua_model` only
through `validate_design_input` (or a sibling validator). A rejected value is never stored.
If you find a path that gets a model-proposed number into a calculation without passing a
gate, that is a bug worth an issue on its own.

---

## Getting set up

```bash
git clone https://github.com/Rekin226/Agronaut.git
cd Agronaut
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                         # deps + the `agronaut` command on your PATH
```

Run things:

```bash
pytest                                   # the full suite (~1,170 tests)
pytest aqua_model/tests -q               # just the deterministic core (fast, no LLM needed)
python -m scripts.safety_eval            # the advice-safety golden set; exits non-zero on a CRITICAL failure
streamlit run app.py                     # the web UI (Calculator + Optimizer need no LLM at all)
agronaut --help                          # the one command over all of the above
python -m skills.aquaponics_engineer.cli size-aquaponics --help   # the portable skill CLI
```

**You do not need an API key to contribute.** The Design Calculator, the Optimizer, the whole
of `aqua_model`, the visual triage table, and the golden set are all fully deterministic. Only
the chat agent needs a model provider.

**On Ubuntu 23.10+, headless-browser QA fails until you allow it.** Chromium dies at startup
with `No usable sandbox!` because the distro restricts unprivileged user namespaces, which
Chromium needs to build its own sandbox. `contrib/apparmor/` has the profile and the reasoning
— including why it is the right fix and `--no-sandbox` is not. Nothing else in this repo needs
it; the suite and the app run fine without.

---

## Ways to contribute, easiest first

### 1. Add a crop or a fish species (no ML, high value)

`aqua_model/crops.py` and `aqua_model/species.py` are cited seed databases. Adding an entry
means finding published values and citing them. Every field needs a source — a paper, an FAO
document, an extension-service guide.

See [#23](https://github.com/Rekin226/Agronaut/issues/23). Bring the citation and we will help
with the plumbing.

### 2. Add a visual-triage rule

`aqua_model/triage.py` maps what you can *see* to a ranked differential. It currently has ~22
rules. Real agronomy has hundreds.

A rule needs: the feature pattern that triggers it, why that pattern implies that cause, the
**checks that discriminate it from its neighbours**, fish-safe first actions, and a `source`
pointing at a file in `knowledge/`. A test asserts every cited document exists.

Two hard constraints, both tested:
- **Never a lone verdict.** If a photo cannot distinguish your cause from another, both are
  candidates. That is the whole design.
- **Never a dose.** No dosing coefficient is cited anywhere in the model, so no amount is
  ever stated.

### 3. Improve or add a knowledge document

`knowledge/*.md` is the curated, cited knowledge base behind the RAG layer and every triage
citation. Corrections from practitioners are especially welcome — if something in there is
wrong or dangerous, an issue saying so is a real contribution.

### 4. Share real system data

The coefficients are literature seeds meant to be **calibrated** against reality. If you run a
system, your FCR, harvest weights, and yields make the model true rather than plausible.
See [#22](https://github.com/Rekin226/Agronaut/issues/22).

### 5. Donate photographs

The vision path is verified against handwritten test strings, not real photographs, because
`data/vision_corpus/` is empty. Field photos of deficient leaves, sick fish, algae, and
root disease would turn an untested claim into a measured one. Run
`python -m scripts.check_vision_corpus` to see exactly which shots are wanted.

### 6. Code

Pick up a `good first issue`, or open an issue describing what you want to change before
writing a lot of it — especially anything touching the trust zone.

---

## How we work

**Tests come first.** This codebase is test-driven: write the failing test, watch it fail,
make it pass. A PR that changes behaviour without a test that would have caught the old
behaviour will get that asked for in review.

**Small PRs, one concern each.** They get reviewed faster and merged sooner.

**Commit messages explain *why*.** The diff shows what changed; the message should say what
was wrong before. Compare "fix regex" with "the guard's lexicon missed bare imperatives, so
'Add chelated iron' reached the user as if they had written it."

**Say what you did not do.** If you left a case unhandled, note it in the PR. Known and stated
is fine; discovered later is expensive.

**An assigned issue is yours until you say otherwise.** If a maintainer ends up doing an
assigned task anyway — it turned out to be urgent, or larger than the assignment implied —
they say so on the issue, say why, and credit the work already done. Silently landing
something out from under a contributor is the fastest way to lose one.

### AI-assisted contributions

Welcome, and used here too — a good deal of this repository was written with a coding agent.
The bar does not move either way, and it is the bar an agent is most likely to walk past:

- **Open a PR, not a comment.** A patch pasted into an issue thread cannot be run, reviewed,
  or merged. If there is no branch, there is no contribution.
- **The files have to exist.** Agents confidently patch plausible paths that this repository
  does not have. Check the diff against a real checkout before you post it.
- **Run the tests you claim to have run.** `pytest` and `python -m scripts.safety_eval`, on
  your branch, merged with `main`. Do not describe a verification you did not perform.
- **Cite the number or leave it out.** Coefficients need a `source=` string pointing at
  published work, and a `low`/`high` envelope. An illustrative or plausible-looking constant
  is worse than a missing one: the missing one is visibly missing, and the invented one gets
  copied forward for years.
- **You are the author.** Review what your agent wrote before it becomes your PR, and be able
  to answer questions about it. "The model produced it" is not an answer in review.

Contributions that fail these are closed and hidden as off-topic, regardless of how they are
formatted. A confident report is not evidence of work.

### Before you open a PR

```bash
pytest                          # green
python -m scripts.safety_eval   # exits 0
```

If you added a rule, a coefficient, or a guard behaviour, add a probe to
`docs/dpg/safety_eval/golden_set.json` so it is scored on every future change. That file is
how this project keeps promises it made months ago.

### Adding an image classifier

`agent/classifier.py` is a finished seam with **no backend** — deliberately. To add one:

1. Register a default model in `DEFAULT_MODELS` and build it in `_build_classifier_backend`,
   importing your library **inside the function** so it never becomes a hard dependency.
2. Return `[Prediction(label, confidence)]` or `[(label, confidence)]`.
3. Map any new label vocabulary in `_LABEL_FEATURES` — to **visible evidence**, not to a
   diagnosis. `late blight` becomes "brown spots on leaves", never "late blight".

The classifier is a *feature source*. It can add evidence; it can never introduce an uncited
candidate. A test enforces that.

---

## Adding a channel

`agronaut_agent/channels/` holds the adapters. A new one (SMS, USSD, Signal, Matrix, a web
widget) implements `ChannelAdapter` and routes to three seams:
`handle_message`, `handle_image`, `handle_voice`. Everything else — memory, tools, the
observation guard, cited knowledge, follow-ups — comes for free. The Telegram and WhatsApp
adapters are the worked examples.

---

## Reporting bugs and security issues

Bugs: open an issue with what you did, what happened, and what you expected.

**Security or safety:** see [SECURITY.md](SECURITY.md). Advice that could hurt someone's fish
or crop counts as a safety issue, not just a bug — please report it that way.

---

## Licence and conduct

Contributions are under the [MIT Licence](LICENSE). By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

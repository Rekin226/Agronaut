# Contributing to Agronaut

Thanks for helping build an honest agronomy agent for aquaponics. Whether you write
Python or run a fish-and-plant system, there's a way in. This guide gets you from clone
to a green test suite to your first PR.

New here? The pinned **[👋 Start here](https://github.com/Rekin226/Agronaut/issues/27)**
issue lists current good-first-issues and where help is most valuable.

---

## The one rule that defines this project

`aqua_model/` is a **trust zone**: pure Python, no LLM, no network, fully tested. It is the
part of Agronaut you can audit without trusting a model.

```
agent / agronaut_agent / app.py   ──imports──▶   aqua_model/   (never the reverse)
   (LLM, Streamlit, channels)                  (pure, tested, cited)
```

Two invariants that every PR must preserve:

1. **`aqua_model/` never imports the LLM, agent, UI, or network layers.** Its only
   dependencies are the standard library and `pandas` (pure compute — no I/O). If you
   find yourself wanting to import `langchain`, `streamlit`, or `requests` into
   `aqua_model/`, the logic belongs in the agent layer instead.
2. **Every number carries a source.** No bare coefficient. A value ships with its range,
   unit, and a citation (FAO 589, UVI/Rakocy, peer-reviewed literature). A
   confidently-wrong design must never masquerade as complete — that's the whole point.

If a change can't satisfy both, it's probably in the wrong layer. Ask in the issue.

---

## Setup

```bash
git clone https://github.com/Rekin226/Agronaut.git
cd Agronaut
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirement.txt
```

Python **3.11 or 3.12** (what CI runs).

## Run the tests

```bash
python -m pytest
```

You should see **102 passed** (and growing). The suite covers three areas:

- `aqua_model/tests/` — the pure engineering core (no model server, no network)
- `agent/tests/` — the Streamlit UI seam
- `agronaut_agent/tests/` — the tool-calling agent (dry-run; no live LLM calls)

If your PR touches `aqua_model/`, add or extend a test. The core is the trust zone — it
stays covered.

---

## How to add a cited coefficient

This is one of the most valuable contributions (see issue
[#23](https://github.com/Rekin226/Agronaut/issues/23)). Follow the existing shape:

1. Add the species/crop entry in `aqua_model/species.py` or `aqua_model/crops.py` with a
   **value + range + unit + source**, matching the surrounding entries.
2. If the source is new, register it in `data/coefficient_sources.json`.
3. Run `python -m pytest` — keep it green; add a row to any parametrized coverage test.

No source, no merge. Good sources: FAO 589, UVI/Rakocy publications, peer-reviewed
aquaponics literature.

## How to contribute real-world data (no coding required)

If you run an aquaponics system, your logs are the project's moat — they're what lets us
*validate* the model against reality. See issue
[#22](https://github.com/Rekin226/Agronaut/issues/22) and the logging standard in
`aqua_model/logging_schema.py`. Comment on the issue and we'll help you map your data.

---

## Pull requests

- **Branch** off `main`; keep the PR focused on one issue.
- **Tests pass**: `python -m pytest` is green locally and in CI.
- **Commits**: clear, present-tense messages (`feat:`, `fix:`, `docs:`, `chore:` prefixes
  are appreciated, matching the existing history).
- **New coefficients carry a source** (see above).
- **No secrets**: never commit API keys, `.env`, or tokens.
- Link the issue your PR closes (`Closes #NN`).

CI runs the full suite on every PR across Python 3.11 and 3.12. A red build blocks merge.

## Reporting bugs & proposing features

Open an issue. For bugs, include: what you ran, what you expected, what happened, and your
Python version. For features, describe the grower problem it solves before the
implementation — Agronaut is built backward from real operator pain.

## License

By contributing, you agree your contributions are licensed under the project's
[MIT License](LICENSE).

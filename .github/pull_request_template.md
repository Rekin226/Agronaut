## What this changes

<!-- What was wrong before? The diff shows what changed; this should say why. -->

## How it was verified

<!-- Paste the relevant output. -->

- [ ] `pytest` is green
- [ ] `python -m scripts.safety_eval` exits 0
- [ ] New behaviour has a test that would have failed before this change

## Trust-zone checklist

<!-- Delete this section if you didn't touch aqua_model/ or add a user-facing number. -->

- [ ] No new import of an LLM, network, or UI library inside `aqua_model/`
- [ ] Any new number lives in `coefficients.py` with a value, range, unit, and **source**
- [ ] Any new user-facing result states what it does **not** model
- [ ] Model-proposed values still reach the core only through a validation gate
- [ ] A probe was added to `docs/dpg/safety_eval/golden_set.json` for the new guarantee

## What this does NOT do

<!-- Known gaps, deliberately deferred cases, anything you left out. Stated is fine; found
     later is expensive. -->

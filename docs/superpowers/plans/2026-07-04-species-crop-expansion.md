# Species & Crop Table Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sourced seed entries to the deterministic model — common carp + kale, swiss chard, spinach, cucumber, pepper — with calibration ranges and tests, purely additively.

**Architecture:** Add frozen-dataclass rows to `species.py`/`crops.py` (auto-registered in the `SPECIES`/`CROPS` dicts and thus the optimizer palettes and trust gate), extend `calibration.py`'s seed-vs-range honesty layer for the new load-bearing coefficients, and add parametrized coverage tests. No formula, trust-gate, or architecture change.

**Tech Stack:** Python 3.12, `aqua_model` (frozen dataclasses), pytest. No new dependencies.

## Global Constraints

- Coefficient values are EXACTLY as specified in the spec tables — do not alter them.
- Canonical keys (the `name` field / dict key): `carp`, `kale`, `swiss_chard`, `spinach`, `cucumber`, `pepper`.
- Every new seed must sit INSIDE its calibration empirical range, so `discrepancies()` stays clean.
- Additive only: existing species/crops, formulas, the trust gate (`validate_design_input`), and the no-new-entry behavior are untouched.
- `FishSpecies` field order: name, feeding_rate_pct_bw, fcr, feed_protein_pct, body_protein_pct, harvest_weight_kg, stocking_density_kg_m3, temp_min_c, temp_opt_low_c, temp_opt_high_c, temp_max_c, source.
- `Crop` field order: name, category, frr_g_per_m2_day, frr_low, frr_high, n_uptake_g_per_m2_day, yield_kg_per_m2_year, edible_protein_pct, ph_min, ph_max, temp_min_c, temp_max_c, source.
- Work on branch `feat/species-crop-expansion` (already checked out). Commit after every task.

---

### Task 1: Add the new species and crops (data rows + coverage tests)

**Files:**
- Modify: `aqua_model/species.py` (add `CARP`, register in `SPECIES`)
- Modify: `aqua_model/crops.py` (add `KALE`, `SWISS_CHARD`, `SPINACH`, `CUCUMBER`, `PEPPER`, register in `CROPS`)
- Test: `aqua_model/tests/test_sizing.py`, `aqua_model/tests/test_optimizer.py`

**Interfaces:**
- Consumes: `FishSpecies`, `Crop`, `size_system`, `validate_design_input`, `optimize`, `OptimizeInput`.
- Produces: `SPECIES` gains key `carp`; `CROPS` gains keys `kale`, `swiss_chard`, `spinach`, `cucumber`, `pepper`.

- [ ] **Step 1: Write the failing tests**

Append to `aqua_model/tests/test_sizing.py`:

```python
import pytest
from aqua_model.species import SPECIES
from aqua_model.crops import CROPS


@pytest.mark.parametrize("fish", ["tilapia", "clarias", "channel_catfish", "trout", "carp"])
def test_every_species_sizes(fish):
    from aqua_model import size_system
    from aqua_model.validate import validate_design_input
    out = size_system(validate_design_input(fish, "lettuce", 20, 24, 5000))
    assert out.fish_count >= 1 and out.feed_g_per_day > 0


@pytest.mark.parametrize("crop", ["lettuce", "basil", "tomato", "kale", "swiss_chard",
                                  "spinach", "cucumber", "pepper"])
def test_every_crop_sizes(crop):
    from aqua_model import size_system
    from aqua_model.validate import validate_design_input
    out = size_system(validate_design_input("tilapia", crop, 20, 24, 5000))
    assert out.fish_count >= 1 and out.feed_g_per_day > 0


def test_new_keys_registered():
    assert "carp" in SPECIES
    for c in ("kale", "swiss_chard", "spinach", "cucumber", "pepper"):
        assert c in CROPS
```

Append to `aqua_model/tests/test_optimizer.py`:

```python
def test_optimize_default_palette_includes_new_entries():
    from aqua_model import optimize, OptimizeInput
    inp = OptimizeInput(grow_area_m2=10, temperature_c=25, water_budget_lpd=5000, objective="food")
    res = optimize(inp)                      # default palettes = all species/crops
    assert res.best is not None              # a feasible best exists with the expanded palette
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest aqua_model/tests/test_sizing.py -k "carp or every_crop or new_keys" aqua_model/tests/test_optimizer.py -k "new_entries" -v`
Expected: FAIL — `KeyError: Unknown fish species 'carp'` (and unknown crops)

- [ ] **Step 3: Add the species row**

In `aqua_model/species.py`, add after `TROUT` (before the `SPECIES` dict):

```python
# Common carp (Cyprinus carpio): among the most-farmed fish worldwide. Hardy, omnivorous,
# very cold-tolerant warm-water fish; moderate feed efficiency.
CARP = FishSpecies(
    name="carp",
    feeding_rate_pct_bw=1.5, fcr=2.0, feed_protein_pct=32.0,
    body_protein_pct=16.0, harvest_weight_kg=1.0, stocking_density_kg_m3=25.0,
    temp_min_c=8.0, temp_opt_low_c=23.0, temp_opt_high_c=30.0, temp_max_c=34.0,
    source="LIT (carp aquaculture)",
)
```

Update the `SPECIES` dict to include it:

```python
SPECIES: dict[str, FishSpecies] = {
    s.name: s for s in (TILAPIA, CLARIAS, CHANNEL_CATFISH, TROUT, CARP)
}
```

- [ ] **Step 4: Add the crop rows**

In `aqua_model/crops.py`, add after `TOMATO` (before the `CROPS` dict):

```python
# More leafy greens (UVI/FAO leafy feeding-rate band ~40-100 g/m2/day).
KALE = Crop(
    name="kale", category="leafy",
    frr_g_per_m2_day=65.0, frr_low=45.0, frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    yield_kg_per_m2_year=20.0, edible_protein_pct=3.3,
    ph_min=5.5, ph_max=7.0, temp_min_c=7.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
SWISS_CHARD = Crop(
    name="swiss_chard", category="leafy",
    frr_g_per_m2_day=60.0, frr_low=40.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=22.0, edible_protein_pct=1.8,
    ph_min=5.5, ph_max=7.0, temp_min_c=10.0, temp_max_c=27.0, source="FAO589/UVI (leafy band)",
)
SPINACH = Crop(
    name="spinach", category="leafy",
    frr_g_per_m2_day=55.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.8,
    yield_kg_per_m2_year=15.0, edible_protein_pct=2.9,
    ph_min=6.0, ph_max=7.0, temp_min_c=7.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)

# More fruiting crops (FAO fruiting feeding-rate band ~80-140 g/m2/day).
CUCUMBER = Crop(
    name="cucumber", category="fruiting",
    frr_g_per_m2_day=100.0, frr_low=80.0, frr_high=130.0,
    n_uptake_g_per_m2_day=1.5,
    yield_kg_per_m2_year=35.0, edible_protein_pct=0.7,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589 (fruiting band)",
)
PEPPER = Crop(
    name="pepper", category="fruiting",
    frr_g_per_m2_day=100.0, frr_low=80.0, frr_high=130.0,
    n_uptake_g_per_m2_day=1.4,
    yield_kg_per_m2_year=20.0, edible_protein_pct=1.0,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589 (fruiting band)",
)
```

Update the `CROPS` dict:

```python
CROPS: dict[str, Crop] = {
    c.name: c for c in (LETTUCE, BASIL, TOMATO, KALE, SWISS_CHARD, SPINACH, CUCUMBER, PEPPER)
}
```

- [ ] **Step 4b: Run the model suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest aqua_model/tests/ -q`
Expected: PASS (all — the new parametrized cases pass; every pre-existing test is unchanged)

- [ ] **Step 5: Commit**

```bash
git add aqua_model/species.py aqua_model/crops.py aqua_model/tests/test_sizing.py aqua_model/tests/test_optimizer.py
git commit -m "feat(aqua_model): add common carp + 5 crops (kale, chard, spinach, cucumber, pepper)"
```

---

### Task 2: Calibration ranges for the new load-bearing coefficients

**Files:**
- Modify: `aqua_model/calibration.py`
- Test: `aqua_model/tests/test_calibration.py`

**Interfaces:**
- Consumes: `SPECIES`/`CROPS` (Task 1) via `get_species`/`get_crop`; `SizingCalibration`, `discrepancies`, `get`.
- Produces: `CALIBRATIONS` gains keys `carp.fcr`, `kale.frr`, `swiss_chard.frr`, `spinach.frr`, `cucumber.frr`, `pepper.frr`.

- [ ] **Step 1: Write the failing test**

Append to `aqua_model/tests/test_calibration.py`:

```python
def test_new_coefficients_present_and_within_range():
    from aqua_model import calibration
    keys = {c.key for c in calibration.all_calibrations()}
    for k in ("carp.fcr", "kale.frr", "swiss_chard.frr", "spinach.frr",
              "cucumber.frr", "pepper.frr"):
        assert k in keys
    # every new seed sits inside its own cited range -> not flagged as a discrepancy
    disc = {c.key for c in calibration.discrepancies()}
    for k in ("carp.fcr", "kale.frr", "swiss_chard.frr", "spinach.frr",
              "cucumber.frr", "pepper.frr"):
        assert k not in disc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest aqua_model/tests/test_calibration.py -k new_coefficients -v`
Expected: FAIL — `assert 'carp.fcr' in keys` (KeyError/AssertionError — not yet registered)

- [ ] **Step 3: Add the calibration entries**

In `aqua_model/calibration.py`, inside `_calibrations()`, extend the local lookups (after the existing `lettuce, basil, tomato = ...` line):

```python
    carp = get_species("carp")
    kale, swiss_chard, spinach = get_crop("kale"), get_crop("swiss_chard"), get_crop("spinach")
    cucumber, pepper = get_crop("cucumber"), get_crop("pepper")
```

Then add these entries to the returned list (place the FCR entry with the other `*.fcr` entries, and the FRR entries with the other `*.frr` entries):

```python
        SizingCalibration(
            "carp.fcr", "Common carp feed conversion ratio",
            carp.fcr, 1.5, 2.5, "g feed / g gain",
            ("Common carp (Cyprinus carpio) pond/RAS grow-out FCR ~1.5–2.5",),
            "One of the most-farmed fish worldwide; seed 2.0 is mid-range.",
        ),
        SizingCalibration(
            "kale.frr", "Kale feeding-rate ratio",
            kale.frr_g_per_m2_day, 45.0, 90.0, "g feed / m² / day",
            ("Somerville et al. (2014), FAO 589; UVI leafy feeding-rate band ~40–100 g/m²/day",),
            "Leafy band; seed 65 is mid.",
        ),
        SizingCalibration(
            "swiss_chard.frr", "Swiss chard feeding-rate ratio",
            swiss_chard.frr_g_per_m2_day, 40.0, 85.0, "g feed / m² / day",
            ("Somerville et al. (2014), FAO 589; UVI leafy feeding-rate band ~40–100 g/m²/day",),
            "Leafy band; seed 60 is mid.",
        ),
        SizingCalibration(
            "spinach.frr", "Spinach feeding-rate ratio",
            spinach.frr_g_per_m2_day, 40.0, 80.0, "g feed / m² / day",
            ("Somerville et al. (2014), FAO 589; UVI leafy feeding-rate band ~40–100 g/m²/day",),
            "Cool-season leafy; seed 55 is mid.",
        ),
        SizingCalibration(
            "cucumber.frr", "Cucumber feeding-rate ratio",
            cucumber.frr_g_per_m2_day, 80.0, 130.0, "g feed / m² / day",
            ("Somerville et al. (2014), FAO 589: fruiting raft ~80–140 g/m²/day",),
            "Fruiting band; seed 100 is mid.",
        ),
        SizingCalibration(
            "pepper.frr", "Pepper feeding-rate ratio",
            pepper.frr_g_per_m2_day, 80.0, 130.0, "g feed / m² / day",
            ("Somerville et al. (2014), FAO 589: fruiting raft ~80–140 g/m²/day",),
            "Fruiting band; seed 100 is mid.",
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest aqua_model/tests/test_calibration.py -v`
Expected: PASS (all — new keys present, none in `discrepancies()`; existing calibration tests unchanged)

- [ ] **Step 5: Commit**

```bash
git add aqua_model/calibration.py aqua_model/tests/test_calibration.py
git commit -m "feat(aqua_model): calibration ranges for carp.fcr + new crop FRRs"
```

---

### Task 3: Agent-facing check + live smoke + README

**Files:**
- Modify: `README.md`
- Test: `agronaut_agent/tests/test_tools.py`

**Interfaces:**
- Consumes: `list_supported_species_and_crops` tool (already lists `SPECIES`/`CROPS`).

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_tools.py`:

```python
def test_list_supported_includes_new_species_and_crops():
    from agronaut_agent.tools import list_supported_species_and_crops
    out = list_supported_species_and_crops.invoke({})
    assert "carp" in out
    for c in ("kale", "swiss_chard", "spinach", "cucumber", "pepper"):
        assert c in out
```

- [ ] **Step 2: Run it — this is a coverage test, expected GREEN**

`list_supported_species_and_crops` enumerates `SPECIES`/`CROPS` live, so once Task 1 landed the new
names are already listed — no production code changes in this task. This test locks that in (it would
only go red if a future change dropped an entry).

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -k list_supported -v`
Expected: PASS (both the existing `test_list_supported` and the new one)

- [ ] **Step 4: Live smoke — size and optimize with a new species/crop**

Run:

```bash
PYTHONPATH=/home/rekin226/Desktop/code_space/Agronaut .venv/bin/python -c "
from aqua_model import size_system, optimize, OptimizeInput
from aqua_model.validate import validate_design_input
d = validate_design_input('carp', 'kale', 20, 24, 5000)
out = size_system(d)
print('carp+kale: feasible=%s fish=%d feed=%.0f g/day' % (out.feasible, out.fish_count, out.feed_g_per_day))
best = optimize(OptimizeInput(grow_area_m2=10, temperature_c=24, water_budget_lpd=5000, objective='food')).best
print('optimizer best fish:', best.fish_species, 'crops:', best.crop_allocation)
"
```
Expected: a feasible carp+kale sizing with positive fish/feed, and the optimizer returns a best (its palette now spans all 5 fish / 8 crops).

- [ ] **Step 5: Add the README note**

In `README.md`, find the line listing supported species/crops (near the model/features description). If a short "supported" list exists, update it to mention the broader set; otherwise append under the "### Consultative agent" subsection:

```markdown
The deterministic sizing model now covers five fish (tilapia, clarias, channel catfish, trout,
common carp) and eight crops (lettuce, basil, tomato, kale, swiss chard, spinach, cucumber,
pepper) — each with cited, calibratable seed coefficients.
```

- [ ] **Step 6: Run the full suite one last time**

Run: `.venv/bin/python -m pytest aqua_model/tests/ agronaut_agent/tests/ -q`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add README.md agronaut_agent/tests/test_tools.py
git commit -m "docs: broaden documented species/crop coverage; test list_supported"
```

---

## Notes for the implementer

- **Run tests with the venv:** always `.venv/bin/python -m pytest ...`.
- **Values are fixed by the spec** — copy the coefficient numbers exactly; they were reviewed and approved.
- **Ordering matters:** Task 2 (`calibration.py`) reads the new seeds via `get_species`/`get_crop`, so Task 1 must land first.
- **Nothing else changes** — the sizing/optimize formulas, the trust gate, and existing seeds are untouched; this is purely new, sourced data plus its honesty-layer ranges.

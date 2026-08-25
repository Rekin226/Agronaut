# Per-Operator Coefficient Calibration (Phase 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real measured outcomes (FCR, harvest weight, crop yield) from an operator's running system calibrate THEIR OWN future sizings and optimizations — as bounded overrides applied inside `aqua_model`, seeds never mutated.

**Architecture:** A calibration is a per-operator overlay. Measurements are stored keyed by the `calibration.py` coefficient key (e.g. `tilapia.fcr`). When ≥2 measurements exist and their mean is within the coefficient's published empirical range, `CalibrationStore.overrides_for` emits a bounded override. `size_system`/`optimize` accept an `overrides` dict and apply it by rebuilding the relevant `frozen` `FishSpecies`/`Crop` dataclass with `dataclasses.replace` — so every downstream formula and the no-override path are untouched. `validate_overrides` (trust gate) refuses any value outside the empirical range.

**Tech Stack:** Python 3.12, `aqua_model` (frozen dataclasses, `dataclasses.replace`), SQLite, LangChain `@tool`, pytest. No new dependencies.

## Global Constraints

- **Seeds are NEVER mutated.** Overrides live only in a `dataclasses.replace`'d copy for one call.
- **Bounded to the published empirical range** (`aqua_model/calibration.py` `get(key).emp_low/emp_high`); out-of-range → `ValidationError` (the same trust gate that rejects bad inputs).
- **Apply only with ≥2 measurements AND an in-range mean;** an out-of-range mean is surfaced (flagged), never applied — sizing falls back to the seed.
- **The LLM only reports measurements;** the store computes bounded overrides deterministically (the LLM never picks override values).
- **A coefficient is calibratable only where `calibration.py` has a range for that exact key** (`{species}.fcr`, `{species}.harvest_weight`, `{crop}.yield`). `overrides_for` looks up via `calibration.get` and skips any key with no range (KeyError).
- **Coefficient keys / model attributes** (exact): `fcr`→`FishSpecies.fcr`; `harvest_weight`→`FishSpecies.harvest_weight_kg`; `yield`→`Crop.yield_kg_per_m2_year`. `FishSpecies.name`/`Crop.name` are the canonical keys (`tilapia`, `lettuce`, …).
- **No-override path must stay byte-identical to today** (regression guard in the model tests).
- All stores share one `_Db`; `AGRONAUT_TOOLS` goes from 11 to **12**; deterministic tests.
- Work on branch `feat/coefficient-calibration` (already checked out). Commit after every task.

---

### Task 1: `aqua_model/overrides.py` — bounded override helper + validation

**Files:**
- Create: `aqua_model/overrides.py`
- Test: `aqua_model/tests/test_overrides.py`

**Interfaces:**
- Consumes: `calibration.get(key)` (`emp_low`/`emp_high`); `FishSpecies`, `Crop`; `ValidationError`.
- Produces:
  - `validate_overrides(overrides: dict) -> None` — raises `ValidationError` if any key is unknown, has no empirical range, or its value is non-numeric or out of range.
  - `apply_overrides(species=None, crop=None, overrides=None) -> tuple` — returns `(species, crop)` with matching overrides applied via `dataclasses.replace` (only to whichever of species/crop is provided and whose `.name` matches the key prefix). Seeds untouched.

- [ ] **Step 1: Write the failing test**

Create `aqua_model/tests/test_overrides.py`:

```python
import dataclasses
import pytest

from aqua_model.overrides import apply_overrides, validate_overrides
from aqua_model.validate import ValidationError
from aqua_model.species import get_species
from aqua_model.crops import get_crop


def test_apply_overrides_replaces_matching_species_attr():
    sp = get_species("tilapia")
    sp2, _ = apply_overrides(species=sp, overrides={"tilapia.fcr": 1.5})
    assert sp2.fcr == 1.5
    assert sp.fcr == 1.7                       # seed object untouched
    assert dataclasses.replace(sp, fcr=1.7) == sp  # (sanity: seed unchanged)


def test_apply_overrides_maps_harvest_weight_and_yield_keys():
    sp = get_species("tilapia")
    cr = get_crop("lettuce")
    sp2, cr2 = apply_overrides(species=sp, crop=cr,
                               overrides={"tilapia.harvest_weight": 0.45, "lettuce.yield": 12.0})
    assert sp2.harvest_weight_kg == 0.45
    assert cr2.yield_kg_per_m2_year == 12.0


def test_apply_overrides_ignores_non_matching_prefix():
    sp = get_species("tilapia")
    sp2, _ = apply_overrides(species=sp, overrides={"trout.fcr": 1.0})  # different species
    assert sp2.fcr == 1.7                       # unchanged


def test_validate_overrides_accepts_in_range():
    validate_overrides({"tilapia.fcr": 1.5})    # 0.9-1.8 -> ok, no raise


def test_validate_overrides_rejects_out_of_range():
    with pytest.raises(ValidationError):
        validate_overrides({"tilapia.fcr": 5.0})  # above 1.8


def test_validate_overrides_rejects_unknown_and_unranged():
    with pytest.raises(ValidationError):
        validate_overrides({"tilapia.bogus": 1.0})       # unknown suffix
    with pytest.raises(ValidationError):
        validate_overrides({"clarias.harvest_weight": 0.6})  # no range for clarias harvest_weight
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest aqua_model/tests/test_overrides.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aqua_model.overrides'`

- [ ] **Step 3: Write minimal implementation**

Create `aqua_model/overrides.py`:

```python
"""Per-operator coefficient overrides.

A calibrated value replaces a seed for ONE sizing/optimize call only — applied by rebuilding
the frozen FishSpecies/Crop with dataclasses.replace, so seeds are NEVER mutated and the
no-override path is unchanged. The override key is the calibration key (e.g. 'tilapia.fcr'):
its prefix names the species/crop it applies to, and its value must sit within the coefficient's
published empirical range (calibration.get) or it is refused at the trust gate.
"""

from __future__ import annotations

import dataclasses

from . import calibration
from .validate import ValidationError

# calibration-key suffix -> (which object, which model attribute)
_SUFFIX_TO_ATTR: dict[str, tuple[str, str]] = {
    "fcr": ("species", "fcr"),
    "harvest_weight": ("species", "harvest_weight_kg"),
    "yield": ("crop", "yield_kg_per_m2_year"),
}


def validate_overrides(overrides: dict) -> None:
    """Raise ValidationError if any override key is unknown, lacks an empirical range, or its
    value is non-numeric or outside that range."""
    errors: list[str] = []
    for key, val in (overrides or {}).items():
        suffix = key.rpartition(".")[2]
        if suffix not in _SUFFIX_TO_ATTR:
            errors.append(f"unknown calibration coefficient {key!r}")
            continue
        try:
            cal = calibration.get(key)
        except KeyError:
            errors.append(f"no empirical range for {key!r}; cannot bound it")
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            errors.append(f"{key}: value {val!r} is not a number")
            continue
        if not (cal.emp_low <= v <= cal.emp_high):
            errors.append(f"{key}: {v} outside empirical range [{cal.emp_low}, {cal.emp_high}]")
    if errors:
        raise ValidationError(errors)


def apply_overrides(species=None, crop=None, overrides: dict | None = None):
    """Return (species, crop) with matching overrides applied via dataclasses.replace. Only an
    override whose key prefix equals the provided species/crop `.name` takes effect. Seeds are
    untouched (replace returns a new object)."""
    if not overrides:
        return species, crop
    sp_repl: dict[str, float] = {}
    cr_repl: dict[str, float] = {}
    for key, val in overrides.items():
        prefix, _, suffix = key.rpartition(".")
        target_attr = _SUFFIX_TO_ATTR.get(suffix)
        if target_attr is None:
            continue
        target, attr = target_attr
        if target == "species" and species is not None and prefix == species.name:
            sp_repl[attr] = float(val)
        elif target == "crop" and crop is not None and prefix == crop.name:
            cr_repl[attr] = float(val)
    if sp_repl:
        species = dataclasses.replace(species, **sp_repl)
    if cr_repl:
        crop = dataclasses.replace(crop, **cr_repl)
    return species, crop
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest aqua_model/tests/test_overrides.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add aqua_model/overrides.py aqua_model/tests/test_overrides.py
git commit -m "feat(aqua_model): bounded per-operator coefficient overrides (seeds immutable)"
```

---

### Task 2: Wire overrides into `size_system` and `optimize`

**Files:**
- Modify: `aqua_model/sizing.py` (`size_system`)
- Modify: `aqua_model/optimizer.py` (`_evaluate`, `optimize`)
- Test: `aqua_model/tests/test_sizing.py`, `aqua_model/tests/test_optimizer.py`

**Interfaces:**
- Consumes: `overrides.validate_overrides`, `overrides.apply_overrides` (Task 1).
- Produces: `size_system(design, overrides=None)`; `optimize(inp, overrides=None)`. Both validate then apply; both are byte-identical to today when `overrides` is falsy.

- [ ] **Step 1: Write the failing test**

Append to `aqua_model/tests/test_sizing.py`:

```python
def test_size_system_harvest_weight_override_changes_fish_count():
    from aqua_model import size_system
    from aqua_model.validate import validate_design_input, ValidationError
    design = validate_design_input("tilapia", "lettuce", 20, 27, 500)
    base = size_system(design)
    # a smaller harvest weight -> more fish for the same biomass
    lighter = size_system(design, overrides={"tilapia.harvest_weight": 0.4})
    assert lighter.fish_count > base.fish_count


def test_size_system_no_override_is_unchanged():
    from aqua_model import size_system
    from aqua_model.validate import validate_design_input
    design = validate_design_input("tilapia", "lettuce", 20, 27, 500)
    assert size_system(design, overrides=None).fish_count == size_system(design).fish_count


def test_size_system_rejects_out_of_range_override():
    from aqua_model import size_system
    from aqua_model.validate import validate_design_input, ValidationError
    design = validate_design_input("tilapia", "lettuce", 20, 27, 500)
    import pytest
    with pytest.raises(ValidationError):
        size_system(design, overrides={"tilapia.harvest_weight": 5.0})  # far above range
```

Append to `aqua_model/tests/test_optimizer.py`:

```python
def test_optimize_yield_override_changes_food_score():
    from aqua_model import optimize, OptimizeInput
    inp = OptimizeInput(grow_area_m2=10, temperature_c=27, water_budget_lpd=5000, objective="food")
    base = optimize(inp)
    higher = optimize(inp, overrides={"lettuce.yield": 30.0})  # top of range vs seed 25
    # a higher lettuce yield can only raise (or equal) the best food score
    assert higher.best.food_kg_yr >= base.best.food_kg_yr


def test_optimize_no_override_is_unchanged():
    from aqua_model import optimize, OptimizeInput
    inp = OptimizeInput(grow_area_m2=10, temperature_c=27, water_budget_lpd=5000, objective="food")
    assert optimize(inp, overrides=None).best.score == optimize(inp).best.score
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest aqua_model/tests/test_sizing.py aqua_model/tests/test_optimizer.py -k "override" -v`
Expected: FAIL — `TypeError: size_system() got an unexpected keyword argument 'overrides'`

- [ ] **Step 3: Wire `size_system`**

In `aqua_model/sizing.py`, add the import near the top (with the other `from .` imports):

```python
from .overrides import validate_overrides, apply_overrides
```

Change the `size_system` signature and the first two lines of its body:

```python
def size_system(design: DesignInput, overrides: dict | None = None) -> DesignOutput:
    if overrides:
        validate_overrides(overrides)
    species = get_species(design.fish_species)
    crop = get_crop(design.crop)
    species, crop = apply_overrides(species=species, crop=crop, overrides=overrides)
```

(everything after this is unchanged — it already reads `species.*`/`crop.*`.)

- [ ] **Step 4: Wire `optimize`**

In `aqua_model/optimizer.py`, add the import (with the other `from .` imports):

```python
from .overrides import validate_overrides, apply_overrides
```

Change `_evaluate` to accept and apply overrides — replace its first line and the `get_crop(c)` reads:

```python
def _evaluate(fish_name: str, alloc: dict[str, float], inp: OptimizeInput,
              overrides: dict | None = None) -> Candidate:
    species, _ = apply_overrides(species=get_species(fish_name), overrides=overrides)
    crops = {c: apply_overrides(crop=get_crop(c), overrides=overrides)[1] for c in alloc}
    area = inp.grow_area_m2

    # Feed from the area-weighted FRR of the mix (FRR is the sizing rule).
    feed_g_day = sum(area * frac * crops[c].frr_g_per_m2_day for c, frac in alloc.items())

    temp_factor = temperature_feed_factor(species, inp.temperature_c)
    eff_pct = species.feeding_rate_pct_bw * temp_factor
    fish_biomass_kg = feed_g_day / (eff_pct / 100.0) / 1000.0 if eff_pct > 0 else 0.0

    # Annual food: crop yield (per allocated area) + fish growth (= feed / FCR).
    crop_food_kg_yr = sum(area * frac * crops[c].yield_kg_per_m2_year for c, frac in alloc.items())
    fish_growth_kg_yr = (feed_g_day / species.fcr) / 1000.0 * 365.0
    food_kg_yr = crop_food_kg_yr + fish_growth_kg_yr

    crop_protein_kg_yr = sum(
        area * frac * crops[c].yield_kg_per_m2_year * (crops[c].edible_protein_pct / 100.0)
        for c, frac in alloc.items()
    )
```

(the rest of `_evaluate` below `fish_protein_kg_yr = …` is unchanged.)

Change `optimize` to validate once and thread overrides into `_evaluate`:

```python
def optimize(inp: OptimizeInput, overrides: dict | None = None) -> OptimizeResult:
    if inp.objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective {inp.objective!r}. Supported: {OBJECTIVES}.")
    if not inp.fish_palette or not inp.crop_palette:
        raise ValueError("fish_palette and crop_palette must be non-empty.")
    if overrides:
        validate_overrides(overrides)
```

and the candidate loop:

```python
    candidates: list[Candidate] = []
    for fish in inp.fish_palette:
        for alloc in allocs:
            candidates.append(_evaluate(fish, alloc, inp, overrides))
```

- [ ] **Step 5: Run the model suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest aqua_model/tests/ -q`
Expected: PASS (all — override tests pass, and every existing sizing/optimizer test is unchanged)

- [ ] **Step 6: Commit**

```bash
git add aqua_model/sizing.py aqua_model/optimizer.py aqua_model/tests/test_sizing.py aqua_model/tests/test_optimizer.py
git commit -m "feat(aqua_model): size_system + optimize accept bounded overrides"
```

---

### Task 3: `measurements` table + `CalibrationStore`

**Files:**
- Modify: `agronaut_agent/store.py`
- Test: `agronaut_agent/tests/test_store.py`

**Interfaces:**
- Consumes: `_Db`, `_now`; `aqua_model.calibration.get`.
- Produces: `CalibrationStore(db)` with:
  - `record(user_id, coefficient, value)` — append a measurement.
  - `overrides_for(user_id) -> dict` — for each measured coefficient with ≥2 values AND a mean within `calibration.get(key)`'s range, `{key: round(mean, 4)}`; keys with no range or <2 values or out-of-range mean are omitted.
  - `calibration_report(user_id) -> list[dict]` — per coefficient: `{coefficient, n, mean, applied, seed, emp_low, emp_high, in_range}` for honest surfacing.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_store.py`:

```python
from agronaut_agent.store import CalibrationStore


def _cal():
    return CalibrationStore(_Db(":memory:"))


def test_overrides_need_two_in_range_measurements():
    cs = _cal()
    cs.record("telegram:1", "tilapia.fcr", 1.4)
    assert cs.overrides_for("telegram:1") == {}          # only one measurement
    cs.record("telegram:1", "tilapia.fcr", 1.6)
    assert cs.overrides_for("telegram:1") == {"tilapia.fcr": 1.5}  # mean 1.5, in range 0.9-1.8


def test_out_of_range_mean_is_not_applied():
    cs = _cal()
    cs.record("telegram:1", "tilapia.fcr", 3.0)
    cs.record("telegram:1", "tilapia.fcr", 3.0)          # mean 3.0 > 1.8
    assert "tilapia.fcr" not in cs.overrides_for("telegram:1")


def test_unranged_coefficient_is_skipped():
    cs = _cal()
    cs.record("telegram:1", "clarias.harvest_weight", 0.6)
    cs.record("telegram:1", "clarias.harvest_weight", 0.6)  # no calibration range exists
    assert cs.overrides_for("telegram:1") == {}


def test_calibration_report_reflects_status():
    cs = _cal()
    cs.record("telegram:1", "tilapia.fcr", 1.4)
    cs.record("telegram:1", "tilapia.fcr", 1.6)
    rep = {r["coefficient"]: r for r in cs.calibration_report("telegram:1")}
    assert rep["tilapia.fcr"]["n"] == 2
    assert rep["tilapia.fcr"]["applied"] is True
    assert rep["tilapia.fcr"]["mean"] == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_store.py -k "override or calibration_report or unranged" -v`
Expected: FAIL — `ImportError: cannot import name 'CalibrationStore'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/store.py`, add the table to `_SCHEMA` (before its closing `"""`, after the `community_insights` block):

```python
-- Per-operator coefficient calibration: real measured outcomes (keyed by the aqua_model
-- calibration key, e.g. 'tilapia.fcr'). Aggregated into bounded overrides at sizing time;
-- seeds are never mutated.
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    coefficient TEXT NOT NULL,
    value       REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_measurements_user ON measurements(user_id, coefficient);
```

Add the class at the end of `store.py`:

```python
class CalibrationStore:
    """Per-operator coefficient measurements -> bounded overrides. A coefficient is applied only
    with >=2 measurements whose mean is within the published empirical range (aqua_model
    calibration); seeds are never touched."""

    _MIN_OBS = 2

    def __init__(self, db: _Db | None = None, path=None):
        self.db = db or _Db(path)

    def record(self, user_id: str, coefficient: str, value: float) -> None:
        self.db.execute(
            "INSERT INTO measurements(user_id, coefficient, value, recorded_at) VALUES (?,?,?,?)",
            (user_id, coefficient, float(value), _now()),
        )

    def _by_coefficient(self, user_id: str) -> dict[str, list[float]]:
        rows = self.db.query(
            "SELECT coefficient, value FROM measurements WHERE user_id=?", (user_id,)
        )
        out: dict[str, list[float]] = {}
        for r in rows:
            out.setdefault(r["coefficient"], []).append(r["value"])
        return out

    def overrides_for(self, user_id: str) -> dict:
        from aqua_model import calibration
        out: dict[str, float] = {}
        for key, vals in self._by_coefficient(user_id).items():
            if len(vals) < self._MIN_OBS:
                continue
            try:
                cal = calibration.get(key)
            except KeyError:
                continue
            mean = sum(vals) / len(vals)
            if cal.emp_low <= mean <= cal.emp_high:
                out[key] = round(mean, 4)
        return out

    def calibration_report(self, user_id: str) -> list[dict]:
        from aqua_model import calibration
        report = []
        for key, vals in self._by_coefficient(user_id).items():
            mean = round(sum(vals) / len(vals), 4)
            try:
                cal = calibration.get(key)
            except KeyError:
                report.append({"coefficient": key, "n": len(vals), "mean": mean,
                               "applied": False, "seed": None, "emp_low": None,
                               "emp_high": None, "in_range": None})
                continue
            in_range = cal.emp_low <= mean <= cal.emp_high
            report.append({
                "coefficient": key, "n": len(vals), "mean": mean,
                "applied": len(vals) >= self._MIN_OBS and in_range,
                "seed": cal.seed, "emp_low": cal.emp_low, "emp_high": cal.emp_high,
                "in_range": in_range,
            })
        return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_store.py -v`
Expected: PASS (all, including the 4 new calibration tests)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/store.py agronaut_agent/tests/test_store.py
git commit -m "feat(store): measurements table + CalibrationStore (>=2 in-range -> bounded override)"
```

---

### Task 4: `runtime` calibration context + `record_measurement` tool

**Files:**
- Modify: `agronaut_agent/runtime.py`
- Modify: `agronaut_agent/tools.py`
- Test: `agronaut_agent/tests/test_tools.py`

**Interfaces:**
- Consumes: `CalibrationStore` (Task 3); `runtime.get_current()` (memory store for the profile).
- Produces:
  - `runtime.set_current(memory_store, user_id, followups=None, community=None, calibration=None)` + `runtime.get_calibration()`.
  - `record_measurement(metric, value) -> str` tool, appended to `AGRONAUT_TOOLS` (→ **12**).

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_tools.py`:

```python
def test_registry_includes_record_measurement():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "record_measurement" in names
    assert len(AGRONAUT_TOOLS) == 12


def test_record_measurement_maps_metric_to_qualified_key():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import record_measurement

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    mem.set_facts("telegram:1", {"fish_species": "tilapia", "crop": "lettuce"})
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        out = record_measurement.invoke({"metric": "harvest_weight", "value": 0.45})
        assert "recorded" in out.lower()
        assert cal._by_coefficient("telegram:1") == {"tilapia.harvest_weight": [0.45]}
        # yield maps to the crop
        record_measurement.invoke({"metric": "yield", "value": 12.0})
        assert "lettuce.yield" in cal._by_coefficient("telegram:1")
    finally:
        runtime.clear_current()


def test_record_measurement_rejects_unknown_metric_and_bad_value():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import record_measurement

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    mem.set_facts("telegram:1", {"fish_species": "tilapia", "crop": "lettuce"})
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        assert "metric" in record_measurement.invoke({"metric": "weight", "value": 1}).lower()
        assert "number" in record_measurement.invoke({"metric": "fcr", "value": -1}).lower()
    finally:
        runtime.clear_current()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -k "record_measurement or registry" -v`
Expected: FAIL — `ImportError: cannot import name 'record_measurement'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/runtime.py`, add the context var and accessor. Add after `_community`:

```python
_calibration = contextvars.ContextVar("agronaut_calibration", default=None)
```

Update `set_current`/`clear_current` and add `get_calibration`:

```python
def set_current(memory_store, user_id: str, followups=None, community=None, calibration=None) -> None:
    _current.set((memory_store, user_id))
    _followups.set(followups)
    _community.set(community)
    _calibration.set(calibration)


def clear_current() -> None:
    _current.set(None)
    _followups.set(None)
    _community.set(None)
    _calibration.set(None)
```

```python
def get_calibration():
    """Return the CalibrationStore for the in-flight message, or None if unset."""
    return _calibration.get()
```

In `agronaut_agent/tools.py`, add the tool (after `schedule_followup`/the community tools, before `AGRONAUT_TOOLS`):

```python
# metric -> (calibration-key suffix, which profile field names the species/crop)
_MEASUREMENT_METRICS = {
    "fcr": ("fcr", "fish_species"),
    "harvest_weight": ("harvest_weight", "fish_species"),
    "yield": ("yield", "crop"),
}


@tool
def record_measurement(metric: str, value: float) -> str:
    """Record a REAL measured outcome from the operator's OWN running system so their future
    sizings calibrate to reality. Call ONLY with a number the user actually measured — never an
    estimate or a model output. metric is one of: 'fcr' (feed used / weight gained),
    'harvest_weight' (kg per fish), 'yield' (kg per m² per year of the crop). The value is
    combined with their species/crop; once you have >=2 in-range measurements it calibrates
    their sizing."""
    cur = runtime.get_current()
    cal = runtime.get_calibration()
    if cur is None or cal is None:
        return "Can't record a measurement right now."
    mem, user_id = cur
    m = (metric or "").strip().lower()
    if m not in _MEASUREMENT_METRICS:
        return f"Unknown metric {metric!r}. Use one of: fcr, harvest_weight, yield."
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "The measurement must be a number."
    if not (0 < v < 100000):
        return "That measurement doesn't look like a real number — please double-check it."
    suffix, profile_field = _MEASUREMENT_METRICS[m]
    subject = mem.get_facts(user_id).get(profile_field)
    if not subject:
        need = "fish species" if profile_field == "fish_species" else "crop"
        return f"Tell me your {need} first so I can record that measurement."
    coefficient = f"{str(subject).strip().lower()}.{suffix}"
    cal.record(user_id, coefficient, v)
    return f"Recorded — I'll use your measurements to calibrate future sizings ({coefficient})."
```

Append `record_measurement` to `AGRONAUT_TOOLS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -v`
Expected: PASS (registry now 12)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/runtime.py agronaut_agent/tools.py agronaut_agent/tests/test_tools.py
git commit -m "feat(tools): record_measurement + runtime calibration context"
```

---

### Task 5: Apply overrides in the sizing/optimize LLM tools + honest surfacing

**Files:**
- Modify: `agronaut_agent/tools.py` (`size_aquaponics_system`, `optimize_fish_crop_ratio`)
- Test: `agronaut_agent/tests/test_tools.py`

**Interfaces:**
- Consumes: `runtime.get_calibration()` (Task 4); `CalibrationStore.overrides_for`/`calibration_report` (Task 3); `size_system`/`optimize` `overrides=` (Task 2).
- Produces: the two model-facing tools fetch the operator's overrides server-side and pass them to the model; the sizing tool appends an honest calibration note.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_tools.py`:

```python
def test_size_tool_applies_calibration_and_labels_it():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import size_aquaponics_system

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    cal.record("telegram:1", "tilapia.harvest_weight", 0.4)
    cal.record("telegram:1", "tilapia.harvest_weight", 0.4)   # mean 0.4, in range -> applied
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        out = size_aquaponics_system.invoke(
            {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
             "temperature_c": 27, "water_budget_lpd": 500})
        assert "FEASIBLE" in out
        assert "calibrat" in out.lower()             # honest calibration note present
        assert "tilapia.harvest_weight" in out
    finally:
        runtime.clear_current()


def test_size_tool_without_calibration_is_unchanged():
    from agronaut_agent.tools import size_aquaponics_system
    # no runtime context -> no overrides, plain design
    out = size_aquaponics_system.invoke(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
         "temperature_c": 27, "water_budget_lpd": 500})
    assert "FEASIBLE" in out
    assert "calibrated from your" not in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_tools.py -k "calibration_and_labels or without_calibration" -v`
Expected: FAIL — the calibration note is absent (`assert "calibrat" in out.lower()`)

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/tools.py`, add a small helper (near the top, after the imports) that builds the honest note:

```python
def _calibration_note(user_id) -> str:
    """A one-line-per-coefficient note of which coefficients were calibrated from the operator's
    own measurements. Empty string if none applied."""
    cal = runtime.get_calibration()
    if cal is None:
        return ""
    applied = [r for r in cal.calibration_report(user_id) if r.get("applied")]
    if not applied:
        return ""
    lines = "\n".join(
        f"- {r['coefficient']}: {r['mean']} — calibrated from your {r['n']} measurements "
        f"(literature seed {r['seed']})"
        for r in applied
    )
    return "\n\nCalibrated from YOUR data (bounded to the published range):\n" + lines
```

Change `size_aquaponics_system` to fetch and apply overrides and append the note. Replace its body's model call:

```python
    try:
        design = validate_design_input(
            fish_species, crop, grow_area_m2, temperature_c, water_budget_lpd,
            _clean_optional(source_water_note),
        )
    except ValidationError as err:
        return serialize.serialize_validation_error(err.errors)
    cur = runtime.get_current()
    overrides, note = None, ""
    if cur is not None:
        _mem, user_id = cur
        cal = runtime.get_calibration()
        if cal is not None:
            overrides = cal.overrides_for(user_id) or None
            note = _calibration_note(user_id)
    return serialize.serialize_design_output(size_system(design, overrides=overrides)) + note
```

Change `optimize_fish_crop_ratio` to apply overrides too (no note needed there — keep it minimal). After it builds `OptimizeInput` and before returning, fetch overrides and pass them:

```python
    cur = runtime.get_current()
    overrides = None
    if cur is not None:
        _mem, user_id = cur
        cal = runtime.get_calibration()
        if cal is not None:
            overrides = cal.overrides_for(user_id) or None
    res = optimize(
        OptimizeInput(
            grow_area_m2=grow_area_m2,
            temperature_c=temperature_c,
            water_budget_lpd=water_budget_lpd,
            objective=obj,
        ),
        overrides=overrides,
    )
    return serialize.serialize_optimize_result(res)
```

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/tools.py agronaut_agent/tests/test_tools.py
git commit -m "feat(tools): sizing/optimize apply per-operator calibration + honest label"
```

---

### Task 6: Agent wiring + system prompt

**Files:**
- Modify: `agronaut_agent/core.py`
- Test: `agronaut_agent/tests/test_core_dryrun.py`

**Interfaces:**
- Consumes: `CalibrationStore` (Task 3); `runtime.set_current(..., calibration=...)` (Task 4).
- Produces: `self._calibration: CalibrationStore`; `handle_message` passes it into `runtime.set_current`; a `SYSTEM_PROMPT` bullet directs recording real measurements.

- [ ] **Step 1: Write the failing test**

Append to `agronaut_agent/tests/test_core_dryrun.py`:

```python
class _MeasureFake:
    """Turn 1 -> record a measurement; then -> final text."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Logged your harvest weight.")
        return AIMessage(content="", tool_calls=[{
            "name": "record_measurement", "id": "m1",
            "args": {"metric": "harvest_weight", "value": 0.45}}])


def test_measurement_reaches_calibration_store(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_MeasureFake())
    agent._mem.set_facts("telegram:1", {"fish_species": "tilapia", "crop": "lettuce"})
    agent.handle_message("telegram", "1", "my tilapia harvested at 0.45 kg")
    assert agent._calibration._by_coefficient("telegram:1") == {"tilapia.harvest_weight": [0.45]}


def test_system_prompt_mentions_record_measurement():
    from agronaut_agent.core import SYSTEM_PROMPT
    assert "record_measurement" in SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/test_core_dryrun.py -k "measurement or record_measurement" -v`
Expected: FAIL — `AttributeError: 'AgronautAgent' object has no attribute '_calibration'`

- [ ] **Step 3: Write minimal implementation**

In `agronaut_agent/core.py`:

Change the store import to add `CalibrationStore`:

```python
from .store import (_Db, ConversationStore, MemoryStore, FollowupStore, CommunityStore,
                    CalibrationStore, _now)
```

In `__init__`, next to `self._community`:

```python
        self._community = CommunityStore(db)
        self._calibration = CalibrationStore(db)
```

In `handle_message`, extend the `set_current` call:

```python
        runtime.set_current(self._mem, user_id, self._followups, self._community, self._calibration)
```

In `SYSTEM_PROMPT`, add a bullet right after the Phase-4 nomination bullet (the line ending `The owner approves before anything is shared.`):

```
- When the operator reports a REAL measured result from their own system — the weight their
  fish reached, their measured FCR (feed used vs weight gained), or their crop yield — call
  record_measurement (metric fcr / harvest_weight / yield). Never for an estimate or a number
  you produced; only their real measurement. It calibrates their future sizings to reality.
```

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

Run: `.venv/bin/python -m pytest agronaut_agent/tests/ -q`
Expected: PASS (all — the 5th `set_current` arg is optional, existing callers unaffected)

- [ ] **Step 5: Commit**

```bash
git add agronaut_agent/core.py agronaut_agent/tests/test_core_dryrun.py
git commit -m "feat(core): wire CalibrationStore + record-measurement prompt"
```

---

### Task 7: Live smoke test + README

**Files:**
- Modify: `README.md`
- No test file (live verification + docs).

**Interfaces:**
- Consumes: the whole feature; a configured NVIDIA provider (already in `.env`).

- [ ] **Step 1: Drive the loop end-to-end (record → calibrate → sized system reflects it)**

Run:

```bash
PYTHONPATH=/home/rekin226/Desktop/code_space/Agronaut .venv/bin/python -c "
import agent
from agronaut_agent.core import AgronautAgent
a = AgronautAgent(db_path='/tmp/calib_smoke.sqlite3')
a._mem.set_facts('telegram:1', {'fish_species':'tilapia','crop':'lettuce'})
# two real harvest-weight measurements (mean 0.4, within tilapia range 0.4-0.8)
a._calibration.record('telegram:1','tilapia.harvest_weight',0.4)
a._calibration.record('telegram:1','tilapia.harvest_weight',0.4)
print('OVERRIDES:', a._calibration.overrides_for('telegram:1'))
from aqua_model import size_system
from aqua_model.validate import validate_design_input
d = validate_design_input('tilapia','lettuce',20,27,500)
print('fish_count SEED    :', size_system(d).fish_count)
print('fish_count CALIB   :', size_system(d, overrides=a._calibration.overrides_for('telegram:1')).fish_count)
"
```
Expected: `OVERRIDES` shows `{'tilapia.harvest_weight': 0.4}`; the calibrated `fish_count` differs from the seed (more fish at a lighter harvest weight) — the operator's real data changed their sizing, within the published range.

- [ ] **Step 2: Add the README note**

Under the "### Consultative agent" subsection in `README.md`, append:

```markdown
And it calibrates to reality: when you report real measured outcomes (harvest weight, FCR,
crop yield), Agronaut tunes *your* future sizings toward your system — bounded to the
published empirical ranges, so a measurement can only move a coefficient within what the
literature allows, and every calibrated number is labeled.
```

- [ ] **Step 3: Run the full suite one last time**

Run: `.venv/bin/python -m pytest aqua_model/tests/ agronaut_agent/tests/ -q`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document per-operator coefficient calibration"
```

---

## Notes for the implementer

- **Run tests with the venv:** always `.venv/bin/python -m pytest ...`.
- **The trust boundary is the whole point of Tasks 1–2:** seeds are never written — `dataclasses.replace` returns a NEW species/crop; every override is bounded by `validate_overrides` to the published empirical range. Keep the no-override path byte-identical.
- **All stores share one `_Db`** — the agent builds `db = _Db(db_path)` once and passes it to every store, now including `CalibrationStore`.
- **`aqua_model` stays LLM-free** — `overrides.py` imports only from within `aqua_model`. The agent computes overrides; the model just validates + applies them.

# Agronaut — Per-Operator Coefficient Calibration / Phase 5 (Design)

**Date:** 2026-07-03
**Status:** Approved for planning
**Scope:** Real measured outcomes from an operator's running system calibrate THEIR OWN future
sizings — a per-operator overlay, bounded to published ranges, seeds never mutated.

---

## 1. Problem

The sizing engine ships seed coefficients meant to be "calibrated against a real running
system" (README). Phase 5 does that per operator: their measured FCR, harvest weight, and crop
yield personalize their sizings toward their reality — without corrupting the deterministic,
auditable trust zone.

This is the roadmap's most sensitive phase: it touches `aqua_model`. The design honors the
existing calibration philosophy in `aqua_model/calibration.py`: seeds are NEVER changed; values
are bounded to a **published empirical range**; out-of-range values are **surfaced, not applied**.

### What already exists (reuse)

- `aqua_model/calibration.py` — each load-bearing coefficient (`{species}.fcr`, `{crop}.frr`,
  `{crop}.yield`, `{species}.harvest_weight`) pinned to a sourced empirical range; `get(key)`
  returns `emp_low`/`emp_high`. It never mutates seeds — the exact philosophy this phase extends.
- `aqua_model/sizing.py` `size_system(design)` reads `species.fcr`, `crop.frr_g_per_m2_day`,
  `species.harvest_weight_kg`, `crop.yield_kg_per_m2_year`.
- `validate_design_input` — the single trust gate for model inputs.
- Per-request `runtime` context vars (Phases 3–4 added stores this way).

---

## 2. Calibration model & data flow

```
operator reports a REAL measurement ("my tilapia finished at 0.45 kg")
        │  LLM maps it → record_measurement(metric, value)
        ▼
measurements row (user_id, coefficient="tilapia.harvest_weight", value=0.45)
        │
        ▼  per-operator calibration = mean of their measurements for that coefficient
        │      • needs ≥2 measurements
        │      • mean must be WITHIN the published empirical range (calibration.py)
        │      • out-of-range mean → FLAGGED, NOT applied (falls back to seed)
        ▼
sizing FOR THAT OPERATOR:
   size_system(design, overrides={"harvest_weight_kg": 0.45, ...})   ← bounded overrides
        │      seeds NEVER mutated; validate_overrides bounds each to the empirical range
        ▼
   output labels each coefficient: "calibrated from your N measurements" vs "literature seed"
```

**Three calibratable metrics** (operator-measurable): **FCR** → `{species}.fcr`, **harvest weight**
→ `{species}.harvest_weight`, **crop yield** → `{crop}.yield`.

**A metric is calibratable only where `calibration.py` provides an empirical range for that exact
species/crop key** — you cannot bound a value without a published range. Today that means FCR for
all four species, harvest weight for tilapia, and yield for lettuce; other species/crops simply
aren't calibrated yet (their coefficient stays on the seed). `overrides_for` looks each key up via
`calibration.get(key)` and skips any key that has no range (KeyError) — never guessing a bound.
Adding a metric later is just adding a range entry in `calibration.py`.

**Trust philosophy preserved end-to-end:**
- **Seeds immutable** — calibration is a per-operator overlay, never a write to shipped coefficients.
- **Bounded by the gate** — `validate_overrides` rejects any override outside the coefficient's
  empirical range; personalization can only move within literature-plausible bounds.
- **Honest** — every calibrated number is labeled (operator's N measurements vs literature seed).
- **Computed inside the model** — overrides flow into `size_system`, so biomass/feed/tank stay
  consistent; nothing is post-hoc patched.

---

## 3. Components

### 3.1 `record_measurement` tool (`tools.py`)
- Args: `metric` (`fcr` | `harvest_weight` | `yield`), `value` (the operator's actual measured
  number — never an estimate or model output).
- Maps `metric` + profile (`fish_species`/`crop`) → the qualified coefficient key (e.g.
  `tilapia.fcr`); stores via `CalibrationStore`. Missing profile field → asks for it. Registry → 12.

### 3.2 `CalibrationStore` (`store.py`)
- `record(user_id, coefficient, value)` — append a measurement.
- `overrides_for(user_id, species, crop) -> dict` — for each of the three coefficients: include a
  bounded override (`{"fcr", "harvest_weight_kg", "yield_kg_per_m2_year"}` model keys) only when
  ≥2 measurements exist AND their mean is within `calibration.get(key)`'s range; otherwise omit.
- `calibration_report(user_id, species, crop) -> list[dict]` — per-coefficient status (n, mean,
  applied/flagged) for honest surfacing, including out-of-range flags.

### 3.3 `size_system` overrides (`aqua_model` — trust-zone change)
- `size_system(design, overrides: dict | None = None)`; applies overrides in place of the read
  seed values for THIS sizing only.
- `validate_overrides(design, overrides)` (`validate.py`): each override value must be within the
  coefficient's empirical range for the design's species/crop — out-of-range raises
  `ValidationError`. The no-overrides path is byte-identical to today (regression guard).

### 3.4 Sizing tool wiring (`tools.py`)
- `size_aquaponics_system` fetches `overrides_for(...)` (server-side, deterministic — the LLM never
  chooses override values) and passes them to `size_system`.

### 3.5 Honest surfacing (`agronaut_agent/serialize.py`)
- The serialized sizing output labels each calibrated coefficient: *"FCR 1.5 — calibrated from
  your 3 measurements (literature seed 1.7)."*

### 3.6 Prompt + runtime (`core.py`, `runtime.py`)
- Prompt bullet: call `record_measurement` when the operator reports a real measured result (weighed
  harvest, computed FCR, measured yield) — never for estimates.
- `runtime.get_calibration()` exposes the store, like the Phase-3/4 stores; the agent constructs
  `CalibrationStore(db)` on the shared `_Db` and passes it into `runtime.set_current`.

---

## 4. Guardrails (structural — trust zone)

- Seeds never written; calibration is per-operator data applied only as a `size_system` override.
- `validate_overrides` bounds every override to the published empirical range.
- ≥2 in-range measurements to apply; out-of-range mean flagged, never applied.
- The LLM only *reports* measurements; the store computes bounded overrides.
- Every calibrated number labeled with provenance (extends existing coefficient honesty).

---

## 5. Error handling / edge cases

- **Unknown `metric`** → tool lists valid metrics, records nothing.
- **Missing species/crop** for the key → tool asks for it first.
- **Non-numeric / ≤0 / absurd value** → rejected with a clear message.
- **<2 measurements** → no override; coefficient stays on the seed (calibration simply not active yet).
- **Out-of-range mean** → flagged; sizing falls back to the seed (never a rejected sizing).
- **Bad override reaching the model** (defensive) → `ValidationError`, surfaced by the tool, never a crash.
- **Concurrency** — `CalibrationStore` uses the shared `_Db` lock.

---

## 6. Testing (deterministic)

- **`aqua_model`:** `size_system(overrides=…)` uses the override and keeps downstream numbers
  consistent; `validate_overrides` accepts in-range, rejects out-of-range; no-overrides path
  unchanged (regression guard).
- **Store:** `record`; `overrides_for` applies only with ≥2 in-range, omits <2 and out-of-range;
  `calibration_report` reflects status.
- **Tool:** `record_measurement` maps metric+profile→key; rejects unknown metric / bad value /
  missing profile; sizing tool passes overrides and labels them; registry == 12.
- **Agent:** with a fake model, a reported measurement reaches the store; a subsequent sizing
  reflects the calibration.

---

## 7. Files touched

| File | Change |
|------|--------|
| `aqua_model/sizing.py` | `size_system(design, overrides=None)` + apply overrides |
| `aqua_model/validate.py` | `validate_overrides(design, overrides)` — bound to empirical range |
| `agronaut_agent/serialize.py` | label calibrated coefficients in the sizing output |
| `agronaut_agent/store.py` | `measurements` table + `CalibrationStore` |
| `agronaut_agent/tools.py` | `record_measurement` (→12); `size_aquaponics_system` applies overrides |
| `agronaut_agent/core.py` | construct `CalibrationStore`; pass into `runtime`; prompt bullet |
| `agronaut_agent/runtime.py` | carry `CalibrationStore` + `get_calibration()` |
| tests | model, store, tool, agent cases above |

---

## 8. Out of scope (YAGNI)

- Global seed-tuning / cross-operator rollup (seeds stay immutable; this is per-operator only).
- Calibrating `frr` or coefficients operators don't directly measure.
- Weighted/Bayesian fits or time-decay — a simple bounded mean suffices.

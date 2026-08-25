# Agronaut — Species & Crop Table Expansion (Design)

**Date:** 2026-07-04
**Status:** Approved for planning
**Scope:** Add sourced seed entries to the deterministic model: 1 fish (common carp) + 5 crops
(kale, swiss chard, spinach, cucumber, pepper), with calibration ranges and tests.

---

## 1. Problem

The knowledge-base audit found the deterministic model narrow: 4 fish, 3 crops. The Tier-1/2
knowledge docs now cover many more fish/crops *qualitatively*, but the sizing/optimize **math**
can only handle the 4/3 it has seed coefficients for. This adds well-sourced seed entries so the
model can size and optimize a broader, common set.

These coefficients enter the **trust zone** (they drive deterministic sizing/optimize math), so
they are sourced and ranged like the existing seeds, and are honest *seed estimates* — anchored
to FAO 589 bands and the existing seeds, meant to be Phase-5-calibrated, not per-cultivar truth.

### What makes this purely additive (verified)

- `OptimizeInput.fish_palette`/`crop_palette` default to `tuple(sorted(SPECIES))`/`sorted(CROPS)`
  — new entries auto-join the optimizer search.
- `validate_design_input` accepts any key present in `SPECIES`/`CROPS`.
- No test hardcodes the species/crop counts; `test_list_supported` only checks that tilapia /
  lettuce / water_efficiency appear (still true).

So the change is: add data rows (`species.py`, `crops.py`) + calibration ranges (`calibration.py`)
+ tests. No architecture change; the trust gate, sizing formulas, and no-new-entry behavior are
untouched.

---

## 2. New fish — Common carp (`Cyprinus carpio`)

`FishSpecies` fields (anchored to the existing tilapia/catfish seeds):

| field | value | rationale |
|-------|-------|-----------|
| name | `carp` | canonical key |
| feeding_rate_pct_bw | 1.5 | grow-out omnivore (matches tilapia/catfish) |
| fcr | 2.0 | carp pond FCR ~1.5–2.5; 2.0 mid |
| feed_protein_pct | 32.0 | carp grow-out feed ~30–35% |
| body_protein_pct | 16.0 | typical freshwater fish wet body |
| harvest_weight_kg | 1.0 | common 0.5–2 kg target |
| stocking_density_kg_m3 | 25.0 | hardy, moderate–high density |
| temp_min_c | 8.0 | very cold-tolerant |
| temp_opt_low_c | 23.0 | |
| temp_opt_high_c | 30.0 | |
| temp_max_c | 34.0 | |
| source | `"LIT (carp aquaculture)"` | among the most-farmed fish globally |

---

## 3. New leafy crops (UVI/FAO leafy FRR band 40–100 g/m²/day; category `leafy`)

| field | kale | swiss chard | spinach |
|-------|------|-------------|---------|
| name | `kale` | `swiss_chard` | `spinach` |
| frr_g_per_m2_day | 65.0 | 60.0 | 55.0 |
| frr_low | 45.0 | 40.0 | 40.0 |
| frr_high | 90.0 | 85.0 | 80.0 |
| n_uptake_g_per_m2_day | 0.9 | 0.85 | 0.8 |
| yield_kg_per_m2_year | 20.0 | 22.0 | 15.0 |
| edible_protein_pct | 3.3 | 1.8 | 2.9 |
| ph_min | 5.5 | 5.5 | 6.0 |
| ph_max | 7.0 | 7.0 | 7.0 |
| temp_min_c | 7.0 | 10.0 | 7.0 |
| temp_max_c | 24.0 | 27.0 | 24.0 |
| source | `"FAO589/UVI (leafy band)"` | same | same |

---

## 4. New fruiting crops (FAO tomato band 80–140 g/m²/day; category `fruiting`)

| field | cucumber | pepper (bell) |
|-------|----------|---------------|
| name | `cucumber` | `pepper` |
| frr_g_per_m2_day | 100.0 | 100.0 |
| frr_low | 80.0 | 80.0 |
| frr_high | 130.0 | 130.0 |
| n_uptake_g_per_m2_day | 1.5 | 1.4 |
| yield_kg_per_m2_year | 35.0 | 20.0 |
| edible_protein_pct | 0.7 | 1.0 |
| ph_min | 5.5 | 5.5 |
| ph_max | 6.5 | 6.5 |
| temp_min_c | 18.0 | 18.0 |
| temp_max_c | 30.0 | 30.0 |
| source | `"FAO589 (fruiting band)"` | same |

---

## 5. Calibration ranges (`calibration.py`)

Extend the seed-vs-empirical-range honesty layer with the new **load-bearing** coefficients, so
`discrepancies()` covers them and Phase-5 calibration can bound them:

- `carp.fcr` — empirical range **1.5–2.5** g feed / g gain (carp pond/RAS aquaculture).
- `kale.frr` — **45–90**, `swiss_chard.frr` — **40–85**, `spinach.frr` — **40–80** (each = its seed
  low–high, UVI/FAO leafy feeding-rate band).
- `cucumber.frr` — **80–130**, `pepper.frr` — **80–130** (FAO fruiting feeding-rate band).

Each new `SizingCalibration` cites at the band level (Somerville et al. 2014, FAO 589; UVI/Rakocy
for leafy). The seed values above all sit **inside** these ranges by construction (so
`discrepancies()` stays clean).

---

## 6. Honesty framing (unchanged posture)

The new coefficients are **seed defaults**, sourced at the FAO 589 / band level exactly like the
existing lettuce/tomato seeds — not precise per-cultivar measurements. The model already surfaces
every coefficient with its source and "calibration seeds, not guarantees" caveat, and Phase-5
per-operator calibration lets a grower tune these toward their real system. No change to the
honesty layer is needed beyond registering the sources.

---

## 7. Error handling / edge cases

- **Naming:** `swiss_chard` and `pepper` are the canonical keys; the LLM/user may say "chard" or
  "bell pepper" — the consultative layer maps synonyms; the model key stays canonical. (No
  synonym table in the model; out of scope.)
- **Temperature feasibility:** carp's wide band and the crops' bands are set so a sensible design
  (e.g. carp + lettuce at 25 °C) is feasible; cold/hot mismatches are handled by the existing
  temperature-feed-factor logic, unchanged.
- Adding entries cannot break existing designs — existing keys and their coefficients are untouched.

---

## 8. Testing

- **Model (`aqua_model`):**
  - Every species in `SPECIES` sizes a valid system (`size_system` → feasible, `fish_count >= 1`,
    positive volumes) with a compatible crop; every crop in `CROPS` sizes with tilapia.
  - `optimize` runs with the default (expanded) palettes and returns a feasible best.
  - The new calibration seeds sit within their ranges: `discrepancies()` does not include any new
    key.
- **Agent (`agronaut_agent`):** `list_supported_species_and_crops` includes the new names;
  `validate_design_input` accepts `carp`/`kale`/…; existing tests unchanged.

---

## 9. Files touched

| File | Change |
|------|--------|
| `aqua_model/species.py` | add `CARP` + register in `SPECIES` |
| `aqua_model/crops.py` | add `KALE`, `SWISS_CHARD`, `SPINACH`, `CUCUMBER`, `PEPPER` + register in `CROPS` |
| `aqua_model/calibration.py` | add `SizingCalibration` entries for `carp.fcr` + the 5 new `*.frr` |
| `aqua_model/tests/` | parametrized size/optimize coverage + discrepancies-clean check |
| `agronaut_agent/tests/` | (if needed) confirm `list_supported` includes the new names |

---

## 10. Out of scope (YAGNI)

- Synonym/alias mapping in the model (the consultative LLM layer handles "chard" → `swiss_chard`).
- Ornamental fish (koi/goldfish) — no food harvest weight, doesn't fit the food/protein objectives.
- Thinly-sourced fish (largemouth bass, yellow perch) — deferred until better-sourced.
- Re-sizing media-bed/NFT configurations (still raft/DWC only).

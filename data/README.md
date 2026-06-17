# Data — open aquaponics datasets (reality layer)

This folder grounds Agronaut's seed coefficients against real running systems. The model
ships with literature/FAO defaults; this is where they meet measured data.

## What's here

| Path | Committed? | What it is |
|---|---|---|
| `raw/IoTPond{1..4}.csv` | **No** (gitignored, ~20 MB) | Raw per-minute pond readings — fetch on demand |
| `empirical_envelope.json` | **Yes** | Operating-envelope reality layer: per-channel distribution + trust flags |
| `coefficient_sources.json` | **Yes** | Sizing-coefficient reality layer: seed vs published range + verdict |
| `reference_systems.json` | **Yes** | Acceptance gate: documented published systems the model must reproduce |

Fetch the raw data (no Kaggle/Mendeley login needed):

```bash
python scripts/fetch_aquaponics_data.py
```

This downloads the four ponds and rebuilds `empirical_envelope.json`.

## Source & license

**Udanor, C.N. et al. (2022).** "An internet of things labelled dataset for aquaponics fish
pond water quality monitoring system." *Data in Brief* 43:108400.
DOI: [10.1016/j.dib.2022.108400](https://doi.org/10.1016/j.dib.2022.108400) — **CC BY 4.0**.

Public mirror used by the fetch script (co-author Ogbuokiri):
Kaggle `ogbuokiriblessing/sensor-based-aquaponics-fish-pond-datasets`.

- **4 ponds**, ~**233,000** readings, per-minute, **19 Jun – 31 Oct 2021**.
- Sensors: temperature, turbidity, dissolved oxygen, pH, ammonia, nitrate + fish length/weight.

## Column mapping

Raw columns are renamed to the canonical `logging_schema` vocabulary on load, so this dataset
and our own future install logs share one schema (see `aqua_model/datasets.py:COLUMN_MAP`):

| Raw column | Canonical field |
|---|---|
| `created_at` | `timestamp` |
| `temperature` | `water_temp_c` |
| `disolved_oxg` | `dissolved_oxygen_mg_l` |
| `ph` | `ph` |
| `ammonia` | `ammonia_mg_l` |
| `nitrate` | `nitrate_mg_l` |
| `turbidity` | `turbidity_ntu` |
| `fish_length` / `fish_weight` | `fish_length_cm` / `fish_weight_g` |

## Honest caveats (read before trusting a channel)

This is raw, low-cost IoT sensor data. Some channels are **pinned at their sensor rail** and
are flagged `low (sensor saturation)` in the artifact — do **not** calibrate against them:

- **turbidity** — median 100 (ceiling); saturated.
- **ammonia** — median 10 mg/L (ceiling); saturated.
- dissolved oxygen and nitrate are noisy with implausible spikes; use medians, not extremes.

**Trustworthy channels:** water temperature, pH, and the fish growth trajectory.

## What it already tells us

The envelope cross-check (`compare_to_model_envelope`) against tilapia + lettuce surfaces two
real tensions between the seed coefficients and reality:

- **Temperature** — real median **24.5 °C**; only ~0.4 % of readings fall inside tilapia's
  27–30 °C "optimal" band (100 % stay within the 14–36 °C survival band). The model's optimum
  runs warmer than these ponds actually did; `temperature_feed_factor` scales feeding down.
- **pH** — real median **7.33**, above the leafy-crop compromise ceiling of 7.0. Real ponds run
  more alkaline than the lettuce ideal.

These are signals for calibration, not failures — exactly what a reality layer is for.

## Sizing coefficients — sourced ranges (`coefficient_sources.json`)

The IoT dataset grounds the *operating envelope*; it does not calibrate the *sizing*
coefficients (FCR, FRR, yield). Those are pinned to the published literature instead, in
`aqua_model/calibration.py`, which reads each seed live from `species.py`/`crops.py` and
checks it against a cited empirical range. Regenerate with:

```python
from aqua_model import calibration; calibration.write_artifact()
```

Key sources: Rakocy et al. (2004), *Acta Hort.* 648 (basil FRR 81–100 g/m²/day); Somerville
et al. (2014) FAO 589 (leafy/fruiting FRR, harvest weight); Shaw et al. (2022), *Sustainability*
[10.3390/su14074064](https://doi.org/10.3390/su14074064) (tilapia FCR 0.86–1.79).

## Acceptance gate — published reference systems (`reference_systems.json`)

The model's calibration gate (`aqua_model/tests/test_calibration.py`) was meant to reproduce
the founder's own running system. With no private system available, it instead validates against
fully documented systems from the literature: the model must reproduce each one's **daily feed
input (= plant area × feeding-rate ratio) within ±15%**.

The reference is the **UVI commercial system** (Rakocy et al. 2004; Rakocy 1988) — the most
completely documented small-scale aquaponic system published. Three operating points:

| System | Crop | Area | Measured feed | Model feed | Error |
|---|---|---|---|---|---|
| UVI batch basil | basil | 214 m² | 17,420 g/day (FRR 81.4) | 18,190 | **4.4%** |
| UVI staggered basil | basil | 214 m² | 21,314 g/day (FRR 99.6) | 18,190 | **14.7%** |
| UVI Bibb lettuce | lettuce | 214 m² | 12,198 g/day (FRR 57) | 12,840 | **5.3%** |

The same paper independently corroborates other seeds: **tilapia FCR 1.79** (seed 1.7),
**pH 7.1–7.4** and **nitrate-N ~42 mg/L** (the alkaline-running pattern the IoT data also showed).
These systems are *coefficient-defining* (the FRR seeds derive from UVI), so the gate proves the
model's machinery reproduces real systems end-to-end; the `independent` flag records this. Add a
private or independent system any time by appending to the JSON — the tests pick it up
automatically.

**Resolved discrepancy:** `basil.frr` was the earlier **70 g/m²/day** stub, below the
UVI-measured basil band (**81–100**). It has been **recalibrated to 85 g/m²/day** (mid-band,
Rakocy et al. 2004), so every coefficient now sits within its sourced range. The old ambiguous
**"catfish"** has been **split** into `clarias` (African catfish, FCR ~0.8–1.0) and
`channel_catfish` (Ictalurus, FCR ~1.5–2.0), each pinned to its own tight, non-overlapping range.

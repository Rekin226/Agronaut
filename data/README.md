# Data — open aquaponics datasets (reality layer)

This folder grounds Agronaut's seed coefficients against real running systems. The model
ships with literature/FAO defaults; this is where they meet measured data.

## What's here

| Path | Committed? | What it is |
|---|---|---|
| `raw/IoTPond{1..4}.csv` | **No** (gitignored, ~20 MB) | Raw per-minute pond readings — fetch on demand |
| `empirical_envelope.json` | **Yes** | Operating-envelope reality layer: per-channel distribution + trust flags |
| `coefficient_sources.json` | **Yes** | Sizing-coefficient reality layer: seed vs published range + verdict |

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

**Resolved discrepancy:** `basil.frr` was the earlier **70 g/m²/day** stub, below the
UVI-measured basil band (**81–100**). It has been **recalibrated to 85 g/m²/day** (mid-band,
Rakocy et al. 2004), so every coefficient now sits within its sourced range. The old ambiguous
**"catfish"** has been **split** into `clarias` (African catfish, FCR ~0.8–1.0) and
`channel_catfish` (Ictalurus, FCR ~1.5–2.0), each pinned to its own tight, non-overlapping range.

# External field-data screening

The [2026-10-20 report](screening_2026_10.json) audits independently published data against
possible uses in Agronaut. It is an evidence inventory, not an irrigation benchmark.

Reproduce it offline from the repository checkout:

```sh
python -S -m scripts.audit_field_datasets --output /tmp/agronaut-field-screening.json
```

The script checks the retained files against publisher MD5 and recorded SHA-256 hashes,
then counts rows, identifiers, categories, blank cells and selected numeric ranges. It
fails on changed source bytes or malformed tables. Blank-cell counts do not establish
sensor quality, completeness of a field campaign or absence of missing events.

| Candidate | Observed evidence | Assessment for current models |
|---|---|---|
| [Burkina Faso soil measurements](https://doi.org/10.23708/LZ8A5B) | 19 samples, 27 columns; original data, dictionary and methods retained | Useful for source-definition and laboratory transcription review. No daily irrigation/weather sequence for soil-water validation. Dictionary wording needs clarification before semantic mapping. |
| [IrriDrone sprinkler measurements](https://doi.org/10.5281/zenodo.22017449) | 674 metadata rows and 23 distinct discharge values; CSV and README retained | Relative pressure categories are not measured heads. No surveyed hydraulic network or catch-test distribution is supplied in the table. Repeated video observations must not be assumed to be independent measurement events. |
| [Taiwan soybean study](https://doi.org/10.5061/dryad.0k6djhbgw) | Published metadata and README inspected; numeric archive unavailable in this audit | Candidate only. Described daily sensor summaries cannot be treated as instantaneous end-of-day readings. Units, depths and actual missingness remain unchecked. |

The `reviewer_assessed_limitations` in the report are Agronaut's assessment of applicability;
`observed_file_summary` is calculated from inspected files. Taiwan has no such summary.
No candidate was scored against a model, imported into a farm account or used to fit a
coefficient. No capability gained field-validation status.

To build a usable comparison, obtain the model's actual required inputs and independently
measured outputs from the same event/site: timing, measurement support, soil or hydraulic
configuration, water/weather records, quality information and source permissions. Establish
calibration/evaluation separation and acceptance tolerances before prediction assessment.
For regional deployment, a willing grower and local specialist still need to review the
chosen crop, site, language and workflow.

## Additional USDA source screening

The [Bushland 2016 report](bushland_2016_screening.json) inspects an independently published
maize water-balance workbook and retains its soil and measurement-convention references.
See the [source inventory](../../../data/field_reference/bushland_2016/README.md) for
attribution, reuse statements, checksums and interpretation limits.

Reproduce with `python -m scripts.audit_bushland_data --output /tmp/bushland-screening.json`
using the repository's `openpyxl` dependency. This separate audit reads the original XLSX
files; the earlier three-candidate audit above still uses only the Python standard library.

All 366 calendar rows are present, but 201 missing markers and one blank measurement
remain. The report exposes those gaps and signed instrument adjustments without imputing
or converting them. No Agronaut model was fitted or scored. Matching input records and
an agreed evaluation protocol are still needed before this becomes a benchmark.

The subsequent [paired weather preparation](bushland_2016_weather.json) adds the original
2016 station workbook and summarizes its 35,136 dated intervals into 366 source days.
Reproduce with `python -m scripts.prepare_bushland_weather --output /tmp/bushland-weather.json`.
It exposes untimed cells, incomplete location-specific rainfall, and differences between
published rainfall representations. It preserves fixed standard time and the distinction
between solar shortwave and net radiation, and between interval means and instantaneous
extremes. No model inputs are declared ready and no predictions are scored.

The [rainfall reconciliation](bushland_2016_rain_reconciliation.json) compares all
140,544 location-specific interval cells across the weather and water-balance workbooks.
Every numeric value and missing marker matches exactly. The 29 differing water-balance
daily totals form 13 pairs of consecutive days and one three-day group; each group's
totals agree within the 1e-8 mm arithmetic tolerance. This is consistent with different
daily allocation, but does not establish its cause or justify selecting either series.
The separate weather daily table still has 590 differences from interval totals.
Adding daily dew to water-balance daily rain does not reconcile the daily tables:
among 1,409 available pairs it increases differences from 442 to 1,123. No correction
is applied. These counts use different availability rules from the interval comparison.

Reproduce offline with
`python -m scripts.reconcile_bushland_rain --output /tmp/bushland-rain-reconciliation.json`.
The script verifies the pinned originals, interval sequence and headers, retains signed
residuals and missing categories, and selects no model inputs. The
[published weather-quality methods](https://acsess.onlinelibrary.wiley.com/doi/10.2134/age2018.09.0036)
provide context about separately processing lysimeter catch and distinguishing irrigation
from precipitation; they do not explain these particular daily discrepancies. Input
definitions and evaluation prerequisites remain unresolved. No model validation occurred.

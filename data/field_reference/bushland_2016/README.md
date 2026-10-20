# USDA Bushland 2016 source screening

These three original workbooks support a local source audit. They are not farm-account
records, model defaults or a completed validation benchmark. The manifest pins download
URLs, dataset versions, byte lengths, publisher MD5 and locally calculated SHA-256 hashes.

Sources and attribution:

- Evett, Steven R. and the USDA-ARS Bushland research team.
  [Maize evapotranspiration and water-balance data](https://doi.org/10.15482/USDA.ADC/1526334), version 1.
  Retained file: `2016_Maize_water_balance.xlsx`.
- Evett, Steven R. and the USDA-ARS Bushland research team.
  [Maize collection](https://doi.org/10.15482/USDA.ADC/1526317), version 1.
  Retained files: soil properties and measurement conventions.

The repository metadata declares [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
The workbook introductions additionally request scientist contact and involvement for
scientific research/publication. Both statements are retained in the manifest for review
before publishing a benchmark. No outreach or publication has occurred.

Run `python -m scripts.audit_bushland_data --output /tmp/bushland-screening.json` from
the repository root using an environment with `openpyxl` from `requirements.txt`.
The script verifies all three files, then inspects the daily water-balance tab only.
It reads stored values without recalculating formulas or executing supplied code.
It distinguishes source missing markers, blanks, zeros and negative values. It checks
the full calendar sequence and reports completeness separately for each lysimeter.
The soil and convention workbooks were read for definitions; their numerical tables
are not automatically mapped to Agronaut models.

The daily table has 366 rows and 26 columns, 201 missing markers and one blank
measurement (SE ET, day 366). No daily-table formulas were found. Rows containing all
six numeric quantities number 349 (NE), 364 (SE), 347 (NW), and 344 (SW). These counts
describe stored cells; they do not certify measurement quality or a complete crop season.

For a future evaluation, retain fixed standard-time conventions, original location and
treatment identities, and the distinction between tank emptying and soil drainage.
Relative lysimeter storage cannot initialize absolute soil water. Missing values must
remain unknown. Crop calendar, matching weather, soil initialization, measurement-quality
review, an independent evaluation split and acceptance criteria are still required.
Texas measurements do not establish performance in Burkina Faso or Taiwan.

The paired [weather dataset](https://doi.org/10.15482/USDA.ADC/1526433), version 2,
now has its original 2016 workbook retained separately in `weather_manifest.json`.
The three-file water-balance audit remains independently reproducible. The original
files here are excluded from the distributable wheel's data allowlist.

## Paired weather preparation

Run `python -m scripts.prepare_bushland_weather --output /tmp/bushland-weather.json`.
This verifies the four original workbooks and prepares 366 daily summaries from
35,136 interval records. Fixed UTC−06:00 is represented by `Etc/GMT+6`; daylight-saving
time is not applied. Each timestamp marks an interval end. Thus January 1, 2017 at
00:00 closes the December 31, 2016 source day. A trailing untimed row contains two
zero cells; the report exposes their coordinates and excludes them from dated totals.

Every aggregate requires all 96 values for its quantity. A missing interval withholds
that total. Original negative precipitation values are counted and retained. The
four-lysimeter average never replaces a missing location-specific observation.
Solar energy is integrated from shortwave irradiance; it is not net radiation.
Reported temperature and humidity extrema are extrema of 15-minute averages, not
instantaneous daily extremes. These distinctions also follow the daily-input definitions
in [FAO-56, Chapter 3](https://www.fao.org/4/X0490E/x0490e07.htm).

The [prepared report](../../../docs/dpg/field_evidence/bushland_2016_weather.json)
retains the source daily rain, interval rain sum and paired water-balance rain and ET.
It records 590 discrepancies against the weather daily table and 29 against the
water-balance table, with comparison counts and individual values. The comparison
threshold, 1e-8 mm, distinguishes summation roundoff; it is not a sensor-accuracy or
model-acceptance tolerance. These representations are related products of the same
research measurements, not independent validation observations. Their disagreement
does not establish which representation is correct, or an error in an Agronaut model.

Before using these data in a model evaluation, resolve rainfall-definition differences,
justify meteorological aggregation and net-radiation estimation, and assemble the
matching crop calendar, coefficients, initial soil state and evaluation protocol.
The report explicitly marks model inputs as incomplete. It creates no farm records,
does not fill gaps or fit parameters, and does not assign field-validation status.

## Rainfall reconciliation

Run `python -m scripts.reconcile_bushland_rain --output /tmp/bushland-rain-reconciliation.json`.
The [retained reconciliation](../../../docs/dpg/field_evidence/bushland_2016_rain_reconciliation.json)
finds exact agreement in all 140,544 interval rainfall cells across the two workbooks,
including missing markers. The 29 differing balance daily totals cancel across 14 groups
of consecutive differing days (13 pairs and one triple). This narrows the discrepancy
to daily aggregation; its cause and the correct model-input series remain unestablished.
The separate weather daily table still differs, and universally adding daily dew does
not reconcile it. Source bytes and values remain unchanged, with no shifted dates,
imputation, clipping or model evaluation. See the evidence README for counts and context.

# Preparing a soil sample and laboratory record

_Domain: soil_
_Evidence: guidance_
_Applicability: General preparation for soil laboratory testing; no universal sampling depth, soil-test interpretation, fertilizer prescription or regional validation._
_Sources: [University of Maryland Extension, Soil Sampling Procedures for Nutrient Management](https://extension.umd.edu/resource/soil-sampling-procedures-nutrient-management); [University of Minnesota Soil Testing Laboratory, Farm/Field and Commercial Horticulture Crops: Soil Analysis Request Sheet](https://soiltest.cfans.umn.edu/sites/soiltest.cfans.umn.edu/files/2023-01/farm_and_field_2023_0.pdf)_

## Agree on the sampling plan

Ask the receiving laboratory and local adviser which tests, depths, timing, containers and
handling instructions fit the crop and question. Minnesota's form illustrates how depth
and handling differ by test; its local requirements are not a protocol for other regions.

Identify areas with similar soil and management history before collecting a composite
sample. Keep distinct management areas separate. Maryland describes representative
coverage, avoiding unusual spots in the routine sample, using a clean container and mixing
subsamples thoroughly. Record any excluded areas; ask the adviser about sampling a problem
patch separately. These US sources support preparation principles, not a validated
sampling plan for Burkina Faso or Taiwan.

## Keep a field notebook

For Agronaut, give each sample a stable identifier linked to its location or management
zone. Keep the actual sampling date, top and bottom depths in centimetres, subsample count,
procedure and notebook reference. Record crop history and recent management alongside the
sampling notes for the laboratory or adviser. Retain the laboratory's submission instructions
and the original report outside Agronaut; a report-source reference does not upload a file.

Unknown depth and subsample count can remain blank in Agronaut. Do not copy numbers from an
example into an actual record. Sampling notes can be prepared before results arrive;
Agronaut's laboratory-report form is for a received report and requires its report date.

## Record the received report

Select the correct soil unit in **Field records**, then open **Record laboratory soil tests**.
Choose the intended crop cycle or leave the report unassigned. Enter laboratory, report and
sample identifiers, actual dates, location, sampling source and report source.

Copy each reported result with its analyte, exact value, unit, method and qualifier. In
Agronaut, a less-than result remains a limit, not an exact concentration; a missing result
remains unknown, not zero. Preserve differing methods as reported. The software does not
convert or average these reports, interpret fertility classes or calculate fertilizer doses
from them. Ask a local adviser to review the original report and sampling context.

For offline entry, inspect `agronaut field --unit SOIL-UNIT-ID describe_soil_tests`, then use
`agronaut field --unit SOIL-UNIT-ID record_soil_test --input soil-report-request.json`.
The bundled `examples/soil_report.json` is synthetic and must be replaced with actual data.
Use a new entry for a new sample and a correction for a transcription error. Corrections
retain revision history and the existing cycle assignment. Assigned reports appear in
season reviews; saving a report does not change nutrient targets or fertilizer applications.

The recording instructions describe Agronaut. French and Traditional Chinese versions are
draft translations requiring local language and agricultural review.

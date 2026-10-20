# Mineral-fertilizer nutrient accounting

Agronaut can compare declared mineral-fertilizer quantities with independently supplied
remaining application targets. This experimental calculator reports N, P₂O₅ and K₂O amounts,
known partial contributions, unknown inputs, and gaps or excesses against the supplied target.
It does not choose fertilizer rates or record an application.

Fertilizer grades conventionally express N, P₂O₅ and K₂O as percentages by mass. Elemental
phosphorus and potassium are different bases and must not be entered in the oxide fields.
[University of Maryland Extension](https://extension.umd.edu/resource/garden-fertilizer-basics)
explains the label convention.

Target rates need appropriate crop, soil-test and local recommendation evidence, with
relevant credits assessed before using this calculator. Agronaut does not derive a target
from soil-test ppm, a crop name or aquaponic coefficients. The
[University of Minnesota Extension guide](https://extension.umn.edu/agriculture/specialty-crops/interpreting-soil-tests-for-fruit-and-vegetable-crops)
describes how recommendation methods and nutrient credits inform fertilizer calculations;
its crop-specific rates are not universal defaults here.

## In the app

Select a soil unit, open **Field records**, and expand **Account for mineral fertilizer
nutrients**. Optionally select the planting first. Enter the crop/stage, area in m², planning
period, remaining targets in kg/ha, target source, and the region/method/credits already
considered. The target-definition checkbox explicitly identifies remaining application
amounts rather than crop uptake requirements.

Add up to ten mineral products in the form. Quantities refer to the whole stated area and
period. Supply product mass in kg, or volume in L with density in kg/L and its source. Supply
N, P₂O₅ and K₂O percentages by mass, with the analysis source. Leave unknown values blank;
enter zero only when explicitly supported. The calculation is saved with its inputs and
sources. It does not log completed fertilization or irrigation.

Preplant budgets can be associated with a later planting; the selected crop and farm timezone
must match. Calculation history and season reviews retain the saved budget. Other clients
cannot save into a unit they do not own, and stale unit selections are rejected.

## From the terminal or chat

These commands work without a language model or network:

```bash
agronaut nutrient-budget --input examples/nutrient_budget.json
agronaut nutrient-budget --input examples/nutrient_budget.json --json
agronaut calculate --system soil --task nutrient_budget --input examples/nutrient_budget.json
agronaut capabilities --system soil --json
```

The capability catalog includes a complete input schema. The scoped chat tools use that
same schema and calculator. To save through the persistent CLI, use `agronaut field save`
with an operation JSON containing `task: "nutrient_budget"`, `inputs`, a stable `request_id`
and optional `cycle_id`.

The request includes `schema_version: 1`, `site: {name, timezone}`, `area_m2`,
`crop: {name, stage}`, `period: {start, end}`, and `target`. Target fields are `n_kg_ha`,
`p2o5_kg_ha`, `k2o_kg_ha`, `basis: "remaining_application"`, `source`, and `scope_note`.
The three nutrient targets accept numbers or explicit nulls. Planning dates use YYYY-MM-DD.

`products` is optional (zero declared product input when omitted), with at most 100 records
in JSON. Each record has a unique `id`, `name`, `kind: "mineral"`, positive `quantity`,
`unit: "kg"` or `"L"`, and `analysis`. Analysis requires `n_pct`, `p2o5_pct`, `k2o_pct`,
`basis: "mass_percent"` and `source`; unknown percentages are explicit nulls. Liters may
include `density_kg_l` and `density_source`. Known density requires a source; kg inputs must
not supply density fields. Unknown density leaves product mass unknown. Labels expressed
by volume, organic amendments, and units such as gallons or pounds are rejected.

## Arithmetic and interpretation

- Product mass is supplied kg, or supplied liters multiplied by supplied kg/L.
- Each declared nutrient amount is product mass multiplied by its mass percentage / 100.
- Field area in hectares is supplied m² / 10,000. Whole-area target mass is the supplied
  kg/ha target multiplied by that area.
- Shortfall and excess compare complete declared product totals with that target mass.
  They are not recommended extra applications or a nutrient-loss estimate.

The bundled example is synthetic: 0.5 ha, 100 kg of 10-10-20 and 100 L of 20-0-0 at a
supplied density of 1.25 kg/L. It contains 35 kg N, 10 kg P₂O₅ and 20 kg K₂O. Against the
illustrative targets of 60/20/40 kg/ha, that is a whole-area N excess of 5 kg and zero P₂O₅/K₂O
difference. These targets, products and density are arithmetic fixtures, not crop advice.

Unknown target, percentage or density values remain unknown. Known partial contributions
are shown separately; they never become a complete total when another contribution is
unknown. An explicit zero label percentage contributes zero even when liquid density is
unknown. No declared products means zero declared input, not zero soil nutrient supply.

## Scope

`feasible` remains null even if every supplied target matches. The result does not model
soil nutrient availability, plant recovery, mineralization, prior applications or organic
credits, nutrient losses, salinity, mixing compatibility, application timing, environmental
or legal limits, economics, or yield response. It does not validate supplied targets or
sources and has no field-validation claim. Agronomic recommendations remain unfinished.
Completed mineral applications can be recorded separately in **Crop records**, with actual
quantities and dates, correction history and an optional link to a saved nutrient plan.
See [fertilizer application records](CROP_RECORDS.md#mineral-fertilizer-applications).

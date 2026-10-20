# Offline agricultural calculations

Agronaut has experimental soil crop-water calculations alongside its existing aquatic
calculators. They work with supplied data without a chat model or network connection.
An experimental drip calculator checks supplied layouts and equipment for flat fields
with pressure-compensating emitters. Experimental sprinkler design and measured surface-event
evaluation are also available; predictive surface-irrigation cutoff times remain unimplemented.
A water budget alone does not establish pressure or runtime.

For a connected offline example from planting through corrected irrigation records and
season review, run the [synthetic field-cycle rehearsal](FIELD_CYCLE_REHEARSAL.md).

## Find a supported task

```bash
agronaut capabilities
agronaut capabilities --system soil --json
```

The catalog declares each task's required and optional inputs, applicability, evidence and
implementation status. Production systems are `aquaponics`, `hydroponics` and `soil`.
Drip, sprinkler and surface irrigation are methods within soil cultivation. Unsupported
tasks fail explicitly rather than silently using another calculator.

## Keep each production unit's records separate

Copy `examples/soil_unit.json` and replace its example farm name, timezone and production
conditions with yours. Create a local CLI unit, then start the terminal conversation:

```bash
agronaut farm create --input /path/to/my_unit.json
agronaut farm list
agronaut
```

Creation selects the new unit. The CLI uses the same `cli:local` identity as the terminal
conversation. Inside chat, `/units` lists your units and `/unit <id>` switches between them.
Telegram and WhatsApp also accept these selection commands for their own account's units.
The CLI does not create units in a Telegram or WhatsApp account.

For guided setup without JSON, type `/newunit` in terminal chat, Telegram, WhatsApp, or
web chat. `/newunit soil`, `/newunit hydroponics`, and `/newunit aquaponics` skip the first
choice. Answer one question at a time: farm, timezone when needed, unit name, environment,
and the relevant medium or water-delivery choices. Numbers and the displayed choice names
are accepted. Soil cultivation never asks for fish details. Rainfed fields have no installed
irrigation method; supplemental and fully irrigated fields choose drip, sprinkler or surface.

`/back` revisits the previous answer and `/cancel` discards the draft. `/newunit` resumes an
unfinished draft after a restart. Existing farms are listed only for your account, and
choosing a legacy farm with no timezone asks you to supply one explicitly. Your existing
unit remains selected during setup. The final answer saves the validated farm/unit and
selects it in one transaction; a failed save leaves no partial farm or selection change.
Setup replies stay outside existing units' facts and conversation history. `/reset` also
cancels unfinished setup, while account export and erasure include saved drafts.

Terminal and bot entry points now allow these deterministic commands when a chat model
cannot be configured; ordinary conversation still needs a working model. A soil request
without a selected unit offers setup before aquatic fact extraction can run. The web's
existing sidebar form remains available without a chat model too.

The web Assistant and My Twin views also provide a **Farm and production units** sidebar.
Use **Add a production unit** to enter the farm timezone and production conditions, or
**Use this unit** to switch. Setup is deterministic and remains available when the agent
can initialize its stores but no chat model is configured. The web identity belongs to
the current browser session; it is separate from the terminal and messaging accounts.

To add another unit to an existing farm, set `farm` to `{"id":"the-existing-farm-id"}`
in the setup JSON. Supply the unit's production system, medium, environment, water strategy
and any irrigation method separately. The setup validates compatibility before saving.
`agronaut farm --db /path/to/file.sqlite3 ...` selects another local database.

Chat recall, calculations, remembered facts, follow-ups and decisions use the selected
unit's context. A soil field cannot dispatch an aquatic sizing or twin tool. Selecting a
legacy unit copies its latest facts and twin state; historical conversations and actions
whose unit was uncertain remain available in the account export.

## Create irrigation zones within a field

An irrigation zone has its own production-unit ID beneath a soil field. In the sidebar,
open **Add an irrigation zone**, choose the parent field, and enter a unique zone key,
name, water strategy, irrigation method, boundary description and source. Ground area is
optional; leave it blank when unknown. **Create and use zone** saves and selects the zone
in one transaction. The selected zone's definition is available in the sidebar.

For the local CLI, replace the synthetic details in `examples/irrigation_zone.json`, then
use the field ID returned by `agronaut farm list`:

```bash
agronaut farm create-zone --unit FIELD_ID --input examples/irrigation_zone.json
agronaut farm zones --unit FIELD_ID
agronaut farm select --unit ZONE_ID
```

The scoped field tool exposes `describe_zones`, `list_zones` and `create_zone`. Tool creation
uses the selected field as parent and keeps that field selected; explicitly switch to the
new zone before recording its inputs. CLI and sidebar creation select the zone immediately.
Repeating an identical creation request returns the same zone. Zone keys are unique within
each parent field.

Only the farm and growing environment are shared at creation. The zone starts with its own
crop cycles, measurements, water logs, calculations and conversation context. Parent and
sibling records are not copied or combined. Select the zone and enter its crop and soil
inputs before using the existing field workflows. Saved field calculations include the
zone definition. Creating a zone leaves the parent field's production profile unchanged.

Zones support supplemental or fully irrigated soil cultivation with drip, sprinkler or
surface irrigation. Their parent must be a top-level soil field with a known growing
environment; nested zones and aquatic parents are unsupported. Zone definitions are immutable:
use a new key and identity for a changed boundary, keeping historical records under the old
identity. Boundary surveying, overlap detection and retirement are not implemented.

Declared ground area describes the zone footprint. It does not automatically set planted
area, wetted fraction, water demand or equipment settings, or cap the area supplied to a
calculation. Review those inputs separately. Parent and zone areas can overlap; do not sum
them as disjoint land. [Farm resource plans](FARM_RESOURCES.md) use explicit work quantities
and flag scenarios containing both a parent field and its zone for overlap review.

## Compute crop demand and replay a soil-water budget

From a source checkout:

```bash
agronaut water-needs --input examples/field_water.json
agronaut irrigation-plan --input examples/field_water.json --json
```

The example is a hypothetical tomato scenario using weather from FAO-56 Example 18; its
soil initialization, efficiency and water events are illustrative inputs, not farm records.
Replace these with observations and sources appropriate to the field before interpreting
the result. A cited, supplied coefficient is not independently verified by the software.

The JSON request uses `schema_version: 1` and contains:

| Field | Meaning |
|---|---|
| `site` | Name and IANA timezone |
| `area_m2` | Positive planted area in square metres |
| `crop` | Name and either a fixed `coefficient` (`value`, `stage`, `source`) or a dated `coefficient_schedule` |
| `weather` | Consecutive dated meteorology or supplied grass-reference ET records |
| `weather_sources` | Optional retained provider snapshots; required when weather references their IDs |
| `rainfall_overrides` | Optional separately sourced daily rain for water balances; replaces weather rainfall without editing the weather |
| `soil` | Field capacity and wilting point in m³/m³, root depth in metres, depletion fraction and source |
| `initial_state` | End-of-day `as_of` date before the first weather day, `depletion_mm` and source; may be absent |
| `application_efficiency` | Fraction greater than zero and at most one, and its source |
| `events` | One entry per weather day with net irrigation and runoff in mm and a source |

Only `site`, `area_m2`, `crop` and `weather` are needed for crop demand. Soil, efficiency and
events are additionally required for the root-zone budget. Without an initial soil state,
crop demand is calculated but irrigation amounts remain unknown. Explicit zero-water events
are required; missing rain is never treated as zero.

Default meteorology uses daily minimum/maximum air temperatures in °C, minimum/maximum relative
humidity in percent, wind speed in m/s and measurement height in metres, site elevation
in metres, **net radiation** in MJ/m²/day, and rain in mm. Solar radiation is not net
radiation. Each record declares its date, source and kind: `observed`, `forecast`,
`historical` or `scenario`. Meteorology forecasts require a timezone-aware `issued_at`.

To supply daily incoming solar energy, explicitly set
`input_type: "solar_radiation_fao56"`. Replace `net_radiation_mj_m2` with
`solar_radiation_mj_m2` and add `latitude_deg` (north positive, south negative).
All other meteorological fields and provenance remain required as above. Supplying both
radiation quantities, or supplying solar without this input type, is rejected.
The [solar example](../examples/field_water_solar.json) is a synthetic scenario:

```sh
python -m agronaut_agent.cli water-needs --input examples/field_water_solar.json
python -m agronaut_agent.cli irrigation-plan --input examples/field_water_solar.json --json
```

This option estimates net radiation using [FAO-56 Chapter 3](https://www.fao.org/4/X0490E/x0490e07.htm),
with grass-reference albedo 0.23. The result retains the original solar input, estimated
shortwave/longwave/net components, equation constants and method version. The upper
solar/clear-sky ratio cap applies only to the longwave term and is visible in the report.
The estimate is not relabelled as a measured net flux. This implementation rejects polar
darkness and negative empirical correction factors; supply suitable net radiation or
reference ET for those conditions. It does not invent daily extremes from interval means.

The web intake exposes the same choice under **Radiation input**. Selecting solar adds
latitude and daily incoming solar energy fields. Changing the choice clears dated inputs
so an existing net-radiation number cannot be reinterpreted as solar energy. JSON, CSV,
CLI and saved-plan replay use the same calculation. Unknown rainfall remains unknown;
saving a calculation creates no irrigation observations.

Alternatively, supplied reference ET records use `input_type: "supplied_et0"`, `day`,
`et0_mm_day`, `rainfall_mm`, `source`, `kind`, `method`, `reference_surface: "grass"`,
`timezone` and `period_hours`. The period must cover the complete local calendar day
(including 23/25-hour daylight-saving days). These records use imported ET directly;
Agronaut does not reconstruct temperature, humidity or radiation from it. Forecasts
need `issued_at` or a separately labelled timezone-aware `retrieved_at` when issuance
is unknown. Retrieval is not proof of model issuance or a newer model run.

The water balance assumes a fixed, uniform root zone. Incoming water is applied before
daily evapotranspiration; overflow drains. The end-of-day refill budget uses supplied
efficiency. Recommendations are not automatically applied on subsequent days: the replay
uses only the supplied irrigation events. Localized drip wetting and capillary rise are
not modeled. Each result carries these limitations, units, assumptions and sources.

The calculation basis is [FAO Irrigation and Drainage Paper 56 (1998)](https://www.fao.org/4/X0490E/X0490E00.htm),
including daily reference ET, crop coefficients and root-zone soil-water accounting.
Equation checks and mass conservation tests establish software behavior; they do not
establish field predictive accuracy.

### Dated crop coefficients

Use either a fixed `crop.coefficient` or a `crop.coefficient_schedule`, never both.
For a dated schedule, each period declares inclusive `start` and `end` calendar dates,
`start_value`, `end_value`, `stage` and `source`. For example:

```json
"crop": {
  "name": "tomato",
  "coefficient_schedule": [{
    "start": "2026-07-06",
    "end": "2026-07-08",
    "start_value": 0.5,
    "end_value": 1.1,
    "stage": "Synthetic transition",
    "source": "Arithmetic example only; not actual stage dates or Kc advice"
  }]
}
```

These short dates and numbers demonstrate arithmetic, not crop-development duration or
recommended coefficients. This example produces Kc values 0.5, 0.8 and 1.1 on the three
dates. Equal endpoints produce a constant period. Otherwise the value is
`start_value + (end_value - start_value) * (day - start).days / (end - start).days`.
Endpoints are the supplied values on those exact dates; a one-day period requires identical
endpoints. The date convention is explicit, with no inferred planting-to-stage duration.

Periods must be ordered, consecutive and non-overlapping, and cover every weather day.
The engine never extrapolates past the supplied curve. Adjacent periods may explicitly
change coefficient at the boundary; it does not silently smooth a supplied change.
Schedules may extend beyond the current weather window to describe known planning
assumptions. For an assigned crop cycle, their start cannot precede its planting date.
The daily report includes Kc and stage; calculation inputs retain the source period.

The approach follows the constant/sloped segment concept in
[FAO-56 chapter 6](https://www.fao.org/4/X0490E/x0490e0b.htm). Dates and locally applicable
coefficients remain supplied inputs. There is no automatic adjustment for climate, mulch,
partial wetting or crop variety, and no inference from a stage report. Root depth uses
the separate fixed or daily-depth inputs described below; a Kc curve does not determine
rooting depth. Soil properties remain homogeneous. Automatic phenology and a dual-coefficient
evaporation model are not implemented.

An offline synthetic example is included:

```bash
agronaut irrigation-plan --input examples/field_water_stages.json
```

For an existing calculation, `field review_crop` accepts `{calculation_id}` and returns
the saved coefficient inputs, current stage reports for its cycle, linked evidence status,
and any current crop/planting-date conflicts. It does not rewrite the saved result.
`field revise_crop` accepts:

```json
{
  "calculation_id": "SAVED-CALCULATION-ID",
  "coefficients": {
    "coefficient_schedule": [{
      "start": "2026-07-06", "end": "2026-07-08",
      "start_value": 0.5, "end_value": 1.1,
      "stage": "Synthetic transition", "source": "Replace with an applicable source for dates and values"
    }]
  },
  "reason": "Replace with the reason for changing the supplied curve",
  "stage_record_ids": [],
  "request_id": "crop-coefficients-001"
}
```

Alternatively, `coefficients` may contain one fixed `coefficient`. The revision preserves
crop identity, area, weather snapshots, initial soil state, soil properties and exact applied
water-record revisions, then recalculates the whole window and saves a linked child. Reasons
are mandatory. Retry the identical request ID to retrieve the same saved revision.

Optional `stage_record_ids` explicitly link current, non-voided stage reports from the same
owned unit and cycle. Locations and source labels stay in their snapshots; a report from
part of a field is not assumed representative of the whole crop. Linking does not determine
the numeric coefficients. Omit the list to create a coefficient revision without linked
stage evidence. Weather revisions and actual-water replay preserve existing stage snapshots.
Later corrections/voids trigger a review flag, never an automatic coefficient change.

In **Field records**, select **Dated coefficients** in the intake form, or use **Review
growth stages and revise crop coefficients** beneath a saved calculation. The same service
is available through the CLI and `field_records` chat tool; the capability description
provides the nested crop schema. No stage observation or irrigation action is created by
saving a coefficient calculation.

## Shared calculation API

```bash
agronaut calculate --system soil --task crop_water --input examples/field_water.json
agronaut calculate --system soil --task root_zone_budget --input examples/field_water.json
agronaut calculate --system soil --task drip_design --input examples/drip_design.json
agronaut calculate --system hydroponics --task size --input /path/to/hydroponics.json
```

`calculate` emits JSON with capability and evidence metadata plus the complete native
result. It returns exit code 2 for invalid or unsupported requests. Existing sizing and
field commands retain their own output formats.

A hydroponic sizing input might contain:

```json
{
  "crop": "lettuce",
  "grow_area_m2": 12,
  "temperature_c": 27,
  "water_budget_lpd": 300,
  "system_type": "nft"
}
```

The aquaponic `size` task also requires `fish_species`. These adapters invoke the existing
validators and engines and preserve their feasibility constraints, bills of materials,
operating envelopes, coefficient citations and caveats. They do not transfer aquatic
coefficients into soil-crop calculations.

Python callers use the same dispatch path:

```python
from agronaut_agent.domains import capabilities, run_capability

catalog = capabilities("soil")
result = run_capability("soil", "crop_water", supplied_inputs)
```

Standalone `calculate`, `water-needs` and `irrigation-plan` commands use only their supplied
inputs; they do not read or change a remembered farm profile. Chat calls also enforce the
selected unit's production system and retain their tool results in that unit's history.

## Check a drip design

```bash
agronaut design-irrigation --method drip --input examples/drip_design.json
agronaut design-irrigation --method drip --input examples/drip_design.json --json
```

This example describes a **synthetic** 500 m² installation, not tested equipment or a
field recommendation. Its equipment ratings, soil limits and water target are illustrative.
Replace every record with sourced measurements or specifications. All records are required;
the `soil.drip_design` capability exposes their field/type contract as `input_schema`.

| Record | Required meaning |
|---|---|
| `layout` | Row/emitter counts and spacing, rows per zone, source-to-field feed length; flat terrain only |
| `emitter` | PC flow and tolerance across its pressure band, pressure limits in metres of head, maximum filter opening in microns, insertion loss coefficient |
| `main`, `manifold`, `lateral` | Actual internal diameter and absolute roughness in mm, maximum rated head in m |
| `supply` | Running-head bounds available across the entire declared flow range, maximum static head, instantaneous flow capacity, daily water and hours available |
| `headworks` | Clean-to-dirty filter loss bounds at all design flows, filter opening/capacity/pressure rating and fitting loss coefficients |
| `application` | Independently chosen gross field-average depth for one day, wetted fraction, measured/justified local application limits, maintenance water and time |
| `limits` | Liquid kinematic viscosity in m²/s and the designer's velocity limit in m/s |

Each record includes `source`. The source boundary is upstream of the filter and
downstream of any source regulator. A static pressure reading paired with an unrelated
pump maximum flow does not establish the running-head envelope. Filter loss bounds must
cover the intended flow range and cleaning condition. Tolerance must bound individual
emitter discharge, not just the batch mean. Smaller filter micron values mean finer openings;
the filter type and water-quality treatment still need equipment-specific review.

The model assumes a trunk along one field edge and separate contiguous zone manifolds.
Branches and emitters start at half their respective spacing, then repeat at full spacing.
Each manifold has identical perpendicular laterals. The source-to-zone trunk distance
increases for later zones. Only one zone runs at a time. Pipe material quantities include
the closed half-spacing tails; flowing path lengths stop at the last outlet. Fitting `K`
values use the upstream velocity in their respective pipe: `main_fittings_k` for the
active trunk/valve path, `manifold_through_k` at every manifold outlet, `lateral_takeoff_k`
for each lateral inlet, and emitter `insertion_k` at each emitter. Include all fittings
in those coefficients rather than silently omitting their loss.

Hydraulic losses use Darcy-Weisbach with EPANET 2.2's laminar, transition and turbulent
friction-factor equations and `K v²/(2g)` local losses. The implementation uses standard
gravity 9.80665 m/s². See the EPA manual's [network model](https://usepa.github.io/EPANET2.2/3_network_model.html)
and [section 12.1 algorithms](https://usepa.github.io/EPANET2.2/12_analysis_algorithms.html).
Checks include pressure at the closest and most distant emitters, source/filter flow,
velocity, static pressure ratings, daily water and available time. Pressure calculations
remain conditional on the assumed PC flow bounds; the model does not solve discharge
outside the compensation band. Infeasible results expose all violated constraints and
set `schedule` to null. Candidate quantities remain available for diagnosis.

Runtime uses the lower emitter-flow bound to deliver the requested gross volume. The upper
bound controls maximum withdrawal and local application depth. Maintenance allowances are
added to daily water/time totals; flushing flows and the maintenance procedure are not modeled.
Zone runtime excludes maintenance time. A valid calculation returns exit code 0 even when
infeasible; inspect `feasible` and `binding_constraints`. Invalid inputs return exit code 2.

Localized application depth and rate are calculated over the supplied wetted fraction and
checked against the supplied limits. Crop demand is **not** multiplied by that fraction.
These are area-average checks, not a soil wetting-front or infiltration simulation. FAO's
[drip guidance](https://www.fao.org/4/s8684e/s8684e07.htm) explains partial root-zone wetting;
the current uniform-root-zone budget does not by itself justify a drip application target.
Actual soil response, distribution uniformity, crop scheduling, terrain, startup/shutdown
transients, clogging and non-PC emitters remain outside this model's validated scope.

## Check a sprinkler design

```bash
agronaut design-irrigation --method sprinkler --input examples/sprinkler_design.json
agronaut calculate --system soil --task sprinkler_design --input examples/sprinkler_design.json
```

The example is a **synthetic** 1,728 m² solid-set grid with two sequential zones. It is
not a product selection, measured installation, or crop recommendation. The same capability
is available to soil-unit chat and can be saved/imported in **Field records** as a sprinkler
equipment design. `soil.sprinkler_design` exposes its complete `input_schema`.

Supported geometry is a flat rectangular grid of identical full-circle sprinklers on
identical risers. Supply, headworks, pipe and operating-limit records have the same units
and source-boundary meaning as the drip contract. The extra `riser` pipe record includes
its internal diameter, roughness and pressure rating. All records require a source.

| Record | Sprinkler-specific inputs |
|---|---|
| `layout` | Lateral count, sprinklers per lateral, laterals per zone, both spacings, feed length and riser height in metres |
| `sprinkler` | Reference flow and head, operating head band, separate static rating, individual-nozzle flow tolerance, minimum wetted diameter, maximum spacing/diameter fraction, wind applicability, filter opening and fitting coefficients |
| `application` | Independently selected net depth, lower-bound application efficiency, design wind, complete-grid peak/average pattern factor, local rate/depth limits, maintenance water/time |

The nozzle relation is `q = q_ref × sqrt(h / h_ref)`. The implementation computes minimum
and maximum nozzle flow over the supplied operating band, including tolerance. Those
flows bound main, manifold, lateral and riser losses. Riser elevation is also subtracted
from source head. A design passes only when the resulting nozzle-pressure interval stays
inside that same operating band. These are conservative bounds, not individually solved
nozzle operating points. This uses the pressure-flow relation and pipe-loss methods in
the [EPANET network model](https://usepa.github.io/EPANET2.2/3_network_model.html).

Both spacings must meet the supplied minimum-diameter/spacing bound, and design wind must
stay within the supplied equipment/pattern envelope. The cell diagonal must also fit inside
the supplied minimum circular footprint diameter, so cell corners are geometrically covered.
This footprint bound needs evidence under the stated wind; it does not simulate a wind-distorted pattern.
No default spacing ratio, wind limit,
soil intake rate or application efficiency is inferred. Wetted diameter and flow tolerance
must apply throughout the pressure band, not only at the nominal pressure. Sprinkler
overlap, wind and soil intake are design considerations described in
[FAO's sprinkler chapter](https://www.fao.org/4/a1336e/a1336e08.pdf); passing a spacing
check does not establish measured distribution uniformity.

Each sprinkler's nominal grid cell area is lateral spacing × sprinkler spacing. Average
emitted application rate is flow divided by that area. The supplied `peak_to_average_factor`
must bound the complete overlapping pattern, including adjacent-zone contributions, over
the stated pressure and wind conditions. It multiplies the upper average rate for the local
rate check. Multiplying that peak rate by the common zone runtime gives the cumulative
local-depth bound. This is a supplied-pattern bound, not a catch-can or infiltration model.

Requested gross volume is net target × grid area ÷ efficiency. Runtime uses the lower flow
bound; the upper flow bound determines maximum withdrawal and application depth. Efficiency
must include boundary overspray, wind and other losses between emitted water and useful
field-average net water. A 100% value is not assumed. No claim is made that every plant
receives the field-average target. Identical cell areas/nozzles give equal zone runtimes,
including a smaller final zone. Maintenance allowances are included in daily totals.

Checks also cover instantaneous source/filter flow, pressure ratings, velocity, daily
available water and operating hours. A failed constraint sets `schedule` to null and leaves
candidate quantities visible for diagnosis. Conservative failure does not prove that every
possible design is infeasible; a detailed hydraulic/pattern evaluation may resolve it.
Materials are preliminary quantities for the fixed grid, including risers and closed tails.
Slopes, partial-circle heads, moving systems, regulators, surge, field boundary patterns,
crop scheduling and field validation remain outside this implementation.

## Evaluate a surface-irrigation event

```bash
agronaut evaluate-surface --input examples/surface_event.json
agronaut evaluate-surface --input examples/surface_event.json --json
agronaut calculate --system soil --task surface_evaluation --input examples/surface_event.json
```

This evaluates an event for one furrow, border or basin using supplied observations and
intake parameters. It does **not predict a cutoff time** or the effects of changing the
inflow schedule. `soil.surface_cutoff` remains unavailable. The example is synthetic and
chosen for independent arithmetic checks; its soil and flow values are not recommendations.
The capability's `input_schema` lists all fields, including the nested records below.

| Record | Required meaning |
|---|---|
| `site` | Name and IANA timezone |
| `geometry` | Method, length, width and slope in m/m, with source; furrow width means represented furrow-to-furrow spacing |
| `event` | Timezone-aware start, observed cutoff and monitoring-end elapsed minutes, observed/scenario label, explicit zero rain/initial surface water, and confirmation that surface water has drained |
| `infiltration` | Sourced Kostiakov–Lewis `k`, exponent, steady rate, unit basis and applicable opportunity-time range |
| `inflow`, `tailwater` | Contiguous intervals with start/end minutes, **interval-mean** flow in L/s and source, covering the entire monitoring window |
| `stations` | Increasing distances including 0 and full field length, advance/recession minutes relative to event start, and sources |
| `assessment` | Uniform pre-event root-zone deficit in mm or explicit `null`, balance tolerance as a fraction, and source |

Flow samples are not automatically interpreted as interval means. Include known zero-flow
intervals; split inflow at cutoff and provide zero thereafter. A single continuous inflow,
including cutbacks, is supported. Surge restarts, incomplete advance, rainfall, initial
ponding and water remaining on the surface are outside this evaluation. Monitoring must
cover every recorded recession. No default soil intake family is selected.

At each station, opportunity time is recession minus advance. Cumulative intake is
`k × opportunity^exponent + steady_rate × opportunity`, using minutes. For borders and
basins, `basis=depth_mm`: `k` is mm/min^exponent and steady rate is mm/min. For furrows,
`basis=volume_per_length_m2`: the corresponding units are m²/min^exponent and m²/min.
Those furrow parameters must already describe intake per metre of this furrow at its
discharge and wetted perimeter. Dividing by furrow spacing and converting metres to mm
gives equivalent field depth; a generic soil curve cannot simply be relabeled. These
relationships follow [FAO's field-measurement guidance](https://www.fao.org/4/t0231e/t0231e05.htm).

The calculation interpolates station depths linearly and weights by distance. Low-quarter
uniformity uses the lowest quarter of represented area, including constant-depth regions.
With a known initial deficit, modeled root-zone storage integrates the smaller of depth
and deficit, including crossings inside segments. The remaining infiltration is estimated
deep percolation. Application efficiency compares stored water with inlet volume; storage
efficiency compares storage with the initial deficit volume. These modeled performance
measures follow [FAO's evaluation framework](https://www.fao.org/4/t0231e/t0231e06.htm).

Water accounting compares inlet volume with tailwater plus modeled infiltration. The signed
residual stays visible: positive means unaccounted input; negative means modeled/measured
outputs exceed input. It is not automatically assigned to deep percolation. Performance
scores are withheld if the discrepancy exceeds the supplied tolerance, or if outputs exceed
input beyond numerical roundoff. Passing this conservative check is not field validation.
Unknown initial deficit leaves storage/efficiency measures unknown. Uniformity is undefined
when average infiltration is zero.

The shared chat tool and **Field records → Import and save a calculation** can evaluate and
save this task. Saved observed events must have ended, match the farm timezone, and start
within an assigned crop cycle. Sources, original inputs and accounting discrepancies remain
in exports. Saving an evaluation does not create daily water logs or automatically update a
soil-water budget. The model does not establish measured crop response or installation quality.

## Save calculations, log actual water, and recalculate

In the web app, select a soil unit and choose **Field records → Build a field-water
calculation** to enter inputs without a JSON file. Choose crop-water demand or a soil-water
budget, a starting date, and up to 31 consecutive days. Longer windows remain available
through JSON import. The form uses the selected farm's timezone and, if assigned, the crop
cycle's crop name.
Create actual planting cycles in **Crop records**, or with `agronaut crop create`;
see [crop cycles, observations and harvest records](CROP_RECORDS.md).
Cycle selectors show whether a planting is open or closed. A closed cycle still accepts
late daily water entries through its end date; later actual entries require reopening or
correcting the cycle in **Crop records**. Rejected evidence remains explicit and does not
extend a cycle's actual duration. Calculations remain available for retrospective analysis;
closing a planting does not rewrite its earlier plans.

The form groups inputs into field/crop, daily weather/water, and soil/application tabs:

- Enter area in square metres or hectares. Choose a fixed coefficient or dated periods,
  with explicit stage labels, Kc values and sources. The form does not infer coefficients
  or growth dates from a crop name.
- Supply daily temperature, humidity, wind, **net** radiation and their sources, plus the
  site's elevation and wind measurement height. Select each day's weather type explicitly;
  forecasts require an issue timestamp with timezone. Rainfall may remain unknown for demand
  alone, but is required for a soil-water budget.
- For budgets, supply soil water contents as volumetric percentages, root depth, the readily
  available fraction, application efficiency and sources. Percentages are converted to the
  calculator's fractions. Enter explicit daily irrigation/runoff scenario assumptions,
  including known zeros. These are not completed-work logs.
- Initial depletion is optional. It describes water missing relative to field capacity at
  the end of the day before the window. Leave both depletion and its source blank if unknown;
  the result then keeps refill amounts unknown. Unknown soil properties require the simpler
  crop-demand calculation until sufficient data are available.

Numeric measurements start blank. Changing the date window clears dated weather, event,
coefficient-period, daily-root and initial-state entries to prevent assigning old values to new dates. Changing the selected
unit or crop cycle clears the intake. **Calculate and save field water** validates through
the shared calculator and saves the result and evidence for the selected unit. It does not
record irrigation as completed. A fixed coefficient or supplied dated curve determines Kc;
root depth stays fixed unless **Use daily root depths** is selected. Calculations remain experimental.

The `field` commands use the selected local CLI production unit and its farm timezone.
They save data in SQLite; the standalone calculators above remain stateless. A calculation
is saved only when requested. Saving a proposed application never records it as completed.
Each result keeps its complete inputs, model version, evidence and creation time.

Prepare a save request containing `task`, `inputs` (the same complete input object accepted
by `calculate`), a stable `request_id`, and optionally an owned `cycle_id`:

```python
import json
from pathlib import Path

inputs = json.loads(Path("my-field-inputs.json").read_text())
request = {"task": "root_zone_budget", "inputs": inputs, "request_id": "initial-field-budget"}
Path("save-request.json").write_text(json.dumps(request))
```

```bash
agronaut field save --input save-request.json
agronaut field list
agronaut field record_day --input daily-water.json
agronaut field replay --input replay-request.json
```

`agronaut field --db /path/to/db.sqlite3 --unit <owned-unit-id> ...` explicitly chooses
the database and unit. Otherwise it uses the same selection as `agronaut farm` and terminal
chat. The JSON result includes saved record IDs. `get` accepts `{"calculation_id":"ID"}`
as its input file and returns the full saved snapshot.

A daily-water request has this shape (values below are illustrative, not field records):

```json
{
  "request_id": "water-day-2026-07-06",
  "data": {
    "day": "2026-07-06",
    "rainfall_mm": 5,
    "net_irrigation_mm": 3,
    "runoff_mm": 1,
    "source": "Replace with the source and method for all three daily totals",
    "provenance": "reported",
    "quality": "unverified"
  }
}
```

These are **whole-day totals for a completed local calendar day**. Net irrigation is
water entering the modeled root zone, not pumped litres or an unadjusted gross application.
The current budget treats runoff as rainfall runoff. All three amounts are required; missing
data cannot become zero. Provenance is `measured`, `reported` or `estimated`; quality is
`unverified`, `checked` or `rejected`. A source label does not validate a measurement.
Supply `cycle_id` when the record belongs to a known crop cycle.

For a correction, send a new `request_id` and the latest record's `supersedes_id`, with
replacement daily totals. Original records remain visible. An accidental second daily
entry is rejected unless it explicitly corrects the preceding revision. Reusing the same
request ID with identical data returns the existing record; conflicting data is rejected.

Replay requests contain:

```json
{
  "request_id": "budget-after-water-log",
  "calculation_id": "SAVED-BUDGET-ID",
  "water_record_ids": ["SAVED-DAILY-WATER-ID"]
}
```

Replay applies to `root_zone_budget` and `dual_crop_water`. Selected records must belong to the same unit
and crop cycle, cover distinct days inside the supplied weather window, and not be rejected.
The default `weather_policy: "observed_only"` requires observed meteorology on every
selected day. To use historical weather estimates, explicitly supply
`"weather_policy": "allow_historical"` in the replay request, or check **Use historical
weather estimates for selected daily records** in the web page. Historical weather and ET
remain estimates: measuring rainfall does not make temperature, radiation or ET observed.
Forecast and hypothetical scenario days cannot receive actual-water replay under either
policy. Import observed weather or historical estimates through `revise_weather` first.
The choice applies to the selected days; other dates keep their existing weather, including
future forecasts. Previously applied records and their accepted weather basis remain attached.
Farm and calculation timezones must
match; crop and dates must match an assigned cycle.

Selected rainfall is stored in `rainfall_overrides`, with its own amount, source, provenance,
quality and exact `water_record_id`. It **replaces** weather rainfall in the balance, and
is never added on top. Weather values, classifications and provider snapshots stay unchanged.
Irrigation/runoff events use the selected daily-record totals. Replay runs again from the saved initial soil state and creates a new
calculation linked to its parent. Other days retain their supplied values, and the result
lists the exact daily-record revisions used. Historical revisions can be selected explicitly
through the API/CLI for reproducibility; the web selector offers current, non-rejected records.
For a root-zone budget, no soil initialization still means no computed refill amount. Localized drip soil response
remains outside the uniform-root-zone calculation.

Reports show the effective rainfall, its provenance/quality, and the weather type separately.
The initial soil state and calculated depletion remain estimates unless independently
measured; the calculation retains experimental status. Older saved results stay readable
unchanged. Replaying or revising an older calculation creates a new version with separate
rainfall inputs for its applied daily records; no database migration is needed.

Offline water-balance inputs may supply `rainfall_overrides` directly. Each entry needs
`day`, `rainfall_mm`, `source`, `provenance` (`measured`, `reported`, or `estimated`) and
`quality` (`unverified` or `checked`). Supply at most one per date within the weather
window. A known separate rainfall total may replace missing weather rainfall; runoff is
bounded by the rainfall actually used. No quality review is inferred from a source label.
Unknown initial soil depletion still prevents a refill amount. Crop-demand-only requests
do not accept rainfall overrides, since they do not perform a water balance.

`water_record_id` is optional for standalone imports. Offline calculation treats it as
declared provenance, not authenticated evidence. Persistent field calculations only attach
owned record IDs through `replay`, verifying the exact unit, cycle and selected totals.
To create a new saved calculation from imported data, keep manually supplied rain without
record IDs, then explicitly replay owned daily records. Saved/exported calculations can
always be recalculated offline with their complete provenance intact.

### Update weather and extend a saved window

`revise_weather` applies complete dated weather records to a saved `crop_water`,
`root_zone_budget` or `dual_crop_water` calculation and saves a new version linked to it. It recalculates from
the original initial soil state. The original version remains available. For example,
prepare a request from an imported JSON array using the weather contract above:

The example below uses a single-coefficient budget. Dual-coefficient extensions require
`dual_days` instead of `events`; see the separate basal-demand and surface-evaporation section.

```python
import json
from pathlib import Path

request = {
    "calculation_id": "SAVED-CALCULATION-ID",
    "request_id": "weather-update-001",
    "weather": json.loads(Path("updated-weather.json").read_text()),
}
Path("weather-revision.json").write_text(json.dumps(request))
```

```bash
agronaut field revise_weather --input weather-revision.json
```

Each supplied record replaces that date's meteorology; omitted dates keep their saved
values. Partial records and duplicate dates fail. Dates can also be appended, but the
combined window must remain consecutive; prepending and truncating are not supported.
Observations may be corrected with new observations, but cannot be replaced by forecasts,
historical records or scenarios. Replacing a forecast with another forecast requires a
strictly newer `issued_at` instant, including its timezone; if both records lack issuance,
they require a newer `retrieved_at`. Mixing timestamp bases is rejected. These checks compare supplied
versions; they do not certify a forecast's freshness or a provider's accuracy. Never label
forecast or scenario values as observed just to enable replay.

For a root-zone budget extension, add `events` covering **exactly the appended dates**:

```json
"events": [{
  "day": "2026-07-07",
  "net_irrigation_mm": 0,
  "runoff_mm": 0,
  "source": "Explicit scenario: no irrigation or rainfall runoff on this appended day"
}]
```

The fragment above is illustrative. Supply your own dated event assumptions, including
known zeros and a source. Existing event assumptions remain unchanged; this operation
does not apply recommended irrigation automatically. Crop-demand revisions need no events.
The supplied coefficient or dated schedule stays attached throughout the extended window.
A schedule must cover every appended weather day; no extrapolation is permitted. Use
`revise_crop` to supply additional periods or changed coefficients before extending weather.
Root depth and other soil parameters remain fixed and require a separate calculation to change.

When a parent budget already uses daily water logs, a weather revision preserves their
exact applied IDs, rainfall and irrigation/runoff totals. Provider rainfall on those days
does not overwrite logged rainfall. The revision stores both the supplied request and
the effective calculation inputs with log provenance. Later corrections to daily logs
are adopted only through an explicit replay. Saving a weather revision does not itself
create observations or completed irrigation records. Reuse its request ID for retries;
different data requires a new ID.

In the web app, choose **Field records**, select a soil unit, import calculation inputs,
record daily water, then select the records to use for recalculation. Numeric log fields
start blank. Corrections preserve the original log, and downloads include the complete
calculation and evidence. **Update weather or extend the planning window** accepts a JSON
object containing `weather` and optionally `events` and `weather_sources`; the selected calculation supplies
the parent ID. These controls need the installed application dependencies but
do not call a chat model. The shared `field_records` chat tool uses the same service and
the unit pinned to the conversation turn.

### Record completed daily water totals in chat

Select a soil unit and use `/waterlog en`, `/waterlog fr`, or `/waterlog zh-Hant` for
English, French, or Traditional Chinese questions. French and Chinese are translation
drafts awaiting local review. Select an existing crop cycle, or explicitly choose `none`
(`aucun` / `無`) to record unit totals without a cycle. The date must be a completed
calendar day in the farm timezone, within the selected crop cycle when applicable.

Enter whole-day rainfall, net irrigation, and **rainfall** runoff in millimetres, then
their source and provenance. Net irrigation is the depth reaching the root zone; pump
volume, runtime, gross application depth and irrigation runoff are not interchangeable
with these inputs. Record the evidence or estimation basis in the source. Numeric fields
have no defaults. Unknown totals cannot become zero: cancel and retain the original
measurements separately if the required totals are unavailable: `/cancel` the daily draft
and use `/measure` for a raw flow, volume, runtime, depth or other supported reading.
No daily total is inferred from that reading. Use a dot for decimal
fractions, without thousands separators. Rainfall runoff cannot exceed rainfall.

Choose `measured`, `reported`, or `estimated`; French also accepts `mesuré`, `rapporté`,
`estimé`, and Traditional Chinese accepts `量測`, `回報`, `估算`. If any total is estimated,
choose estimated; otherwise use reported if any component is reported. Explain component
sources separately. These labels are operator declarations, not independent verification.
All totals saved by this intake have quality `unverified`.

Review the preview, then reply `save` (`enregistrer` / `儲存`). If totals already exist for
the exact unit, cycle and day, inspect the existing record, explicitly reply `replace`
(`remplacer` / `取代`), and supply a correction reason before the final review and save.
A correction replaces the whole day's record and retains the original revision. It is
not an additional irrigation event. A concurrent new record or correction requires
resuming `/waterlog` and reviewing the latest record before saving.

`/back`, `/cancel`, and `/reset` work on the current intake. Drafts survive restarts and
remain separate across units. Language changes preserve answers but require a new review
revision. Finish or cancel unit setup first; only one planting, scouting, measurement, water-log or
field-planning intake can run per unit. Image-generated facts cannot fill daily totals.
This works without a model through terminal, web chat and messaging adapters.

Saving daily totals does not recalculate a plan, adopt soil state or operate equipment.
Use the existing explicit replay workflow to select recorded totals and suitable weather
for a child calculation. Corrections do not silently update earlier calculation snapshots.
This net-depth log does not replace the separate surface-application record contract.

The scoped `field_records` tool and offline `agronaut field` CLI expose:

- `start_water_log`: `{language?}`; `en`, `fr`, `zh-Hant`, or omit to resume unchanged.
- `get_water_log`: `{}`.
- `answer_water_log`: `{answer, expected_revision, back?}`.
- `save_water_log`: `{draft_id, expected_revision}`, after explicit review and save request.
- `cancel_water_log`: `{expected_revision}`.

Schema v20 adds resumable drafts with the existing transactional migration and private
backup. Saving a record and deleting its draft are atomic. Retries return their original
receipt and cannot delete a newer draft. Account export includes `water_log_intakes`;
erasure removes owned drafts. The daily record retains its intake language, revision and
any correction reason under `operation.intake`.

### Review weather retained in a saved calculation

In **Field records**, select a saved water calculation and open **Review saved weather age**.
Enter a positive maximum forecast age in hours, then click **Review weather now**. No age
limit is preselected: this is an operator review policy, not an agronomic standard.
The result shows its review timestamp and can be downloaded. Changing the selected plan
or age limit clears the displayed review; click again to refresh its clock.

The offline CLI and scoped `field_records` tool expose `review_weather`:

```bash
agronaut field review_weather --input weather-review.json
```

```json
{"calculation_id":"SAVED-CALCULATION-ID","max_age_hours":24,"as_of":"2026-10-20T08:00:00Z"}
```

The 24-hour limit is illustrative. Choose a suitable review policy explicitly. Omit `as_of`
to use the current UTC time, or supply an offset-bearing timestamp for a reproducible
metadata comparison. A supplied past time does not prove the plan or inputs were available
then; `calculation_created_at` remains visible in the output.

Forecast age uses `issued_at` when supplied. Otherwise `retrieved_at` gives download age,
and `model_issuance_unknown` remains a review flag even when that age is within the limit.
Exactly the supplied limit passes the age comparison; greater ages fail it. Timestamps
after the review instant and issuance after retrieval are flagged separately. Forecast days
before the review date in the **saved site's timezone** are elapsed; forecasts are never
relabelled as observations. A changed farm timezone is reported without changing that basis.

Historical estimates, observations and scenarios are not subject to the forecast age limit.
Observed or historical dates after the review date are flagged. Their labels do not prove
measurement quality. Only direct weather inputs are reviewed; equipment calculations with
no such inputs return `no_weather_inputs`, and linked water calculations need their own review.
An age check does not establish accuracy, completeness, site representativeness or irrigation
readiness. Review neither fetches weather nor changes records. Use the existing explicit
weather revision workflow to adopt replacement data in a new calculation version.

### Fetch and retain weather model data

Field records → **Build a field-water calculation** → **Fetch Open-Meteo** provides
an explicit preview before saving. Enter the date window and WGS84 coordinates, click
**Fetch weather preview**, then review the returned grid location, daily ET/rainfall,
retrieval time and assumptions. Coordinates are sent only when fetching. Changing the
dates or coordinates invalidates the preview; changing units clears it. Supply the
crop coefficient and, for a budget, soil properties and explicit daily water events.
Fetches create no farm records. Saving creates an immutable calculation with its raw
provider response and transformation assumptions; it creates no completed irrigation.

The CLI and `field_records` chat tool share `fetch_weather` with data:

```json
{"latitude": 5.6, "longitude": -0.2, "start": "2026-10-20", "end": "2026-10-22"}
```

These are illustrative coordinates and dates; supply the intended location and an
available date window. The selected owned soil unit supplies the farm timezone:

```bash
agronaut field fetch_weather --input weather-request.json > weather-preview.json
```

Copy **both** `weather` and `weather_sources` from that result into a field calculation
input or a `revise_weather` request. Do not send the entire preview as calculation inputs:
its other fields describe the preview. Ordinary offline `water-needs`, `irrigation-plan`
and saved workflow calculations validate and replay the retained snapshot without
network access. Snapshot hashes detect changes, not provider authenticity. Weather
revisions retain snapshots still referenced by any day and preserve the parent version.

The adapter requests hourly grass-reference ET0, precipitation and snowfall in UTC,
then sums the preceding-hour accumulations over each actual local calendar day.
Fractional-offset day boundaries prorate the boundary hour under a declared uniform
rate assumption. Missing, duplicate or null hours, incorrect units, nonfinite or negative
values, and any snowfall reject the fetch; no gap is filled with zero. Incoming solar
radiation is never substituted for net radiation. Past model data are `historical`;
today/future data are `forecast`, with retrieval time separately recorded. See the
[Open-Meteo variable and time definitions](https://open-meteo.com/en/docs).

Each request spans at most 16 days, within the last 90 days through 14 days ahead;
availability still depends on the provider/model. Network failures leave existing
calculations untouched, with manual JSON/CSV meteorology or supplied ET still usable.
The public endpoint is the default; deployments with customer access may set
`OPEN_METEO_API_KEY` on the server. The key is excluded from snapshots and error messages.
Review [Open-Meteo's usage terms](https://open-meteo.com/en/terms) for the intended deployment.
Attribution remains attached to the returned data and calculation sources.

This adds model-data intake, not field validation. Provider historical estimates are not
observations. Explicit `allow_historical` replay can combine them with separately recorded
rainfall and irrigation, retaining their sources and quality labels as described above.

Database schema v4 adds saved calculations and daily water records. Upgrade creates a local
SQLite backup before changing an existing database; failures roll back the schema change.
Account export includes both record types, and erasure removes them with the owning farm.
Schema v5 adds account-owned guided-setup drafts with the same backup and erasure behavior.

## Soil measurements and state reconciliation

In **Crop records → Record a field measurement**, enter a quantity, value, timestamp
with UTC offset, source and provenance. Readings can belong to a crop cycle or be
unassigned within the selected unit. They start as `unverified`; quality review remains
a separate action. The same operation is available through `crop_records` in chat and
`agronaut crop record_measurement --input reading.json`:

```json
{
  "request_id": "sample-2026-07-06-north",
  "data": {
    "quantity": "soil_water_content",
    "value": 0.25,
    "unit": "m3/m3",
    "observed_at": "2026-07-06T18:00:00+00:00",
    "source": "Synthetic sampling example; not a real field measurement",
    "provenance": "measured"
  }
}
```

This is an illustrative payload, not a reading to record against a real farm.
`soil_depletion` uses mm; `soil_water_content` uses m³/m³, not percent. The crop
`describe` operation lists all supported measurement quantities and units. A future
timestamp requires `forecast` provenance. Repeating a request ID returns the original
reading; conflicting data fail. Correct a reading by recording a new one and explicitly
reviewing the old reading as rejected when appropriate. No recorded value is overwritten.

For a saved root-zone budget, open **Field records → Reconcile soil state with a
measurement**. Supply the represented root depth, sampling coverage across depth and
field area, the end-of-day date, a signed depletion adjustment from the sampling time
to closing local midnight, its source or explicit zero-change assumption, and a reason.
Positive adjustment means drier. The represented depth must equal the calculation's
complete effective root depth. A point sensor is not automatically a root-zone average.

The water-content conversion is `1000 × (field_capacity − water_content) × root_depth_m`,
following [FAO-56 equation 87](https://www.fao.org/4/X0490E/x0490e0e.htm), which requires
average water content over the effective root zone. For this synthetic example, field
capacity 0.32 and depth 0.8 m give 56 mm depletion; a supplied +2 mm timing adjustment
adopts 58 mm. The adjusted state must lie between zero and total available water;
values outside that range fail rather than being clipped. Sampling representativeness,
sensor calibration and the timing adjustment remain supplied evidence, not verified facts.

`field_records` and `agronaut field` expose:

- `review_soil {calculation_id}`: available readings, attached evidence and later review changes.
- `apply_soil_state {calculation_id, observation_id, as_of, represented_depth_m,
  representation_note, depletion_adjustment_mm, adjustment_source, reason,
  expected_review, request_id}`: save a child calculation. `expected_review` is the
  selected reading's current `quality_review.id`, or null before any review.
- `remove_soil_state {calculation_id, as_of, reason, request_id}`: save a child
  without that date's update; preserve any other updates.

Adoption requires a completed local day and a non-rejected, non-forecast reading
from the same unit and matching cycle (or an unassigned reading). The reading must
fall within that local day or exactly at its closing midnight. Day boundaries handle
daylight saving and fractional UTC offsets. A changed review rejects a stale adoption
request; a retry of an already successful request still returns its saved version.

Updates may initialize the boundary before the first weather day or reconcile a day
within the calculation window. The model calculates the day's physical water balance
first, then replaces the end state. Reports expose prior modeled depletion, adopted
depletion and the correction separately. Subsequent days start from the adopted state.
An update can initialize an unknown state without inventing balances for preceding days.
No water log is created or changed. Original inputs and parent calculations remain
available. Exact reading and quality-review snapshots accompany saved revisions and
survive crop, weather and water-log recalculations. Later quality changes flag review;
they never silently rewrite prior results. Downloads and account export include these
snapshots; account erasure removes them with the owning farm.

Standalone `irrigation-plan` inputs accept a `soil_state_updates` array using the
contract returned by `soil.root_zone_budget` description. This is supplied evidence,
without authenticated observation IDs. Use the owned adoption operation to attach
actual stored measurement snapshots. A state update must cover the complete root depth on
its date, including any supplied growth. Layered soil, automatic sensor aggregation and
independent field validation remain open.

## Guided field-water intake in chat

Select a soil unit with `/units` and `/unit <id>`, or create one with `/newunit soil`.
Then use `/fieldplan` in web chat, the terminal chat, Telegram or WhatsApp. The intake
asks one question at a time and runs without a language model. It does not require a
complete JSON calculation request.

Choose 1 for crop-water demand or 2 for a soil-water budget, an optional existing crop cycle,
planted area with units, a start date and 1–31 consecutive local days. Supply the crop
coefficient, stage and source, then choose `manual`, `meteorology` or `open_meteo` for weather.
Manual entry asks for the type, method, source and daily grass-reference ET0 totals.
A budget also asks for soil properties, initial depletion, application efficiency,
daily rainfall, net irrigation and rainfall runoff, with sources.
The prompts state the units. For example, `1 hectare` becomes 10,000 m²; field capacity
`32` percent becomes 0.32 m³/m³. Manual forecast input requires an issuance timestamp with an
explicit UTC offset. Daily totals cover the farm's actual local day, including daylight
saving changes.

With `meteorology`, supply daily station readings directly. Choose `net` for daily net
radiation or `solar` for daily incoming solar energy and an explicit FAO-56 net-radiation
estimate. Both paths ask for elevation, wind measurement height, source, daily minimum
and maximum temperature and relative humidity, and mean wind speed. The solar path also
asks for latitude. Radiation must be a full-day energy total in MJ/m², not an instantaneous
W/m² reading; extrema of interval averages do not establish true daily extremes.
Declare observed, historical, scenario or forecast inputs; forecasts require an issuance
timestamp with UTC offset. Budget rainfall remains an explicit daily answer. Crop-demand
calculations leave rainfall unknown. These questions use local calculations and do not
fetch weather. Validation rejects an unsupported radiation estimate at its question;
the report retains the original readings and any solar-to-net calculation trace.

With `open_meteo`, supply WGS84 latitude and longitude in decimal degrees. The intake
shows the coordinates, local date window and farm timezone before requesting `fetch`.
Only that reply sends a weather request; starting, resuming, previewing and saving do
not fetch. This adapter supports 1–16 consecutive days within the last 90 days through
14 days ahead; provider coverage can vary. Use `/back` to edit or return to manual entry
if the window is unavailable. A failed fetch leaves the draft at the same question.

Fetched weather replaces manual ET0 and rainfall questions. The preview labels each
day as a historical model estimate or forecast, shows model-grid coordinates and
retrieval time, and retains the original provider response and aggregation assumptions.
Retrieval time is not forecast issuance; historical estimates are not observations.
Budget inputs still require explicit irrigation and runoff; runoff cannot exceed the
fetched day's rainfall. No crop coefficient, soil property or efficiency is inferred.
The retained snapshot supports resuming and saving offline. Backtracking before the
fetch discards it, so changed coordinates or dates require a new fetch. A response
arriving after a draft edit, cancellation or erasure cannot overwrite the newer state.

`/back` restores the preceding answer. Invalid entries leave the current question
unchanged. `/cancel` discards only the selected unit's field draft. `/fieldplan` resumes
that unit's draft after a restart. Switching units leaves the other draft intact;
return to that unit to resume it. If unit setup is also active, `/back` and `/cancel`
apply to unit setup first. `/reset` discards the selected unit's unfinished field intake.

The completed intake displays a calculation preview with sources and limitations.
Reply `save` to store it as a field calculation. Until then, it remains a draft.
Saving and draft removal are atomic; retrying the same completed save returns the
same calculation. Nothing in this flow records irrigation as completed. Reply `unknown`
for unavailable initial soil depletion; the result then retains unknown refill amounts.
Unknown rainfall, ET, soil properties and efficiency are never replaced with defaults.
Image-generated descriptions cannot answer the intake's measurement questions.

This conversational path uses daily grass-reference ET0, one Kc and one root depth.
The water path does not estimate Kc from a crop name or infer soil properties.
Equipment checks and conditional runtimes use the separate choices described below.
Use the full JSON/web intake for supplied meteorology.
Saved calculations can later
receive dated coefficient/root-depth revisions and explicit soil-measurement updates.
If a cycle's crop or planting date changes before saving, current cycle validation can
reject the stale assumptions; cancel and restart to choose the corrected cycle.

The same draft operations are available through `field_records` and the standard-library CLI:

- `start_intake {}` and `get_intake {}` return the current question, `draft_id` and `revision`.
- `answer_intake {answer, expected_revision, back?}` supplies a text answer, or uses
  `back: true` with an empty answer to edit. Stale revisions fail without changing the draft.
- `cancel_intake {expected_revision}` discards the current draft.
- `save_intake {draft_id, expected_revision}` stores a ready draft after review.

For example, `agronaut field start_intake` starts or resumes questions for the selected
local CLI unit. Answer operations use `--input` as other field commands do; ordinary
terminal conversation is available through `agronaut chat` and `/fieldplan`.

Schema v9 adds unit-owned intake drafts. Existing databases receive a private SQLite
backup before migration, and failed migrations roll back. Account export includes draft
answers, edit history, cycle snapshots and timestamps; account erasure removes them with
the owning farm. Draft storage does not create or modify farm observations.
Drafts created before the weather-source choice was added retain their original manual
questions. Draft versions 2–5 keep their existing manual/provider choices; new version 6
drafts add meteorological readings without requiring another database migration.

## Field-planning language

Use `/fieldplan fr` for French questions or `/fieldplan zh-Hant` for Traditional Chinese
questions in terminal chat, web chat, Telegram or WhatsApp. `/fieldplan en` selects
English. Plain `/fieldplan` resumes the current draft's language; new drafts default
to English. Selecting a different language keeps the answers and changes the draft
revision, so replies based on an older revision are rejected.

The translated questions cover water demand, soil-water budgets, drip/sprinkler equipment,
saved-budget target adoption and surface events, including meteorological, station and flow-interval
questions. They are **draft translations pending local review**. Calculation reports,
weather-provider details and detailed validator errors remain English, as stated in the
conversation. Use a decimal point and the units shown; narrative sources and crop names
are stored as supplied.

Canonical answer tokens shown in the questions remain valid. Localized shortcuts include
`inconnu` / `未知` for unknown depletion, `oui` / `是` for the documented affirmative
equipment/surface choices, and `utiliser` / `採用` for explicit budget adoption. Save with
`enregistrer` or `儲存` after previewing; `save` remains valid. Weather retrieval still
requires the explicit `fetch` reply after the coordinates and date range are displayed.

The offline service also accepts `field start_intake` with an input file containing
`{"language":"fr"}` or `{"language":"zh-Hant"}`. Translations need no model or network.
If an installed question translation is missing or no longer matches the English source,
Agronaut reports `FIELD_TRANSLATION_UNAVAILABLE`; resume with `/fieldplan en`. A language
choice does not infer a region, weather source, crop coefficient or irrigation method.

## Guided drip and sprinkler equipment checks

Start `/fieldplan` and choose **3** (or `drip`) for a flat pressure-compensating drip
installation, or **4** (or `sprinkler`) for a flat fixed solid-set full-circle sprinkler
grid. These experimental checks use the same engines and operating envelopes as the
JSON/CLI designs described above. They check a supplied layout; they do not select
products, size pipes automatically or establish when a crop needs irrigation.

The intake collects an optional crop cycle, geometry, emitter/nozzle specifications,
pipe dimensions and pressure ratings, source flow/head bounds, filtration and fitting
losses, application targets, soil limits, maintenance allowances and operating limits.
Each section requires its source and applicability. The questions state units: pipe
diameters and absolute roughness are in mm; head is in metres of water; flow is in
L/hour; time is in hours; depth is in mm. Percentages are converted to fractions for
the engine. No example specification or default soil limit is inserted into a draft.

Drip asks for **gross** application depth over the whole layout area and a supplied
wetted-area percentage. Sprinkler asks for **net** application depth and efficiency
that includes applicable wind and boundary losses. Supply an irrigation target from
a reviewed water budget or an explicit scenario, with its date and area applicability
in the application source. Crop ET alone does not establish a refill target. The intake
can explicitly adopt a saved budget as described below; it does not reserve farm resources.

Unsupported terrain, non-pressure-compensating emitters and unsupported sprinkler
patterns are rejected. Invalid counts, units, pressure bounds and other numeric inputs
leave the current question unchanged. Unknown values require evidence before continuing;
zero is accepted only for quantities where the model permits an explicit zero.

The ready preview includes zone flow/head bounds, candidate runtimes, daily water/time
totals, preliminary materials, binding constraints, sources and limitations. An infeasible
design is labeled **FAIL — no schedule**. Before the candidate runtimes, each failed
constraint names its zone or whole-design scope, evaluated value, required minimum or
allowed maximum, and unit. Short review prompts identify the supplied specifications
and assumptions to check against their sources before recalculating the whole design.
Values retain enough precision to show small failures, and the JSON result keeps the
structured constraint records. Saving retains that failed check for review and
does not turn it into an operating instruction. Reply `save` only after reviewing it.
Saved equipment calculations appear with other Field records. No irrigation, purchase,
approval, resource reservation or equipment operation is recorded by this intake.

The same `/back`, `/cancel`, unit switching, restart, export and erasure rules apply.
One field-planning draft is active per unit, so `/fieldplan` resumes an unfinished
water or equipment draft. Cancel it to start another type. Older water drafts keep their
original choices; starting a new draft enables equipment choices. The existing
`start_intake`, `answer_intake` and `save_intake` operations also expose these questions
through `field_records` and the standard-library CLI; no new schema migration is needed.

### Adopt a saved water-budget target

At the equipment application's source choice, enter `budget` instead of `manual`.
Choose a listed soil-water calculation or enter its ID, select a closing soil-state date,
and describe why that budget applies to the full equipment layout. The source must be
a `root_zone_budget` for the same unit, crop-cycle assignment and farm timezone.
Its area must match the whole layout; subset scaling is not inferred. An unknown soil
state cannot supply a target. A known below-threshold state can supply an explicit zero.

The intake displays the selected version and date, area, selected day's weather kind,
net/gross refill and efficiency before asking for `use`. This is an **end-of-day refill
budget**: that day's rain and irrigation inputs have already been included. Adopting it
does not record that amount as same-day irrigation, set an irrigation time, or account
for subsequent weather. For drip, assess the applicability of a uniform root-zone bucket
to localized wetting; adoption is an equipment-sizing scenario, not a validated drip
scheduling model.

Explicit adoption supplies the gross target to drip. For sprinkler it supplies the net
target and the budget's efficiency together, preserving their relationship. Those questions
are then skipped; local application limits, pattern/wetting evidence, maintenance and
their sources remain required. `/back` before adoption removes the imported values;
returning to `manual` restores independent input. Existing unfinished equipment drafts
retain their original manual sequence.

The preview, saved application source and `water_target` metadata retain the linkage,
applicability statement and complete immutable source calculation. Offline resume and
save work without fetching weather. Current crop/planting assumptions, linked stage
corrections, linked daily-water corrections and soil-measurement review changes are
checked at selection, adoption and save. Resolve changed evidence in the water budget
before adopting it into a new equipment plan. Each saved calculation remains unchanged.

For a saved equipment plan, `field_records review_target {calculation_id}` (also
`agronaut field review_target --input request.json`) reports whether that linked evidence
has changed. **Field records** displays the target and a warning when it needs review;
its downloadable evidence includes the full snapshot. This check does not automatically
adopt newer weather/calculation versions or unrelated observations. Retrying an already
successful save returns the original calculation even if evidence has since changed.

## Revise a saved drip or sprinkler design

In **Field records**, select a saved equipment calculation and open **Revise this
equipment design**. Choose one or more sections, edit their supplied specifications
and sources, and provide a reason. **Preview equipment revision** shows the exact
input changes, the original and revised constraint status, and the full recalculated
report. Editing an input invalidates that preview. **Save equipment revision** creates
a linked child and selects it; the original remains available for comparison.

The same workflow is available offline. Create a request using the ID of your saved
`drip_design` or `sprinkler_design` calculation:

```json
{
  "calculation_id": "SAVED_EQUIPMENT_ID",
  "patch": {
    "supply": {
      "daily_available_l": 2000,
      "source": "Replace with the source supporting this revised daily allowance"
    }
  },
  "reason": "Replace with the reason for this change",
  "target_policy": "preserve"
}
```

```bash
agronaut field preview_equipment_revision --input equipment-revision.json
```

Review the returned changes and result. Add its `preview_token` and a new `request_id`
to that same request, then run:

```bash
agronaut field revise_equipment --input equipment-revision.json
```

The preview token covers both the request and its calculated result. Changed inputs
or a changed engine result require another preview before a new revision is saved.

Patch fields use the existing engine's units and schema. Every changed section must
explicitly include its `source`; unmentioned fields retain their saved values. Unknown
fields, unsupported geometry, invalid values and empty revisions are rejected. All
constraints are recalculated together, and an infeasible child can be saved as an
inspectable failed scenario. Saving does not buy equipment, reserve resources or log
performed irrigation. Repeating the identical successful request returns the same child.

For a budget-linked design, `target_policy: "preserve"` retains its exact target and
source snapshot. The represented area must still match; drip must retain its gross
depth, while sprinkler must retain its net depth and efficiency. Current crop assumptions,
corrected daily water and changed measurement reviews are rechecked before saving.
A preview cannot authorize retaining evidence that changed afterward.

To withdraw that link, explicitly use `target_policy: "detach"` and include
`application.source` explaining the independent target, alongside any changed application
values. The child records this decision and the prior budget ID; the original retains
the complete linked evidence. Detaching does not automatically select a newer budget.
To adopt a different budget, use the guided equipment consultation's budget selection.

## Guided surface-irrigation event evaluation

Start a new `/fieldplan` draft and choose **5** (or `surface`). This evaluates one
furrow, border or basin using supplied observations or an explicit scenario. It uses
the existing experimental surface-event engine; it does not predict advance/recession,
fit infiltration coefficients or recommend a cutoff time.

The questions collect geometry, event conditions, infiltration coefficients and their
applicable opportunity-time range, then station and flow records. All following times
are **elapsed minutes from the offset-aware start instant**, including across daylight
saving changes. Inflow and tailwater are interval-average **litres per second**. The
infiltration basis is explicit: border/basin depth in mm, or one furrow's cumulative
volume per metre of furrow in m², with time in minutes. Furrow width means represented
spacing, not wetted perimeter. Each section, station and flow interval requires a source.

Supply the number of stations including inlet and field end, then each location and
advance/recession time. Distances must increase; advance times cannot decrease;
recession must fall within monitoring and the supported infiltration-time range.
Supply the number of inflow and tailwater intervals, then each start/end, mean flow and
source. Both series must cover the entire monitoring window continuously. Inflow splits
at cutoff and explicitly records zero afterwards. A no-tailwater event still needs an
explicit zero-flow interval. Missing timings or flows are never filled with zero.

The supported event starts without ponded surface water, receives no rain and has fully
receded by monitoring end. Confirm those conditions explicitly. `observed` requires a
completed event; future or hypothetical timings must be labeled `scenario`. Supply the
pre-event uniform root-zone deficit or reply `unknown`, and supply a justified accounting
tolerance as a percentage. Unknown deficit leaves the corresponding performance metrics
unknown. Failed water-accounting checks withhold performance metrics in the preview.

Review the complete calculation and evidence before replying `save`. A saved evaluation
appears in Field records but creates no daily water log or equipment action, including
when its event is observed. A single event is not automatically treated as the entire
unit's daily water total. Current farm timezone, crop-cycle dates and observed-event
completion are checked when saving.

The same CLI/tool operations and web, terminal, Telegram and WhatsApp question flow
apply. `/back`, `/cancel`, restart, unit scope, export and erasure retain their meanings.
Older drafts retain their original choices; cancel and start a new draft to choose surface
evaluation. New drafts keep reversible edit records instead of repeated full snapshots,
so long station logs and retained provider/budget evidence are not copied on every answer.
Existing full-snapshot draft histories remain readable and editable.

## Daily rooting depth

A root-zone budget may include `root_zone_schedule`, with exactly one ordered entry
for each weather day. `soil.root_depth_m` remains the depth at the initial boundary,
before the first weather day. Daily depths apply at the start of their dates, before
rainfall, irrigation and ET. The example below starts from an initial depth of 0.8 m:

```json
"root_zone_schedule": [
  {"day": "2026-07-06", "root_depth_m": 0.8,
   "source": "Synthetic rooting assumption"},
  {"day": "2026-07-07", "root_depth_m": 0.9,
   "source": "Synthetic rooting assumption",
   "added_layer_water_content": 0.25,
   "added_layer_source": "Synthetic average water content immediately before inclusion"}
]
```

These values demonstrate arithmetic, not a recommended crop growth rate. No depth or
growth rate is inferred from crop, stage or age. Depths cannot decrease in this model.
Each increase requires average volumetric water content in the newly reached layer,
with a source describing its timing and applicability. It must lie between the supplied
wilting point and field capacity. Unchanged depths must omit both added-layer fields
(or supply null for both). Missing days, unexpected dates and extrapolation fail.

Following [FAO-56 Example 38, note 5](https://www.fao.org/4/X0490E/x0490e0e.htm), the
depletion added before that day's balance is
`1000 × (field_capacity − added_layer_water_content) × increase_in_depth_m`.
The newly represented available water is
`1000 × (added_layer_water_content − wilting_point) × increase_in_depth_m`.
With field capacity 0.32, wilting point 0.15, new-layer content 0.25 and an extra 0.1 m,
the storage capacity grows by 17 mm: 7 mm depletion and 10 mm available water.

The report separates these storage changes from rainfall, irrigation and drainage.
Daily stress and refill thresholds use the new capacity. An unknown prior state remains
unknown; adding a known layer cannot establish water content throughout the existing root zone.
End-of-day soil measurement adoption runs after that day's growth and water balance and
must match the complete depth on that date. Initial-boundary updates use the initial depth.

In the web intake, select **Use daily root depths** to enter each date's values. Existing
budgets provide **Review and revise daily root depths**. CLI and scoped chat use
`revise_roots {calculation_id, root_zone_schedule, reason, request_id}`. This saves a
child calculation with initial state, soil properties, crop, weather, applied water logs
and measurement snapshots preserved. An empty schedule restores the initial fixed depth.
If a retained measurement no longer represents its date's depth, explicitly withdraw
that soil-state update before revising roots, then adopt suitable evidence. Revisions
never silently reinterpret a sample's represented depth.

When extending weather for a calculation with daily roots, supply `root_zone_days`
alongside `events` to `revise_weather`; both arrays must cover exactly the appended dates.
Existing root entries are preserved. Use `revise_roots` to change them. Fixed-depth budgets
retain their existing extension behavior.

An offline synthetic example is available:

```bash
agronaut irrigation-plan --input examples/field_water_roots.json
```

This model retains one set of hydraulic properties and one homogeneous daily bucket.
Water transport below the current root zone, root contraction, layered properties,
localized wetting and independent field validation remain outside its implemented scope.

## Mineral nutrient accounting

Soil units also expose **Account for mineral fertilizer nutrients**, plus the
`nutrient-budget` CLI and `soil.nutrient_budget` capability. This compares declared mineral
product quantities with sourced remaining application targets; it does not derive crop
requirements or record completed fertilization. See [mineral-fertilizer nutrient accounting](NUTRIENT_ACCOUNTING.md)
for the input contract, unknown-value behavior and synthetic example.

## Sprinkler catch-grid evaluation

In **Field records → Evaluate sprinkler catch cans**, enter a complete rectangular grid of
calibrated catch depths from one sprinkler zone. Supply the test start with an explicit
timezone offset, elapsed exposure in seconds, observed/scenario label, grid spacing, sources
and conditions. Confirm that every position represents equal area and received the same full
rain-free exposure. Wind speed may be explicitly unknown; the report retains that limitation.
In the depth box, enter one grid row per line and comma-separated depths in millimeters.
Use `?` for an unreadable or unknown depth and `0` only for an explicit zero reading.

The experimental calculation reports mean caught depth, mean catch rate and low-quarter
distribution uniformity. It implements the depth-ratio method described by
[UF/IFAS AE194](https://ask.ifas.ufl.edu/publication/AE194), using the one-zone grid/exposure
approach described by [Utah State Extension](https://extension.usu.edu/cwel/research/simple-sprinkler-performance-testing-for-washington-county).
For N equal-area positions, the arithmetic is:

- Mean depth = sum of all depths / N.
- Low-quarter mean = mean of the lowest N/4 depths.
- Low-quarter DU (%) = 100 × low-quarter mean / overall mean.
- Mean catch rate (mm/h) = mean depth × 3600 / exposure seconds.

No intermediate rounding is applied. Equal-depth ties use row/column order for the audit
list of low-quarter positions; ties do not change the statistic. This implementation requires
4–2500 positions, at least two rows and columns, and a count divisible by four. That count
rule is an implementation boundary, not a requirement to discard field readings or a
statement that four collectors establish field performance. Other sample layouts need a
different supported method. Grid spacings must be positive; depths are 0–1000 mm and exposure
is 1–86400 elapsed seconds. Duration crosses daylight-saving transitions in elapsed UTC time.

Every declared position must appear exactly once. An unknown depth withholds all statistics
rather than replacing it with zero or dropping that position. If all depths are explicitly
zero, mean depth and rate are zero but DU is undefined. Unequal-area layouts, center-pivot
radial weighting, rain-contaminated tests, unequal exposures and volume-only readings are
unsupported. Collector calibration, evaporation, splashing, obstructions and representative
placement need field review. Grid spacing does not establish the total irrigated area.

The output describes the catch sample and its conditions. It does not infer net root-zone
water, irrigation efficiency, adequate crop water, savings, a pass/fail rating or a runtime
recommendation. It does not certify field validation or validate a saved hydraulic design.
Observed tests must have ended; scenarios remain hypothetical.

**Evaluate and save catch test** stores an immutable calculation in the selected soil unit
and optional crop cycle. The farm timezone must match, and a selected cycle must contain the
test's local start and end dates. Repeating identical input through the form is idempotent.
The full grid, sources and report are downloadable; season calculation history includes the
test and flags later planting/timezone conflicts. Account export and erasure include it.
Saving does not create field measurements, daily water totals, completed tasks or equipment
actions. Re-enter corrected readings as another calculation while retaining the original.

The same method runs offline:

```bash
agronaut evaluate-sprinkler-catches --input examples/sprinkler_catches.json
agronaut evaluate-sprinkler-catches --input examples/sprinkler_catches.json --json
agronaut capabilities --system soil
```

The checkout example is synthetic and contains 16 values totaling 148 mm: mean 9.25 mm,
low-quarter mean 7 mm, DU about 75.676%, and mean rate 18.5 mm/h over 1800 seconds. It is an
arithmetic fixture, not field evidence. `soil.sprinkler_catch_test` exposes the strict JSON
schema through the capability registry and scoped tool dispatch. Saving through the existing
field workflow uses task `sprinkler_catch_test` and preserves its original evidence. The
model, adapter and CLI use only the Python standard library.

## Drip-emitter field evaluation

**Field records → Evaluate drip emitter flows** accepts timed-volume samples from distinct
surface-drip emitters in one zone. The sampled emitters must share a specification and equal
representation weights, and zone operation must be stable. Supply the test window, sample
count, layout/coverage source and conditions. Record each emitter's ID, lateral, position,
collected milliliters, collection duration in seconds, pressure in kPa and source.

The method follows [UC ANR timed-volume sampling](https://ucanr.edu/site/maintenance-microirrigation-systems/emitter-evaluations)
and its [low-quarter discharge uniformity calculation](https://www.ucanr.edu/site/maintenance-microirrigation-systems/what-does-professional-evaluation-information-mean).
Agronaut normalizes each sample separately:

```
flow (L/h) = volume (mL) × 3.6 / collection duration (seconds)
low-quarter DU (%) = 100 × mean of lowest N/4 flows / mean of all N flows
```

Collection times may differ. Averaging raw volumes would therefore give the wrong result.
The result includes individual flows, mean/minimum/maximum flow, the low-quarter mean and
IDs, plus exact rational audit values. Equal-flow ties use ID order without changing the
statistic. Supplied pressures retain their locations and sources; the pressure range reports
known readings only and displays the unknown count. No pressure cause or clogging diagnosis
is inferred.

Declare 4–2500 expected emitters with a count divisible by four, then include every expected
sample. This is the implementation's arithmetic boundary, not a field sampling sufficiency
rule. Do not discard samples to fit it. IDs and lateral/position pairs must be unique.
Repeated samples of one emitter, mixed specifications or unequal weights require another
method. Volumes are 0–1,000,000 mL; collection durations are at least one second and no longer
than the test window; supplied pressures are 0–10,000 kPa. These are input bounds, not
recommended equipment operating ranges.

In the form, enter one comma-separated line per emitter, without a header:

```
E1,L1,"Head, west",10,30,?
E2,L1,Middle,20,60,150
E3,L2,Head,30,30,155
E4,L2,Tail,40,60,150
```

Use `?` for unknown volume or pressure; use zero only when it is explicitly recorded.
Collection duration must be supplied for every sample. Missing volumes withhold all
sample-wide flow and uniformity statistics while retaining known individual flows. All-zero
flows yield zero mean flow and undefined DU. Missing pressures remain unknown without
preventing the flow calculation. The form's reading source applies to every row; JSON clients
can supply a different source for each sample.

The test window uses aware timestamps and spans 1–86,400 elapsed seconds, including across
daylight-saving transitions. Individual collection duration must fit within that window;
the window does not establish when each sample was taken or whether samples were concurrent.
Observed tests must have ended. Scenarios remain hypothetical. Saving requires the farm
timezone and, when assigned, a crop cycle containing both local start and end dates.

Saved calculations preserve inputs and results through account export/erasure, JSON download
and season calculation history. Changed planting/timezone assumptions are flagged without
rewriting history. Identical form submissions return the original saved test. Saving creates
no observations, daily irrigation totals, completed tasks or equipment actions. Corrections
are separate saved calculations. The result describes sampled emitter discharges; it does
not establish whole-field soil-water uniformity, design emission uniformity, efficiency,
savings, total zone flow, sufficient sampling, a pass/fail rating or an operating schedule.

Offline commands and the `soil.drip_emitter_test` capability expose the same method:

```bash
agronaut evaluate-drip-emitters --input examples/drip_emitters.json
agronaut evaluate-drip-emitters --input examples/drip_emitters.json --json
agronaut capabilities --system soil
```

The synthetic checkout example has eight emitters with different collection durations.
Their mean flow is 3.9 L/h, low-quarter mean 1.2 L/h and DU 400/13% (about 30.769%). These
values check arithmetic only; they are not field evidence. Saved field-workflow requests use
task `drip_emitter_test`. The model, schema, adapter and CLI require only the standard library.

## Separate basal crop demand and surface evaporation

**Field records → Calculate basal demand and soil evaporation** provides an experimental
daily dual-coefficient calculation. It reports basal ET, soil evaporation and potential
crop demand separately, plus the evaporating layer's water balance. Its basis is
[FAO-56 (1998), Chapter 7](https://www.fao.org/4/X0490E/x0490e0c.htm), equations 69, 71,
73–75 and 77–79. The implementation was checked against Example 31's dry-down and the
supplied wetting-fraction cases in Examples 32–34. Example 35's first-day wetting and
surface-percolation arithmetic is also covered. Source reviewed October 20, 2026;
these numerical checks are not independent field validation.

Supply surface field capacity and wilting point as volumetric fractions, evaporating
depth, readily evaporable water (REW), and initial **surface** depletion at the preceding
day's closing boundary. Total evaporable water (TEW) is computed from the surface properties.
REW must be below TEW and initial depletion must be within zero and TEW. This surface
depletion is a separate state from the root-zone depletion used in irrigation budgets.
No soil-texture values or initial wet/dry condition are assumed.

For every day, supply grass-reference ET with its method, provenance and full local-day
duration, rainfall, net irrigation, runoff, basal Kcb, Kc max, canopy cover, physical wetted
fraction and exposed-and-wetted fraction. The last fraction must fit within both exposure
and wetting. Sources must explain canopy shading, mixed rain/irrigation and any changing
fractions. Kcb is a basal coefficient, not the existing single Kc; no automatic conversion,
climate adjustment, rainfall wetting rule or shading correction is applied.

With **Enter daily weather**, the native form accepts ten CSV columns per day and computes
each local day's duration from the farm timezone. The JSON interface also accepts separately
dated forecast issue or retrieval metadata. Observed/historical inputs require completed
local days. Forecast labels remain forecasts in results, including retrospective calculations.

With **Import weather snapshot**, paste the downloaded Agronaut weather bundle containing
`weather` and `weather_sources`. Supply eight CSV columns: date, net irrigation, runoff,
Kcb, Kc max, canopy cover, physical wetted fraction, exposed-and-wetted fraction. The crop
rows must cover exactly the snapshot's dates. ET and rain come from the retained records;
their values, labels and timestamps must match the snapshot's reproducible transformation.
No network request runs during import. The existing weather preview can produce the bundle;
CLI or tool callers can include its `weather` and `weather_sources` arrays in their inputs.
Reports retain the provider's assumptions and show daily weather, retrieval time, requested
location and grid location. Snapshot consistency does not prove site representativeness or
independent measurement quality. Historical provider estimates remain historical estimates;
download time is not invented model issuance.

Rain and irrigation arrive at the beginning of each modeled day. Irrigation depth is
averaged over the represented field, while its concentration uses the physical wetted
fraction. Evaporation uses the exposed-and-wetted fraction. The surface store drains after
wetting above its capacity. A finite-water cap reduces effective Ke if the requested
evaporation would exhaust the store; the output flags that cap and reports a balance residual.
Basal ET includes basal diffusive evaporation and should not be read as measured transpiration.

The caller must explicitly confirm unstressed basal ET, negligible root extraction from
the evaporating layer, and absence of mulch, snow or frozen soil, with applicability evidence.
The scalar surface state carries between days even when supplied fractions change; it does
not track separate wet and dry patches. Shallow roots, drip beneath canopy and changing
wetting patterns particularly need review. This calculation does not simulate root stress,
layered transport, salinity or a subdaily event sequence.

```bash
agronaut dual-crop-water --input examples/dual_crop_water.json --json
agronaut calculate --system soil --task dual_crop_water --input examples/dual_crop_water.json
```

The example is synthetic. Scoped `field_records save` with `task: "dual_crop_water"` and
the native form save immutable results with their complete inputs and sources. Saving
selects the new result for inspection; repeated identical requests return it. The plot
shows modeled basal ET and surface evaporation, and the download includes daily states,
requested/effective Ke and source evidence. Unit/zone/cycle ownership, farm timezone and
cycle dates are checked. Season review flags later crop, planting, timezone and closure
conflicts. Export/erasure includes the calculations.

**Update weather or extend the planning window** and `field_records revise_weather` also
accept saved dual-demand calculations. Supply `{calculation_id,weather,weather_sources?,
dual_days?,request_id}`. Existing dates retain their initial surface state, daily coefficients,
fractions, net irrigation and runoff. Revisions save a new linked calculation and leave the
parent unchanged. Forecast replacements require newer issuance or download time on the same
timestamp basis; observed weather cannot be downgraded to a forecast. Replacing rainfall
still requires the retained runoff to be physically consistent with that rainfall.

For an extension, `dual_days` must contain exactly one complete daily input object per
appended weather day, using the same fields as `days` in the example. New dates must remain
consecutive and within the selected crop cycle. Coefficients and irrigation are never copied
forward implicitly. Partial provider updates retain all snapshots still used by the combined
weather window and discard superseded snapshots only when no remaining day references them.
Weather revisions neither record performed irrigation nor revise the root-zone budget.

Saved dual-demand calculations also support **Recalculate with selected records** and
`field_records replay`. Select daily-water records for this unit and crop cycle. Observed
weather is accepted by default; historical weather requires the explicit `allow_historical`
choice. Forecast and scenario weather cannot be used for selected actual-water days. The
calculation keeps its original weather values and provider snapshots: selected recorded rain
is retained separately in `rainfall_overrides` and replaces weather rain in the surface balance.
Irrigation and runoff replace the matching daily amounts. They carry a separate `water_source`,
leaving the daily crop-coefficient and fraction source intact. Results retain the selected
record IDs and their provenance/quality. Neither replay nor later weather refreshes write
performed-work records. Corrections are adopted only through another explicit replay.

Replay preserves surface soil, initial depletion, canopy/wetting fractions and crop assumptions.
Review their applicability to the actual events first: daily water totals do not prove early-day
wetting, an unstressed crop, or the represented wetting pattern. The result remains a modeled
surface balance and potential crop demand. It does not measure crop water use or validate the
assumptions. Other days keep their supplied inputs, including any future forecasts.

Offline requests may supply a `rainfall_overrides` array with `day`, `rainfall_mm`, `source`,
`provenance` (measured/reported/estimated), `quality` (unverified/checked), and optional
`water_record_id`. Overrides must be unique and within the calculation window. Unknown weather
rain is usable only when a separate rain value covers that day. Runoff is checked against the
rain actually used, and rainfall is never counted twice. A new owned calculation cannot import
record IDs as claims of owned evidence: save inputs without those references, then select the
owned records through replay. Exported calculations can still be reproduced offline with their
complete inputs; offline reproduction does not verify ownership of annotated IDs.

Potential demand is not an irrigation requirement. Surface-layer percolation is not a
whole-root-zone drainage loss. These results are not automatically adopted into the
single-coefficient root-zone model or daily actual-water logs. Coupled surface/root stress
and water accounting remain unfinished. Water-log replay uses the separate surface model;
it does not apply the existing root-zone algorithm to these results.

## Assess water storage across soil layers

**Field records → Assess soil layers and water storage** accepts a depth profile for the
selected field or irrigation zone. Supply the represented time, effective root depth, a
sampling/coverage rationale and source, then one row per contiguous layer. Each row requires
top and bottom depths in meters, field capacity, wilting point and water content in m³/m³,
separate soil-property and water-reading sources, provenance and quality. All fractions must
use the same bulk-soil-volume basis, including consistent treatment of coarse fragments.
No soil-texture defaults or gravimetric-to-volumetric conversion are inferred.

Use decimal strings with up to six fractional digits. The profile must start at depth zero,
have no gaps or overlaps, and reach the declared root depth. A partly rooted layer contributes
only its thickness above that depth. Layers below the roots remain in the evidence but do
not contribute to root-zone totals. There may be 1–100 layers, with depths up to 10 m.
These input bounds are software limits, not crop rooting recommendations.

Enter `?` in the native form, or JSON `null`, for unknown water content. Zero is an explicit
reading. Missing or rejected readings in any rooted layer withhold root water-storage and
depletion totals; supplied field-capacity, wilting-point storage and available-water capacity
remain visible. The input values and quality labels remain in the saved evidence. Unverified
readings can be calculated but are not thereby verified. Quality describes this supplied
assessment; it does not alter the separate measurement journal or its reviews.

The calculation integrates each included layer thickness times its volumetric fraction,
converting meters of water to millimeters. It follows the storage definitions in
[FAO-56 Chapter 8, equations 82 and 87](https://www.fao.org/4/X0490E/x0490e0e.htm) and depth-zone
summation illustrated by the Renshaw and Hubbard examples in
[UMN Extension's irrigation-scheduling guide](https://extension.umn.edu/natural-resources/conservation/agricultural-soil-and-water/basics-of-irrigation-scheduling).
FAO-56 is the 1998 edition; the UMN page is marked reviewed 2026, accessed October 19, 2026.
Source links and method references accompany each result. Numerical tests reproduce the
Renshaw cumulative available-water examples after unit conversion; synthetic field-capacity
and wilting-point pairs in those tests are arithmetic fixtures, not local soil estimates.

Above-field-capacity and below-wilting-point amounts are reported separately for the rooted
layers. Their signed deficits may cancel in the sum, but the result withholds bounded depletion
if any rooted layer is outside its own bounds. It never clips a reading or assumes that wet
layers immediately supply dry layers. Exact fractions accompany six-decimal display values.
An apparent displayed zero does not override an exact out-of-bounds comparison.

This is an experimental storage assessment. Layer-to-layer flow, drainage timing, root uptake,
capillary rise and salinity are not simulated. Point readings do not establish representative
layer or field averages. The assessment supplies no irrigation trigger, rate or runtime and
does not replace the existing homogeneous daily water-balance state. Field validation and
layer-resolved transport/uptake remain future work.

```bash
agronaut evaluate-soil-profile --input examples/soil_profile.json --json
agronaut calculate --system soil --task soil_profile --input examples/soil_profile.json
agronaut capabilities --system soil --json
```

The example is synthetic. Native **Assess and save soil profile** and scoped `field_records`
`save` with `task: "soil_profile"` save immutable assessments with sources. Identical request
retries return the original; changed inputs need a new request. Select **Saved calculation**
to inspect prior assessments or download full evidence. Use an unassigned assessment for
preplant sampling; assigned profiles must fit the crop cycle. Saving requires the farm's
timezone. Observed timestamps cannot be in the future; scenarios retain their label.
Crop/planting, timezone or later closure conflicts are flagged in season review without rewriting results.
Assessments participate in owner export and erasure and preserve zone identity where applicable.

## Compare soil predictions with measurements

For a saved root-zone budget, **Compare soil predictions with measurements** creates a
read-only diagnostic report. Select observations, declare whether the dataset is synthetic
or field data, and explain how the observations were selected and whether they were used
to develop the model. That declaration is retained; it does not establish authenticity or
independence. No acceptance threshold or field-validation status is inferred.

For each selected reading, supply the prediction's end-of-day date, represented complete
root depth, sampling coverage, and an explicit depletion adjustment to closing local midnight
with its source. A point measurement is not automatically representative of the modeled
root zone or field. Volumetric readings are converted using the calculation's soil properties
and root depth on that date. The existing full-depth, timing and bounded-depletion rules apply.
Unknown, forecast or rejected target observations cannot be treated as measured reference
states; estimated and reported readings retain their own provenance in the diagnostic.

The comparison uses the saved water-balance prediction **before** any same-day state update.
It does not compare a newly adopted reading with itself. If no prediction existed before the
reading was adopted, comparison is unavailable. An observation already linked to an earlier
state update is shown but excluded from aggregate metrics. Undeclared reuse, calibration of
parameters against the targets, and selection bias cannot be detected automatically.

Error is predicted depletion minus the adjusted reference depletion, in mm. Positive error
means the model predicts drier soil. The report gives signed mean error (bias), mean absolute
error (MAE), root mean square error (RMSE), maximum absolute error, and sample counts. Each
included day has equal weight, with at most one observation per day. Empty eligible selections
produce unknown metrics, not zero error. Groups retain observation provenance, effective
quality, and the prediction's weather kind. Forecast predictions may be compared after the
day is complete; their weather remains labelled forecast. Counts are not independent sample
sizes, and missing dates do not imply zero error or complete-season coverage.

The report includes the saved calculation, selected observation/review snapshots, conversion
inputs, exclusions and limitations. Download it from the page, or use the scoped `field_records`
operation `compare_soil` with this request shape (the dates and values below are synthetic):

```json
{
  "calculation_id": "SAVED-ROOT-ZONE-BUDGET-ID",
  "dataset_kind": "synthetic",
  "selection_note": "Synthetic diagnostic example; independent field evidence is not established",
  "comparisons": [{
    "observation_id": "OWNED-SOIL-OBSERVATION-ID",
    "as_of": "2026-07-06",
    "represented_depth_m": 0.8,
    "representation_note": "Synthetic full-root-depth and field mean",
    "depletion_adjustment_mm": 2,
    "adjustment_source": "Synthetic net depletion between sampling and midnight",
    "expected_review": null
  }]
}
```

```bash
agronaut field compare_soil --input soil-comparison-request.json > soil-comparison.json
```

Use `review_soil` to obtain the current quality-review ID for `expected_review`, or `null`
when no review exists. The page captures the review shown before submission and rejects a
concurrent change. A cached report is hidden when its selections, declarations or review IDs
change. Comparison requires owned observations from the unit and compatible crop cycle,
matching farm timezone, completed local days and valid modeled states. It does not save a new
calculation, adopt a soil state, alter quality, record irrigation, or calibrate parameters.
Independent pilot protocols, calibration/evaluation separation, specialist acceptance criteria
and real field evidence are still needed before claiming field validation.

## Laboratory soil reports

Before collecting samples, read **Field guides → Preparing a soil sample and laboratory record**,
or use `agronaut field-guide --topic soil-sampling --language fr` (also `en`/`zh-Hant`).
The guide covers laboratory instructions, sampling context and report transcription;
sampling depths and handling requirements must be agreed with the receiving laboratory.

**Field records → Record laboratory soil tests** stores source-backed transcriptions of lab
reports for the selected soil unit and optional crop cycle. Select the intended cycle first;
**No cycle assigned** shows unassigned reports only. A report keeps a local entry name, lab
name and report/sample identifiers, sample location, sampling and reporting dates, sampling
depth in cm, optional subsample count, sampling procedure source and report reference.
Sampling can predate planting, and sampling/reporting can follow crop closure. Dates must
describe actual events, be nonfuture in the farm timezone, and put reporting on or after
sampling. Both depths must be known and ordered, or both left unknown.

Each result preserves the analyte, qualifier, exact numeric text or categorical text, unit
and analytical method. The importance of retaining methods is illustrated by
[UMN's soil-test explanation](https://soiltest.cfans.umn.edu/soil-test-results-agronomic-crops):
different phosphorus test methods can produce values on different scales. Agronaut does not
convert units, equate methods, average samples or derive fertilizer requirements from these
transcriptions. A future interpretation needs a suitable crop, region and calibrated method.

The form accepts six comma-separated columns per row, without a header:

```
ph,pH,=,6.20,pH,"water, 1:1"
p,Extractable phosphorus,<,0.010,mg/kg P,Olsen
n,Nitrogen,NR,?,?,?
trace,Example analyte,ND,?,mg/kg,?
texture,Texture class,TEXT,Sandy loam,?,Laboratory description
```

The examples are synthetic. Copy the laboratory's actual wording and number formatting;
quote text containing commas. `=`, `<`, `<=`, `>` and `>=` retain a numeric result or bound.
`NR` means not reported and `ND` means not detected; both require `?` for the value. If the lab
provides a numeric detection limit, retain it with the appropriate inequality. `TEXT` retains
a categorical or descriptive result. `?` in the unit or method column means unknown. A zero
is an explicitly reported numeric result, never a replacement for a detection limit or an
unreported result. Separate rows may describe the same analyte under different methods.

**Save soil report** preserves an immutable version. Select its latest version to correct
it, supplying a reason. The local entry and cycle assignment remain fixed within the history;
a different sample needs a different entry. **Void soil report** appends a reasoned void
without deleting the original. Correcting the latest void with a reason restores the entry.
Stale corrections fail, and retrying the same request returns the original receipt. The web
form avoids creating a revision when inputs have not changed. Use **Soil report revision
history** to inspect earlier values and **Download soil report history** for full sources.

Assigned reports appear in season reviews and cross-season views. Current reports are shown
separately with their methods, units, depths, dates and qualifiers; full correction history is
included in season downloads. Voided or superseded reports do not appear as current. No
assigned reports means unknown soil-test history. Unassigned reports are not attached to a
crop automatically. Recording a report does not alter soil-water state, fertilizer targets,
application records, or crop lifecycle. `operator_transcribed` means entered by an operator,
not verified with the laboratory.

The CLI and scoped `field_records` tool expose the same operations:

```bash
agronaut field --unit SOIL-UNIT-ID describe_soil_tests
agronaut field --unit SOIL-UNIT-ID record_soil_test --input soil-report-request.json
agronaut field --unit SOIL-UNIT-ID list_soil_tests
agronaut field --unit SOIL-UNIT-ID get_soil_test --input report-id.json
agronaut field --unit SOIL-UNIT-ID void_soil_test --input void-request.json
```

`examples/soil_report.json` is a synthetic request illustrating the schema. A save uses
`{data,request_id,cycle_id?,supersedes_id?,reason?}`. Corrections name the latest `supersedes_id`
and a reason; first saves omit them. Listing takes `{cycle_id?}`, where omitted/null means
unassigned only. Getting takes `{report_id}`; voiding takes `{report_id,reason,request_id}`.
`describe_soil_tests` returns the full contract and qualifier names (`eq`, `lt`, `le`, `gt`,
`ge`, `not_reported`, `not_detected`, `text`). IDs, owner and unit scope come from the selected
owned unit, not from supplied report data.

Reports allow 1–200 uniquely identified result rows. Numeric values remain decimal strings
(up to 64 characters, finite with an adjusted decimal exponent from -100 to 100); signed and
scientific notation are retained without physical-range interpretation. Text results allow
up to 500 characters. Depths are 0–1000 cm with top strictly less than bottom, and subsample
counts are 1–1,000,000 or explicitly unknown. These are storage bounds, not sampling advice.
Schema v11 adds the report table through the transactional migration and pre-migration backup.
Account export includes `soil_tests`, and owner erasure removes the reports and their history.

## Coupled soil-water replay

The experimental `coupled_soil_water` task advances drainage, infiltration, stored surface
water, soil evaporation and crop root uptake through a shared soil profile. It accepts
1–366 consecutive local days. Evaporation can draw below wilting point; root uptake cannot.
Daily results distinguish incoming rainfall and surface irrigation, runoff, deep drainage,
evaporation and transpiration. This is a modeled water balance, not an irrigation prescription.

Run the synthetic example offline:

```bash
agronaut coupled-soil-water --input examples/coupled_soil_water.json
agronaut coupled-soil-water --input examples/coupled_soil_water.json --json
agronaut capabilities --system soil --json
```

The example's crop and numerical parameters are demonstrations, not calibration for a
region or farm. Supply the represented area, disjoint patch fractions, contiguous soil
compartments, initial water and drying memory, drainage/evaporation properties and daily
crop/root inputs with sources. Root depth and maximum evaporation depth must coincide with
compartment boundaries. Soil/profile quantities are nonnegative decimal strings with at
most six fractional digits; weather uses the existing supplied-ET contract. Missing water
cannot become zero. Surface-water capacity and zero activity below roots must be explicit.
Volumetric soil fractions use m3 water per m3 bulk soil. `maximum_rate_per_day` is a root
sink in m3 water per m3 bulk soil per day, before the local stress reduction; it is not
millimeters per day. JSON results retain the corresponding diagnostic units.

Daily `water.irrigation_at_surface_mm` is the local patch depth arriving at the soil surface
after upstream losses. It is **not** the net water reaching roots used by `field record_day`.
Supplied patch rain replaces any rainfall in the ET weather record, while the original
weather remains retained. Provider weather requires its exact `weather_sources` snapshots.
No fetch occurs during calculation. All coefficients must represent the chosen crop and
conditions; bulk-soil properties and absence of capillary rise, freezing and salinity effects
must be explicit. Soil aeration/cold factors and root activity are supplied, not inferred.

To save through the owned workflow, put `task: "coupled_soil_water"`, the complete example
or your reviewed request under `inputs`, a stable `request_id`, and optionally `cycle_id`
in a JSON file, then run:

```bash
agronaut field --unit UNIT_ID save --input save-coupled.json
```

Farm timezone, unit ownership, crop identity, planting date and cycle closure are checked.
Saving retains the original inputs, daily results, exact initial/ending checkpoints and
sources. It does not adopt a farm water state or record irrigation as completed. In Field
records, **Import and save a calculation** accepts the same input file when **Coupled
soil-water replay** is selected; saved reports display the separate water terms.

Alternatively, open **Calculate coupled soil water** in Field records. Enter the represented
area, initial date, evidence and applicability conditions, then supply patch, layer and
drying-memory rows. Each table's help lists its columns and units. Paste comma-separated
rows without headers, quote sources containing commas, and use a decimal point. Blank
quantities are rejected; enter zero explicitly where supported by evidence. The selected
crop cycle supplies the crop name, but supplies no numerical soil or crop defaults.

Enter daily weather with its source and method, or choose **Import downloaded weather**
and upload the retained weather snapshot. Daily patch water, evaporation coefficients,
root parameters and activity for every layer must be supplied separately. Unknown weather
rainfall uses `?`; patch rainfall still requires an explicit value. Use **Calculate and save
coupled soil water** to save and select the result without preparing a calculation JSON file.

To extend an owned calculation, supply its `calculation_id`, consecutive new `weather`
and `days` arrays, a `reason`, a fresh `request_id`, and any new `weather_sources`:

```bash
agronaut field --unit UNIT_ID continue_coupled_water --input next-coupled-days.json
```

Continuation uses the parent's exact ending checkpoint and saves a child record. It rejects
overlapping or missing days, another unit's record, changed crop-cycle context and days
beyond cycle closure. The full soil geometry and static properties remain fixed. Explicit
root-depth changes use already represented water; they do not initialize new soil to field
capacity. Repeat the same request ID and input for a retry. Existing records remain unchanged.

In Field records, select the saved calculation and open **Continue coupled soil water**.
Review its ending layer water contents, enter the continuation purpose and consecutive new
daily inputs, then choose **Continue and save coupled soil water**. The child becomes the
selected calculation. Initial drafts clear when the unit or crop-cycle context changes;
continuation drafts also clear when the selected parent changes. Correcting a selected
cycle's crop or planting date clears its draft so it can be reviewed again.

JSON outputs include readable six-decimal values plus exact rational audit values. Checkpoints
retain full fractional precision and model versions. Standalone replay can accept
`initial_state: {"checkpoint": "...", "source": "..."}` using a prior ending checkpoint;
its area and timezone must match. Checkpoint validation does not establish observation quality
or field accuracy. Reviewed ending-state revisions are described below; independent field
validation remains unfinished.

### Compare coupled predictions with soil samples

Saved coupled calculations support read-only `review_soil` and `compare_soil` operations.
Record a `soil_water_content` observation in `m3/m3` through the crop journal, then use
`review_soil` to inspect the observation's current quality review. Depletion readings are
not converted to layered water content. Comparisons require a selected patch, a depth
interval and a sampling note explaining how the reading represents that patch footprint
and interval. A point sensor is not automatically representative of either.

For a coupled calculation, a `compare_soil` request looks like:

```json
{
  "calculation_id": "SAVED_CALCULATION_ID",
  "dataset_kind": "synthetic",
  "selection_note": "Synthetic example only; replace with actual selection rationale",
  "comparisons": [{
    "observation_id": "OWNED_OBSERVATION_ID",
    "as_of": "2026-07-06",
    "patch_id": "plot",
    "top_m": "0.05",
    "bottom_m": "0.2",
    "representation_note": "Synthetic patch mean over 0.05–0.2 m depth",
    "water_content_adjustment": 0,
    "adjustment_source": "Explicit synthetic assumption: no change before closing midnight",
    "expected_review": null,
    "independence": "unknown"
  }]
}
```

Run `agronaut field --unit UNIT_ID compare_soil --input comparison.json`. Depths are decimal
strings in meters with up to six fractional digits. The observation must fall within the
selected completed local day or exactly its closing midnight; the signed numeric adjustment
maps it to that midnight in `m3/m3`. Positive adjustment means wetter. Zero must be explicit
and have a source or assumption. `expected_review` is the review ID displayed by `review_soil`,
or null when no review exists. Changed reviews, rejected readings, forecasts, foreign
records, duplicate observation IDs, unsupported dates and invalid intervals are rejected.

Prediction uses the exact saved water contents, averaged by the thickness of each layer
intersecting the declared interval. Partial layers assume uniform modeled water content.
The report retains exact values and rounded displays. It reports prediction minus adjusted
observation: positive error means wetter modeled soil. It does not clip observations to
model dry/saturation bounds; adjusted volumetric content must lie within 0–1.

Declare each reading's prior use as `held_out`, `used_for_initialization`,
`used_for_calibration` or `unknown`. Only declared `held_out` readings enter bias, MAE and
RMSE, unless the saved evidence shows they already conditioned the prediction's starting
state. Others remain visible with an exclusion reason. Metrics use `m3/m3`, with equal weight
per reading; groups separate patch, depth interval, provenance, quality and weather kind.
Counts are not independent sample sizes. Declarations and source text cannot prove that
readings were held out; these reports do not establish field validity or acceptable error.

The web page offers **Compare coupled soil water with measurements** below a saved coupled
calculation. Select observations, enter coverage and timing details, and choose **Compare
coupled predictions without changing state**. Download includes the calculation and reviewed
observation evidence. Changed selections or quality reviews hide a stale report until the
comparison is rerun. Unit/cycle/calculation changes clear comparison drafts. These operations
do not save a new calculation, adopt a soil state, recalibrate parameters or alter water logs.

### Record actual surface water and replay a coupled calculation

`record_surface_day` records completed local-day rainfall, supplied rainfall runoff and
irrigation arriving at the soil surface for **every patch** in a selected owned coupled
calculation. It retains that calculation's represented area and patch fractions as the
declared footprint. Irrigation is the local patch depth after upstream losses, not pumped
volume or the net-root-zone depth used by `record_day`. Do not convert those older logs
without additional evidence. Matching patch IDs and fractions cannot verify physical
locations; the coverage note must explain the represented patches and day.

```json
{
  "calculation_id": "SAVED_COUPLED_CALCULATION_ID",
  "request_id": "surface-day-2026-07-06",
  "data": {
    "day": "2026-07-06",
    "patches": [{
      "id": "plot",
      "rainfall_mm": "5",
      "rainfall_runoff_mm": "1",
      "irrigation_at_surface_mm": "3",
      "source": "Synthetic patch totals; replace with actual evidence"
    }],
    "source": "Synthetic demonstration log",
    "representation_note": "Synthetic whole-day coverage of the represented plot",
    "provenance": "estimated",
    "quality": "unverified"
  }
}
```

Use `agronaut field --unit UNIT_ID record_surface_day --input surface-day.json`.
Amounts are explicit nonnegative decimal strings in local patch mm, with up to six decimal
places and a 0–2000 mm input bound. Supplied rainfall runoff cannot exceed rainfall. It is
distinct from additional overflow calculated by the soil model. Unknown quantities are
rejected rather than assumed zero. Provenance is `measured`, `reported` or `estimated`;
quality is `unverified`, `checked` or `rejected`. These labels remain operator declarations.
No numerical example constitutes regional calibration or an irrigation recommendation.

One record chain exists per unit, crop cycle and day. Corrections supply the latest
`supersedes_id`, the complete corrected data and a nonempty `reason`; earlier records remain
intact. A correction with `quality: "rejected"` retains an unusable record in history. The
same request ID and exact request return the original result even after a later correction;
changed data require a different request ID. Accepted records must lie within crop dates
and constrain subsequent planting/closure corrections. Timezone changes cannot silently
reinterpret an existing day's correction.

`review_surface_water` takes `calculation_id` and returns the declared footprint, record
history, whether each footprint matches, and corrections affecting already applied evidence.
Replay explicitly selected records with:

```json
{
  "calculation_id": "SAVED_COUPLED_CALCULATION_ID",
  "record_ids": ["CURRENT_SURFACE_WATER_RECORD_ID"],
  "request_id": "replay-surface-day-2026-07-06",
  "weather_policy": "observed_only"
}
```

Run `agronaut field --unit UNIT_ID replay_surface_water --input surface-replay.json`.
Replay replaces the selected daily patch supplies and recomputes the full saved window
from its original starting state. It preserves weather and its provider evidence, soil
properties and crop/evaporation/root parameters; review their applicability to the actual
conditions before replaying. A new child stores the exact applied record snapshots under
`surface_water_evidence`. Repeating replay does not add the same application again. Existing
net-root-zone records remain separate and cannot supply this operation.

Only current, non-rejected records with the same unit, cycle, timezone, area and patch
fractions can be applied. Days must be in the replay window. Default `observed_only`
requires observed weather on applied dates; `allow_historical` explicitly permits historical
weather estimates. Neither policy accepts forecast or scenario weather as actual replay
evidence. Historical records and calculations retain their original labels and values.

Continuation carries forward the evidence that conditioned its starting checkpoint. If an
applied record is later corrected or rejected, new continuation/replay cannot silently reuse
it. Replay the affected earlier calculation with the current record, then continue that
revised branch. Applying an earlier correction directly to a later window is rejected; the
later window cannot reconstruct the state before its checkpoint. Saved historical branches
and successful request retries remain readable and unchanged.

In Field records, open **Record and replay actual surface water** under the selected coupled
calculation. Review patch areas, enter complete CSV rows with the column order in help, and
save the daily record. Select the latest record to correct it. Select recorded days and
the weather policy to **Replay actual surface water as a new calculation**. Saving a record
alone does not create a calculation. Drafts clear when the unit, cycle or calculation changes.
Season review and account export retain surface records separately from net-root-zone water;
their depths are not added together. Account erasure removes the records and correction history.
Schema v16 creates the surface-water table through the existing transactional migration and
pre-migration backup. No hardware operation or independently validated field result is implied.

### Use reviewed soil observations for a continuation state

`review_coupled_state`, `apply_coupled_state` and `remove_coupled_state` provide an explicit
state-reconciliation workflow. It applies only at the selected calculation's final local-day
boundary. The daily predictions and their water-flux balances remain unchanged. A child
record stores a separate continuation checkpoint and the signed change in soil storage.
That adjustment is not observed rainfall, irrigation, drainage or evapotranspiration.

Begin with `review_coupled_state` and a `calculation_id`. Its response includes the current
continuation state, candidate soil-water-content observations and current reviews of adopted
evidence. An observation must represent one **complete modeled layer and its patch footprint**.
Depth-interval comparisons can use partial layers, but partial or point samples cannot
initialize an entire compartment without explicit supporting coverage. The selected observation
must fall within the final completed local day or exactly its closing midnight. Supply an
explicit signed timing adjustment in `m3/m3`; positive adjustment means wetter.

```json
{
  "calculation_id": "SAVED_COUPLED_CALCULATION_ID",
  "request_id": "reviewed-upper-layer-state",
  "dataset_kind": "synthetic",
  "reason": "Synthetic state-reconciliation example",
  "readings": [{
    "observation_id": "OWNED_SOIL_OBSERVATION_ID",
    "patch_id": "plot",
    "layer_id": "upper",
    "top_m": "0",
    "bottom_m": "0.1",
    "representation_note": "Synthetic sample representing the complete layer and patch",
    "water_content_adjustment": 0,
    "adjustment_source": "Explicit synthetic no-change assumption until closing midnight",
    "expected_review": null
  }],
  "evaporation_memories": [{
    "id": "plot",
    "retain": true,
    "source": "Explicit synthetic review supporting the current drying history"
  }]
}
```

Run `agronaut field --unit UNIT_ID apply_coupled_state --input reviewed-state.json`.
Depths must match the named layer exactly. `expected_review` is the review ID the operator
inspected, or null when no review exists. Forecast/rejected observations, stale review IDs,
duplicate layer targets, reuse of one observation for multiple current cells and adjusted
contents outside the soil's dry/saturation bounds are rejected; no value is clipped. A
`field` dataset declaration does not independently establish measurement accuracy.

Each affected patch requires explicit drying-memory review. `{id, retain: true, source}`
retains its exact current memory after that review. Alternatively supply
`{id, stage_one_remaining_mm, active_depth_m, stage_two_start_fraction, source}` with the
same decimal-string units and stage rules as initial coupled inputs. Do not infer drying
history from a water-content reading. Replacement memories must cover exactly the affected
patches. Soil geometry/properties, surface-water storage and unselected layers remain unchanged.
Further revisions at the same boundary preserve other adopted cells and replace the evidence
for explicitly selected cells.

The saved child's `coupled_state_revision` contains the prior and revised checkpoints,
per-layer changes, an exact adjustment balance and cumulative soil-storage difference from
the original prediction. `coupled_state_evidence` retains the reviewed observations and
coverage/timing declarations. `continue_coupled_water` uses this revised checkpoint and
carries its evidence forward. The original `result.ending_checkpoint` remains the modeled
prediction, so standalone continuation from an export must deliberately select
`coupled_state_revision.ending_checkpoint` when that revision exists.

If an adopted observation's quality review changes, new continuation must wait for a
reviewed replacement or withdrawal at the original boundary. Supply the current review ID
when reapplying the affected layer. `remove_coupled_state` takes `calculation_id`, `reason`
and `request_id`; it saves a new version restoring the original modeled ending state and
withdraws **all** soil revisions at that boundary. Earlier observations conditioning the
initial checkpoint remain retained. To correct those earlier states, revise the ancestor
and continue the revised branch. Historical branches and request retries remain unchanged.

Surface-water replay cannot silently drop an active ending-state revision. Withdraw that
revision, replay the corrected water inputs, then review and reapply observations against
the recomputed prediction. Replays of later windows retain and check the evidence already
conditioning their starting checkpoint.

Prediction diagnostics continue to use the original prediction before same-day adoption.
If an adopted observation is selected for a later prediction, its known initialization use
excludes it from summary metrics even when someone declares it held out. Undeclared reuse,
source authenticity and sampling independence still cannot be established automatically.

In Field records, open **Review soil measurements for the continuation state**, select the
observations and complete layers, supply coverage/timing evidence and review drying memory.
**Save reviewed soil continuation state** selects the new child. The page identifies the
separate storage adjustment and displays the state that continuation will use. An explicit
withdrawal form restores the modeled ending state in another version. Unit/cycle/calculation
switches clear drafts, and stale form submissions cannot write into a previously selected
cycle. Schema v17 marks these new continuation semantics so older readers refuse the database
instead of silently using an unreconciled state; the upgrade retains the existing backup path.
## Offline field irrigation guidance

The deterministic field advice boundary is also exercised by the existing CI command
`python -m scripts.safety_eval`. Its [field probes](dpg/safety_eval/README.md) check that
unknown inputs, failed designs, unsupported methods and draft translations do not become
amounts, schedules, cutoff advice or stronger evidence claims. These synthetic development
probes run through the shared calculation tool. They do not establish LLM-answer quality
or field validity; those require separate evaluation.

Read the bundled field guides without a model, retrieval index or internet connection:

In web chat, Telegram, WhatsApp or terminal chat, `/guide` lists the seven topics.
Use `/guide fr` or `/guide zh-Hant` for a translated catalog, and follow a topic command
such as `/guide soil-sampling fr` or `/guide sprinkler zh-Hant`. Long guides include
a next-page command, for example `/guide soil-sampling fr 2`. The language defaults
to English. French and Traditional Chinese are drafts pending local review.

Every page includes applicability, source links, the English source fingerprint and
any translation notice. Pages use the currently installed guide bundle; they are not
a saved snapshot. The command preserves the selected unit and any unfinished intake,
does not record an observation, and does not call the chat model or retrieval index.
No farm setup is required to read a guide. Messaging delivery still needs the channel's
connection; the standalone command below also works offline.

```sh
agronaut field-guide
agronaut field-guide --topic water-needs
agronaut field-guide --topic choose-method
agronaut field-guide --topic drip --json
agronaut field-guide --language fr --topic drip
agronaut field-guide --language zh-Hant --topic surface --json
```

The seven topics are `water-needs`, `choose-method`, `drip`, `sprinkler`, `surface`,
`crop-scouting` and `soil-sampling`. All seven have English originals (`en`, the default), French drafts (`fr`) and Traditional
Chinese drafts (`zh-Hant`). Select a language explicitly; country names do not choose
the language or establish regional applicability. Each guide combines an explanation, cited primary sources,
observation preparation and the limits of the relevant Agronaut tools. These are general
guides, not locally validated recommendations, default crop parameters, equipment
selections or irrigation prescriptions. Source links require connectivity to open;
reading the bundled guidance does not.

The soil unit's **Field records → Field guides** panel exposes the same text,
with a language selector, translated topic titles and a UTF-8 Markdown download. Changing
language preserves the topic. Downloads retain the source links and draft review notice.
Chat can read it through the registered `soil.field_guidance` task with
`{"topic":"drip","language":"fr"}`; omitting `language` retains English.
`agronaut capabilities --system soil --json` lists the allowed topics and languages.
Its evidence status is `guidance` and feasibility is unknown. Reading a guide neither
creates a saved calculation nor changes farm records.

The crop-scouting guide adds a preparation workflow for symptoms, plant/field patterns,
recent operations and review of uncertain causes. It connects to the existing crop scouting
journal and distinguishes plant counts from insect/leaf counts. Its extension sources support
general observations, not local pest prevalence, treatment thresholds or confirmed diagnoses.
Read it with `agronaut field-guide --topic crop-scouting --language fr` (or `en`/`zh-Hant`).

The soil-sampling guide connects preparation with the existing laboratory-report records.
It retains sample identity, depth, procedure, source, methods and detection qualifiers;
it does not interpret fertility or prescribe fertilizer. Its cited US laboratory and
extension sources do not establish a sampling protocol for Burkina Faso or Taiwan.
Read it with `agronaut field-guide --topic soil-sampling --language fr` (or `en`/`zh-Hant`).

The English originals also enter the soil/irrigation retrieval corpus. Optional `_Evidence: ..._`,
`_Applicability: ..._` and `_Sources: ..._` single-line headers become document metadata
and survive both supported chunking modes. Retrieved passages carry those fields even
when the selected chunk lacks the document's introduction or references. Existing
guides without these headers retain their previous behavior. The metadata cache version
was advanced so old indexes rebuild with the new context. The field-guide CLI uses an
explicit topic allowlist and the normal `AGRONAUT_CORPUS_DIR` override; missing or
incomplete installed guide files produce an error rather than an aquatic fallback.

Translations are Agronaut drafts, not official translations of the cited publications.
Their language and agricultural terminology still require independent local review. The
original English publication links remain attached and are labeled as English sources.
The result includes `language`, `source_language`, `source_sha256`, `translation_status`
and `translation_note`; translated results also carry the note in their limitations.
No guide claims local field validation or assigns region-specific coefficients.

The bundles in `field_guides/fr.json` and `field_guides/zh-Hant.json` ship with the wheel
and use the same `AGRONAUT_CORPUS_DIR` root as the original guides. Each translation
records a SHA-256 of its English source text. A changed source blocks that translation
until it is checked and updated; other current guides remain readable. Missing or
incomplete translations produce explicit errors, without silently switching to English.
These directly selected translations are outside the English retrieval corpus. This
feature does not establish French or Chinese semantic-search performance or translate
the rest of the app. See `field_guides/README.md` for the maintenance procedure.

Maintainers can evaluate the small field-guide smoke set with the same soil-domain
filters used by chat:

```sh
python -m scripts.retrieval_eval --golden docs/dpg/retrieval_eval/golden_set_field_guidance.json
```

Those English queries were authored with the guides. They check retrieval wiring and
do not establish performance on independent grower questions, French or Mandarin.

The 2026-10-20 remeasurement used 3,959 chunks, including the existing books and all
27 local guides. The full floor/cap/beta sweep selected the existing settings
(1.50 / 1 / 0.90). The legacy 33-query set retained its 0.879 hit rate and 0.636 MRR;
recall changed from 0.833 to 0.818 and MAP from 0.604 to 0.593. No on-topic query was
silenced and 8/10 negative controls were rejected. The five scoped field questions
each ranked their intended guide first; both field negative controls were rejected.
Reports are in `docs/dpg/retrieval_eval/{baseline_field_guides_2026_10,field_guides_2026_10,sweep_field_guides_2026_10}.json`.

After adding crop scouting, a separate offline check used only the 28 local English
Markdown documents (120 chunks). All six field smoke queries ranked their intended guide
first, and both unrelated controls were rejected. See
`docs/dpg/retrieval_eval/local_field_scouting_2026_10.json`. This isolated check excludes
remote books and web sources; it does not replace the full-corpus baseline above or
establish translated semantic-search performance. Retrieval settings were unchanged.

Adding soil sampling brought that isolated check to 29 local documents / 126 chunks.
All seven field queries ranked their intended guide first, and both unrelated controls
were rejected. The report is `docs/dpg/retrieval_eval/local_field_soil_sampling_2026_10.json`;
the same local-only and development-authored-query limitations apply. Settings remain unchanged.

The existing Goddek book now uses its verified publisher PDF URL because the OAPEN
mirror returned HTTP 403. The loader identifies itself as Agronaut and retries a
failed content-type probe before choosing a parser. Repeated transport failure leaves
the source unavailable; it no longer sends an unknown response into the HTML parser.
## Surveyed sprinkler nozzle and riser hydraulics

`soil.sprinkler_lateral` calculates each nozzle's pressure and flow on one surveyed,
constant-diameter lateral closed immediately after its last sprinkler. Each station has
one identical unregulated nozzle and a riser with an explicit height, pipe path length
and branch fitting loss. Riser diameter, roughness and pressure rating are shared across
the lateral. The separate `soil.sprinkler_design` task retains its flat-field design
envelope; this new task resolves individual hydraulic operating points.

In **Field records → Evaluate a surveyed sprinkler lateral**, enter the nozzle curve,
lateral/riser pipes, inlet, limits and sources. Numeric fields start blank and applicability
checks start unconfirmed. Supply every station in order using this CSV header:

```csv
identifier,distance_m,elevation_m,upstream_minor_k,riser_height_m,riser_length_m,riser_minor_k,source
```

Distances are cumulative pipe lengths; base elevations are relative to the inlet. Include
all crests and sags in the piecewise-linear profile. Riser height is relative to that
station's base, and its path must climb monotonically with length at least its height.
Through-fitting K is assigned at the segment's downstream station and uses the incoming
lateral-segment velocity; branch K uses that riser's
velocity. Supply these separately without counting a fitting twice. Nozzle discharge
already represents the outlet relation; do not add a nozzle exit loss to riser K.

Choose fixed inlet pressure (schema v1) or a bounded, nonincreasing pressure–flow curve
(schema v2). Curve points use `flow_lph,pressure_head_m`; they describe pressure at this
lateral's inlet after upstream equipment, under one stated configuration. A raw pump
head-gain curve is not this boundary. The curve uses total flow through every modeled
nozzle. Static inlet head and available-flow limits remain explicit. Switching supply
mode clears its prior inlet evidence and confirmation; switching unit/cycle/timezone
clears the unsaved form.

The [EPA EPANET network model](https://usepa.github.io/EPANET2.2/3_network_model.html)
supports the square-root nozzle relation, Darcy–Weisbach losses and fitting-loss equations.
The friction-factor regimes follow its
[analysis algorithms](https://usepa.github.io/EPANET2.2/12_analysis_algorithms.html).
Agronaut evaluates `q = q_ref * sqrt(h_nozzle / h_ref)` only within the supplied nozzle
pressure band. It inverts each nozzle/riser branch and uses bounded reverse shooting to
match the inlet boundary. Neither nozzle nor inlet curves are extrapolated. This is a
nodal hydraulic-head model without kinetic-head recovery at takeoffs.

Reports include base/nozzle heads, individual flows, lateral/riser losses and velocities,
continuity/head residuals and supplied-limit failures. Static profiles assume connected,
water-filled pipes held at maximum static inlet pressure with outlets shut; they check
lateral, riser and nozzle ratings at their respective elevations. A supported prediction
can fail declared limits; an unsupported numerical operating point has `prediction=null`
and `feasible=null`, while static checks remain visible.

```sh
agronaut evaluate-sprinkler-lateral --input examples/sprinkler_lateral.json
agronaut evaluate-sprinkler-lateral --input examples/sprinkler_lateral_curve.json --json
```

Both examples are synthetic. The CLI works with the standard library; capability discovery,
the shared calculation tool, owned saved Field records and downloadable reports use the
same validator. Saved versions retain their full input/evidence snapshot and never write
completed water records. No database migration is needed.

Independent single-nozzle laminar quadratic references and a constructed three-nozzle
network check the numerical solver. These are software evidence, not field validation.
Nominal flow variation is not catch-can distribution uniformity. This task does not predict
wind drift, wetted patterns, application rates, crop demand, irrigation runtime, pump
selection, upstream manifolds, regulators, moving systems, leakage, surge or nozzle wear.
Observed distribution assessment remains available through `soil.sprinkler_catch_test`.

### Compare sprinkler predictions with pressure and flow readings

Use **Crop records → Record a field measurement** to record timestamped pressure in kPa
or flow in L/h, with a source and sampling location. Assign the same crop cycle used by
the sprinkler scenario (or leave both unassigned). Review the reading's quality through
the existing measurement-review controls. Original values and all review revisions remain
available. A reading marked checked reflects an operator review, not independent validation.

Select a solved sprinkler scenario in **Field records**, then open **Compare sprinkler
predictions with pressure and flow readings**. Define each completed event's start/end
timestamps with offsets, steady-operation declaration, matching equipment/source evidence,
and use in model development. Events cannot overlap. Select each reading once and map it
to the inlet, a riser base, a nozzle, or explicitly unmatched. Inlet and unmatched locations
use no station ID; base/nozzle mappings require an exact modeled station. Base mappings
accept pressure only. A target/quantity pair can be mapped once per event.

Pressure comparison needs an explicit gauge-pressure reference at the mapped location and
water density with its source. Leave density null and document why it is unknown when
unavailable. Flow comparison needs neither density nor a pressure reference. Unknown
reference/density withholds pressure error without removing the reading or a known flow.
Catch-can depths cannot be substituted for nozzle discharge readings.

The report shows predicted minus recorded values, original readings and current quality
reviews, exclusions, and all model targets without mapped readings. Only mapped measured
or reported readings marked checked from events declared held out enter metrics.
Unverified readings can show point errors but remain excluded from aggregate scores;
rejected, estimated or forecast readings retain their original values without usable
errors. Calibration-use and unknown-independence events remain visible and excluded.

Bias, MAE and RMSE are separate for nozzle flow, nozzle pressure, base pressure, inlet flow
and inlet pressure. Each event with eligible pairs has equal aggregate weight within its
category; more readings in one event do not give it more weight. Counts do not establish
statistical independence. No acceptance threshold, calibration, field-validation claim,
irrigation schedule or equipment action is generated. A scenario that fails supplied
design limits remains identifiable as such in the report.

The form submits the quality-review revisions it displayed. Changed reviews require a
refresh; existing reports are hidden when their selection, declarations or current reviews
change. Downloaded reports remain historical snapshots. Comparison uses the current farm
timezone, planting date and crop closure; it never edits saved predictions, measurements
or completed water records.

The offline CLI and scoped `field_records` tool expose the same operations:

```sh
agronaut field describe_sprinkler_comparison
agronaut field review_sprinkler_measurements --input sprinkler-review.json
agronaut field compare_sprinkler_lateral --input sprinkler-comparison.json
```

The review input is `{ "calculation_id": "SAVED-ID" }`. The describe operation returns the
event/mapping contract; the review operation returns owned pressure/flow records, their
current `quality_review.id` values and modeled targets. Each mapping must supply that ID
as `expected_review`, or null when no review exists. Full input and evidence snapshots
travel with the diagnostic report. No database migration is required.

## Surveyed drip lateral pressure and flow

`soil.drip_lateral` calculates a nominal hydraulic scenario for one constant-diameter,
closed-tail surface-drip pipe with explicit emitter positions and elevations. It supports
identical emitters with a supplied pressure-dependent curve, including non-compensating
emitters. The separate `soil.drip_design` task retains its flat-field PC design envelope.

```sh
agronaut evaluate-drip-lateral --input examples/drip_lateral.json
agronaut evaluate-drip-lateral --input examples/drip_lateral.json --json
agronaut calculate --system soil --task drip_lateral --input examples/drip_lateral.json
```

In **Field records → Evaluate a surveyed drip lateral**, enter the equipment, inlet,
curve and limit evidence, then the surveyed stations. Numbers start blank and applicability
confirmations unchecked. The form accepts CSV with this exact header:

```csv
identifier,distance_m,elevation_m,upstream_minor_k,source
```

Supply one complete row for every emitter in increasing distance order; quote sources
containing commas. Distances are cumulative pipe lengths, not horizontal map distances.
Elevations are relative to the inlet; negative values are lower. The profile must be
linear between stations without omitted crests or sags. Station identifiers must be
unique; `inlet` is reserved. There is one emitter per station, with the closed pipe end
immediately after the last. A change of unit or crop cycle clears the draft evidence.

JSON inputs use `schema_version: 1`, `site: {name, timezone}`, plus these required records:

| Record | Fields |
|---|---|
| `pipe` | `internal_diameter_mm`, `roughness_mm`, `max_head_m`, `source` |
| `emitter` | `reference_flow_lph`, `reference_head_m`, `exponent`, `min_head_m`, `max_head_m`, `max_static_head_m`, `source` |
| `inlet` | `running_head_m`, `max_static_head_m`, `max_flow_lph`, `source` |
| `limits` | `viscosity_m2_s`, `max_velocity_m_s`, `max_flow_variation_fraction`, `source` |
| `conditions` | Explicit true values for `closed_tail`, `surface_emitters`, `steady_inlet_head`, `surveyed_profile`, `identical_emitters`, plus `source` |
| `stations` | 1–2000 records with `identifier`, `distance_m`, `elevation_m`, `upstream_minor_k`, `source` |

The pressure-discharge relation is
`q = reference_flow_lph * (head_m / reference_head_m)^exponent`, a normalized form of
the [FAO emitter power relation](https://www.fao.org/4/w4367e/w4367e0c.htm). The exponent
is supplied within 0–1; zero represents ideal constant flow only over the declared band.
No exponent, pressure band or equipment rating is inferred from an emitter name.

The model uses nodal hydraulic head (pressure head plus elevation), continuity, and the
existing Darcy-Weisbach loss implementation based on [EPA EPANET 2.2](https://usepa.github.io/EPANET2.2/3_network_model.html)
and its [pipe-resistance equations](https://usepa.github.io/EPANET2.2/12_analysis_algorithms.html).
It uses a series-pipe bisection solver, not EPANET's general network solver. Starting at
the final emitter, it accumulates discharges and losses upstream, then matches the
supplied inlet head. Every trial evaluates emitter flows only within the supported curve
band. Fitting K uses the incoming segment velocity; kinetic-head recovery at outlets is
not included. Inlet head is the running pressure **after** upstream filters and manifolds,
maintained at the calculated flow; a pump shutoff head is not a substitute.

Results retain each station's pressure, discharge, incoming segment flow and losses,
head residuals, total-flow residual, and the maximum-static-head profile. Pressure
extremes are checked throughout the surveyed profile, not assumed to lie at its ends.
Static head includes elevation and assumes continued hydraulic connection to the inlet.
Pipe/emitter ratings, available flow, velocity and the supplied flow-variation limit are
checked. Flow variation is `(maximum - minimum) / maximum`; the minimum/mean ratio is
also reported. Neither is measured field uniformity or a manufacturing-tolerance bound.

An unsupported solve returns `prediction: null` and `feasible: null`, with constraints.
A converged scenario that fails supplied ratings or capacity limits retains its nominal
prediction and returns `feasible: false`. These are conditional hydraulic calculations,
not operating recommendations. Saved records, exports, the agent adapter and JSON import
retain the input evidence; saving never records completed irrigation or creates a schedule.

The example is synthetic. This model does not simulate upstream network interaction,
pump curves, branched or looped pipes, air entry, surge, clogging, leakage, emitter
manufacturing variability, subsurface backpressure, soil wetting or crop-water delivery.
It does not choose pipe sizes or irrigation duration. Field measurements and specialist
review remain necessary to assess the model's applicability at a particular site.

### Matching a lateral to its inlet pressure–flow curve

Select **Pressure–flow curve** in the lateral form, or use `schema_version: 2` with
`examples/drip_lateral_curve.json`. The same CLI, calculation tool and saved-record workflow
accept both versions. Version 1 retains its fixed-pressure contract. Version 2 replaces
the inlet record with `curve`, `max_static_head_m`, `max_flow_lph`, `head_reference`,
`configuration` and `source`; other input records remain the same.

```sh
agronaut evaluate-drip-lateral --input examples/drip_lateral_curve.json --json
```

Each curve point contains `flow_lph` and `pressure_head_m`. Supply 2–100 points with
strictly increasing flows and nonincreasing pressure; flat segments are allowed.
The form accepts the CSV header `flow_lph,pressure_head_m`. Pressure must be positive
within the supported 0.001–1000 m software envelope. No point, shutoff pressure, source
setting or static rating is inferred. `max_static_head_m` is independently supplied and
must be at least the largest tabulated running head. A source flow limit remains a
separate constraint, even when an operating point can be computed.

`head_reference` must be `lateral_inlet_pressure`: the curve represents pressure at this
lateral's inlet, after upstream filters, pipes, elevation changes and valves, versus the
flow entering this lateral. A manufacturer's pump **head-gain** curve cannot be substituted
directly. Document the pump/control settings, source water level, upstream equipment and
concurrent loads in `configuration`, with the measurements or derivation in `source`.
Changing that configuration requires new evidence. `conditions.steady_inlet_head` confirms
a steady source for that configuration in version 2. The web form explicitly requires
confirmation of the pressure reference, and clears inlet evidence when switching modes.

The solver matches the lateral's required hydraulic head to linearly interpolated inlet
pressure at the computed total flow. It uses piecewise-linear interpolation for every
supplied curve, including three-point curves; it does not use EPANET's special three-point
power fit or infer extra endpoints. The [EPANET network manual](https://usepa.github.io/EPANET2.2/3_network_model.html)
documents pressure-dependent emitters, hydraulic-head continuity and piecewise-linear
multi-point curves. Agronaut's inlet-boundary representation and bounded series solver
are a declared implementation choice, not a complete EPANET pump/network simulation.

Both emitter pressure and inlet flow must remain within their respective supplied curve
ranges. The result includes `prediction.inlet_pressure_head_m`, total flow, every station's
pressure/discharge and head/mass residuals. A missing supported intersection returns no
prediction and no feasibility claim; the solver never extrapolates either curve. It does
not calculate pump suction/NPSH, energy efficiency, variable-speed control, transient
stability, other changing loads or an irrigation schedule. Independent field review is
still required. The example and reference tests are synthetic scenarios.

### Comparing predicted emitters with recorded tests

`field compare_drip_lateral` compares an owned saved lateral scenario (either input version)
with saved `drip_emitter_test` evaluations in the same unit and crop cycle. In **Field records**,
select the lateral scenario and open **Compare drip predictions with emitter measurements**.
The operation is also available through the shared `field_records` agent tool. It reads
immutable snapshots and creates no observation, calibration, water log or equipment action.

Supply a request shaped as follows, replacing the saved IDs and including one mapping for
**every** sample in the selected lateral:

```json
{
  "calculation_id": "saved-lateral-scenario-id",
  "dataset_kind": "synthetic",
  "selection_note": "Explain how these tests were selected and any exclusions",
  "comparisons": [{
    "test_calculation_id": "saved-emitter-test-id",
    "lateral": "L1",
    "mappings": [
      {"sample_id": "sample-1", "station_id": "E1", "mapping_note": "Evidence of the same physical emitter"},
      {"sample_id": "sample-2", "station_id": null, "mapping_note": "This emitter is outside the surveyed model"}
    ],
    "independence": "unknown",
    "representation_note": "Evidence that geometry, pipe and emitter specifications match",
    "conditions_note": "Evidence that inlet/source settings and steady conditions match",
    "pressure_reference": "unknown",
    "water_density_kg_m3": null,
    "density_source": "Density unavailable"
  }]
}
```

```sh
agronaut field --db farm.sqlite3 compare_drip_lateral --input comparison.json
```

The native form uses mapping CSV with header `sample_id,station_id,mapping_note`. A blank
station ID is an explicit unmatched sample; it requires a note and remains in the report.
Sample/station IDs match exactly, and a modeled station may be assigned at most once in a
test. Lateral names match after trimming outer whitespace and ignoring case. Samples from
other laterals and model stations without measurements remain listed. Partial mapping lists,
duplicate samples/stations, cross-unit/cycle references and duplicate event starts are
rejected. Current farm timezone, planting date and cycle closure are checked again.
Up to 100 tests and 20,000 total samples can be compared in one report.

Flow comparisons use the saved nominal emitter discharge and each recorded timed-volume
flow, in L/h. Pressure comparisons convert predicted pressure head to kPa using
`density_kg_m3 × 9.80665 × head_m / 1000`. Water density is explicitly supplied for each test
(500–2000 kg/m³ software envelope) with its source; no density or gauge reading is inferred.
Pressure readings must also declare `pressure_reference: gauge_at_emitter` (relative to
atmosphere), with instrument/reference evidence in the conditions note. Use `unknown`
when that reference cannot be established; absolute pressure is not silently treated as
gauge pressure. Unknown reference, density or readings leave the corresponding error unknown. Zero readings remain
zero, and observations outside the model's pressure band are not clipped. A supplied pressure
reading does not replace predicted head or refit the emitter curve.

Errors are **predicted minus recorded**. Per-test summaries show known-pair bias, MAE, RMSE
and maximum absolute error separately for flow and pressure. Aggregate summaries include
only tests explicitly declared `held_out`; `used_for_calibration` and `unknown` tests remain
visible with their individual diagnostics. Each test with eligible pairs has equal weight;
within a test, its known pairs have equal weight. Thus aggregate RMSE is the square root of
the mean of per-test mean squared errors, not a pooled average that favors densely sampled
tests. Counts distinguish events and paired samples. Empty groups have unknown metrics.

`dataset_kind: field` requires tests labeled `observed`, but those labels, station mappings,
matching conditions and held-out status remain declarations. The report states
`independence_status: not_established` and `acceptance_status: not_assessed`. Missing uniformity
statistics or failed scenario limits do not remove known readings from comparison. Neither
field validation, measurement-quality acceptance nor a pass/fail threshold is inferred.
The comparison does not validate inlet pressure or total inlet flow and does not extrapolate
sampled discharges into a zone total. Downloadable JSON retains the complete prediction,
test snapshots, mapping notes, exclusions and assumptions for specialist review.

## Simultaneous drip laterals at one junction

`soil.drip_zone` solves 2–20 surveyed laterals operating simultaneously from one explicitly
common hydraulic junction, with at most 2000 emitters total. It finds the pressure and total
flow that satisfy every branch and a supplied junction pressure–flow curve. Independent
single-lateral operating points are not added: opening another branch can change source
pressure and therefore every noncompensating emitter's discharge.

```sh
agronaut evaluate-drip-zone --input examples/drip_zone.json
agronaut evaluate-drip-zone --input examples/drip_zone.json --json
agronaut calculate --system soil --task drip_zone --input examples/drip_zone.json
```

The input has `schema_version: 1`, `site: {name, timezone}`, `supply`, `junction` and `laterals`.
`supply` uses the same bounded piecewise-linear curve record as lateral input version 2:
`curve`, `max_static_head_m`, `max_flow_lph`, `head_reference`, `configuration` and `source`.
Here the flow coordinate is **total flow entering all represented laterals** and the
pressure reference is their common junction. Keep `head_reference: lateral_inlet_pressure`;
raw pump head gain is not this boundary condition. Static pressure and total capacity are
independent supplied limits, not inferred curve endpoints.

The `junction` record requires explicit true values for `common_hydraulic_junction`,
`all_active_laterals_included`, `no_unmodeled_connection_losses` and
`steady_source_configuration`, plus `source`. Each lateral requires a unique `identifier`,
its own `max_flow_lph` and `source`, followed by the existing `pipe`, `emitter`, `limits`,
`conditions` and `stations` records. Every survey starts at this common junction and uses
its elevation as zero. Connection length, elevation and fitting losses belong in each
branch survey; different diameters along a branch remain unsupported. Pipe/emitter types
may differ between branches, but each branch has one diameter and one emitter specification.

The shared-junction approximation must be justified. It is not valid to treat a long,
lossy manifold's separate takeoffs as equal-pressure connections without representing
those losses. Distributed manifolds, arbitrary branching, loops and unmodeled feeder
losses are not implemented by this task. The source curve must describe the declared
upstream configuration with this zone active; changing settings or other loads needs new
evidence. No topology is inferred from matching site names or selecting saved calculations.

An outer bounded solve enforces `junction_head = supply_head(sum(branch_flows))`; each
inner series solve retains emitter pressure–discharge laws and nodal head losses. This
implements the [EPA head and mass conservation relations](https://usepa.github.io/EPANET2.2/12_analysis_algorithms.html)
using nested bisections for this topology, not EPANET's general gradient algorithm.
No branch is omitted to make the zone appear feasible. Both source-flow and individual
emitter-pressure envelopes are enforced. A missing common numerical solution returns
`prediction: null`, `feasible: null` and an explicit constraint, while retaining static
profiles. A solved point that violates total capacity or branch ratings retains the
conditional prediction with `feasible: false` and identified constraints.

Results contain the common pressure, total source flow, source-head residual, junction
and emitter mass residuals, and every branch's full station results and limits. Cross-branch
flow uniformity is not calculated when emitter specifications differ. The numerical source
head tolerance is `1e-8 + maximum_tabulated_source_head * 1e-9` metres; each series solve
retains its tighter own head tolerance. These are implementation tolerances, not field
acceptance criteria, measurement uncertainty or independent model validation.

In **Field records → Solve simultaneous drip laterals at one junction**, select saved
lateral geometry snapshots from the same unit and crop cycle. Review each survey and its
emitter/pipe limits, name the physical lateral, provide its permitted flow and evidence,
then supply a new common-junction source curve and confirmations. Original single-lateral
inlet settings/results are not reused. Changing the selected branch set clears the shared
source evidence and confirmations. The saved zone retains full geometry snapshots and
their source IDs; originals remain unchanged. JSON import supports a complete zone input
without creating individual lateral scenarios first.

The same task works through the shared calculation tool and owned saved-record workflow.
Saving creates no completed irrigation, water allocation or equipment action. The example
is synthetic, and no irrigation runtime, crop-water schedule, pump selection, efficiency,
NPSH, surge analysis or bill of materials is produced. The earlier emitter-comparison
operation accepts individual lateral scenarios. The network comparison below supports
saved zones and manifolds; independent field validation remains a separate requirement.

## Surveyed drip distribution manifold

`soil.drip_manifold` connects 1–20 surveyed laterals at ordered takeoffs on one full,
constant-diameter manifold, closed immediately after the last takeoff. It solves distinct
takeoff pressures and total source flow; at most 2000 emitters are supported across all
laterals. The common-junction task above retains its original scope and input contract.

```sh
agronaut evaluate-drip-manifold --input examples/drip_manifold.json
agronaut evaluate-drip-manifold --input examples/drip_manifold.json --json
agronaut calculate --system soil --task drip_manifold --input examples/drip_manifold.json
```

The version-1 input contains `site`, `supply`, `pipe`, `limits`, `conditions` and `takeoffs`.
The example is synthetic. `supply` requires a bounded pressure–flow curve, maximum static
head, maximum total flow, configuration and evidence. Its pressure reference must be
`manifold_inlet_pressure`: pressure after upstream equipment at the manifold inlet,
versus **total flow through the whole manifold**. A pump's head-gain curve or a curve
measured at an individual lateral is not interchangeable with this boundary.

`pipe` supplies internal diameter, absolute roughness, pressure rating and source.
`limits` supplies water kinematic viscosity, maximum manifold velocity and source.
`conditions` requires true confirmations for `closed_tail`, `surveyed_profile`,
`all_active_laterals_included`, `steady_source_configuration`, and
`no_unmodeled_connections`, plus supporting evidence.

Each takeoff supplies `distance_m`, `elevation_m`, `upstream_minor_k`, `source` and a
complete `lateral` record in the same format as the common-junction task. Distances are
increasing cumulative manifold pipe lengths; elevations are relative to the manifold
inlet. Every lateral's station distances and elevations instead start at **its own
takeoff**. Include all crests and sags; interpolated segments cannot contain an omitted
extremum. Through-fitting K uses the incoming manifold segment velocity. Branch entry
losses belong in that lateral's first segment, without counting the same loss twice.

Reverse shooting solves each branch at its takeoff pressure, accumulates downstream
flow and manifold friction/elevation effects, then matches required inlet pressure to
the source curve at total flow. This uses the
[EPA head-loss and continuity equations](https://usepa.github.io/EPANET2.2/12_analysis_algorithms.html)
for the declared topology; it is not EPANET's general network solver. Results retain
per-takeoff pressures and flows, segment losses, full branch results, head/mass residuals
and static profiles for the manifold and all laterals. A downhill connection can have
higher pressure than an upstream connection even though total hydraulic head decreases.

A failed rating, flow or velocity limit keeps the conditional numerical solution with
`feasible: false`. If a complete operating point cannot be found within every supplied
curve and the software envelope, `prediction` and `feasible` are null, with an explicit
constraint and retained static checks. No lateral is silently omitted or source/curve
capacity invented. Software limits include takeoff operating pressure of 0.001–1000 m
head and takeoff maximum-static head at most 10000 m; these are numerical bounds, not
recommended operating conditions.

In **Field records → Solve simultaneous drip laterals**, choose **Surveyed manifold**.
Select saved lateral geometry from the same unit and crop cycle; review each local survey
and equipment limits. Supply physical IDs, branch flow limits, manifold pipe/fluid data,
takeoff survey and source evidence. Switching topology clears the shared source,
manifold survey entries and confirmations. Changing the active branch set requires
renewed source evidence and confirmations. The saved scenario retains complete geometry
snapshots; it creates no actual-water record, allocation or equipment command. Complete
JSON input also works without previously saved individual laterals.

This remains an experimental nominal hydraulic scenario. It does not establish field
validity, emitter manufacturing/clogging variation, cross-lateral uniformity, soil wetting
or crop-water delivery. Loops, multiple sources, varying pipe diameters, omitted outlets,
transient pressure, air entry, pump selection and NPSH/efficiency are unsupported.
No irrigation schedule is produced. The emitter diagnostics below support saved manifold
scenarios, but do not validate the source curve or whole-manifold flow balance.

### Compare zone or manifold emitters with recorded tests

`field compare_drip_network` compares a solved saved `drip_zone` or `drip_manifold` scenario
with complete saved emitter-test evaluations in the same unit and crop cycle. In **Field
records**, select the network scenario and open **Compare drip predictions with emitter
measurements**. This is also available through the shared `field_records` tool and offline CLI:

```sh
agronaut field --db farm.sqlite3 compare_drip_network --input comparison.json
```

Use the single-lateral comparison's request fields and declarations, with two differences:
omit the comparison entry's `lateral` field, and include `lateral_id` in every sample
mapping. Include **every sample in each test**, across all recorded laterals. For example,
the mappings below refer to distinct modeled emitters even though both use station `E1`:

```json
{
  "calculation_id": "saved-manifold-or-zone-id",
  "dataset_kind": "synthetic",
  "selection_note": "Explain selection and exclusions; include every sample mapping",
  "comparisons": [{
    "test_calculation_id": "saved-emitter-test-id",
    "mappings": [
      {"sample_id": "sample-1", "lateral_id": "L1", "station_id": "E1", "mapping_note": "Same physical emitter on first branch"},
      {"sample_id": "sample-2", "lateral_id": "L2", "station_id": "E1", "mapping_note": "Same physical emitter on second branch"},
      {"sample_id": "sample-3", "lateral_id": null, "station_id": null, "mapping_note": "Recorded emitter outside modeled layout"}
    ],
    "independence": "unknown",
    "representation_note": "Evidence for physical branch identities, local surveys and equipment",
    "conditions_note": "Evidence for the complete active branch set, shared source settings and steady operation",
    "pressure_reference": "unknown",
    "water_density_kg_m3": null,
    "density_source": "Density unavailable"
  }]
}
```

This illustrates the request shape; add mappings for all samples in the selected test.
The native form accepts CSV headed `sample_id,lateral_id,station_id,mapping_note`. Leave
both lateral and station IDs blank for an explicitly unmatched sample, with a reason.
A mapping with only one null ID is rejected. Modeled IDs match exactly. Recorded lateral
names are grouped after trimming whitespace and ignoring case, and their declared
association with modeled laterals must be one-to-one within each test. No association is
guessed from names, sample order or pressure values. A modeled lateral/station pair may
be mapped at most once per test; separate laterals may reuse station IDs.

The report retains every recorded sample and the saved scenario's full source, branch and
survey evidence. It uses each emitter's saved prediction at that branch's calculated
pressure. It does not recalculate the network from recorded pressure or sum sampled flows
into a source-flow estimate. Unmatched samples, stations without mappings, missing readings,
failed scenario limits and measurements outside modeled curve bands stay visible.

Overall metrics weight paired emitters equally within each eligible test and then weight
tests equally. Per-lateral metrics apply the same rule separately to each branch, exposing
branches with no eligible readings. Consequently, the overall result is not an equal-weight
average of lateral scores. Calibration and unknown-independence tests retain individual
metrics but do not enter aggregates. Gauge-reference and density requirements, units,
current timezone/cycle checks, duplicate-event checks and the 100-test/20,000-sample limits
are the same as the single-lateral comparison above.

The operation is read-only and retains `independence_status: not_established` and
`acceptance_status: not_assessed`, including for records labeled observed field data.
Physical associations and matching operating conditions remain declarations; complete
mapping does not eliminate sampling or selection bias. No acceptance threshold, independent
field validation, source/inlet-flow validation, takeoff-pressure validation, automatic
calibration, irrigation schedule or regional applicability is inferred. The downloaded JSON
includes inputs, predictions, test snapshots, mappings, per-event/per-lateral diagnostics
and exclusions for independent review.

## Sloping surface advance scenarios

`soil.surface_advance` estimates arrival at the half-length and end of one uniformly
sloping furrow or wide rectangular border. It is an experimental implementation of the
two-point volume-balance approach in [FAO-45 (1989), §§4.3.4 and 5.3.1](https://www.fao.org/4/T0231e/t0231e07.htm).
It does not predict recession, tailwater or cutoff timing. The separate
`soil.surface_cutoff` capability remains unavailable.

```sh
agronaut predict-surface-advance --input examples/surface_advance.json
agronaut predict-surface-advance --input examples/surface_advance.json --json
agronaut capabilities --system soil --json
```

The bundled example reproduces the manual's approximately 65-minute advance case;
it is not a farm recommendation. Its represented spacing, acceptance limits and intake
time envelope are explicitly labeled scenario assumptions.

In the app, select a soil unit and open **Field records → Predict surface irrigation
advance**. All measured/estimated numbers start blank and applicability confirmations
start unchecked. The form saves a scenario, including withheld predictions and their
constraints. It does not write a completed water record. The same input is accepted
through the calculation import panel, registered chat task and owned `field_records`
save operation. Unit/cycle changes clear form evidence. Saved results use the existing
export and erasure mechanisms.

The strict request includes `schema_version: 1`, `site: {name, timezone}`, and these
records, each with a nonempty `source`:

| Record | Required evidence |
|---|---|
| `geometry` | `method` (`furrow` or `border`), `length_m`, `width_m`, positive `slope_m_m`. Width is furrow spacing or whole border width. |
| `hydraulics` | `inflow_lps` for the whole represented strip, SI Manning `manning_n`, SI hydraulic-section `section_p1` and `section_p2`, and `surface_shape_factor`. Border geometry uses exactly p1=1 and p2=5/3. |
| `infiltration` | `k`, `exponent`, `steady_rate`, `basis` (`depth_mm` or `volume_per_length_m2`), `min_opportunity_min: 0`, and positive `max_opportunity_min`. |
| `limits` | `max_advance_min` (at most 1440), `max_inlet_velocity_m_s`, and `max_inlet_flow_area_m2` for the whole inlet cross-section. These must be justified for the site. |
| `conditions` | Explicit true values for `uniform_sloping_field`, `normal_inlet_flow`, `initially_unponded`, `no_rain_or_runon`, `constant_inflow`, and `no_downstream_backwater`. |

Intake is `k * opportunity_minutes^exponent + steady_rate * opportunity_minutes`.
Depth-basis coefficients use mm/min^a and mm/min. Volume-per-length coefficients use
m²/min^a and m²/min and already represent the entire strip width; they are not multiplied
by width again. The surface factor multiplies inlet flow area and wetted length to
approximate surface storage. It is supplied rather than chosen from a soil name.

The calculation converts inflow to m³/min and derives inlet area from the SI normal-flow
relation. For borders, the hydraulic relation uses flow per metre width, then restores
whole-border area and volume. Output includes border depth and depth/width for reviewing
the wide-flow assumption. The supported inlet-area limit must also preserve that
approximation; the model does not independently establish a suitable width/depth range.

The implementation uses bracketed time and exponent solves, avoiding a singular intake
derivative at zero. It never solves beyond the smaller of the requested time limit and
the intake envelope. Inlet area/velocity violations, insufficient flow relative to
constant intake, and an unbracketed solution withhold the prediction. Such a result is
not a proof of general real-world infeasibility. Unknowns are not filled with defaults.

Each solved anchor reports supplied, surface-stored and infiltrated litres, plus the
numerical balance residual. The fitted power trajectory does not guarantee water
conservation at intermediate distances. Tight numerical residuals do not remove physical
approximation errors: [Bautista et al. (2012)](https://www.ars.usda.gov/ARSUserFiles/20200000/Bautista_et%20al.%20JIDE727%202012.pdf)
demonstrate potential bias from power-advance and infiltration-shape assumptions.
Independent advance and surface-storage observations are required to assess local
applicability. No field validation, operational cutoff recommendation, automatic inflow
selection, level-basin hydraulics or unsteady hydraulic solution is claimed.

### Compare a saved advance scenario with recorded events

In **Field records**, select a saved advance scenario and open **Compare advance
predictions with surface events**. Select saved event evaluations for the same unit
and crop cycle, declare whether the dataset is synthetic or field data, explain the
selection, and record each event's representation evidence and calibration use.
The comparison is read-only. Its JSON download includes the complete prediction and
reference snapshots, declarations, paired errors, exclusions and limitations.

The same operation is available through the `field_records` agent tool and offline CLI:

```sh
agronaut field --db farm.sqlite3 compare_surface_advance --input comparison.json
```

```json
{
  "calculation_id": "saved-advance-id",
  "dataset_kind": "synthetic",
  "selection_note": "Software exercise using synthetic events, not a field trial",
  "comparisons": [
    {
      "event_calculation_id": "saved-event-id",
      "independence": "held_out",
      "representation_note": "Synthetic event uses the same strip and advance conditions"
    }
  ]
}
```

Select 1–100 distinct events. Geometry and interval-mean inflow through observed
full-length arrival must match the scenario exactly. Arrival must occur before or at
cutoff, and the event must start without ponded surface water. Flow changes after observed
full-length arrival are allowed; a slower prediction
is not discarded merely because its arrival falls after the actual cutoff. Events must
share the saved scenario's exact crop-cycle scope and current farm timezone, and fit
current planting/closure dates. Multiple evaluations with the same UTC event start
cannot count as separate events. Selected flow/station records are limited to 20,000.
`field` datasets require completed events recorded as `observed`; that label alone
does not verify measurement authenticity or quality.

Errors are predicted minus recorded minutes: positive means predicted arrival is later.
Only exact half-length and full-length stations are paired. Missing half-length readings
remain missing; intermediate stations are not interpolated or scored. Bias, mean absolute
error, root mean squared error and maximum absolute error are computed separately for
each anchor, with equal weight per eligible event. Events declared `used_for_calibration`
or `unknown` remain visible but are excluded from aggregate metrics. Empty metrics are
`null`, not zero. Post-event modeled water-balance failures do not exclude arrival
observations, since that would select measurements by model fit.

These are diagnostic statistics, not independent field validation. Calibration-use and
representation declarations are not independently verified; interval means cannot prove
constant instantaneous inflow. Correlation between events, measurement uncertainty and
selection bias remain unquantified. No acceptance threshold, regional applicability,
recession/runoff validation or operational cutoff advice is inferred. The report changes
no model coefficients, saved calculations, actual-water logs or capability evidence labels.

## When a hydraulic prediction was saved

Drip lateral/network, sprinkler lateral and surface-advance comparison reports use schema
version 2. Each selected event includes `prediction_timing`: the saved calculation timestamp
and event boundaries normalized to UTC, with one of these statuses:

| Status | Meaning |
|---|---|
| `saved_before_event` | The application saved the calculation strictly before event start |
| `saved_during_event` | The save time is at/after start and strictly before end |
| `saved_at_or_after_event_end` | The save time is at or after event end |
| `unknown` | The save timestamp or event window is missing, invalid or lacks an offset |

`timing_counts` counts all selected events, including those excluded from numerical metrics.
The text report shows the same counts. Older reports without timing evidence render an
explicit “not recorded” message. Surface-event end means the recorded monitoring end;
drip and sprinkler use their supplied event end timestamps.

These labels describe application save chronology. An imported prediction might have been
made earlier; a calculation saved before an event might still use an inappropriate input
or depend on calibration data. Neither timestamp ordering nor a declared held-out label
proves prospective validation or independence. Original records remain in the report, and
the application does not verify external clocks or input-availability history. Diagnostic
eligibility, weighting and residuals are unchanged. Reports do not grant field-validation
status or overwrite observations, calculations or actions.

## Soil prediction save times and observation boundaries

Root-zone and coupled soil diagnostic reports use schema version 2. Each comparison adds
`observation_timing`, which keeps the saved calculation snapshot's timestamp, original
measurement timestamp, and adjusted closing-local-midnight boundary as separate UTC values.
The save timestamp is classified as `before`, `at_or_after` or `unknown` relative to each
of the latter two timestamps. Equal timestamps are `at_or_after`, not `before`.

For example, a reading at 18:00, calculation saved at 19:00 and reference adjusted to
midnight produces **at/after the measurement** and **before the reference boundary**.
The time adjustment does not turn that saved calculation into a prediction made before
the reading. `observation_timing_counts` includes all selected readings, including those
excluded from residual metrics. The text report displays both sets of counts. Missing,
invalid or offset-free timestamps remain unknown; older reports explicitly say that
save timing was not recorded.

The timestamp belongs to the selected saved snapshot, including when it is a child of
another calculation. It does not establish the earliest time an equivalent prediction
existed. Original snapshots, observation timestamps, timing-adjustment inputs and lineage
remain inspectable. The comparison still uses the prediction before same-day observation
adoption and excludes known prior target reuse under its existing rules. Metrics, sampling
requirements, corrections and quality reviews are unchanged. These chronology fields do
not prove independent evaluation, clock accuracy or when the model inputs became available.

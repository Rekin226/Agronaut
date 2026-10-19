# The agriculture capability taxonomy: one vocabulary, and an honest ceiling on what it claims

## Why this document exists

Issue #243 asks for something that sounds bureaucratic and is not: a crop appearing in a
knowledge document must stop implying that its irrigation schedule is validated. Today it can.
`knowledge/*.md` mixes cited operator guidance with numbers computed by `aqua_model`, and a
reader has no signal that separates "a source says this" from "we calculated this" from "a field
trial showed this". Adding field irrigation on that footing would make the confusion permanent.

So this file fixes three things before any field model is added: **what the words mean**, **which
combinations are in scope at all**, and **what each level of claim requires before it may be
made**. The vocabulary and the compatibility matrix live in `taxonomy.v1.json`, which is
normative; this document explains it, and a test fails if the two disagree.
Source citations live in `source_registry.v1.json`, which is versioned and grows one read
locator at a time.

## Two axes, because one axis conflates two questions

The failure this taxonomy exists to prevent is the sentence "aquaponics with drip irrigation is
similar to hydroponics with drip irrigation". It is not: the first shares its water with fish,
the second shares it with nothing. A production system answers *where the roots live and what
feeds them*; an irrigation method answers *how water reaches the growing area*. They combine,
they do not replace each other.

| Production system | Question it answers |
|---|---|
| `aquaponics` | Fish and plants in one recirculating loop; fish waste is the nutrient source, and fish-safe chemistry constrains every plant-side decision |
| `hydroponics` | Plants fed from a prepared nutrient solution; no fish, so no fish-safe ceiling |
| `soil_cultivation` | Plants rooted in soil, which is itself a nutrient and water store |

| Irrigation method | Question it answers |
|---|---|
| `drip` | Localized emitter delivery to part of the root zone, not the canopy |
| `sprinkler` | Spray over canopy and soil surface, so foliage wets and evaporates |
| `surface` | Gravity delivery across the soil surface; depth set by inflow and time |
| `rainfed` | No applied water; a deficit is a risk, not a valve setting |

`aquaponics` is **not** an irrigation method and `drip` is **not** a production system. The test
asserts the two axes share no member, which is the mechanical form of "without conflating
production and irrigation".

## The compatibility matrix, and what its rows are worth

Twelve combinations, each with a status and a reason. The reason field distinguishes a
**support decision** (this project's scope choice, sourced from nothing) from a **sourced** row.
Most rows here are decisions, and that is stated in the data rather than implied by a table that
looks equally authoritative on every line.

| Production system | Method | Status | Why |
|---|---|---|---|
| aquaponics | drip | conditional | Media beds only: the bed is the biofilter. Not where unsettled solids would reach a bare-root channel |
| aquaponics | sprinkler | incompatible | Foliar fish-culture water on edible leaves; food-safety and biofilm questions unresolved, and no registry source answers them |
| aquaponics | surface | incompatible | A flow-through loss from a loop whose mass balance assumes the water returns |
| aquaponics | rainfed | incompatible | The loop needs a controlled, measurable volume |
| hydroponics | drip | supported | Emitter delivery to a substrate is the configuration the hydroponic sizing path already assumes |
| hydroponics | sprinkler | conditional | Aeroponic misting inside a closed chamber, which is not spraying a field canopy |
| hydroponics | surface | incompatible | Flood-and-drain is its own bookkeeping; treating it as field surface irrigation loses the solution volume the nutrient balance needs |
| hydroponics | rainfed | incompatible | Rain dilutes a defined concentration |
| soil_cultivation | drip | supported | The case the FAO-56 water balance fits most cleanly: localized wetted fraction, known application depth, schedule in mm |
| soil_cultivation | sprinkler | supported | Same, with evaporation and foliar wetting carried explicitly rather than buried in an efficiency factor |
| soil_cultivation | surface | supported | Same, with depth set by inflow and duration |
| soil_cultivation | rainfed | supported | The same balance with irrigation set to zero, so a deficit is a stated risk |

The four `soil_cultivation` rows are marked `source_pending`: the procedure is registered, the
worked reference case is not written yet. Until it is, nothing from those rows may be presented
as a verified calculation.

## Capability statuses: the ceiling on every claim

Four levels. A claim may be made at a level only when that level's evidence exists, and the
level must be named wherever a number reaches a user.

| Status | What it means | What it requires | Available today |
|---|---|---|---|
| `sourced_guidance` | What a named source says, and nothing about our arithmetic | A registry entry whose locator names a section, table or equation, plus its verification level | Yes, the whole knowledge corpus |
| `verified_calculation` | A number we computed and can reproduce: inputs, equation and arithmetic on the record, with a reference case and a tolerance | An equation-level or table-level locator, a worked case with an expected value and tolerance, and a test that fails outside it | Aquaponic sizing only. Nothing irrigation-side |
| `experimental_simulation` | A modelled scenario whose parameters are assumed or fitted, useful for comparing options | Every parameter traceable to a registry entry or an explicit assumption, plus a stated sensitivity | No |
| `field_validated_operation` | A recommendation shown to hold on a real system under recorded conditions | Pilot data under the protocol below, the case recomputed against the measured outcome, and a named region, season and operator record | **No.** No field trial, no partner system, no measured irrigation outcome |

The last row is the one to read twice. Agronaut has no field validation for irrigation, so no
page, answer or generated document may imply otherwise — and the test fails if a future edit
marks that status as available without the evidence fields being filled in.

## The pilot data protocol

A pilot that records only yield cannot settle anything. The minimum record for a result that
could ever be called field-validated:

- **Site** — latitude, longitude, altitude, soil texture class.
- **Season** — sowing or transplant date, and the crop cycle covered.
- **Weather** — daily Tmax, Tmin, relative humidity, wind speed at 2 m, and solar radiation or
  sunshine hours, each with the instrument and its height.
- **Water applied** — method, date, depth in mm, and the source of the water.
- **Crop** — cultivar, and measured yield per unit area.
- **Operator decisions** — every deviation from the schedule, with the reason given at the time.

A missing required field is recorded as missing, with its cause; it is never back-filled from a
default. Data that omits any required field cannot support a field-validation claim, and a single
site cannot support a regional recommendation.

## What this does not do

- **No worked reference cases yet.** #243 also asks for independently calculated ETo,
  crop-demand and soil-water examples with expected values, tolerances and a missing-data case.
  They are not in this change. They need locators that have not been read yet — Chapter 1 for
  Eq. 6's confirmed form, Chapter 6 for the crop coefficient tables, Chapter 8 for the water
  balance — and the `pending` list in `source_registry.v1.json` is that checklist. Writing the
  numbers first and finding the sources afterwards is the exact failure this registry prevents.
- **No runtime model, no coefficient, no code path.** Nothing in `aqua_model/` reads these files.
- **No regional or operational choice has been made.** Which region, which season, which soil,
  and which crop cycle the tomato case should describe is an operator decision, not a
  contributor's — tomato is proposed as the first crop, not selected.
- **The registry is two entries deep.** That is the honest depth of what has been read. A
  document-level locator is accepted only for definition-level entries; anything carrying a
  number needs an equation or table locator, and the test enforces that split.

## How this is guarded

`agronaut_agent/tests/test_agriculture_capability_taxonomy.py` checks the invariants that make
the above true rather than aspirational: the two axes share no member, every required case from
#243 is present, no combination is listed twice or listed as both supported and incompatible,
every cited source resolves to a registry entry that carries an edition, a locator and a reuse
permission, a numeric entry never rests on a document-level locator, the four capability statuses
are exactly the four defined ones with increasing rank, `field_validated_operation` claims no
availability, and every status named here still exists in the data. It is a source check, not an
implementation-mirroring test: it recomputes nothing, because there is no arithmetic to recompute
until the reference cases land.

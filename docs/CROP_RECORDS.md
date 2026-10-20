# Crop cycles, observations and harvest records

Agronaut records actual plantings, transplants, growth stages, scouting observations and harvested batches for soil, hydroponic and aquaponic
production units. Each cycle belongs to one owned unit; dates use its farm timezone.
An aquatic unit can keep plant harvests here alongside its existing aquatic tools.
Soil units also support completed mineral-fertilizer applications, including preplant work.
All three systems can record payments and receipts assigned to a crop cycle and compare
the recorded outcomes of explicitly selected cycles in the same unit.

## In the app

Select a unit, choose **Crop records**, and use **Record a planting**. Enter the crop,
actual planting date and source. The new cycle is also available in the **Field records**
cycle selector. Future planting plans are not recorded as completed plantings.

Choose a cycle and open **Record or correct a harvest**. Enter a batch ID, date, positive
quantity and unit, product, produce basis, grade, source and provenance. Dates and numeric
quantities start blank. Use a stable batch ID such as `2026-07-06-morning`; repeating a
batch requires an explicit correction instead of creating a second total.

Select an existing batch to correct its complete quantity or other harvest details. Only
the latest revision can be corrected. **Void an incorrect harvest batch** excludes it from
totals while preserving its history and the reason. A correction to the latest voided
revision can restore it. To move a harvest to a different cycle or batch ID, void the wrong
entry and record it in the intended cycle with a new request ID.

The page shows current totals and revision history and downloads the full crop journal.
Changing units clears the form. Planting corrections preserve the original values and source
history, as described below.

## Across farms and production units

Choose **Farm overview** to see recorded crop cycles and crop-calendar work across
your soil, hydroponic and aquaponic units without changing the active unit. Each farm
uses its own timezone and local date. Open, closed and future-dated cycles remain
distinct; a missing or invalid timezone withholds current-date and task-timing
classification rather than defaulting to UTC.

The view shows current task counts and work that is due, overdue or needs review.
Planting corrections, closed-cycle conflicts and changed completion evidence retain
the same review flags as the individual crop calendar. Completed tasks whose linked
evidence was corrected or voided can therefore still appear for review. Original and
current work windows, source notes and exact record IDs accompany the task details.
The JSON download includes all current crop-calendar tasks, including upcoming and
resolved work. Reports with an unknown farm timezone show an unclassified task count
and withhold the calendar details until timing can be established.

The same farm snapshot includes active equipment bookings, counted once per booking
even when they reserve multiple items or work intervals. It shows today's reservations
and any booking whose equipment, timezone or plan evidence needs review. Each interval
retains the booking, plan and work identifiers, source, equipment name/key, original UTC
times and times in the current farm timezone. All current active bookings, including
future and elapsed reservations outside today's view, appear in the JSON download.
Released and superseded bookings remain in the separate equipment calendar's history.

Intervals include their start and exclude their end. Today's overlap uses local midnight
boundaries, including daylight-saving changes. An interval can be upcoming, in progress or
elapsed; these describe reserved time, not actual equipment use or completed work. An
elapsed reservation remains active until explicitly released. A missing or invalid farm
timezone leaves today's count and local timestamps unknown, retains UTC timing, and shows
the active reservations for review. Existing bookings are never moved to fit a new timezone.

No recorded tasks does not mean that a farm needs no attention. Follow-ups have their
own view. Parent fields and irrigation zones remain separate;
the overview does not sum potentially overlapping areas, water volumes or yields.
Use the unit selector and **Crop records** to plan or resolve crop work; use
**Farm resources / Shared equipment calendar** to inspect, replace or release bookings. The overview
creates no records, recommendations, notifications or equipment commands.

The same read-only snapshot is available offline, without a selected unit:

```sh
agronaut farm overview
agronaut farm overview --farm OWNED_FARM_ID
```

Omitting `--farm` includes all farms owned by the CLI identity. It never changes that
identity's selected unit. The browser supports the overview even without a chat model.

In web chat, Telegram, WhatsApp or the terminal REPL, use `/overview`, `/overview fr`
or `/overview zh-Hant`. English is the default. French and Traditional Chinese labels
are drafts awaiting independent local review; names and source notes remain as entered.
Replies show cycle summaries and due, overdue or review-flagged tasks, with their farm,
unit, crop, local date and record identities. Long labels/source excerpts are shortened
with an ellipsis; full details remain in the app and the JSON overview.

Use the displayed next-page command, for example `/overview fr 2`. `/overview 2` means
English page 2. Every page reads the current records, so changes between requests can
change page contents; restart at page 1 after editing a calendar. No page cursor or
language preference is saved. The command leaves the selected unit and any unfinished
setup or record-entry draft intact, and requires no chat model. It sends only the
requested reply, not scheduled alerts or automatic record exports.

## Guided planting in chat

Select an owned unit with `/units`, or create one with `/newunit`. Use `/plant en`,
`/plant fr`, or `/plant zh-Hant` to record an actual planting in English, French, or
Traditional Chinese. The translations are drafts awaiting local review. The flow supports
soil, hydroponic and aquaponic crop cycles and works without a chat model.

Supply the crop name, actual planting date in `YYYY-MM-DD` format, and source, such as a
dated planting notebook. Dates use the farm timezone and cannot be in the future. Review
the preview and reply `save` (`enregistrer` in French, `儲存` in Traditional Chinese) to
create the cycle and its planting-source record. Then use `/scout` to record observations.
Free-text names and sources are preserved as supplied; location never selects a language.

If the crop name and date match existing cycles, the flow lists them. Reply `new`
(`nouveau` in French, `新增` in Traditional Chinese) only to create a distinct planting,
then review and save. Otherwise cancel and use the existing cycle. This check compares
crop names without case sensitivity; it does not recognize synonyms or translated names.
If matching cycles change during entry, `/plant` refreshes the list and requires another
review. A changed farm timezone requires cancelling and restarting the draft.

`/plant` resumes the saved draft, `/back` edits the previous answer, and `/cancel` discards
the draft. Changing language preserves answers and invalidates the previous review revision.
Drafts are separate for each owned unit and survive restarts. `/reset` removes the current
unit's draft; account export includes `planting_intakes`, and erasure removes owned drafts.
Finish or cancel unit setup first. Planting, scouting, water-log, measurement and field-planning intakes cannot run
together for the same unit. Image-generated facts cannot fill planting answers.

The crop CLI and scoped `crop_records` tool expose `start_planting` (`{language?}`),
`get_planting` (`{}`), `answer_planting` (`{answer, expected_revision, back?}`),
`save_planting` (`{draft_id, expected_revision}`), and `cancel_planting`
(`{expected_revision}`). Saving requires a ready, explicitly reviewed draft. Cycle creation,
the planting record, and draft deletion share one transaction. Retrying the same saved
revision returns its original result without creating another cycle or deleting a new draft.
Schema v19 adds the draft table with the existing transactional upgrade and private backup.

## Correcting a planting

Use **Correct this planting** to fix a mistaken crop name or actual planting date. Supply
the complete corrected crop/date, a source and a reason. The cycle keeps its ID and unit;
use a new cycle for a new planting. A correction updates the current cycle and appends a
planting revision with the previous cycle values. Nothing is removed from the journal.
Existing imported cycles without a planting-source record preserve their original values
in the first correction. This also allows an imported future-dated cycle to be corrected
to its actual planting date; new future plans are not treated as completed plantings.

The new date cannot be after today in the farm timezone or after a closed cycle's end.
Current stage, scouting, transplant, harvest, accepted daily water and in-crop fertilizer
records must remain on or after planting. Preplant fertilizer must remain on or before it.
A conflicting current entry must be corrected, voided, or rejected using its applicable
workflow first; its date is never moved automatically. Historical superseded/voided entries
and rejected water totals do not block corrections. Restoring them must respect the current
cycle dates. Typed measurements may legitimately predate planting and keep their timestamps.

From the CLI or chat, read `crop get` and use `planting.revision_id` as `expected_revision`
(null only when the cycle has no planting record). The operation is `correct_planting`:

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "crop": "tomato",
  "planted_on": "2026-07-02",
  "source": "Operator's corrected planting notebook",
  "reason": "Planting date was transcribed incorrectly",
  "request_id": "planting-correction-001",
  "expected_revision": "LATEST-PLANTING-RECORD-ID"
}
```

Stale revisions and reused request IDs with different data are rejected. Identical retries
return their original result without reapplying it. Retrying the original creation returns
its original crop/date; use `get` for the current cycle. Planting revisions cannot be voided;
use another sourced correction to undo a mistaken change.

Saved calculations retain their original inputs and results. New calculations also preserve
a cycle snapshot. Season reviews identify changed crop/date fields and current input conflicts;
older calculations may lack a snapshot, so their full past cycle metadata remains unknown.
New calculations and replays must satisfy the current cycle's constraints. Water and nutrient
intake forms clear unsaved assumptions when the selected crop or planting date changes.

## Transplant batches

Use **Record or correct transplant** to record an actual transfer into the selected cycle,
including a partial batch or replacement plants. Enter an entry ID, actual date, destination
location or extent, seedling origin or nursery batch, source, and provenance (`measured`,
`reported`, or `estimated`). Enter a positive whole plant count up to one billion, or leave
it blank when unknown. JSON requires `plant_count`, using explicit null for an unknown count.
Dates run from planting through today in the farm timezone, capped by the cycle end.

`record_transplant` accepts `cycle_id`, `request_id`, optional `supersedes_id`, and this `data`:

```json
{
  "entry": "transfer-001",
  "day": "2026-07-06",
  "location": "Synthetic row 1",
  "origin": "Synthetic nursery batch A",
  "plant_count": 120,
  "source": "Synthetic transfer notebook",
  "provenance": "reported"
}
```

Corrections replace the full entry using its latest revision ID, keeping the same cycle and
entry ID. `void_transplant` takes `record_id`, `reason`, and `request_id`; correcting its latest
voided revision restores it. Current transplants constrain cycle closure and planting-date
corrections. Separate batches are not added into a standing plant population: replacements
or repeated transfers can overlap. Records do not infer survival, change the planting date,
alter crop coefficients, or transfer inventory from another unit. The nursery origin is
traceability text. The journal, season review and account export include every revision;
account erasure removes them. These operations use the existing journal without a migration.

## Growth stages and scouting

For observation preparation, read `agronaut field-guide --topic crop-scouting` or select
**Field records → Field guides → Investigating crop symptoms**. English, draft French
(`--language fr`) and draft Traditional Chinese (`--language zh-Hant`) are available offline.
This guide helps organize evidence; it does not confirm a cause or choose a treatment.

Choose a cycle and use **Record or correct growth stage** or **Record or correct scouting**.
Supply an entry ID, observation date, location or extent within the planting, source and
provenance (`observed` or `reported`). Dates must fall between planting and today in the
farm timezone. Use a new entry ID for a separate observation; choose an existing entry to
correct it. Entry IDs are unique within each cycle and kind, so a stage and a scouting
entry can share an inspection ID.

Growth stages are explicit free-text observations. The page shows all current reports on
the most recent reported date, preserving their locations and sources. Different parts of a
planting may be at different stages. These records do not assign crop coefficients, estimate
root depth, or alter irrigation calculations automatically.

Scouting records describe symptoms (or explicitly state `none observed`) and optionally a
sample's inspected and affected **plant** counts. Supply both integer counts or leave both
blank (`null` in JSON). Inspected count must be positive; affected count may be zero but
cannot exceed inspected count. Counts are limited to one billion per entry. Unknown counts
remain unknown. Affected means plants with the described symptoms, not confirmed disease.
Samples remain separate because their areas or plants may overlap; Agronaut does not sum
them into a field incidence estimate or infer a diagnosis or treatment.

Each kind has its own correction and void controls. A correction replaces all observation
details while preserving the cycle, kind and entry ID. Voiding removes the entry from current
reports and keeps its history and reason. Correct the latest voided revision to restore it.
To reassign a record to a different cycle or entry ID, void it and create a new entry there.
The journal download includes all stage, scouting, planting and harvest revisions.

### Guided scouting in chat

Use `/scout` with a selected soil, hydroponic or aquaponic unit that already has a crop
cycle; use `/plant` first if the actual planting has not been recorded. Choose `/scout en`, `/scout fr` or `/scout zh-Hant` for English, French or Traditional
Chinese questions. French and Chinese translations are drafts awaiting local language and
agronomic review. This choice applies to the current unit's scouting draft, not to other
forms or conversations. Agronaut asks for the crop cycle, observation
date, sample location, symptoms, inspected/affected plant counts, source and whether the
observation was directly observed or reported by someone else. Enter `unknown` for both
counts when they are unavailable; explicit `10,0` records ten inspected plants with no
affected plants observed. The flow does not infer disease, a treatment or whole-field
incidence from those answers.

French accepts `inconnu` for unknown counts, `observé` for a personal observation and
`rapporté` for another person's report. Traditional Chinese accepts `未知`, `親自觀察`
and `他人回報`, respectively. English tokens remain accepted in every language. Counts
use whole plant numbers separated by an ASCII comma, such as `10,2`; they never use a
decimal comma. Free-text descriptions and source notes are preserved in their supplied
language, while stored provenance remains `observed` or `reported`.

The draft persists between sessions. `/scout` resumes it, `/back` revisits the previous
answer, and `/cancel` removes only that draft. Switching units leaves the other unit's
draft separate. `/reset` discards the current unit's draft; account erasure removes all
owned drafts. Exports include them under `scouting_intakes`. Only one of the field-planning,
scouting, planting, measurement or water-log intakes can run for the same unit, avoiding ambiguous routing of plain-text answers.
Unit setup must be finished or cancelled before starting scouting.

Without a language argument, `/scout` resumes the draft in its saved language; a new draft
defaults to English. An explicit language changes only the draft's display language and
revision, preserving its answers. Review the new preview before saving. Older drafts
without language metadata still resume in English. Nothing infers a language from location.

The final preview shows the supplied observation and counts. Only replying `save` (or
`enregistrer` in French, `儲存` in Traditional Chinese) appends
a new crop-journal observation. Saving and deleting the draft occur in one transaction;
repeating the same save receipt returns the same record without duplicating it. Current
farm timezone, planting details, date and crop closure are checked again. If planting or
timezone changes during entry, cancel and restart to review the current context. Later
corrections and voids use the journal's existing observation controls.

This works without a chat model through terminal, web chat and the messaging adapters.
Image-generated descriptions cannot answer its questions automatically; the operator must
supply the observation text. Transport behavior is covered with mocks, not live messages.
Common chat validation errors are translated; unexpected service errors retain their
original technical details under a localized explanation. The CLI/API error contract and
the rest of the application's interface remain in English.

The `crop_records` tool and offline crop CLI expose the same revision-checked operations:

- `start_scouting`: `{language?}` with `en`, `fr` or `zh-Hant`; omit to resume unchanged.
- `get_scouting`: `{}`.
- `answer_scouting`: `{answer, expected_revision, back?}`.
- `save_scouting`: `{draft_id, expected_revision}`, after reviewing the ready draft.
- `cancel_scouting`: `{expected_revision}`.

Schema v18 adds the scoped draft table. Upgrading creates the existing private pre-upgrade
backup and runs transactionally; older application versions must refuse this schema.
Language is stored in the existing draft payload and save receipt; it requires no further
schema migration. Views expose `language` and `translation_status` (original or draft).

## From the terminal

These commands use the selected local production unit and work without a model or network.
`--db`, `--user` and `--unit` explicitly select a database, CLI identity and owned unit;
they do not change a messaging account's records.

```bash
agronaut crop describe
agronaut crop create --input planting.json
agronaut crop correct_planting --input planting-correction.json
agronaut crop list
agronaut crop record_transplant --input transplant.json
agronaut crop void_transplant --input void-transplant.json
agronaut crop record_cash --input cash-entry.json
agronaut crop void_cash --input void-cash.json
agronaut crop compare_seasons --input comparison.json
agronaut crop record_stage --input stage.json
agronaut crop record_scouting --input scouting.json
agronaut crop record_fertilizer --input application.json
agronaut crop void_fertilizer --input void-application.json
agronaut crop record_harvest --input harvest.json
agronaut crop get --input cycle-request.json
agronaut crop void_harvest --input void-request.json
agronaut crop close_cycle --input close-request.json
agronaut crop reopen_cycle --input reopen-request.json
```

Example `planting.json` (replace the illustrative details):

```json
{
  "crop": "tomato",
  "planted_on": "2026-07-01",
  "source": "Operator's planting notebook, bed A",
  "request_id": "planting-bed-a-2026-07"
}
```

The response contains `cycle.id`. Use it in `harvest.json`:

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "request_id": "harvest-morning-001",
  "data": {
    "batch": "2026-07-06-morning",
    "day": "2026-07-06",
    "quantity": 12.5,
    "unit": "kg",
    "product": "fruit",
    "basis": "fresh",
    "grade": "ungraded",
    "source": "Operator's scale reading",
    "provenance": "reported"
  }
}
```

`cycle-request.json` contains `{"cycle_id":"SAVED-CYCLE-ID"}`. For a correction, submit
a complete replacement harvest request with the latest record's `supersedes_id` and a new
`request_id`. Keep the same cycle and batch ID. A void request has this form:

```json
{
  "record_id": "LATEST-HARVEST-RECORD-ID",
  "reason": "Entered against the wrong planting",
  "request_id": "void-morning-001"
}
```

Reusing a request ID with identical data returns the existing result; conflicting data is
rejected. Crop creation and its planting-source record are atomic. Owner and unit identities
cannot be supplied inside an operation's JSON data.

## Guided harvest logging in chat

Select the production unit with `/units` and `/unit`, then use `/harvest`, `/harvest fr`
or `/harvest zh-Hant`. This works through web chat, Telegram, WhatsApp and the terminal
without a chat model. French and Traditional Chinese are draft translations awaiting
independent language and agricultural review.

Choose the crop cycle, then supply the physical batch identifier, actual harvest date,
product, quantity unit and amount, quantity basis, grade, source and quantity provenance.
Use `kg`, `g`, `t` or `count`; counts must be whole. Numeric answers use a decimal point
without a thousands separator. Unknown and zero quantities are not saved as harvests.
Choose fresh, dry or as-harvested condition explicitly; quantities are never copied from a
prediction. Describe the actual grade, or explicitly say ungraded or unknown as appropriate.

Review the complete preview, then reply `save` (`enregistrer` in French or `儲存` in
Traditional Chinese). `/back` edits an answer; `/cancel` discards the draft. `/harvest`
resumes the current unit's draft, including after a restart. Changing language preserves
the answers but requires review of the new preview. Changing units keeps their drafts
separate. Other record intakes must be finished or cancelled before starting another one
on the same unit. Unit setup takes precedence while it is active.

Use the same identifier for the same physical batch. If it already exists, correct the
existing record in **Crop records** instead of logging a second batch. Save retries return
the original receipt; a concurrent entry of that batch blocks the new save. Harvest date
must fall between planting and farm-local today, and on or before closure for a closed
cycle. A changed crop, planting date or timezone requires restarting the draft.

Saving adds one harvest record to the existing journal and seasonal totals. It does not
close the crop cycle, complete a calendar task, record income or infer harvested area,
yield per area or a full season's completeness. Totals retain product, basis, grade and
provenance distinctions. Images cannot automatically supply answers to this form.

The structured CLI and scoped crop tool expose `start_harvest {language?}`,
`get_harvest {}`, `answer_harvest {answer,expected_revision,back?}`,
`save_harvest {draft_id,expected_revision}` and `cancel_harvest {expected_revision}`.
For example: `agronaut crop start_harvest --input harvest-language.json`, where the file
contains `{"language":"fr"}`. Inspect `agronaut crop describe` for all operations.
Saving requires an explicit request after reviewing the preview.

Schema v22 stores owned drafts; migration retains a private backup. The harvest record,
save receipt and removal of the draft are one transaction. Account export includes
`harvest_intakes`; erasure removes owned drafts. The saved record retains the intake
language and reviewed revision under `operation.intake`.

## Harvest quantities and provenance

- Units are `kg`, `g`, `t` or `count`; counts must be whole numbers. Unknown quantities
  are not zero, and zero quantities are not recorded as completed harvests.
- Produce basis is `fresh`, `dry` or `as_harvested`. Agronaut does not convert moisture
  bases. For counts, the product label should identify what is counted, such as heads.
- Provenance is `measured`, `reported` or `estimated`. Sources preserve where entries
  came from; these labels do not independently verify them. Grade is explicit free text;
  use `ungraded` if no grade was assigned.
- Totals convert mass to kg using decimal arithmetic, keep counts separate, and group by
  product, basis, grade and provenance. Summary quantities are decimal strings; original
  entries retain their supplied quantities and units.
- Superseded and voided revisions remain in history but do not contribute to totals.
  Harvest dates must fall between planting and today in the farm timezone.

The shared `crop_records` chat tool uses this contract and the unit pinned to the
conversation. It records requested outcomes; yield forecasts must not become harvests.
The CLI and web forms do not invoke a model.

Schema v6 adds the crop journal. Existing databases receive a local SQLite backup before
upgrade; failed migrations roll back. Account export includes all journal revisions.
Erasure removes owned cycles and records, and later writes to erased units are rejected.

## Observation JSON

`crop describe` returns the shared operation contract. Example `scouting.json` (synthetic
values; use your actual observations):

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "request_id": "scouting-row1-001",
  "data": {
    "entry": "2026-07-06-row1",
    "day": "2026-07-06",
    "location": "Row 1, first 10 plants",
    "symptoms": "Yellow leaf margins",
    "inspected_count": 10,
    "affected_count": 2,
    "source": "Operator's inspection notebook",
    "provenance": "reported"
  }
}
```

For `record_stage`, keep the envelope and common data fields (`entry`, `day`, `location`,
`source`, `provenance`), replacing `symptoms` and both count fields with `stage`, such as
`"flowering"`. Use a different request ID. Corrections add `supersedes_id` at the envelope
level and supply the full replacement data. `void_observation` accepts
`{ "record_id": "LATEST-RECORD-ID", "reason": "Wrong planting", "request_id": "void-001" }`.
It applies to stage and scouting entries; `void_harvest` remains harvest-only.

The existing v6 journal stores these additional kinds without a database migration.
`get` adds `latest_stage_reports`, containing the full current records from the latest
reported date (or an empty list). Earlier dates and all corrections remain in `records`.

## Closing and reopening a crop cycle

In **Crop records**, select a planting and open **Manage crop cycle**. **Close cycle**
records its actual end date, reason and source. A planting can end because harvest finished,
the crop failed, or it was abandoned; closing does not imply success or automatically record
any yield. A harvest never closes a cycle automatically. Cycles without a closure are
labelled **open**, meaning not marked ended, rather than verified to be growing.

The end date must be between planting and today in the farm timezone, and on or after the
latest current crop/field activity assigned to that cycle. This check includes harvests,
transplants, fertilizer applications, stages, scouting, daily water totals and typed field observations. Cash
entries keep their own transaction dates and do not constrain closure. Superseded, voided and rejected
records do not constrain closure; forecasts do not either. Correct or void an incorrectly
dated journal entry before closing. Daily water records can be explicitly rejected through
a correction. Use **Review field measurements** to reject a mistaken typed observation with
a reason and source before closing. Its original value and timestamp remain in history;
closure uses the latest reviewed quality.

Closed cycles remain visible and downloadable. Late crop/field activity entries and corrections are
allowed through the end date. Later actual activity requires **Reopen cycle** or **Correct
cycle end**, each with a reason and source. Reopening removes the end-date restriction;
previous closure records remain in history. Use a new cycle for a new planting. Field records
also display closed status and enforce the boundary for actual water logs. Forecasts,
rejected evidence and calculations remain available for historical analysis; closing does
not rewrite old plans or control equipment. Field activity without a cycle is not attributed
to the ended planting.

For the CLI or chat tool, first call `get` for the cycle. Copy its `lifecycle.revision_id`
into the required `expected_revision` field (use `null` when no lifecycle record exists).
Example `close-request.json`:

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "ended_on": "2026-07-10",
  "reason": "Final harvest completed and planting removed",
  "source": "Operator's crop notebook",
  "expected_revision": null,
  "request_id": "close-cycle-001"
}
```

`close_cycle` also corrects an existing end date, using its current revision ID and a new
request ID. To reopen, send the same envelope with `cycle_id`, `reason`, `source`,
`expected_revision` and `request_id`, omitting `ended_on`. Stale expected revisions are
rejected. Reusing the same request ID and data returns the original response even if a
subsequent action changed the cycle; it never reapplies an old closure. Read `get` for current
state after a retry. Both lifecycle operations return the appended journal record.

`get.lifecycle` and each cycle in `list.cycles` expose `status`, `ended_on`, `revision_id`,
`reason`, `source` and `recorded_at`. The complete lifecycle history is in `get.records`.
Closures are stored in the existing v6 journal and included in account export and erasure.

## Reviewing field measurements

Open **Crop records → Review field measurements** to inspect recorded typed observations
such as rainfall, soil-water content, flow or pressure. This includes observations without a
crop cycle, so a cycle is not required to review them. These observations are separate from
crop scouting entries, daily water totals and the aquaponics live-twin readings. This page
reviews existing records; it does not import or create measurements.

Select a measurement, then choose `unverified`, `checked` or `rejected` and supply a reason
and source. `checked` means an operator reviewed the evidence; Agronaut has not independently
validated it. Each review preserves its predecessor. Original values, units, timestamps,
source, provenance and original quality remain unchanged. A mistaken value/date can be
rejected here and recorded correctly as a new observation through its ingestion source.

The latest review determines `effective_quality`. Rejected actual observations do not
constrain crop closure. To undo a rejection, append an `unverified` or `checked` review.
Restoring actual evidence after a closed crop's end date requires reopening or correcting
the cycle first. A reviewed forecast remains a forecast, even when its quality is checked.
No review creates a completed irrigation event, diagnosis or treatment.

The same operations work in the CLI and scoped chat tool:

```bash
agronaut crop list_measurements
agronaut crop review_measurement --input review-request.json
```

`list_measurements` takes optional `cycle_id` to filter the selected unit's observations;
when omitted or null, it includes all observations in the unit, including unassigned ones.
`get` includes observations belonging to that crop cycle in `measurements`. Each returned
observation includes its original `quality`, its `effective_quality`, the latest
`quality_review` (null before any review) and the complete `quality_history`.

Use the latest `quality_review.id` as `expected_revision`, or null initially:

```json
{
  "observation_id": "SAVED-OBSERVATION-ID",
  "quality": "rejected",
  "reason": "Gauge reading entered with the wrong observation date",
  "source": "Operator's dated gauge notebook",
  "request_id": "review-gauge-001",
  "expected_revision": null
}
```

Reusing identical request data returns the original review without reapplying it. Conflicting
request IDs and stale expected revisions are rejected. The web page downloads all selected-unit
measurements with their reviews; crop journal downloads include that cycle's measurements.
Account export retains original observations and a separate `observation_reviews` history.
Erasure removes both and prevents later writes to the erased unit.

Schema v7 adds the review table. Upgrades from v6 preserve original observations and create a
local backup first; failed migrations roll back. Existing records initially retain their
original quality as effective quality.

## Reviewing a season

Open **Crop records → Review this season** for a read-only snapshot of the selected planting.
It combines the crop journal, current harvest, fertilizer and cash totals, stage and scouting history, typed
measurements and quality reviews, daily water records and saved calculation versions.
The snapshot is available for open or closed cycles; **Download season review** preserves its
generation time, farm timezone, source records and revision IDs. It does not change records
or create a saved calculation.

For soil units, water-log coverage spans the planting date through the last completed local
day, capped by the cycle end date when closed. It reports compact ranges of days without
accepted daily totals. Missing or rejected days remain unknown. Totals cover only the
recorded days, preserve quality and provenance groups, and use decimal strings for mm depths.
Current revisions replace earlier daily totals; they are never added together. Field
measurements are not added to those totals because their sampling periods may overlap.
Daily soil-water log coverage does not apply to hydroponic or aquaponic units; their crop
journals and harvest totals remain available.

Optionally select a saved **root-zone budget** to compare its supplied daily rainfall,
net irrigation and runoff with current accepted daily records. No calculation is selected
automatically. The table shows recorded minus input, with missing, rejected and out-of-window
days clearly marked. These differences do not establish water savings or prediction accuracy:
calculation inputs can be scenarios, and a replayed calculation already contains actuals.
The review identifies the exact replayed record IDs, including older revisions, alongside
the current log IDs. The chosen calculation's complete saved snapshot is included in the
download. Calculation version history retains parent IDs and evidence status.

**Irrigation event comparisons** shows outcome reviews assigned to this exact crop cycle.
Create these from an allocated, linked irrigation job in **Farm resources** after recording
event-volume or runtime measurements. The season page lists each current plan/job review
separately, with its original allowance, selected observation, difference, provenance, quality
and current evidence flags. An unselected metric stays unknown. Review counts count review
records, not distinct performed irrigation events; no reviews means unknown comparison history.

Use **Irrigation outcome review history** to inspect current and superseded revisions. The
season download retains full source designs, original observations and their quality histories,
plus current review flags. Changed planting dates or farm timezone do not hide assigned
history: planned and actual dates retain each saved plan's timezone. Unassigned observations
or reviews from another cycle are not attached automatically. Make corrections in **Farm
resources**; opening a season remains read-only.

Alternative plans can reuse the same measurements. Their event comparisons are never summed
or added to daily net-water totals or coverage. Source-water volume and pump operating time
have a different basis from net soil-water depth. Differences from conservative allowances
do not demonstrate savings, irrigation adequacy, task completion or crop response.

No recorded harvest means unknown yield, not zero. Scouting samples remain separate. This
review does not infer diagnoses, causes of yield changes, harvested area, water productivity,
profit or full-season completeness. Explicit harvest-per-area reviews are described below;
they retain the operator's footprint, batches and collection coverage. Open means the cycle has not been marked ended.

Season reviews also include **Assigned laboratory soil reports**, entered through
**Field records → Record laboratory soil tests**. Current reports retain exact values,
detection qualifiers, methods, units, sample depths and dates. Full correction/void history
is included in the season download. Sampling or reporting dates may fall outside the crop
activity period; assignment is explicit. Unassigned or other-cycle reports are not imported.
Cross-season comparisons show current assigned reports separately, with no conversion,
averaging, soil-test interpretation or fertilizer recommendation. See the laboratory-report
section of [Field calculations](FIELD_CALCULATIONS.md) for entry and correction rules.

```bash
agronaut crop season_review --input season-request.json
```

`season-request.json` contains `{"cycle_id":"SAVED-CYCLE-ID"}`. Add an optional
`"calculation_id":"SAVED-ROOT-ZONE-BUDGET-ID"` for the comparison. The calculation must belong
to the same owned unit and cycle. The scoped chat tool exposes the same operation. Reviews
read their entire evidence snapshot in one database transaction and require no new migration.

## Mineral fertilizer applications

For a soil unit, select a planting and open **Record or correct a fertilizer application**.
Enter an entry ID, actual date, phase, treated area in m², treated location or extent,
actual placement or method, source, and quantity provenance (`measured`, `reported`, or
`estimated`). Choose `preplant` for applications on or before planting, or `in_crop` for
applications on or after planting. All dates must be on or before today in the farm timezone
and on or before the recorded cycle end. Completed preplant work can also be recorded for
an existing future-dated cycle; creating future plantings remains unavailable here.

Supply 1–100 mineral products. Each has a unique product ID, name, positive quantity actually
applied in kg or L, and label analysis. The web form assigns product IDs. Quantities cover
this event and treated area, which can be only part of the unit. Use N, P₂O₅ and K₂O mass
percentages with an analysis source. Unknown percentages are blank in the form or null in
JSON. For L, known mass also requires density in kg/L and its source; unknown density is
allowed and leaves the affected nutrient totals unknown. An explicitly zero label percentage
contributes zero even if density is unknown. kg quantities must not include density fields.
Organic amendments and per-hectare product quantities are not accepted in this workflow.

The operation shape is:

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "request_id": "application-001",
  "data": {
    "entry": "preplant-001",
    "day": "2026-06-30",
    "phase": "preplant",
    "area_m2": 5000,
    "location": "Synthetic north half",
    "placement": "Synthetic broadcast",
    "source": "Synthetic operator notebook",
    "provenance": "reported",
    "products": [{
      "id": "product-1",
      "name": "Synthetic 10-10-20",
      "kind": "mineral",
      "quantity": 100,
      "unit": "kg",
      "analysis": {
        "n_pct": 10,
        "p2o5_pct": 10,
        "k2o_pct": 20,
        "basis": "mass_percent",
        "source": "Synthetic label"
      }
    }]
  }
}
```

This is an arithmetic example, not an application recommendation. Replace its cycle ID
and synthetic details with actual evidence. `crop describe` includes the complete product
schema. An optional `data.calculation_id` links a saved `nutrient_budget` owned by the same
unit and cycle. The link never copies planned quantities into the application or changes
the plan. Saving a plan alone never records fertilizer use.

Corrections use a new request ID and the latest `supersedes_id`, replacing the full data
while preserving the cycle and entry ID. `void_fertilizer` takes `record_id`, `reason` and
`request_id`. Voids preserve history; correcting the latest void restores the entry. An
application dated after a proposed cycle end prevents closure until corrected or voided.
Late entries within an already closed cycle's dates are allowed.

Each revision preserves its raw quantities and a versioned label-accounting snapshot.
The journal, season review and account export include this history. Current totals exclude
voided and superseded entries, group by phase and provenance, and retain known partial
nutrient mass separately from unknown complete totals. Preplant totals can predate the
season review's planting date. Treated areas may overlap, so areas and kg/ha are never
summed across entries. No applications recorded means unknown history, not zero use.
These totals are label nutrients, not measured uptake or plant-available nutrients, and
do not automatically subtract credits from future targets. Export and erasure use the
existing crop journal; no new database migration is required.

## Payments and receipts

Open **Record or correct a payment or receipt** for the selected crop cycle. Enter a stable
entry ID, actual payment/receipt date, direction (`expense` paid or `income` received),
amount, currency, category, description, allocation note, provenance and source. Use actual
paid/received amounts, including bank transactions; quotes, unpaid invoices and forecasts
do not belong here. Enter only the amount assigned to this cycle and explain whether that
is the whole transaction or a share. Shared transaction allocations are not automatically
reconciled across cycles or units.

Amounts are positive **decimal strings** in JSON and text in the form, preserving exact
decimal arithmetic. Up to 15 integer digits and six fractional digits are accepted, with
no exponent, comma, sign, leading zeroes or currency symbol. Use direction for outflows
versus inflows. Unknown or zero amounts are not recorded as completed cash entries.
Currencies use three uppercase letters, such as `USD` or `XOF`, retained as supplied;
this validates the format, not a live currency registry. Agronaut does not apply currency
rounding rules or exchange rates.

Provenance is `documented`, `reported`, or `estimated`. Documented means the operator
provided a document reference; Agronaut does not audit it. An estimated amount must still
refer to an explicitly completed payment or receipt. Sources are required for every write.
Dates use the farm timezone and cannot be in the future. Cash can precede planting or follow
crop closure, so it neither constrains crop closure nor moves when planting is corrected.
Season reviews show cash-date ranges separately from the crop's activity period.

Example `cash-entry.json` for `agronaut crop record_cash --input cash-entry.json`:

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "request_id": "cash-payment-001",
  "data": {
    "entry": "receipt-001",
    "day": "2026-06-20",
    "direction": "expense",
    "amount": "12.50",
    "currency": "USD",
    "category": "seeds",
    "description": "Synthetic seed purchase",
    "allocation_note": "Entire synthetic payment assigned to this cycle",
    "source": "Synthetic receipt",
    "provenance": "documented"
  }
}
```

Replace the illustrative values with actual evidence. Corrections replace the full data,
using the latest `supersedes_id` and a new `request_id`, while preserving cycle and entry ID.
`void_cash` takes `record_id`, `reason`, and `request_id`. Voids preserve history; correcting
the latest voided revision restores it. Duplicate entry IDs, stale corrections and conflicting
request retries are rejected. All revisions participate in journal downloads, account export
and erasure. No new database migration is required.

The journal and season review report exact decimal-string subtotals by currency and
provenance, with a category breakdown. Current revisions replace old ones; voided entries
are excluded. **Recorded net cash** is recorded receipts minus recorded payments. It is
not profit: missing costs/receipts, unpaid work, accruals, depreciation, stock value and
other unrecorded amounts remain unknown. No cash entries means unknown history. A zero
subtotal means no entries for that direction in the group, not proof of zero actual activity.

## Comparing recorded seasons

Open **Compare recorded seasons** and explicitly select 2–10 crop cycles in the same unit.
There is no automatic baseline or ranking. The comparison shows each cycle's crop, dates,
open/closed state, harvest groups, accepted water totals and missing-day coverage, fertilizer
groups, and recorded cash groups. Currencies, provenance, harvest product/basis/grade, and
fertilizer phase remain separate. Empty harvest or cash histories are shown as unknown;
aquatic cycles do not receive an invented soil-water coverage score.

Current irrigation outcome reviews also appear separately for each cycle and plan/job, with
their original dates/timezones, quantities and current flags. Counts include current,
superseded and current-needing-review records; the comparison's detailed rows include only
current revisions. Open the individual season review for full correction history and source
snapshots. Unknown comparison history is not zero irrigation. Alternatives may share
measurements, so no combined event totals, savings or additional daily water coverage are
calculated.

Different crops, areas, durations, weather and completeness can make raw totals unsuitable
for judging performance. Explicit harvest-per-area reviews retain their area, selected batches,
collection coverage and evidence-change flags separately for each cycle. Agronaut does not
combine cycle totals, infer missing area, infer causal effects or identify a most profitable season. Use individual cycle journals
to inspect sources and full correction history.

The CLI and scoped chat tool expose `compare_seasons`:

```json
{"cycle_ids": ["FIRST-CYCLE-ID", "SECOND-CYCLE-ID"]}
```

Run `agronaut crop compare_seasons --input comparison.json`, or download the comparison
from the app. The read-only result preserves selection order, generation time, cycle details,
current evidence IDs and calculation summaries. It is assembled in one database transaction
and does not create records or calculations. Cross-unit or cross-owner selections fail.

## Reviewing recorded harvest per area

In a soil unit's **Crop records → Review harvest per area**, select one or more whole,
current harvest batches from the chosen planting. Enter an inclusive date window, the
harvested ground footprint in square metres, its location and measurement/estimate source,
and a note explaining how the batches belong to that footprint. Enter the footprint **once**
for repeated pickings from the same area. Do not use the production unit's full area unless
the selected harvest actually covers it. A batch mixing multiple footprints must be corrected
into appropriate batch records before it can support a narrower area review.

Select collection coverage explicitly:

- `complete`: the operator reports that the selected batches cover the harvest collected
  from this footprint during these dates.
- `partial`: some of that harvest is missing from the selection.
- `unknown`: the operator cannot establish how much collection is represented.

Complete is a statement about this footprint and period. It does not verify the record,
establish a full season, include unharvested losses, or establish that a crop succeeded.
At least one current batch is required; an absent harvest history cannot establish zero
production. The current implementation supports soil production units, including rainfed
and irrigated fields; it does not assign a ground-area denominator to aquatic systems or
stacked growing surfaces.

Within each product, fresh/dry/as-harvested basis, grade and provenance group, Agronaut sums
the selected mass (g and t converted to kg) or count, then computes:

```text
recorded harvest per hectare = selected quantity × 10,000 / harvested area in m²
```

Mass and count remain separate. For example, two pickings of 10 kg and 500 g from the same
300 m² footprint produce a recorded ratio of 350 kg/ha. With partial collection coverage,
that remains a partial recorded ratio. Selecting multiple grades produces separate ratios;
there is no combined marketable or whole-crop total. Alternative reviews may overlap and
are never summed or ranked.

Each save retains the exact decimal area, selected batch snapshots, planting, timezone,
lifecycle, and available batch IDs in the period. Arithmetic uses exact fractions. JSON
results expose `quantity` and `per_ha` as `{exact, display}` pairs; display values round
half-even to six decimal places. Retain the exact values when a rounded value is insufficient.
No actual harvest, planting, water log, cost or soil state is changed by saving a review.

Correct a review using its latest revision and an explicit reason. Voiding retains evidence;
correcting the latest void restores a review after rechecking current batch eligibility.
A corrected or voided selected batch flags the old review. A change to the set of current
batches in the period, crop/planting date, farm timezone or lifecycle also flags it for review.
These flags ask the operator to reassess the scope; new batches may belong to another
footprint, so they are not silently included. Original ratios remain unchanged until a new
review revision is explicitly saved. Stale batches remain visible in the form and must be
replaced explicitly before a correction can be saved.

`crop get` and `season_review.journal` include `harvest_reviews.history` and `.current` with
live change flags. Season comparisons show current alternatives separately, including their
coverage and area. The page provides original-review inspection and a history download.
Owner export and erasure use the existing crop journal; no schema upgrade is needed.

The offline CLI and scoped `crop_records` tool use these operations:

```sh
agronaut crop describe
agronaut crop record_harvest_review --input review.json
agronaut crop void_harvest_review --input void-review.json
```

Example `review.json` (synthetic placeholders; use real owned cycle and current record IDs):

```json
{
  "cycle_id": "CYCLE-ID",
  "request_id": "north-harvest-review-1",
  "data": {
    "entry": "north-week1",
    "start": "2026-07-01",
    "end": "2026-07-10",
    "area_m2": "300",
    "location": "North plot",
    "area_source": "Operator's ground survey, dated 2026-07-01",
    "coverage": "partial",
    "scope_note": "Both whole batches came from this same footprint; some later pickings are missing.",
    "source": "Collection notebook, pages 12–13",
    "harvest_record_ids": ["FIRST-CURRENT-HARVEST-ID", "SECOND-CURRENT-HARVEST-ID"]
  }
}
```

Area must be a positive plain decimal **string**, with at most 12 integer and six fractional
digits. Select 1–1000 distinct current non-voided harvest records from the same unit/cycle and
within the review dates. Dates must be between planting and today in the farm timezone and
on/before cycle closure. All text sources and scope notes are required. Corrections add
`supersedes_id` and `reason` to the save request. Void requests contain `record_id`, `reason`
and `request_id`. Reusing a request ID returns its original saved response; different data
with that ID is rejected. Use a new request ID for a deliberate correction or reassessment.

## Crop work calendar

Use `/task`, `/task fr` or `/task zh-Hant` in web chat, Telegram, WhatsApp or terminal
chat to enter work one question at a time without a language model. Select a production
unit first; use `/plant` if it has no recorded crop cycle. The French and Traditional
Chinese questions are draft translations pending local review; detailed validation errors
remain English. Narrative titles, notes and sources are stored as supplied.

Choose `plan` to create a task. Select a crop cycle, unique entry name, title, activity,
location, priority, notes and source. Choose `date` for fixed farm-local dates or `planting`
for signed day offsets from planting. The preview shows the resulting work window. These
are operator-supplied plans, not automatically recommended agronomic schedules.

You may also link the record that prompted a task and explain your reason. In `/task`,
enter `none` or `crop_record:ID`, `measurement:ID` or `water_day:ID`; the calendar form
offers available sources. Supported crop records are scouting, stage, transplant,
fertilizer and harvest entries. The source must be current, non-rejected and belong to
the same unit and crop cycle. A saved task retains the exact source snapshot, including
the measurement's quality review. The guided preview displays the selected source;
technical source field labels remain English in all three languages.

New `/task` drafts list eligible sources five at a time, with dates, record types,
provenance, quality and short supplied details. Choose a displayed number, enter a full
reference, or use `none`. At this question, `next` and `previous` change pages; `refresh`
reloads current sources and returns to the first page. These are plain text replies.
Numbered choices persist across a restart, language change and newly added records until
you refresh or leave this question. Each number is accepted only on its displayed page.
The next step shows the selected record before asking why it prompted the task.

If a numbered source is corrected, voided, rejected or receives a new quality review after
the list was displayed, selection is rejected. Reply `refresh` and review the list again.
An empty list allows `none`; it does not claim there are no field problems. `/back` edits
the preceding answer rather than navigating pages; returning to this question rebuilds
the source list. Browsing does not create a task or an actual activity record.

A link records the operator's explanation. It does not establish a diagnosis, confirm
treatment suitability or prove completion. Forecast measurements retain their forecast
provenance and cannot serve as actual completion evidence. Changed, rejected or voided
sources flag the task for review, including completed and cancelled tasks. Changes do
not rewrite the retained snapshot or silently select a replacement. A source changed
during guided entry requires `/back` to select and review it again.

Choose `completed`, `cancelled` or `reopen` to update a saved task. New drafts list current
tasks in the selected unit, including future work and resolved tasks that may be absent
from `/overview`'s attention list. Each option shows its title, status, activity, crop and
planting date, entry name and location. `reopen` lists only completed or cancelled tasks.
Choose a displayed number or enter an exact current task record ID. Use `next`, `previous`
and `refresh` as in source selection. Numbered choices retain their identities across
restart, language changes and new records; a revised task requires refresh and selection
again. Browsing leaves task status unchanged.

The list covers the crop cycles known when this draft started. If a new crop cycle was
added afterward, cancel and restart `/task` to include it. If the list is empty, `/back`
can change the action or `/cancel` can end the draft; no task is selected automatically.
An exact current ID remains accepted even when it is not on the displayed page.

Completion requires an actual date no later than today in the displayed farm timezone.
Enter `none` for an operator report without
linked evidence, or `crop_record:ID` / `water_day:ID` to link an existing actual record.
Linked evidence must be current, accepted, in the same unit and cycle, match the activity,
and have the same actual date. Each resolution requires a reason and source. Completing a
task does not create water, fertilizer, harvest or other quantity records or send reminders.

New drafts also list matching completion records five at a time. Choose a displayed number,
enter an exact reference or choose `none`. `next`, `previous` and `refresh` work as in source
selection. Candidates use the same completion validator as saving: matching activity and
actual date, same unit and cycle, current non-void records, and non-rejected water records
with positive net irrigation. Measurements and forecasts are not completion evidence.
Maintenance and other tasks have no mapped quantity evidence; an unlinked report remains
available. An empty list does not prove that the work was not performed.

The selected record is shown in a short preview and retained in the draft. Corrections,
voiding or rejection after the list was displayed require refresh and another selection.
Changes after selection block saving; `/back` returns to the evidence question to review
the current records. Backtracking to change the completion date rebuilds the list for that
date. Linking an existing record does not duplicate its quantities.

Review the preview and explicitly reply `save`, `enregistrer` or `儲存` in the selected
language. `/back` edits the previous answer, `/cancel` discards the draft, and `/task`
resumes it after a restart. Drafts are scoped to the selected unit. Other record-entry
drafts cannot start in that unit until this one is completed or cancelled. Changed crop
or lifecycle context requires restarting the draft; changed task revisions or evidence
are rejected before saving. Existing task-plan corrections remain available through the
crop-calendar form and `plan_task` API.

The offline CLI exposes the same intake with `agronaut crop start_task`, `get_task`,
`answer_task`, `save_task` and `cancel_task`, using `--input` JSON. Answers require
`expected_revision`; saves require `draft_id` and `expected_revision`. Schema v23 adds
owned task drafts with a private pre-upgrade backup. The task record, immutable save
receipt and draft deletion are one transaction; retries return the original result even
after later task revisions, without deleting a newer draft. Account export and erasure
include task drafts.

Schema v24 marks the task-source snapshot and review semantics so older readers cannot
silently omit source-change flags. Existing guided drafts without an intake version
retain their original questions and create unlinked tasks. Existing intake v2 drafts keep
reference-only source selection. Intake v3 adds the numbered source list and retains
full-ID task selection. Intake v4 adds numbered task selection. New drafts use intake v5
with numbered source, task and matching completion-record lists. Earlier draft versions
retain their original completion questions and exact-reference inputs.
These lists are part of the owned draft and follow its export and erasure rules.

In **Crop records → Crop work calendar**, supply a task title, activity, location, instructions,
priority and source. Choose explicit dates or a window relative to the planting date. Day 0
is planting; negative offsets represent preparation. The calendar supports scouting, stage
checks, irrigation, fertilizer, transplanting, harvest, maintenance and other work across soil,
hydroponic and aquaponic crop cycles. These are operator-supplied plans, not recommended
treatment dates or automatic equipment instructions.

Example `work-plan.json` for `agronaut crop plan_task --input work-plan.json`:

```json
{
  "cycle_id": "SAVED-CYCLE-ID",
  "request_id": "work-plan-001",
  "data": {
    "entry": "inspection-001",
    "title": "Inspect the north row",
    "activity": "scouting",
    "due": {"basis": "planting", "start_offset_days": 5, "end_offset_days": 7},
    "location": "North row",
    "notes": "Synthetic example: record observed symptoms and sample counts",
    "priority": "normal",
    "source": "Synthetic operator work plan"
  }
}
```

The example window is illustrative. Use a schedule supplied for your actual crop and site.
Offsets must be whole days from -365 to 3650, with the end at or after the start. Alternatively,
use `"due": {"basis": "date", "start": "2026-10-20", "end": "2026-10-21"}`.
Priority is `low`, `normal` or `high`. Source and instructions are required.

For API/CLI plans, the optional `data.trigger` is
`{"kind":"crop_record","id":"SOURCE-RECORD-ID","reason":"Inspect the reported symptoms"}`.
Use `get.calendar.trigger_candidates` to discover eligible sources. Measurement links
also require `review_revision_id`, using the current review ID or `null` when no review
exists. The server creates `trigger_snapshot`; callers cannot supply their own snapshot.
When revising a linked plan, explicitly retain or replace `trigger`, or set it to `null`
to remove the link. Omitting it is rejected. Historical task revisions keep their original
links. The calendar, farm overview and season review expose the link and later review
flags; owner exports retain the complete snapshots.

`get.calendar` shows upcoming, due and overdue plans as of the farm's local date. If the crop
or planting date is corrected, pending tasks are flagged for review. Relative windows display
their recalculated dates alongside the originally saved window; absolute dates stay fixed.
Save an explicit plan revision to accept the current crop/date assumptions. A pending crop
activity whose window extends beyond a closed cycle is also flagged. Maintenance and other
work can cover cleanup after closure. Planning or completing a generic task does not constrain
crop closure; linked physical records retain their own activity-date checks.

To revise a pending plan, send its full data with the latest `supersedes_id` and a new
`request_id`. Entry and cycle IDs stay unchanged. Completed or cancelled tasks must be
reopened before their plan can be changed. Every revision remains in the journal.

Use **Task to complete, cancel or reopen**, or `agronaut crop resolve_task --input task-status.json`:

```json
{
  "record_id": "LATEST-TASK-RECORD-ID",
  "status": "completed",
  "completed_on": "2026-10-19",
  "reason": "Operator reports inspection finished",
  "source": "Synthetic work log",
  "request_id": "work-complete-001"
}
```

Completion requires an actual date through today in the farm timezone. Without a linked
activity record, this is an operator completion report only. It never creates water,
fertilizer, transplant or harvest quantities. Optionally include
`"completion_record": {"kind": "crop_record", "id": "ACTUAL-RECORD-ID"}` or use
`"kind": "water_day"` for irrigation. The record must belong to the same unit and cycle,
match the activity and completion date, and be current, nonvoid and non-rejected. Irrigation
evidence must record positive net irrigation. The link preserves the exact record snapshot;
later corrections, voids or rejection trigger review rather than silently changing that link.
An operator-reviewed or reported record is not independent verification of work.

Use `status: "cancelled"` to cancel, or `status: "planned"` to reopen, with the latest
`record_id`, reason, source and new request ID. Omit completion date and link for these states.
Completed and cancelled tasks retain their saved work windows; completion timing is compared
with that saved window. Reopening restores pending-window calculation and review flags.
Retries with the same request ID and payload return the original result; stale revisions or
conflicting retries fail. Task history is included in crop downloads, account export and
erasure. Season reviews and comparisons include calendar summaries. No notifications are sent.

## Timestamped field measurement entry

### Guided raw readings in chat

Use `/measure en`, `/measure fr`, or `/measure zh-Hant` with a selected soil, hydroponic
or aquaponic unit. French and Traditional Chinese prompts are drafts awaiting local review.
Choose a crop cycle or explicitly leave the reading unassigned, then select a quantity,
enter its value, observation time, source/sampling details and provenance. Review the
preview and reply `save` (`enregistrer` / `儲存`) before any observation is saved.

The choices use the existing typed observation units: rainfall and irrigation depth in mm,
irrigation volume in L, soil depletion in mm, soil water content in m³/m³, air temperature
in °C, flow in L/h, pressure in kPa, harvest mass in kg and runtime in hours. Enter a plain
decimal using a dot, without thousands separators or a unit suffix. Soil water content
is a fraction from 0 to 1; 25 percent must be supplied as 0.25. Only air temperature may
be negative. Unknown values cannot be recorded as zero. The flow does not convert units
or infer a number from an image.

Enter `YYYY-MM-DD HH:MM` in the displayed farm timezone, or an ISO timestamp with an
explicit UTC offset. Local times during a clock change are checked by conversion to UTC
and back. Ambiguous or nonexistent local times are refused; consult the original record
and supply its explicit offset when known. The preview shows the resolved timestamp and
its farm-local equivalent. Future timestamps are refused by this actual-reading flow.
Point measurements may predate planting but cannot fall after a selected cycle's closure.
The advanced `record_measurement` API retains its separate forecast-provenance support.

State the instrument or source, sampling location/depth and represented interval. For
irrigation depth, state whether it is gross or net. Choose `measured`, `reported` or
`estimated`; the French and Chinese equivalents are also accepted. Every saved reading
starts unverified. A raw reading does not establish a field average, a daily water total,
a harvest batch, or a new soil state. Review and adoption remain separate existing actions.
If a reading was wrong, record a new one and review the original; old readings are immutable.

`/measure` resumes the draft, `/back` edits, `/cancel` discards it, and `/reset` removes the
current unit's draft. Unit setup has priority, and measurement entry cannot run alongside
planting, scouting, daily-water entry or field planning for the same unit. Language changes
preserve answers but invalidate earlier review revisions. Changed farm timezone or crop
details require cancelling and restarting. The workflow needs no chat model and is shared
by terminal, web chat, Telegram and WhatsApp; transport tests use mocks.

The crop CLI and scoped tool expose `start_measurement` (`{language?}`), `get_measurement`
(`{}`), `answer_measurement` (`{answer, expected_revision, back?}`), `save_measurement`
(`{draft_id, expected_revision}`) and `cancel_measurement` (`{expected_revision}`). Saving
requires explicit review and confirmation. The observation, immutable save receipt and
draft deletion share one transaction. Retrying the same receipt returns the original
result, even after a quality review, and cannot delete a newer draft.

Schema v21 adds drafts and receipts with the existing transactional migration and private
backup. Account export includes `measurement_intakes` and `measurement_intake_receipts`,
including the originally entered time and the timezone used to interpret it. Erasure
removes owned drafts, receipts and readings.

### Structured measurement entry

The crop-record service now supports `record_measurement` with
`{cycle_id?, request_id, data:{quantity, value, unit, observed_at, source, provenance}}`.
`observed_at` requires an explicit UTC offset. The `describe` operation lists the allowed
quantities and units. Values are supplied by the operator, not inferred from photos or
model output; new readings are unverified and quality reviews remain separate.
This is available in the crop-record web form, scoped chat and
`agronaut crop record_measurement --input reading.json`. Readings may be unassigned
to a crop cycle while remaining owned by the selected unit. Stable request IDs prevent
duplicate retries; conflicting reuse fails. Correct values with a new reading and an
explicit quality review of the original when appropriate. See
[soil-state reconciliation](FIELD_CALCULATIONS.md#soil-measurements-and-state-reconciliation)
for using eligible soil readings in a saved water budget.

### Importing a notebook or sensor export

Use **Crop records → Import field measurements from CSV** to download the header,
upload a mapped file, preview every reading and explicitly save the reviewed import.
The importer accepts 1–1,000 rows for one selected unit, with a UTF-8 file limit of
2 MB. It supports soil, hydroponic and aquaponic units without a model or network.
Prepare the mapping from the original data dictionary; Agronaut does not guess column
meanings, convert units, infer missing readings or resolve local timestamps here.

The CSV requires each of these columns exactly once, in any order:

```csv
request_id,cycle_id,quantity,value,unit,observed_at,source,provenance
```

Use canonical quantities/units from `agronaut crop describe`. `value` is a decimal
number (scientific notation is accepted), with no unit suffix or thousands separator.
Unknown, blank, non-finite, out-of-range and underflowing values are rejected, not
replaced with zero. CSV quoting supports commas and line breaks in `source`.
`observed_at` must include a UTC offset; original offsets are retained. An empty
`cycle_id` explicitly leaves the reading unassigned; a supplied ID must belong to the
selected unit. Every source should identify the notebook/instrument, location, depth
and represented interval when applicable. Provenance remains measured, reported,
estimated or forecast; importing a forecast does not turn it into an actual event.

Choose a stable `request_id` for each original record, such as
`notebook-2026-07:row-42`. Keep it unchanged when retrying or splitting the same export.
IDs share the namespace used by individual `record_measurement` calls within that unit.
Repeated identical requests reuse the original observation; conflicting reuse fails.
Duplicate IDs within a single file are rejected. Different IDs represent distinct
readings even when their values match, so changing IDs can create duplicates.
The [synthetic CSV example](../examples/measurements.csv) illustrates the format only;
replace its data and identities with actual records before using it for a farm.

The same flow is available offline:

```sh
agronaut crop preview_measurements --csv readings.csv
# Inspect the returned entries, then copy preview_token into the command below.
agronaut crop import_measurements --csv readings.csv --preview-token COPIED_PREVIEW_TOKEN
agronaut crop list_measurements
```

For JSON/API use, `preview_measurements` accepts
`{rows:[{request_id,cycle_id?,data:{quantity,value,unit,observed_at,source,provenance}}]}`.
`import_measurements` takes those same `rows` plus the returned `preview_token`.
Pass these objects with `--input` instead of `--csv`. Scoped chat must show the preview
and receive an explicit saving request before calling the import operation.

Preview validates every row without inserting observations. It shows `new` or
`existing`, the reading and any existing quality review. IDs for new rows are
deterministic; their preview recording timestamps are provisional until saved.
The token binds the exact payload, owner and selected unit; it is a change-detection
token, not an approval signature or a reservation of database state. Editing rows or
switching units requires another preview. Save rechecks current ownership, cycle
closure and ID conflicts in one transaction. A late failure rolls back all new rows
from that import. Existing observations and their quality histories remain intact.

Each new reading starts unverified. Review it through **Review field measurements**
before using it as evidence where required. Imported readings appear in the ordinary
measurement list, assigned crop journal and account export, and are removed by account
erasure. Imports add no separate batch table or schema migration. They do not create
daily water totals, harvest batches, soil-state adoption, calculations or equipment
commands; those operations retain their explicit workflows.

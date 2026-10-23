# Farm resources: scenarios and shared budgets

**Farm resources** allocates explicitly requested work across units in one farm using
shared water, pump-time, labor-time and cash pools. The base calculation is experimental
aggregate accounting. Optional timing checks audit supplied work intervals, equipment and
labor availability, and reservoir balances. Start-time proposals search explicit permitted
windows and pass those same checks. These features do not establish agronomic suitability,
hydraulic performance or crop outcomes.

In the app, select **Farm resources**, then choose the farm explicitly. No model provider is
needed. Add resource pools and work entries, enter the planning period and source, then
evaluate or save the scenario. Draft changes are local to the page; evaluation is read-only
and **Save resource scenario** stores an alternative. The separate **Shared budgets and
commitments** controls below explicitly reserve allocated quantities. Farm changes clear the draft.

## Work within irrigation zones

[Irrigation zones](FIELD_CALCULATIONS.md#create-irrigation-zones-within-a-field) appear as
independent production units under their parent fields. Assign each job to the intended
zone ID and supply that zone's work quantities and crop cycle. A saved scenario retains
the zone definition; zone area does not automatically generate water or equipment demand.
Parent records and sibling-zone records are not inherited or aggregated.

Scenarios containing both a parent field and one of its zones display an overlap warning
and save the affected IDs in their snapshot. Review whether the jobs duplicate work before
reserving resources. Both entries remain available because they may describe different
tasks. Allocation uses the quantities explicitly supplied; it does not deduplicate jobs,
sum field areas, detect geometric overlap or establish hydraulic compatibility.

## Shared budgets and explicit commitments

Saved scenarios remain alternatives until the operator chooses **Commit allocated resources**.
Use **Shared budgets and commitments** to create one named budget for each distinct supply
and period. This allows separate field, hydroponic and aquaponic plans to use the same balance.
For example, a 1,000 L budget with a 600 L field commitment has 400 L left for another unit;
a concurrent attempt to reserve another 600 L is rejected.

Each budget has a stable `resource_key`, kind, unit, inclusive local-date period, decimal
capacity and source. Reuse the key for the same supply across periods. Keys are normalized
to lowercase and allow 1–80 ASCII letters/numbers/dots/underscores/hyphens. Distinct periods
for the same key cannot overlap; overlap is checked in UTC even if the farm timezone changes.
Kind and unit must remain consistent. Capacity is the total assigned to this ledger for the
period, after commitments **outside Agronaut**. Do not subtract existing Agronaut commitments
again; the ledger already counts them. Resource aliases and overlapping real-world supplies
cannot be identified from names alone, so do not give one source several keys.

Select a saved scenario and explicitly map **every** pool to a current budget revision with
the same kind and unit. Each budget must cover the whole scenario period in the same timezone.
Several scenario pools may map to one budget; their assigned quantities are summed before
checking its remaining capacity. The form shows included work by unit and the allocated and
unallocated blocks. A partial allocation requires explicit acceptance; unallocated blocks
are never reserved. Empty allocations, ended scenario periods, changed plan evidence, and
saved failed timing checks or unsuccessful schedule searches cannot be committed.

Committing checks every mapped budget and writes one immutable receipt in a single SQLite
transaction. Concurrent writers cannot both spend the same remaining allowance. Only one
scenario in a parent/child plan family may have an active commitment. Independently created
duplicate plans still require operator reconciliation; no similarity-based work deduplication
is inferred.

To replace an active commitment, select it, choose the reviewed scenario and current budget
revisions, and provide a source and reason. The old reservation is excluded and the replacement
is checked in the same transaction. If any check fails, the old reservation stays active.
To withdraw unused or cancelled work, select **Release commitment** and provide a source and
reason. Release returns its allowance to the ledger; it does not record consumption or refill
physical water. **Keep used quantities counted against their original period**, using the
explicit settlement workflow below when totals are known. Commitments do not expire automatically, and old-period amounts
do not count against a separate non-overlapping budget for a later period.

Budget corrections preserve resource identity, kind, unit and period, and require the latest
revision plus a reason. Capacity cannot be reduced below charged reservations and recorded use;
an increase may partially repair an existing overdraft. An existing
budget retains its original timezone when corrected; newly created budgets use the current
farm timezone. Budget revisions, crop/lifecycle changes and changed linked irrigation evidence
are visible as review flags. They never silently release a commitment or rewrite its original
plan and budget snapshots. A released commitment can be explicitly restored/replaced after
fresh validation. Downloads include budget and commitment history, balances and review flags.

These are reservations of **declared quantities within Agronaut**. Pump and labor hours are
aggregate allowances. Use the separate equipment calendar below to check and book named
physical items across plans. Quantity commitments alone do not enforce simultaneous equipment
use, reservoir trajectories, hydraulic feasibility, named worker availability or physical
supply. Optional per-scenario timing checks still apply only to that scenario. No commitment
or release creates an actual water log, measurement, purchase, task completion or device command.

The offline CLI and scoped `farm_resources` tool expose `save_budget`, `ledger`, `commit_plan`
and `release_commitment`. Call `describe` for the full operation contracts. Synthetic budget:

```json
{
  "request_id": "borehole-week43-v1",
  "data": {
    "resource_key": "borehole",
    "kind": "water",
    "unit": "L",
    "period": {"start": "2026-10-19", "end": "2026-10-25", "timezone": "UTC"},
    "capacity": "1000",
    "source": "Synthetic operator allowance net of outside commitments"
  }
}
```

Save with `agronaut resources --farm FARM-ID save_budget --input budget.json`. Use returned
current budget revision IDs when mapping a plan's pool IDs, for example:

```json
{
  "plan_id": "SAVED-PLAN-ID",
  "bindings": {"shared-water": "CURRENT-BUDGET-REVISION-ID"},
  "source": "Operator explicitly commits the allocated work",
  "accept_partial": false,
  "request_id": "field-commitment-1"
}
```

Use `commit_plan --input commitment.json` with the same explicit `--farm`. Replacements add
`supersedes_id` (the latest commitment revision) and `reason`. A release request contains
`commitment_id`, `source`, `reason`, and `request_id`. Budget corrections add `supersedes_id`
and `reason` to the budget request. Reuse a request ID only for a retry: it returns the original
receipt even if that receipt has since been superseded. Conflicting reuse is rejected.
`ledger` is read-only and includes `budgets` with `committed`/`remaining`, `budget_history`,
all `commitments`, and `active` commitments. Amounts use exact integer micro-units; there is
no floating-point overshoot, rounding credit, or currency conversion.

Schema v12 adds `farm_resource_budgets` and `farm_resource_commitments` through the existing
transactional migration and restricted-permission backup. Owner exports include
`resource_budgets` and `resource_commitments`; erasure cascades both histories. Existing
scenario and actual-activity data are preserved. No independent field-validation claim is
implied by passing the ledger's accounting checks.

## Settle recorded resource usage

Select the latest commitment under **Shared budgets and commitments → Commitment to review**,
then use **Settle recorded usage**. Record complete usage totals for the committed work that
has finished or stopped. Explain how the totals cover that work, including maintenance and
all pools mapped to each budget. Remaining future work belongs in a separate plan. Settlement
does not mark crop-calendar tasks completed or create irrigation, measurement or cash records.

Supply actual start/end dates inside every original budget period and through today in the
saved budget timezone. These dates can differ from the planned work window. A later farm
timezone change does not move old usage to a different day. Transferring consumption to another
budget period is not supported by this operation; do not falsify dates to make it fit.

For **every budget root**, explicitly enter a complete used amount or leave the amount unknown:

- A known nonnegative decimal amount stays charged as recorded use and replaces that budget's
  reservation for this commitment. Enter the budget's original unit: L, hours, or its currency.
  Manual totals require a source and `measured`, `documented`, `reported` or `estimated`
  provenance. These labels describe supplied evidence, not an independent audit. Cash totals
  concern the amount charged to the work, not revenue or profit; no currency conversion occurs.
- Blank in the app (`null` in JSON) means unknown. It retains at least the original allowance
  and the previous charge as reserved. Provide an explanation in the source. A later correction
  from known to unknown does not make previously charged resources available again.
- An explicitly known `0` releases that budget's allowance. It is different from an unknown
  total. If the original allowance was zero and actual use is unknown, the unresolved count
  remains visible; a zero held allowance does not establish zero physical consumption.

For example, settling a 600 L reservation with 450 L of complete recorded use keeps 450 L
charged and returns 150 L of allowance. Reporting 1,200 L against a 1,000 L budget is accepted
as an actual-use report: the ledger shows `remaining: -200`, `overdrawn: 200`, and `available: 0`.
It does not reject the evidence to keep the plan apparently feasible or invent extra supply.
Further reservations must fit the resulting balance. Other active reservations are retained.

The ledger separates `reserved`, `recorded_use`, signed `remaining`, nonnegative `available`,
and `overdrawn`. `committed` remains their total charge (`reserved + recorded_use`) for existing
clients. `unresolved_usage_count` counts current unknown totals for that budget. Recorded-use
totals include only known amounts; the remaining allowance is conditional on declared budgets
and retained reservations, not a verified physical inventory. `settlements` includes current
`settled` and `partially_settled` records; `active` continues to contain ordinary reservations.

### Link a complete set of irrigation event outcomes

For budgets covered entirely by recorded irrigation events, optionally select current outcome
reviews from the **exact committed scenario**. The form shows the event's original exact
totals, dates, provenance and quality. Supply their exact combined amount. Agronaut requires
one review for every positively allocated job/pool mapped to this budget, with no duplicate
readings, missing events, rounding, or extra unlinked usage. Provenance becomes `linked_outcomes`;
each original reading's provenance and quality remain in the snapshots.

A review for a parent, child, or alternative scenario is not automatically a review for the
committed scenario. If outcome reviews cover only part of the committed work, or the budget
includes other kinds of work, use a separately sourced **complete manual total** instead.
Quantities retain the ledger's maximum 15 integer and six fractional decimal digits. A linked
total that cannot be represented exactly at that precision is rejected; no rounding credit is
created. Unknown totals cannot claim linked evidence as a complete total.

Superseded outcomes and changed/rejected measurement evidence cannot be newly linked. Later
outcome or quality changes flag the saved settlement, preserve its original charges, and block
new commitments against affected budgets until an explicit correction reconciles the evidence.
`settlement_evidence_changed` identifies that condition even when the numeric balance is positive.
A measurement cannot appear in two current linked settlements. Manual duplicate totals cannot
be identified automatically and require operator reconciliation. Linking existing outcomes does
not create or modify those outcomes or their measurements.

### Corrections, late reports and history

Every settlement and correction requires a source, attribution, reason, and latest commitment
revision. It replaces previous charges atomically. An older request retried with the same ID
returns its original receipt; a conflicting retry or stale predecessor is rejected. Original
allowances, plan/budget snapshots, actual-use declarations, linked outcomes and correction
history remain available in the ledger download and owner export.

Once a record is settled or partially settled, ordinary **Release commitment** or reservation
replacement cannot erase its used quantities. Correct the settlement explicitly. A late
actual-use report can also be recorded against a released commitment; it charges usage again
and flags any other current commitment/settlement in that plan family for reconciliation.
The ledger preserves both records rather than deciding whether they describe duplicate work.

The CLI and scoped tool expose `settlement_inputs` and `settle_commitment`. Read
`settlement_inputs` with `{"commitment_id":"LATEST-COMMITMENT-ID"}` to inspect original budget
roots, plan-pool mappings and current outcome candidates. Synthetic manual settlement:

```json
{
  "commitment_id": "LATEST-COMMITMENT-ID",
  "request_id": "field-use-v1",
  "reason": "Operator reconciled the finished work",
  "data": {
    "actual_start": "2026-10-20",
    "actual_end": "2026-10-20",
    "attribution": "Complete source-water total for the committed event, including maintenance",
    "source": "Synthetic operator reconciliation",
    "usage": {
      "BUDGET-ROOT-ID": {
        "amount": "450",
        "provenance": "reported",
        "source": "Synthetic complete event notebook total",
        "outcome_ids": []
      }
    }
  }
}
```

Run `agronaut resources --farm FARM-ID settle_commitment --input settlement.json`, then read
`ledger`. Include every budget root shown in the commitment; these are stable roots, not
scenario pool names or the latest budget revision IDs. Unknown rows require `amount: null`,
`provenance: null`, a source explanation, and `outcome_ids: []`. Linked rows use
`provenance: "linked_outcomes"` and the selected current review IDs. Corrections use the new
latest commitment ID and a new request ID. All sources, amounts and dates must be explicit.

Schema v13 adds the settlement accounting semantics to the existing ledger payloads. The
backed-up version upgrade preserves v12 records and causes older readers to refuse the new
database, preventing them from treating settled usage as free capacity. No new table is needed.
Owner erasure removes settlements through the existing commitment-history cascade.

## Describe the shared resources once

Each pool has a unique name, kind, unit, available amount and source. Availability is the
amount available to this scenario over the entire period, after commitments outside it.
Use one pool for a shared source: listing the same tank under two names can double-count
the water. Agronaut cannot establish physical pool identity from a name alone.

| Kind | Unit | Meaning |
|---|---|---|
| `water` | `L` | Available supply or withdrawals for this period |
| `pump_time` | `h` | Available pump operating time |
| `labor_time` | `h` | Available person-hours for the declared crew |
| `cash` | Three uppercase currency letters | Available funds in that currency |

Keep distinct supplies, equipment, labor groups and currencies in separate pools. Amounts
are nonnegative decimal strings, with up to 15 integer and 6 fractional digits. Zero means
explicitly no availability; unknown is not zero and cannot be evaluated. No unit or currency
conversion occurs. Water circulation and supply withdrawals are different quantities: define
the pool and per-work requirements consistently.
Changing a draft pool's type or currency clears its work requirements. Re-enter those amounts
explicitly before evaluating, so existing quantities cannot be reinterpreted in a new unit.

Water in storage may contribute to a declared water budget. Base allocation does not simulate
storage levels or work timing; the optional checks below require additional inputs for those.
Pump time is not a flow or pressure check. Person-hours alone do not assign particular workers
to concurrent jobs.

## Define meaningful work blocks and priorities

Each job belongs to an owned production unit in the selected farm, optionally to one of its
crop cycles. Soil, hydroponic and aquaponic units can share a scenario. Supply a description,
what one block means, number of blocks requested, minimum useful number, priority and source.
For every resource pool, state the amount needed **per block**. Enter explicit zero for a
pool the job does not use. A missing requirement is unknown and fails validation.

A block is an operator-defined indivisible package, such as one specified watering event
or one defined maintenance task. Agronaut does not derive a treatment, irrigation volume,
cost or block size. Use a minimum equal to the requested number for all-or-nothing work.
Use a smaller minimum only when that amount of partial work is useful. All block counts
and priorities are whole numbers from 1 to 1,000,000; minimum cannot exceed requested.
Priorities must be unique, with lower numbers first. Edit the draft to resolve ties explicitly.

The `priority-blocks-v1` algorithm:

1. Visit jobs in ascending priority order.
2. Find the maximum whole number of requested blocks that fits **every** remaining resource.
3. If fewer than the job's minimum fit, allocate none and keep those resources available.
4. Otherwise allocate that many blocks and subtract all of their resource use.
5. Continue to lower-priority jobs and show full, partial or unallocated requests.

The calculation uses integer micro-units, so decimal rounding cannot allocate an extra block.
Results retain per-job assigned quantities, limiting resources and reasons, plus each pool's
requested, assigned, remaining and unmet requested quantities. Unmet quantities describe
unallocated work; they are not a recommendation to purchase that amount of each resource.
Another pool may be the actual constraint. Priority order and block definitions affect the
result. The algorithm does not maximize yield, profit, fairness or the number of completed jobs.

For a synthetic example, a shared pool holds 1,000 L. The first-priority job requests one
700 L block; the next requests one 500 L block. The first receives its block, the second
receives none, and 300 L remain. Neither job can silently spend the other's allocation.
With 2,000 L available instead, both fit and 800 L remain. These are arithmetic examples,
not irrigation prescriptions.

## Shared equipment calendar

The **Shared equipment calendar** coordinates named physical items across saved plans and
manual calendar blocks. Register each pump, tractor, implement, vehicle or other item once
with a stable key, name, kind, availability status and source. One record represents one
physical item. Keys follow the same lowercase ASCII naming rules as budget keys, but equipment
and budgets have separate identities. Different aliases for the same item cannot be detected.

To book a scenario, first save passing timing checks or a successful schedule proposal.
Choose **Timed scenario to book**, then assign equipment to every work interval. Each interval
requires exactly the number of distinct physical pumps declared by its concurrent pump-time
counts, summed across pump pools. Other equipment can be assigned explicitly as needed; an
empty list means no equipment is assigned to an interval with no pump requirement. Each
selected item is held for that interval's entire duration. A pump count is not evidence that
its pressure, flow, connections or other specifications suit the work: supply an operator
review source. Partial allocations require explicit acceptance and book only allocated work.

Bookings check current farm, crop and linked-design evidence and are written atomically.
The same equipment cannot overlap another assigned interval within the request, an active
booking from another plan, or a manual calendar block. Endpoints may touch: 08:00–09:00 and
09:00–10:00 can use the same pump. No travel, setup, changeover or cleanup time is inferred;
include those explicitly in the declared intervals or add calendar blocks. One plan family
can have only one active scenario booking. To change alternatives or equipment assignments,
replace the latest booking and give a reason. A failed replacement leaves the old intervals
held. Two concurrent requests cannot both acquire an overlapping interval on one item.

Choose **Calendar block** for maintenance, equipment used outside Agronaut, travel or other
unavailability. Select one or more items, a title, start/end timestamps and source. Blocks
can also record maintenance on equipment marked unavailable; they do not establish suitability
for work. Supply timestamps with explicit offsets matching the farm timezone. UTC comparisons
handle repeated daylight-saving hours and farm timezone changes. Intervals need positive
elapsed time and an end in the future. A booking can cover ongoing work, but it is still a
calendar declaration, not evidence that the work occurred.

Equipment corrections preserve the stable key and kind and require a reason. Mark an item
unavailable when appropriate; existing bookings remain held and show review warnings, while
new scenario bookings cannot assign it. Superseded equipment revisions must be explicitly
reselected in the form. Old timestamps and equipment/plan snapshots never move or update
silently when the farm timezone, equipment status or crop evidence changes.

**Release equipment booking** requires a source and reason and preserves history. Expired
bookings also remain in history; their past intervals do not conflict with later intervals.
The downloadable calendar includes equipment revisions, booking revisions, original snapshots
and review flags. Farm export and erasure include the same records.

Equipment bookings and quantity commitments are separate actions. Booking a pump does not
reserve water, labor hours or cash; releasing its calendar booking does not release those
quantities or alter recorded use. Use both workflows when a plan needs both kinds of allocation.
Neither workflow creates actual activities or device commands. The calendar checks declared
equipment occupancy, not full farm feasibility: operator qualifications, shared worker shifts,
hydraulics, unknown outside use and storage across plans still need separate checks.

The stdlib CLI and scoped tool expose `save_equipment`, `equipment_calendar`, `equipment_inputs`,
`book_equipment`, `reserve_equipment` and `release_equipment`. Call `describe` for schemas and
`equipment_inputs` with a saved timed plan ID to list its work IDs, times and pump counts.
For example, a booking request for two sequential work intervals is:

```json
{
  "plan_id": "SAVED-TIMED-PLAN-ID",
  "assignments": {
    "field-run": ["CURRENT-PUMP-REVISION-ID"],
    "greenhouse-run": ["CURRENT-PUMP-REVISION-ID"]
  },
  "source": "Operator reviewed pump suitability and full intervals",
  "accept_partial": false,
  "request_id": "equipment-booking-1"
}
```

Identical request retries return the original receipt; conflicting reuse fails. Replacements
add `supersedes_id` with the current booking revision and a nonempty `reason`. These are
synthetic identifiers to replace with owned records, not equipment recommendations.

## Save and compare alternatives

Saved scenarios are immutable. They preserve the canonical inputs, model version, result,
farm, unit and crop-cycle snapshots, source, creation time and optional parent scenario.
**Use as starting scenario** copies a saved scenario into the editable draft. Saving that
draft creates an alternative linked to its parent. Existing allocations remain unchanged.
These alternatives belong to the same work family, including after its usage is settled.
If an associated crop or its lifecycle changes, reading the old scenario flags that change
for review. The flag does not recalculate or silently accept the new assumptions.

Compare 2–10 explicitly selected scenarios from the same farm, period and timezone. Inspect
the sources, pool availability, per-block requirements and priority changes alongside their
allocations. Block counts from different block definitions are not interchangeable. Comparisons
do not add the scenarios together or pick a best option automatically.

**Saving a scenario does not reserve resources.** Each work family needs an explicit commitment
against shared budgets. Saving does not record water applied, labor performed, payments, harvest or completed
calendar tasks, and does not control equipment or send notifications. Actual activities
remain in their respective journals. Missing jobs or omitted physical resource needs limit
what the calculation proves; a complete allocation only fits the declared aggregate limits.

## Start separate work from a saved scenario

For another event or round of work, select **Start separate work from this scenario**.
The form copies the resource pools and jobs for review, clears the period and plan source,
and requires a reason explaining the separate work. Review the copied units, crop cycles,
resource limits and per-block needs before saving. Linked irrigation designs retain their
original event dates; a new period requires an explicitly reviewed design job for that date
or a detached link with an independent source. Dates and quantities are never shifted automatically.

The saved plan starts a new work family with `parent_id: null`. Its `new_work` metadata keeps
the source scenario ID, title, period, creation time and reason. Current farm, unit, cycle
and design inputs are validated and allocations calculated afresh. Previous timing checks,
schedule proposals, commitments and actual usage are not copied. Subsequent edits to this
draft create alternatives within the new work family. **Start blank work plan** clears the
draft while preserving saved scenarios and ledger history.

Saving separate work leaves all existing budget charges intact. For example, if previous
work used 450 L of a 1,000 L budget, a new commitment still has only 550 L available. A new
600 L scenario can be saved for analysis, but its commitment is rejected until the allocation
fits the available budget. Separate work can occur on the same day; the operator must declare
that it represents additional work. Similar names or inputs do not prove that work is distinct.

CLI and tool callers use `save_new_work` with the complete reviewed plan:

```json
{
  "template_id": "SAVED-SCENARIO-ID",
  "data": "REPLACE WITH COMPLETE PLAN OBJECT",
  "reason": "Separate second irrigation event",
  "request_id": "unique-new-work-request"
}
```

The template must belong to the selected farm. Repeating an identical request ID returns
the original saved record; conflicting reuse is rejected. For later alternatives, use
`save` with the new plan's ID as `parent_id`.

## CLI and chat

The standard-library CLI exposes the same owned-farm workflow:

```bash
agronaut farm list
agronaut resources --farm SAVED-FARM-ID describe
agronaut resources --farm SAVED-FARM-ID evaluate --input resource-request.json
agronaut resources --farm SAVED-FARM-ID save --input resource-request.json
agronaut resources --farm SAVED-FARM-ID list
```

Use an operation object in `resource-request.json`: `{"data": PLAN}` for evaluation;
`{"data": PLAN, "request_id": "unique-request", "parent_id": null}` for saving. The plan is:

```json
{
  "title": "Synthetic shared-water scenario",
  "source": "Synthetic operator work plan",
  "period": {"start": "2026-10-20", "end": "2026-10-20", "timezone": "UTC"},
  "resources": [
    {"id": "shared-water", "kind": "water", "unit": "L", "available": "1000", "source": "Synthetic net availability"}
  ],
  "jobs": [
    {
      "id": "field-work", "unit_id": "SAVED-FIELD-ID", "cycle_id": null,
      "title": "Specified field work", "block_label": "One operator-defined event",
      "priority": 1, "requested_blocks": 1, "minimum_blocks": 1,
      "requirements": {"shared-water": "700"}, "source": "Synthetic requirements"
    },
    {
      "id": "greenhouse-work", "unit_id": "SAVED-GREENHOUSE-ID", "cycle_id": null,
      "title": "Specified greenhouse work", "block_label": "One operator-defined event",
      "priority": 2, "requested_blocks": 1, "minimum_blocks": 1,
      "requirements": {"shared-water": "500"}, "source": "Synthetic requirements"
    }
  ]
}
```

Replace IDs, timezone, period and all synthetic values with explicit inputs. Period timezone
must match the farm. An `evaluate` request writes nothing. Retry `save` with the same request
ID and inputs to obtain the original record; a conflicting retry fails. `get` accepts
`{"plan_id": "SAVED-ID"}`; `compare` accepts `{"plan_ids": ["FIRST-ID", "SECOND-ID"]}`.
Cross-farm units, cycles, parents and selections are rejected, including another farm owned
by the same person.

The `farm_resources` chat tool operates on the selected unit's farm. Its `describe` operation
lists that farm's units, cycles and operation contracts. It never infers inputs or applies
an allocation. CLI users must supply the farm ID explicitly.

Database schema v8 adds farm resource scenarios. Upgrading an existing database creates a
private restorable backup before the transaction; a failed migration rolls back the new
table and version. All scenarios participate in owner-scoped account export and erasure.

## Check proposed work times and storage

Select a saved resource scenario and open **Check proposed work times and storage**. The
timing model, `resource-timing-v2`, checks your proposed intervals against that exact saved
allocation. It does not invent start times, fill gaps, reschedule conflicts or allocate the
previously unallocated work.

Provide the following evidence:

- **Work intervals:** an interval name, job, block count, start, end and source. Together,
  intervals must account for exactly every allocated block. Split a multi-block job into
  several intervals if needed; individual blocks remain indivisible. To model work that needs
  pauses or changing resource use within a block, revise its block definition in a new
  allocation first. For every pump and labor pool, supply a concurrent unit count: positive when the
  job uses it and explicit zero otherwise. Units remain occupied for the entire interval.
- **Availability:** nonoverlapping windows for each pump and labor pool, with available
  pump/worker counts and sources. Availability must be net of other commitments. Unlisted time
  is explicitly unavailable. A pool's aggregate time budget cannot exceed its listed capacity
  hours; scheduled occupied time cannot exceed that budget. Each work interval must be long
  enough to accommodate its declared resource requirement with its assigned unit count.
- **Storage:** capacity, initial volume at period start, minimum reserve and source for each
  water pool. Supply all planned inflows and outside withdrawals as positive total volumes
  over explicit intervals, with sources. Empty lists mean explicitly no such flows. Unknown
  capacity or flow information cannot be replaced by zero. This model requires a reservoir
  balance for every water pool; it does not infer a fictitious tank for an unsupported supply.

Use `YYYY-MM-DDTHH:MM:SS` timestamps with `Z` or a UTC offset matching the farm's timezone at
that instant. For example, Taipei uses `2026-10-20T08:00:00+08:00`. Intervals lie within the
saved plan's local dates, including an end at midnight immediately after the final date.
They are half-open: work ending at 09:00 does not overlap work starting at 09:00. UTC offsets
distinguish repeated daylight-saving times; nonexistent local times and mismatched offsets
are rejected. Durations are elapsed time in UTC, including any clock change.

Water demand for the blocks in a work interval is withdrawn uniformly over that interval.
Listed inflows and outside withdrawals also have constant rates within their intervals.
The model checks every boundary where a rate changes. Between boundaries the volume is
linear, so the boundary extrema establish whether the whole interval respects the reserve
and capacity. The supplied aggregate water budget must not exceed initial volume plus inflows,
minus outside withdrawals and minimum reserve.

These are **unconstrained balance trajectories**. A negative balance reports missing water;
it does not mean a tank physically holds negative water. An above-capacity balance reports
overflow risk; the model does not silently spill the excess or let it disappear. A late refill
cannot repair an earlier deficit. No unlisted evaporation, leakage, return flow, refill or
pump connection is inferred. Define separate intervals when a constant-rate assumption is
not appropriate, and include all known outside flows.

The result shows failed checks, occupied-versus-available units, and reservoir trajectories.
The chart uses UTC time and rounded display values. Calculations use exact fractions; JSON
downloads also retain each reported quantity's exact numerator/denominator value. Display
values are rounded to six decimals independently of the process's decimal settings.

For example, two one-hour jobs can fit a two-hour daily pump budget yet conflict if both
start at 08:00 and only one pump is available. A tank may end the day at its reserve yet run
empty before an afternoon refill. These checks expose both failures. A passing result covers
the supplied allocated work and assumptions only: it does not prove pressure, flow capacity,
equipment connections, worker qualifications, treatment suitability, cash availability at
particular times, or the truth of reported inputs.

**Evaluate timing checks** writes nothing. **Save timed scenario** creates an immutable child
containing the timing inputs and result plus the parent's unchanged allocation and assumption
snapshot. Failed checks can be saved for review and comparison; saving is not approval.
Changing times does not clear a crop/lifecycle review flag inherited from the parent. To
accept revised crop assumptions, review and save a new allocation, then check its timing.
Older scenarios without timing data are shown as **not checked**, never as passing.

CLI operations use the same contracts:

```bash
agronaut resources --farm SAVED-FARM-ID evaluate_timing --input timing-request.json
agronaut resources --farm SAVED-FARM-ID save_timed --input timing-request.json
```

For the earlier 1,000 L example, only the first 700 L work block was allocated. A complete
timing request for that allocation is:

```json
{
  "plan_id": "SAVED-ALLOCATION-ID",
  "data": {
    "source": "Synthetic timing scenario",
    "work": [
      {"id": "morning", "job_id": "field-work", "blocks": 1,
       "start": "2026-10-20T08:00:00Z", "end": "2026-10-20T09:00:00Z",
       "concurrent": {}, "source": "Synthetic operator interval"}
    ],
    "storage": [
      {"resource_id": "shared-water", "capacity_l": "1000", "initial_l": "1000",
       "minimum_l": "0", "inflows": [], "external_withdrawals": [], "source": "Synthetic tank state"}
    ],
    "availability": []
  }
}
```

The empty concurrency and availability objects are appropriate here because that illustrative
allocation declared no pump or labor pools. They do not establish that real work needs no
equipment or people. Add those resources to the allocation when applicable, then supply their
timing data. For `save_timed`, add a unique `request_id` alongside `plan_id` and `data`. Chat's
`farm_resources` tool exposes the same operations; `describe` provides the full timing schema.
Timed children use the existing schema v8 table and participate in export and erasure.
They create no reservations, actual quantities, approvals, notifications or equipment actions.

## Check water outlet delivery limits

A sufficient daily volume and full tank do not establish that two zones can draw water
simultaneously. For example, two zones each withdrawing 500 L over an hour demand 1,000 L/hour
while they overlap. An outlet limited to 500 L/hour can serve them sequentially under those
uniform-flow assumptions, but cannot serve them at the same time.

Enable **Check water outlet delivery limits** in the timing section. Add windows for every
water pool, with start/end timestamps, a maximum combined flow in L/hour and a source describing
the operating conditions. These windows apply both to supplied timing checks and to start-time
proposals, including proposals using named equipment. Saved scenarios reload their windows.
Unchecking the control explicitly removes delivery checks from the new alternative.

Timing and scheduling JSON accept the optional `water_delivery` field. For a declared water
pool named `shared-water`, an example is:

```json
"water_delivery": [
  {"resource_id": "shared-water", "windows": [
    {"id": "morning-outlet", "start": "2026-10-20T08:00:00Z",
     "end": "2026-10-20T10:00:00Z", "max_flow_lph": "500",
     "source": "Synthetic outlet limit for development; replace with applicable evidence"}
  ]}
]
```

The list must cover every water pool exactly once. Windows cannot overlap within a pool;
touching endpoints are allowed. Maximum flow is a nonnegative decimal string; zero closes
the outlet. Outside listed windows capacity is zero. A pool with no windows is closed for
the whole planning period. At most 1,000 windows per pool and 5,000 total are accepted.
Timestamps follow the existing timezone and planning-period rules, including daylight-saving
transitions. The overall list `[]`, or omission on a previously unchecked scenario, means
delivery capacity is **not checked**. A parent with limits requires explicit inclusion of
`water_delivery`, or `[]` to remove its checks; omission is rejected.

Every modeled job withdrawal and listed outside withdrawal from the pool shares its outlet
limit. Inflows replenish storage but do not cancel outlet demand. The model compares exact
fractions at every rate or capacity change, reports affected work and outside-withdrawal IDs,
and retains exact rates in the downloaded result. Search prunes placements exceeding a limit
and verifies complete proposals with the same timing checker. Scenario comparisons distinguish
whether delivery capacity was checked. Failed checks may be saved for review; they cannot be
used to book equipment or commit a plan through the existing passing-check requirements.

These are operator-supplied limits on **uniform modeled withdrawals**. A whole-event average
does not establish actual peak flow; changing irrigation-zone flows and maintenance events
need explicit intervals and quantities. The checks do not derive pump curves, pressure losses,
pipe connections or irrigation adequacy. Every outside withdrawal listed against a pool is
assumed to share that pool's limit. Separate physical paths, aliased supplies and simultaneous
plans are not automatically reconciled. Delivery windows do not reserve a shared flow capacity
across independent plans; include relevant outside use explicitly and review the physical layout.

## Propose start times within permitted windows

For a saved allocation, **Propose work start times** searches for times for every allocated
block. First enter storage, external flows and pump/labor availability in the timing section
above it. Keep each entry in the draft. Then define runs with the allocated job, block count,
duration in elapsed seconds, concurrent pump/worker counts and a source for these assumptions.
Add one or more permitted windows for each run. A whole run must fit within one window;
an empty window list explicitly permits no starts. Split work only by supplying separate runs
whose blocks sum to the saved allocation. The proposal never drops blocks to find a fit.

Enter a grid step in elapsed seconds and maximum attempted placements, then choose
**Propose start times** for a read-only result or **Save scheduling scenario** for an immutable
child. The child retains the parent's allocation and assumption snapshot. Limited and failed
searches can also be saved, exported and compared; they contain no proposed work intervals.
Switching farms clears scheduling drafts. Editing kept supply/run entries clears the displayed
proposal. Grid and search-limit edits require resubmission. Existing manual work intervals are
separate inputs: scheduling uses the runs and shared supply entries, not those manual starts.

The experimental `resource-schedule-v3` search assigns prerequisite runs before their
successors, uses ascending job priority then run ID among ready runs, and tries earlier starts
first. It backtracks when a choice prevents another job from fitting. Priority orders the
search; only declared dependencies force chronological precedence, and the first
passing proposal is not an optimum for cost, yield, fairness or completion time.

All start times lie on a grid anchored at the first local midnight of the planning period,
advanced by elapsed seconds in UTC. Windows and results use the farm's correct UTC offsets,
including repeated hours at daylight-saving transitions. Ends follow the fixed elapsed
duration and need not fall on the grid. Overlapping run windows are merged for candidate
counting. The calendar is searched lazily rather than materializing every possible start.

| Outcome | Meaning |
|---|---|
| `feasible_schedule_found` | All allocated blocks have intervals passing the exact timing and storage checker. |
| `no_feasible_schedule_on_grid` | All starts on this grid are ruled out, possibly by an input constraint independent of start times. Other grids or inputs may work. |
| `search_limit_reached` | The placement limit stopped the search. Feasibility remains unknown. |

The result reports candidate counts, attempted placements, complete candidates checked during
search, and any start-independent failures (for example, too few declared working hours).
Partial placements are pruned for excess pump/labor occupancy or violated dependencies. Reservoir checks run on
complete schedules: a later-assigned withdrawal can prevent overflow, so partial overflow
does not justify rejecting a branch. Every returned proposal includes the complete existing
timing result, exact rational balances, sources and assumptions. The checker still assumes
uniform withdrawals/flows within intervals and interchangeable units within each resource pool.

### Search around named equipment bookings

Enable **Consider shared equipment calendar** in the scheduling section to check fixed
physical equipment assignments as part of the search. Select the current equipment revision
for every run and provide a source for its suitability. Each run needs exactly its declared
concurrent pump count in distinct physical pumps; other equipment may be assigned explicitly.
The search chooses start times for those items, not substitute equipment. With this option
off, ordinary scheduling checks the declared aggregate timing inputs and does not consult
the equipment calendar.

The proposal avoids active calendar blocks and bookings on the selected equipment, including
overlaps between runs in this proposal. For example, with a pump booked from 08:00 to 09:00,
two one-hour runs permitted from 08:00 to 12:00 can be proposed at 09:00 and 10:00, provided
the storage, time-resource and dependency checks also pass. Starts cannot precede the captured
search time, rounded up to a whole second and then to the supplied grid. The result reports
equipment-pruned placements and retains the selected item revisions, relevant booking
summaries, explicitly excluded booking, busy intervals and clock boundary. Historical
snapshots use summaries to avoid nesting entire earlier plans recursively.

If the plan family already has an active booking, explicitly select it under **Existing
booking to replace after review**. Only its current revision may be excluded from this search;
another plan's booking or a maintenance block cannot be excluded. The original booking stays
held while alternatives are explored. A rejected or unsuccessful search leaves it unchanged.

Search runs outside the database transaction. Before returning or saving, Agronaut rechecks
ownership, plan evidence, selected equipment revisions and relevant calendar records. A change
during search requires a fresh search. Changes after saving flag the old calendar snapshot
without rewriting the original result. Booking the proposal itself also changes calendar
state, so a snapshot-change warning does not mean the new booking was released.

Use **Review equipment booking for proposal** (or the saved-proposal button after reopening)
to load the proposed scenario, item assignments and replacement target into **Shared equipment
calendar**. Review the source, equipment and any replacement reason, then explicitly book.
The booking operation checks current equipment revisions and conflicts again in its own atomic
transaction. Another booking may have taken the proposed slot in the meantime. Saving a
proposal does not reserve equipment, release an existing booking, commit budget quantities
or create actual records.

The CLI and scoped tool expose `equipment_schedule_inputs`, `propose_equipment_schedule` and
`save_equipment_schedule`. The first lists current equipment and active same-family bookings.
The latter two take `plan_id` and `data` containing the ordinary `schedule` object,
`assignments` mapping every run ID to current equipment revision IDs, a `source`, and explicit
`replaces_booking_id` (null for new work). Saving also requires `request_id`. Retries return
the original saved snapshot, even if the calendar later changes; use a new request ID for a
fresh search. Raw user-supplied busy intervals are not accepted by the farm scheduling APIs.
For standalone deterministic replay, pass the saved `equipment_schedule.constraints` as
`equipment` alongside `schedule_inputs` to the model directly.

The same bounded-search statuses apply. No feasible start on this grid does not prove that
other grids, equipment choices or run definitions are infeasible; a search limit leaves
feasibility unknown. Equipment counts and occupancy do not prove compatibility, hydraulic
performance, shared-worker qualifications or available water across multiple plans. The
existing storage check uses the supplied scenario flows; it does not infer flows from other
equipment bookings. Manual timing edits and ordinary scheduling do not carry this calendar
check forward automatically; select the equipment-aware workflow to search it again.

CLI and scoped chat use the same `propose_schedule` and `save_schedule` operations:

```bash
agronaut resources --farm SAVED-FARM-ID propose_schedule --input schedule-request.json
agronaut resources --farm SAVED-FARM-ID save_schedule --input schedule-request.json
```

Using the earlier water-only example, a request is:

```json
{
  "plan_id": "SAVED-ALLOCATION-ID",
  "data": {
    "source": "Synthetic operator scheduling constraints",
    "step_seconds": 900,
    "search_limit": 1000,
    "runs": [
      {"id": "field-run", "job_id": "field-work", "blocks": 1,
       "duration_seconds": 3600, "concurrent": {},
       "windows": [{"start": "2026-10-20T08:00:00Z", "end": "2026-10-20T12:00:00Z"}],
       "source": "Synthetic operator duration and permitted window"}
    ],
    "storage": [
      {"resource_id": "shared-water", "capacity_l": "1000", "initial_l": "1000",
       "minimum_l": "0", "inflows": [], "external_withdrawals": [], "source": "Synthetic tank state"}
    ],
    "availability": []
  }
}
```

For saving, add a unique `request_id` alongside `plan_id` and `data`. Repeating the same save
returns the original receipt without another search; reusing its key with changed inputs
fails. Current crop/lifecycle changes remain flagged through the parent snapshot. Search
runs outside a database transaction; ownership and parent existence are checked again before
returning or saving. Records use the existing resource-plan table and owner export/erasure.

Limits are 100 runs, 100 windows per run, 1,000 run windows total, and 10,000 attempted
placements. Durations and grid steps are integers from 1 to 1,000,000 seconds; a duration must
fit the planning period. Storage/availability retain the timing checker's limits. Required
durations, suitable agronomic windows, named workers, equipment connections and precedence
dependencies are not inferred. Proposals do not reserve resources, approve work, create
actual records or operate devices.

## Require work order and minimum waiting intervals

Add **Work dependencies** when checking manual intervals, or **Run dependencies** when
proposing start times. Each dependency identifies a prerequisite and a successor by their
work/run names, an integer minimum waiting interval in elapsed seconds, and the source for
the requirement. For example, a declared preparation run can be required to finish before
another run starts. Agronaut does not decide which agricultural operations need this order
or what waiting period is appropriate.

Both timing and scheduling inputs accept an optional `dependencies` list:

```json
{
  "dependencies": [
    {"id": "prepare-before-water", "predecessor": "preparation-run",
     "successor": "watering-run", "min_gap_seconds": 1800,
     "source": "Synthetic operator sequence and waiting interval"}
  ]
}
```

This is a field inside the `data` object, not a standalone request. The two IDs must exist
in that request's `work` or `runs` list; job IDs alone are insufficient. The successor may
start only when the predecessor has finished and the minimum interval has elapsed. Zero
allows it to start at the exact finish instant. Multiple prerequisites must all be satisfied.
Waiting intervals use UTC elapsed seconds, so a repeated or missing local hour does not
change their duration. Waiting consumes no equipment or labor; represent active waiting as
explicit work with its resource requirements.

Use unique dependency IDs and one row per predecessor/successor pair. Self-dependencies,
unknown IDs and cycles are invalid. At most 1,000 dependencies are allowed; minimum gaps
are whole numbers from 0 to 1,000,000 seconds. Only finish-to-start minimum gaps are modeled;
maximum waits, start-to-start links, named workers, unlisted tasks and inferred agronomic
restrictions are outside this contract.

Manual checks report `dependency_not_satisfied`, retaining the supplied intervals and the
actual versus required gap. Successful proposals include a complete dependency audit with
source, predecessor finish, successor start and actual elapsed seconds. Dependencies are
part of the exact checker, not just a search heuristic. Search counts dependency-pruned
placements and retains the same distinction between grid infeasibility and a search limit.

Old requests without `dependencies` retain their original shape and mean no declared
dependencies. `[]` explicitly declares none. If a saved parent has dependencies, subsequent
timing or scheduling requests must include the field; omission fails rather than silently
dropping constraints. To remove them intentionally, submit `[]` as a new alternative with
its own source. The immutable parent and original search/check results remain unchanged.
Saved version-1 results remain readable; new checks and searches use version 2.

The web page restores the parent's dependencies into both editors, including when the other
mode has no work entries yet. Manual and proposed scheduling maintain separate dependency
drafts after loading. When defining new work or runs, retain the referenced names or explicitly
revise the dependency list. Empty lists are
explicitly saved as none. Deleting work does not silently delete its dependencies: repair
or remove the dangling references before submitting. Changes clear the displayed draft
result; the saved parent remains available for comparison. CLI, scoped chat, export, erasure
and idempotent saves use the same contracts.

## Bring a saved irrigation design into a resource plan

**Use a saved irrigation design** imports a complete drip or sprinkler event into the
allocation draft. It uses the saved model's conservative event water total and operating
hours, including the supplied maintenance allowances. Both totals must be positive. Both quantities round upward to six
decimal places for resource accounting. This is conditional experimental design evidence;
importing does not establish that the equipment or treatment works on the farm.

Add water and pump-time pools first. Select a saved calculation belonging to a soil unit in
the selected farm, choose an event date and priority, and explicitly map the water and pump
pools. All supplied operating and maintenance hours are charged to that pump pool. Confirm
that this interpretation is applicable in the source field. Supply every other pool's demand
independently, including labor and cash; explicit zero means unused. No labor rate, cost,
additional treatment or suitable date is inferred.

Choose **Preview irrigation job**, review its totals, source calculation, assumptions and
download, then choose **Keep reviewed irrigation job in draft**. These actions do not save a
resource scenario or record actual activity. Keeping repeats the ownership and evidence
checks; changes to resource pools require a fresh preview. Saving the whole scenario retains
the original complete equipment calculation, any adopted soil-water target, derived quantities
and conversion method as an immutable snapshot. The source evidence is visible on the saved
resource page and in account exports.

A linked job is one indivisible event: requested and minimum blocks are both one. Its event
date must lie within the plan period and crop lifecycle. Manual work intervals and proposed
run windows must remain within that local day. The same calculation/date cannot appear twice
in one plan. This import does not scale to a partial field, split zones, repeat an event across
dates, or generate an operating schedule. Enter a separate reviewed event for another date.

Failed equipment checks cannot be imported. Changed crop/planting assumptions or corrected
water-target evidence require a revised source design before new adoption or allocation save.
If the design adopted an end-of-day soil-water state, the planned event date must follow that
day. Later dates still need an operator's applicability assessment; no forecast or continuing
water balance is inferred. Existing resource plans keep their original inputs, with current
crop, lifecycle, timezone and source-review flags alongside them. Idempotent retries return
their original receipts.

The work editor retains the link and locks its derived water/time amounts, unit, cycle and
block counts. Other supplied requirements, title and priority remain editable. To enter
independent quantities, choose **Convert to manual work entry** and supply a new source. This
explicitly removes the link from the draft; it does not modify the equipment calculation or
saved alternatives. CLI/chat clients must likewise remove `irrigation_design` explicitly to
stop claiming that quantities come from the design. Direct changes that conflict with a
retained link are rejected when evaluating or saving.

CLI and scoped chat expose `design_job`. `describe` lists this farm's saved equipment
calculation IDs and the request schema. For example, send the following operation request
with `agronaut resources --farm SAVED-FARM-ID design_job --input request.json`:

```json
{
  "data": {
    "calculation_id": "SAVED-DRIP-OR-SPRINKLER-ID",
    "day": "2026-10-20", "job_id": "field-event", "title": "Complete supplied irrigation event",
    "priority": 1, "water_resource_id": "water", "pump_resource_id": "pump",
    "resources": [
      {"id": "water", "kind": "water", "unit": "L", "available": "10000", "source": "Synthetic net water availability"},
      {"id": "pump", "kind": "pump_time", "unit": "h", "available": "24", "source": "Synthetic net pump availability"},
      {"id": "crew", "kind": "labor_time", "unit": "h", "available": "8", "source": "Synthetic net crew availability"}
    ],
    "other_requirements": {"crew": "2"},
    "source": "Synthetic operator review of full-area design, event date, pump/maintenance mapping and supplied labor requirement"
  }
}
```

Keep the returned `job` object unchanged in a resource plan's `jobs` list, alongside the same
resource definitions and a period containing the event date. The saved allocation verifies
the link against owned evidence again. Availability amounts are operator inputs and can
change; the source calculation does not establish shared availability or physical pool
identity. Mapping a pump does not verify its connection, flow or pressure. The uniform
withdrawal assumption of resource timing does not reproduce changing zone flows or maintenance
events in an equipment design. Whole-event timing therefore remains a declared scenario,
not a validation of the detailed hydraulic/storage sequence. No reservation, approval,
purchase, actual water log or equipment action is created.

## Compare a planned irrigation event with recorded totals

Open a saved resource scenario and expand **Compare irrigation plan with recorded event
totals**. Select an allocated job linked to a saved drip or sprinkler design. Record its
measurements first through **Crop records → Record a field measurement**, then explicitly
select the source-water event volume (`irrigation_volume`, L), pump operating time
(`runtime`, h), or both. Each selected measurement must belong to the job's exact production
unit and crop cycle, including an unassigned cycle when the job has none.

Supply the actual event date, an explanation of how each reading covers the complete event
including maintenance on the mapped source/pump, and the review source. The date uses the
saved plan's timezone; each selected observation must fall on that date and cannot be in
the future. The actual date may differ from the planned date. Agronaut reports that difference
without inferring task completion or actual start and finish times.

Only event totals share this comparison basis. Net soil depth, flow rates, cumulative meter
counters and totals covering other work are not interchangeable with source-water volume or
pump runtime. No conversion or attribution is inferred. Forecasts and rejected measurements
are refused. Measured, reported and estimated evidence retain their provenance and operator
quality labels. An omitted metric remains unknown; an explicitly recorded zero is a value.

**Evaluate event comparison** is read-only. It compares each selected total with the saved
allocation's conservative, upward-rounded allowance. Results retain exact rational values
and readable differences. Being below an allowance does not establish water savings,
adequate irrigation, crop response or model accuracy.

**Save outcome review** stores the original job, allocation, equipment calculation, selected
observations and their quality-review histories separately from resource scenarios. Later
measurement reviews or changed plan assumptions produce current review flags alongside the
original values. Saving changed inputs creates a correction referencing the current review;
earlier revisions remain available in **Saved outcome review history** and JSON downloads.
Reusing identical inputs in the web form returns the existing review. Reviews of alternative
plans can use the same readings and must not be summed as distinct actual events.

CLI and scoped chat expose four operations:

```bash
agronaut resources --farm SAVED-FARM-ID outcome_inputs --input selection.json
agronaut resources --farm SAVED-FARM-ID evaluate_outcome --input comparison.json
agronaut resources --farm SAVED-FARM-ID save_outcome --input comparison-save.json
agronaut resources --farm SAVED-FARM-ID list_outcomes --input plan.json
```

`outcome_inputs` takes `{plan_id,job_id}` and supplies scoped candidates with their current
`expected_review_id` (null before any review). A comparison request is:

```json
{
  "plan_id": "SAVED-ALLOCATION-ID",
  "job_id": "field-event",
  "data": {
    "actual_day": "2026-07-07",
    "water": {"observation_id": "RECORDED-EVENT-VOLUME-ID", "expected_review_id": null},
    "pump": null,
    "attribution": "Synthetic complete event volume including maintenance, from the mapped source",
    "source": "Synthetic operator review"
  }
}
```

For saving, add a unique `request_id` and `supersedes_id` alongside `plan_id`, `job_id` and
`data`. Use null for the first review, then the current review ID for a correction. A stale
quality-review ID or correction predecessor fails; reload and review current evidence before
resubmitting. Repeating the same request ID and operation returns the original receipt;
reusing it with changed data fails. `list_outcomes` takes `{plan_id}` and returns all revisions,
their current/superseded status, and current review flags. `describe` includes these schemas.

Schema v10 adds the outcome-review table through the existing transactional, backed-up
migration. Account export includes `resource_outcomes`; owner erasure removes them. Saving
a review does not alter measurements, daily net-water logs, task status or resource plans,
and does not create reservations or operate equipment. These comparisons support an audit
trail; they do not constitute independent field validation.

Reviews assigned to a crop cycle are also available under **Crop records → Review this
season → Irrigation event comparisons**, including original source snapshots, correction
history and current evidence flags. **Compare recorded seasons** lists the current reviews
for each selected cycle separately. Unassigned reviews remain available on their resource
plans. Season views do not aggregate alternative event comparisons into actual water use or
daily net-water coverage.

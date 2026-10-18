# Agronaut agriculture platform plan

Date: 2026-10-17. Status: proposed roadmap; implementation has not started.

The enduring product direction is saved in [Agronaut vision](VISION.md). Contributor work
is tracked in [GitHub epic #78](https://github.com/Rekin226/Agronaut/issues/78).

Agronaut will grow into a whole-farm agricultural intelligence platform that helps farmers
plan, design, operate, troubleshoot, and improve different agricultural enterprises. The
ambition, “The GOD of Agriculture,” becomes a practical promise: understand the farm,
explain decisions, compute what can be computed, and learn from measured outcomes.

The first expansion is irrigated soil cultivation. Deliver a complete drip-irrigation
workflow, extend it to sprinkler and surface irrigation, then build seasonal crop management
and coordination across the farm. Aquaponics and hydroponics remain supported specialties.

This plan governs agricultural expansion. The existing [gap-closure plan](PLAN.md) continues
to track infrastructure, channel, and other unfinished work. Prioritize defects affecting
data integrity or existing users alongside expansion; reassess other backlog items against
these milestones rather than treating the entire older backlog as a prerequisite.

## Product scope

The initial new audience is a grower or extension worker managing soil-grown vegetables with
limited water. Use one partner region and a small crop set to establish a useful workflow.
Tomato is the proposed first crop; add onion and maize after reviewing sources and local
pilot needs. These are planning assumptions, not established coverage.

Agronaut should eventually answer five connected questions:

1. What can I grow with my land, climate, water, equipment, and budget?
2. How should I design and equip the production system?
3. What needs attention today, and what evidence supports that advice?
4. What is going wrong, and which observations would distinguish possible causes?
5. What happened after I acted, and what should change next season?

“Supports a crop” must be qualified by task, production system, region, and evidence. A
crop can have sourced guidance without having a validated irrigation or yield model.
Publish a capability matrix with separate statuses for guidance, calculation, experimental
simulation, and field-validated operation. Attach validation to the tested conditions.

## Model the farm correctly

Represent these dimensions separately rather than expanding a single `system_type` enum:

| Dimension | Examples |
|---|---|
| Enterprise | Vegetables, cereals, orchards, aquaculture; later livestock and agroforestry |
| Production system | Soil cultivation, hydroponics, aquaponics |
| Environment | Open field, greenhouse, indoor |
| Growing medium | Soil, water, substrate |
| Water strategy | Rainfed, supplemental irrigation, fully irrigated |
| Irrigation method | Drip, sprinkler, surface |
| Method subtype | Surface/subsurface drip; fixed sprinkler; furrow, basin, border |

Aquaponic raft/NFT/media-bed choices remain domain-specific properties. An irrigation
method is not a replacement for a production system. Validate compatible combinations.
Rainfed systems have no installed irrigation method unless supplemental irrigation exists.

Use a Farm → ProductionUnit → ProductionCycle hierarchy. A unit may be a field, greenhouse,
orchard block, or aquaponic installation. Keep irrigation zones beneath units and shared
water sources, storage, pumps, and equipment at farm level. The initial UI can manage one
farm while the schema supports multiple farms per owner.

Every observation, recommendation, action, and outcome needs an owner, unit, applicable
cycle, timestamp, units, provenance, and quality status. Distinguish measured, reported,
estimated, and forecast values. Store timezone and observation time separately from entry
time. Preserve versions of plans and the inputs that produced them.

## Architecture and migration

Keep deterministic agricultural calculations independent of LLMs, networks, and persistence.
Use small in-process modules and the existing SQLite deployment initially.

| Component | Proposed responsibility |
|---|---|
| `agro_model/` (new) | Shared typed quantities and results, field crops, soil water, irrigation calculations and validators |
| `aqua_model/` (existing) | Existing aquaponics and hydroponics engines with stable public entry points |
| `agronaut_agent/domains/` (new) | Built-in capability registry, required facts, tool routing, knowledge scope, result adapters |
| `agronaut_agent/profile.py` | Domain- and goal-specific questions with an explicit active unit |
| `agronaut_agent/store.py` | Farm records, ownership, migrations, scoped history and action logs |
| Agent data adapters | Fetch/import weather, equipment specifications and observations before validation |
| Existing channels and CLI | Shared consultations and reports across terminal, web, Telegram and WhatsApp |

Each domain exposes supported tasks, input validators, tools, knowledge filters, result
schema, and evidence status. Results carry calculations, units, source versions, assumptions,
missing inputs, feasibility constraints, and applicability limits. Tool availability is
enforced in dispatch as well as described in the prompt.

Introduce shared types only as concrete use cases require them. Do not move every existing
module or build a third-party plugin loader in the first release. Extract reusable hydraulic
primitives only after checking their assumptions: existing tank-layout plumbing is not a
validated field-irrigation network model. Update packaging and isolated-core CI for new modules.

Migration must preserve existing profiles, commands, tool names, reports, and twin state.
Create a default farm and legacy production unit for each existing profile. Infer its domain
only from reliable saved facts or tool history; otherwise mark it unresolved and clarify on
use. Preserve original facts and provenance. Historical messages with ambiguous unit scope
remain user-level history rather than being arbitrarily assigned to a field.

Back up before migration, migrate transactionally, verify row counts and relationships, and
support restoring the backup with the previous application version. Test upgrades from
supported schemas and refusal of newer unsupported schemas. Export and deletion must include
all newly introduced records. Unit switching must scope recall, tools, follow-ups, and approvals.

## Delivery milestones

### M0 Establish the pilot and evidence baseline

Choose the initial crop, region, production conditions, and partner with access to irrigation
records. Inventory existing regressions, establish capability labels, and create example
conversations spanning aquaponics, hydroponics, drip, sprinkler, surface, and rainfed farming.
Recruit an agronomist or irrigation specialist to review coefficients and acceptance cases.

Start with the FAO crop-water methodology and irrigation-method guidance. Record exact
editions, equations, tables, applicability, and reuse permissions in the source registry.
Review available revisions before freezing the implementation baseline; do not mix editions
silently. FAO-56 covers reference evapotranspiration, crop coefficients, and soil-water
accounting; the irrigation-method manual covers surface, sprinkler, and drip methods.
[FAO crop-water guidance](https://www.fao.org/4/X0490E/x0490e00.htm),
[FAO irrigation methods](https://www.fao.org/4/s8684e/s8684e00.htm).

**Exit:** a bounded pilot protocol, a reviewed source list, and reference examples with
expected results and numerical tolerances. Start partner recruitment now; evidence gathering
runs alongside engineering.

### M1 Farm foundation and consultation routing

Implement the farm hierarchy, migrations, active-unit selection, capability registry, and
domain-aware questions. A soil-crop design must not ask for fish or tank volume. A plants-only
hydroponic design must not inherit aquaponic essentials. Ambiguous requests ask which unit or
production system the farmer means before assigning facts or selecting a calculator.

Scope retrieval to agricultural domain and task, with crop and regional applicability where
available. Expand evaluation controls so new field questions are recognized as in-domain while
unrelated questions are still rejected. Do not claim computational support based on retrieval.

**Exit:** one account can switch between an existing aquaponic unit and a soil-grown tomato
field without mixing facts or actions; existing regression checks pass; irrigation capabilities
still under development are reported accurately.

### M2 Crop water and drip irrigation pilot release

Build the shared crop-water engine before equipment design. Add weather records containing
dates, location, source, air temperature, rainfall, humidity and wind information, radiation,
and the site metadata required by the selected evapotranspiration method. Allow validated
reference ET imports with method and provenance. Keep observed weather separate from forecasts
and historic scenarios. Reject gaps or use an explicitly documented fallback with its own
applicability; never silently treat missing rainfall as zero.

Model crop stage, rooting depth, soil water capacity, initial water status, rainfall,
irrigation, depletion, and drainage/runoff assumptions. Separate crop demand, net irrigation
requirement, gross application, and runtime. A daily schedule requires a stateful root-zone
balance; demand alone does not establish when to irrigate.
The scientific basis for depletion and irrigation timing is
[FAO-56 Chapter 8](https://www.fao.org/4/X0490E/x0490e0e.htm).

For drip, implement a declared operating envelope: a single crop, soil cultivation, supported
field geometry, and documented terrain limits. Collect row and emitter spacing, equipment
flow/pressure specifications, lateral dimensions, source capacity and pressure, elevation,
and filtration requirements. Account for localized wetting with a reviewed method. Produce
zone flow, runtime, daily source demand, pressure/head requirements, and a preliminary bill of
materials. Runtime uses the flow of the active zone; a volume budget cannot substitute for
instantaneous pump capacity. Report infeasible designs and the binding constraint.

Deliver through a deterministic CLI first, followed by the shared chat tools and web view.
Proposed commands are `agronaut water-needs`, `agronaut design-irrigation`, and
`agronaut irrigation-plan`; final naming is an implementation decision. Calculations run
offline with supplied data. Fresh weather requires connectivity or a dated import.

**Exit:** the farmer can say “I have one hectare of tomatoes, a borehole, and limited water,”
complete a consultation, receive a sourced plan, log actual irrigation and rainfall, and
receive an updated plan. Missing soil state yields a planning estimate and a request for
initialization rather than an unjustified operational schedule. Existing aquatic systems pass
regression checks. This is an experimental pilot release until field evidence meets M3.

### M3 Validate drip and add sprinkler and surface methods

Release each method separately against the same crop-water and farm contracts.

| Method | Additional work | Acceptance evidence |
|---|---|---|
| Drip validation | Compare planned and delivered water, soil response, pressure and runtime | Held-out field records within the declared operating envelope |
| Sprinkler | Nozzle pressure/flow, spacing and overlap, application rate, wind applicability, infiltration and zone scheduling | Reviewed hydraulic cases plus distribution measurements from field catch tests |
| Surface | Furrow/basin/border geometry, slope, inflow, infiltration, advance/recession, cutoff and drainage | Method-specific measured irrigation events and specialist review |

Surface irrigation begins with feasibility and water budgeting. Operational cutoff-time advice
requires the appropriate hydraulic/infiltration model and local measurements; multiplying crop
demand by an assumed efficiency is insufficient. Orchard, flooded-rice, saline-water, and
complex-terrain scenarios remain explicitly unsupported until their own modules and evidence
exist. Sprinkler and surface methods do not inherit drip's field-validation status.

**Exit per method:** reference calculations pass; equipment and environmental constraints are
visible; field evaluation meets preregistered tolerances; the capability matrix names the
tested crop, method, and conditions. A failed gate keeps the method experimental.

### M4 Seasonal crop management

Add planting and transplanting records, stage updates, crop calendars, soil-test interpretation,
sourced nutrition planning, scouting, harvest logs, costs, and seasonal comparisons. Expand
plant-health knowledge and evaluate it with real field observations and photographs. Distinguish
observed symptoms from confirmed causes. Nutrition calculations need crop-, soil-, and
region-specific methods; aquaponic coefficients are not transferable by default.

Include rainfed and supplemental irrigation workflows, then orchard and protected-cropping
extensions. Preserve actual observations separately from predicted growth and yield. Add each
capability to the registry only at its demonstrated evidence level.

**Exit:** one complete crop cycle connects the initial plan, recommendations, recorded actions,
harvest, and a season review; the farmer can identify which decisions changed and why.

### M5 Coordinate the whole farm

Allocate water, pump time, storage, labor, and budgets across production units. Begin with
transparent constrained allocation and user priorities. Compare feasible scenarios against
the current plan; enforce farm-level limits so two fields cannot each spend the same water.
Introduce yield or profit objectives only with appropriate crop response evidence and dated
local cost/price inputs. Show assumptions and uncertainty alongside comparisons.

**Exit:** a mixed farm receives a feasible plan that respects shared resources and explains
tradeoffs, with an auditable path from observations through decisions to outcomes.

### M6 Expand into additional agricultural enterprises

Add standalone aquaculture, livestock, agroforestry, nursery production, post-harvest handling,
and other specialties according to user demand and available expertise. Each requires a domain
owner, evidence sources, typed data, useful tools, an evaluation set, and a pilot. Sensor
integration and eventual equipment control are separate workstreams; physical actuation needs
device-specific fault handling and the existing approval model before release.

The expansion priority is demonstrated farmer value and sufficient evidence, rather than the
number of domain names the assistant can discuss.

## First implementation backlog

Each row is a reviewable work package; split large packages into focused PRs. Dependencies
refer to these IDs. All are pending.

| ID | Deliverable | Depends on | Completion check |
|---|---|---|---|
| P01 | Capability vocabulary, pilot scope, source registry and reference cases | — | Reviewer can identify supported tasks and reproduce expected examples |
| P02 | Farm, unit, cycle and observation types with validators | P01 | Reject invalid units and incompatible combinations; preserve provenance |
| P03 | SQLite migration, ownership and export/delete coverage | P02 | Upgrade/restore fixtures pass; no cross-user or cross-unit data leakage |
| P04 | Domain registry and legacy engine adapters | P01, P02 | Existing tools retain behavior; unsupported domain/tool combinations are blocked |
| P05 | Unit selection, domain-specific questions and scoped recall | P03, P04 | Aquatic and field consultation transcripts pass without mixed facts |
| P06 | Weather import/fetch contract and quality validation | P02 | Missing days, units, forecast dates and source freshness handled explicitly |
| P07 | Reference ET and crop-demand calculations | P01, P06 | Independent reference cases match documented tolerances |
| P08 | Soil-water state, observations and irrigation requirement | P07 | Water balance closes; rainfall is not counted twice; state updates reproducible |
| P09 | Drip flow, pressure, zoning and runtime | P08 | Feasible/infeasible cases correctly classified against source and equipment limits |
| P10 | CLI, report, packaging and isolated-core CI | P09 | Clean install runs an offline worked example and exports its assumptions |
| P11 | Chat/web workflow, domain retrieval and evaluation set | P05, P10 | Consultation → plan → action log → recalculation works end to end |
| P12 | Pilot replay, field evidence report and capability publication | P11 | Reviewer can reproduce metrics and see where validation succeeds or fails |

## Schedule and resources

Planning estimate: one experienced engineer, a part-time agronomist/irrigation specialist,
and an engaged pilot operator. This is a sequencing estimate, not a delivery commitment.

| Elapsed engineering time | Target |
|---|---|
| Weeks 1–2 | M0 scope, reference cases, partner protocol and migration design |
| Weeks 3–6 | M1 farm records, migration, routing and consultation |
| Weeks 7–10 | Weather, crop water and soil-water engine |
| Weeks 11–16 | Drip design, CLI/chat integration and internal pilot candidate |
| Weeks 17–20 | Integration fixes, pilot onboarding and initial evidence report |
| After pilot evidence | M3 releases, then M4 and M5, re-estimated from measured workload |

The first 90-day target is a working farm foundation and tested crop-water engine with drip
integration underway. A pilot candidate is a roughly 4–5 month engineering target under these
assumptions. Field validation follows real irrigation events and at least a crop cycle for
seasonal outcomes; it may take longer than the software schedule. Without a pilot partner,
engineering can continue but operational validation cannot be declared complete.

Reserve effort for maintaining existing workflows throughout. Budget for specialist review,
field visits and measurement equipment, hosting/weather access where needed, and operator
support. Estimate amounts after the region, equipment, and staffing are known.

## Evaluation and release gates

Use three separate evidence levels: mathematical verification, workflow verification, and
field validation. Passing one does not establish the others.

- Numerical checks: independent worked examples, dimensional consistency, water conservation,
  extreme weather, missing data, zero flow, unavailable pressure and exhausted supply. Set
  equation-specific tolerances from reference precision before implementation.
- Workflow checks: legacy migrations, ambiguous domains, concurrent sessions, unit switching,
  delayed observations, corrected records, duplicate events, tool failures and offline imports.
- Knowledge checks: applicability-aware citations, field questions that must be answered,
  unrelated questions that must be declined, and irrelevant aquatic guidance that must not
  leak into field recommendations.
- Field checks: proposed starting cohort of 3–5 farms in one region, subject to partner capacity;
  log delivered water, runtime, rainfall, equipment characteristics, soil observations, crop
  stage and harvest. This is a feasibility pilot, not proof of universal effectiveness.

Before collecting evaluation outcomes, the specialist and operator define measurement quality,
acceptable water/runtime error, soil-stress indicators, data completeness, and comparison
procedures. Compare against farmer practice and a simple scheduling baseline where feasible.
Separate calibration data from held-out evaluation by farm or time; report failures and the
conditions tested. Water savings must be interpreted with crop outcome and weather context.

Track consultation completion, corrections required, weekly use, accepted versus actually
performed actions, predicted versus delivered water, and data completeness. Track yield and
cost outcomes when a season permits them. The durable product metric is useful decisions with
recorded outcomes, supported by retention and evidence of improved resource use.

## Main dependencies and scope decisions

| Dependency or risk | Planned response |
|---|---|
| Existing profiles assume one aquatic system | Migrate explicitly and enforce unit scope before adding field advice |
| Current climate data omit key irrigation inputs | Introduce a separate weather contract; retain existing twin formats |
| Soil and emitter properties are often unknown | Collect measurements or expose bounded assumptions and missing prerequisites |
| Shared farm abstractions could delay useful work | Keep the registry built-in and deliver one complete drip workflow first |
| Regional knowledge and coefficients vary | Start with one partner region; record applicability in data and results |
| Field data or expert review arrive late | Continue verification, keep capability experimental, delay validated release claims |
| Expansion breaks existing users | Preserve public interfaces and make regression checks part of every milestone |

The next concrete implementation task is P01: establish the capability vocabulary and pilot
reference cases. P02–P05 then remove the aquaponics-only assumptions, while P06–P09 build the
first field specialty. The long-term vision is fulfilled through successive useful, measured
workflows built on that foundation.

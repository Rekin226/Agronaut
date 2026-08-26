# The feed-response protocol: create the dataset the field is missing, in 21 days

## Why this document exists

The twin's decisive validation gap is a record nobody publishes: **measured feed and
nitrogen species in the same rows, in physical units** (#87). Two attempts to work around
it are now on the record:

- **Inference** (`scripts/validate_twin.py`): feed inferred from fish growth on the
  12-pond Kaggle set, scored against nitrate on held-out days. Verdict: MIXED — the twin
  tracks the *direction* of nitrate on 5 of 7 ponds, but uncalibrated hobby sensors
  (nitrate in the thousands of "mg/L", ammonia glitching to 10¹¹) cannot support a
  level claim. **The bottleneck is sensor quality, not missing feed records.**
- **Creation** (this protocol): a designed experiment on a real system, using manual
  titration test kits — which, unlike the IoT probes in every surveyed dataset, read in
  true mg/L. Three weeks, one operator, ~30 minutes a day.

One operator running this once produces a more decisive dataset than everything surveyed
in #87 combined, because it contains the one thing none of them do: **a known, deliberate
change in the forcing function, with the response measured through it.**

## What the experiment is

A **feed step-response**: hold feeding steady, then step it up 50%, then back down, while
measuring the nitrogen cascade daily. The twin makes a specific, falsifiable prediction
about what happens (ammonia rises first, nitrite follows with a lag, nitrate's slope
changes); the data either matches or it doesn't. That is validation — not curve-fitting.

```
phase A  days 1-7    baseline feed F        (establishes the system's steady state)
phase B  days 8-14   step UP to 1.5 x F     (the twin predicts the transient)
phase C  days 15-21  back DOWN to F         (the recovery is a second, free experiment)
```

## What you need

- A running, cycled system with fish actively feeding (any size; note everything below).
- A drop-titration water test kit reading NH₃/NH₄⁺, NO₂⁻, NO₃⁻ (e.g. API Freshwater
  Master or equivalent, ~USD 25-35). **Not strips if avoidable** — resolution matters:
  the kit reads ammonia to ~0.25 mg/L and nitrite to ~0.25 mg/L, which is enough to see
  the step (the twin predicts a baseline-dependent TAN rise typically well above that
  for a 50% feed step on a normally loaded system).
- A kitchen scale for feed (±1 g), a thermometer, and 10 minutes at a fixed time daily.

## The log — exactly the v1.0.0 schema

Log one row per day in the format `aqua_model/logging_schema.py` already defines
(`python -c "from aqua_model import logging_schema; print(logging_schema.csv_header())"`).
The non-negotiable columns for this experiment:

| column | rule |
|---|---|
| `feed_g` | **weigh it, don't scoop it** — this column is the whole point |
| `ammonia_mg_l`, `nitrite_mg_l`, `nitrate_mg_l` | same kit, same person, same hour daily |
| `water_temp_c` | at test time |
| `makeup_water_l` | any water added — dilution moves nitrate and must be on the record |
| `note` | anything unusual: uneaten feed, a death, rain, a cleaning |

Rules that protect the data's value:

1. **Same hour every day**, ideally before the morning feed — diurnal swings are real.
2. **If fish leave feed uneaten, record what they actually ate**, not what you offered,
   and say so in `note`. Uneaten feed still dissolves N — the note is what lets the
   analysis handle it either way.
3. **No other changes during the 21 days**: no new fish, no bed cleaning, no media
   swaps, no algaecide. If life forces one, log it — the run is not ruined, but the
   analysis must know.
4. **Don't abort on a "boring" phase B.** A system whose biofilter absorbs the step
   without a visible ammonia rise is a *result* (capacity exceeds load by ≥50%) and
   validates the twin's capacity-limited nitrification just as hard as a spike would.
5. Safety override, no exceptions: if ammonia or nitrite crosses **1 mg/L**, or fish
   gasp or refuse feed, end phase B early and log the day. A shortened, honest record
   beats a completed, harmful one. (Thresholds: knowledge/nitrogen_cycle_and_cycling.md.)

## What the analysis does with it

With `feed_g` measured, every free quantity the inference route had to fold into a
fitted scale becomes known or fittable with data to spare: the twin runs forward from
day 1 with YOUR volume, YOUR feed, YOUR temperature, and its prediction for days 8-21
is compared against measurements it never saw. Phase C doubles the test for free.
`scripts/validate_twin.py`'s scoring (holdout RMSE vs flat and trend nulls, shape
correlation) applies unchanged — pointed at real mg/L this time.

## Who should run it

Anyone with a cycled system and three weeks. The obvious first candidates are the
project's own systems (Burkina Faso and Taiwan — two climates, two species, one
protocol). Every additional operator who runs it through #22/#75 turns this from one
validation into a dataset the whole field lacks — which reframes the missing-data
problem the way #87 proposed: the record no one publishes is one we can produce.

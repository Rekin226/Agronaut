# The production twin: simulate a system at a place, then walk through it in 3D

This page is the working map of the digital-twin subsystem added on `feat/production-twin`
(epics #85/#26, data survey #87). It covers what each piece does, where the data comes from,
and how to drive it end to end.

## The pipeline in one picture

```
scripts/fetch_climate.py        scripts/data_registry.py
  (NASA POWER / Open-Meteo)       (survey -> fetch -> vetting gate)
        |                                |
        v                                v
data/climate/<site>.json         data/raw/<id>/            data/dataset_registry.json
        |
        |  aqua_model (pure, deterministic, cited — the trust zone)
        v
climate.py --> production.py <-- fishgrowth.py (TGC)
                  |    ^
                  |    +-------- cropgrowth.py (cited yield x light/temp/N)
                  |    +-------- twin.py       (nitrogen cascade, unchanged)
                  v
        ProductionRun (season trajectory + summary + limiting factor)

sizing.py --> layout.py --> scene3d.py --> scripts/render_3d.py --> one offline HTML
              (placement)   (scene JSON)   (embeds web/vendor/three.min.js)
                                ^
                                |  optional: state + trajectory
                    ProductionRun / mirror.ProductionState
```

## Simulating a season

```python
import json
from aqua_model import (DailyClimate, GreenhouseParams, ProductionParams,
                        from_records, simulate_production, start_state, format_summary)
from aqua_model.species import get_species
from aqua_model.crops import get_crop

days = from_records(json.load(open("data/climate/taichung_2025.json"))["days"])
init = start_state(volume_l=3000, fish_count=80, start_weight_g=50,
                   water_temp_c=25, species=get_species("tilapia"))
run = simulate_production(init, days, get_species("tilapia"), "tilapia",
                          get_crop("basil"), grow_area_m2=24,
                          params=ProductionParams(greenhouse=GreenhouseParams()))
print(format_summary(run, site_label="Taichung"))
```

Scenario comparison is two runs with one change (`GreenhouseParams(shade_to_ambient=True)`,
`heat_setpoint_c=26`, a different crop, a different stocking). Relative differences are the
trustworthy output; the summary says so itself.

Chat path: the `simulate_season`, `what_if_nitrogen` and `design_system_3d` tools in
`agronaut_agent/tools.py` wrap exactly these calls.

## The 3D design

```
python scripts/render_3d.py --species tilapia --crop basil --area 24 \
    --temp 28 --water 500 --system-type raft -o design_3d.html
```

`layout.plan_layout` turns a `DesignOutput` into placed components (tanks split above
2.5 m³, filtration row, gridded beds, aisles, a greenhouse envelope derived from the
design); `scene3d.to_scene` serializes it; the renderer embeds three.js (vendored,
r147, MIT) so the file works offline. Media-bed systems carry no separate biofilter
vessel; tower systems raise the ridge. The layout declares itself a proposal, not a
site plan.

## Binding the twin to the drawing

Until this landed there were two twins that had never been introduced (#118): `mirror.py`
held one operator's real state, `scene3d.py` drew a design, and nothing joined them, so the
3D view had never once shown anyone's actual pond.

`to_scene` now takes an optional `state` (a `ProductionState`) or `trajectory` (a
`ProductionRun.trajectory`) and embeds per-day FRAMES the viewer scrubs through:

```bash
python scripts/render_3d.py --crop basil --site taichung_2025 --days 365 -o first_year.html
```

```python
from agronaut_agent import twin_view
snap  = twin_view.compute(mem, user_id, days=14)     # this operator's live twin
scene = twin_view.scene_for(snap, mem.get_facts(user_id))   # ...bound to their drawing
```

What a frame decides, and where it comes from:

| in the picture | from | decided in |
|---|---|---|
| fish count and size | `ProductionState.fish` (the cohort) | `scene3d._fish_block`, length by `FULTON_K` |
| water colour | ammonia / nitrite / nitrate vs the action bands | `scene3d.water_band`, thresholds from `advisory.py` (healthy water carries no override: the viewer keeps its own blue) |
| crop size and pallor | the day's `CropFactors` | `scene3d._crop_block`, chlorosis onset = `f_nitrogen(NO3_LOW_MG_L)` |
| the badge | `today` / `forecast` / `projected` | `scene3d.build_frames` |

`state` is what TODAY means. `/forecast` prints it as "Now" and `advisory.recommend` reasons
about it, so when it is bound the picture shows it and the whole trajectory behind it is
forecast — exactly how `format_summary` labels the same run. Letting the first SIMULATED day
stand in for today would have put a different pond in the picture from the one the bot is
discussing in the same reply (measured: 200 g / NH3 0.00 in the bot, 202 g / NH3 0.26 in the
drawing). Today's crop appearance comes from that first simulated day, because a stored state
carries no crop factors and today's conditions are precisely what it evaluates.

Three rules hold this together:

1. **The viewer decides nothing.** Every appearance is a number in the frame. A colour chosen
   in JavaScript would be a second, uncited opinion about when a pond is in trouble.
2. **The three things stay distinct.** A design, the system today, and a projection are
   labelled on screen at all times. `today_index=None` marks a run that is not anchored to a
   calendar, and every frame in it says "projection" rather than borrowing today's authority.
3. **The geometry is still a proposal** even when the state is the operator's own, and the
   scene says so in `twin.geometry_note`. A tank volume that disagrees with what the grow area
   implies is stated in the subtitle rather than quietly drawn over.

A long run is downsampled to ~120 frames, but the peaks of each nitrogen channel, every
harvest day, today and the endpoints are pinned in: a stride that landed either side of the
nitrite spike would show a season in which it never happened.

## Where the data comes from, and what is still missing

`data/dataset_registry.json` is the #87 survey made executable —
`python scripts/data_registry.py list | fetch <id> | vet <id-or-csv>`. The vet gate fails
normalised units, flags dead-sensor sentinels (-127/-999/65535) and saturation pinning, and
reports coverage against `logging_schema` v1. Adopted so far: the in-repo IoTPond set
(calibration), the Mendeley red-tilapia pond (held-out validation, CC BY 4.0), the CC0
Wageningen greenhouse-climate sets (AGC editions 1–2), and the two climate APIs.

The decisive gap stands: **no public dataset pairs feeding records with nitrogen species**
(#87). Two routes around it are now on the record:

- **Inference, tried honestly** (`scripts/validate_twin.py`): feed inferred from fish
  GROWTH (measured fortnightly) via literature FCR, and the twin scored against NITRATE
  on held-out days — different observables, so not circular. Verdict, computed not
  asserted: MIXED. The twin tracks the direction of held-out nitrate on 5 of 7 QC-passing
  ponds (shape r median +0.30, best +0.98) but does not beat a linear-trend null on
  level, because the pond sensors are uncalibrated (nitrate in the thousands of "mg/L").
  Full method, caveats and per-pond results: `data/twin_validation.json`; the inferred
  paired series, provenance in every row: `data/inferred_feed_nitrogen.csv`.
- **Creation** (`docs/feed_response_protocol.md`): a 21-day feed step-response experiment
  on a real system with titration kits that read true mg/L — one operator produces a more
  decisive record than everything surveyed, because it contains a KNOWN change in the
  forcing function. This is the route the inference result points to: the bottleneck is
  sensor quality, not missing feed records.

Until such a record exists, the twin's absolute numbers are literature-seeded projections,
and every report states that.

## Honesty contract

Every module keeps its own cited parameters (`fishgrowth.TGC`, `cropgrowth.DLI_SATURATION`,
`climate.GreenhouseParams` docstring) and its own `NOT_MODELLED`; `production.NOT_MODELLED`
is the union and travels with every summary. The invariant tests to preserve when extending:
feed eaten by the fish model equals feed dissolved by the nitrogen model
(`test_the_fish_and_nitrogen_models_eat_the_same_feed`), determinism, immutability, and
dt-consistency.

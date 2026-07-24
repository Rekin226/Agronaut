# Feasibility and Business Case

A design that is *engineering-feasible* (the water balance closes, the biofilter is sized) can
still be *economically* or *operationally* infeasible. Run all three checks before building.

## 1. Resource feasibility

- **Water budget.** Recirculating systems use little water, but evapotranspiration + evaporation
  set a daily makeup requirement. If it exceeds the available budget the design is not feasible —
  shrink the grow area to the nearest-feasible size (Agronaut computes this).
- **Energy.** Continuous pumping and aeration, plus heating/cooling to hold the temperature band,
  are the binding constraint in most real deployments. Confirm a reliable power source (grid,
  generator, or solar) before anything else.
- **Source water quality.** High salinity, hardness, or chlorine/chloramine can make an otherwise
  fine site unworkable without treatment (see `water_source_and_treatment`).
- **Climate.** The mean temperature must suit both the fish and the crop; wide diel swings or
  seasonal extremes may require a greenhouse or cooling that changes the cost basis.

## 2. Operational feasibility

- **Labour and skill.** Daily monitoring (fish behaviour, pH, EC, water level) and weekly testing
  are non-negotiable. A system with no one to watch it fails, regardless of design.
- **Supply chains.** Reliable access to fingerlings, feed or nutrient salts, and replacement parts.
  A design that depends on an import that arrives twice a year is fragile.
- **Failure tolerance.** Power cuts crash dissolved oxygen within hours (see
  `dissolved_oxygen_and_aeration`). Backup aeration is often the difference between a setback and a
  total loss.

## 3. Market and business feasibility

- **Who buys the output, at what price?** Feasibility is crop value vs energy + labour + inputs.
  Leafy greens alone rarely clear it; high-value herbs plus fish protein, sold locally for a
  freshness premium, are where the case closes.
- **Scale.** Small systems carry the same fixed costs (your time, a controller, a structure) as
  larger ones but spread them over less output. Many pilots are feasible only as demonstrations,
  not businesses — that is a legitimate goal, but name it.

## For grants and pilots (funder-facing)

Programs (FAO, WFP, GIZ, CGIAR) fund pilots that show: a named local partner, a realistic
outcome estimate (food/water/income), a cost that includes energy and labour, and a plan for who
operates and maintains it after the grant. A cited, honest design that lists what it does *not*
model is more fundable than an optimistic one — funders scrutinise over-claims closely.

_Sources: FAO Technical Paper 589; Gates/GIZ AIEP advisory-tool lessons (2025); WFP H2Grow
hydroponics deployment reports; practitioner feasibility guidance._

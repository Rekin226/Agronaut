# Outreach & Evidence

The code is not the bottleneck anymore — **evidence and distribution are**. Funders
(FAO/DPGA, WorldFish/CGIAR, GIZ, WFP) converge on the same minimum package: a named
partner, real users with usage data, an outcome survey, a local language, a documented
advice-safety evaluation, and a privacy policy. Agronaut already has the last two
([safety eval](../dpg/safety_eval/), [privacy policy](../dpg/PRIVACY.md)); this folder holds
the scaffolding for the rest.

## What's here

- **[CASE_STUDY_TEMPLATE.md](CASE_STUDY_TEMPLATE.md)** — fill this with your OWN system.
  You are user zero; one documented before/after is the seed crystal of the whole evidence
  package. No partner needed to start.
- **[WORLDFISH_BRIEF.md](WORLDFISH_BRIEF.md)** — a one-page outreach brief for the single
  best-matched institutional partner (CGIAR Asia Digital Hub at WorldFish), framed as the
  MIT digital implementation of FAO Technical Paper 589.
- **[EVIDENCE_CHECKLIST.md](EVIDENCE_CHECKLIST.md)** — the funders' minimum package, what's
  done, and exactly what remains.

## The move (from the gap analysis)

1. **Submit the DPG application** ([draft ready](../dpg/APPLICATION_DRAFT.md)) — cheapest,
   highest-leverage credibility unlock; opens the FAO/DPGA door.
2. **Become user zero** — run your own system through Agronaut, log real outcomes (the
   calibration loop is built for exactly this), fill the case-study template.
3. **Approach WorldFish** — DPG status + your case study is the pitch.
4. **Generate a pilot proposal** with `render_pilot_proposal` (or `aqua_model.pilot`) once a
   site/partner is real.

Steps 1–2 are yours and need no more code. Everything here exists to make them faster.

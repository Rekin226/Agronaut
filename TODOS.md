# TODOS

> **Active plan:** the phased gap-closure plan (vs hermes-agent + DPG funding ladder) lives in
> [`docs/PLAN.md`](docs/PLAN.md) — work it top-to-bottom, one task per PR.

## Product (deferred from CEO review 2026-06-16)
- [x] **Pilot-proposal generator.** A funder-ready document: proposed system for a site + the
  ask, projected food/water outcomes, and the data the install will produce.
  - **Why:** the artifact that moves a B2G deal.
  - **DONE (2026-07-25):** `aqua_model/pilot.py` (`PilotInfo`, `projected_outcomes`,
    `to_pilot_proposal`) + agent tool `render_pilot_proposal`. Deterministic, cited, honesty
    layer preserved. Built ahead of a partner conversation as evidence/outreach scaffolding
    (the go-to-market pivot), with the framing designed to be tuned once a partner engages.
- [ ] **Report sensitivity table.** Show outcome deltas when water budget or crop mix vary
  (e.g. +20% water → +X kg/yr). Persuasive for funders.
  - **Why:** makes a design feel analyzed, not asserted.
  - **Context:** a slice of the M2 optimizer surfaced in the report. Build when M2 lands rather
    than hand-rolling in M1. Depends on: M2 optimizer. Priority P3.

## Cleanup
- [ ] **Dedupe boilerplate-text filter.** `looks_like_boilerplate` (inside `build_vector_store`,
  `srcs/chatbot.py:204`) and module-level `_is_boilerplate_text` (`srcs/chatbot.py:238`) are
  near-identical. Collapse into one helper.
  - **Why:** DRY; two copies drift out of sync.
  - **Context:** Both live in the RAG layer that the design doc demotes to a citation tool in
    M3. Fold this dedupe into the M3 refactor rather than a standalone change.
  - **Depends on:** M3 (RAG demotion) — see the approved design doc.

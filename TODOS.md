# TODOS

## Product (deferred from CEO review 2026-06-16)
- [ ] **Pilot-proposal generator.** A funder-ready document: proposed system for a site + the
  ask, projected food/water outcomes, cost, and the data the install will produce.
  - **Why:** the artifact that moves a B2G deal.
  - **Context:** thin wrapper over the M1 PDF report export. Build at sequencing step 3 — after
    the Taiwan-system validation exists and a real in-region partner / program officer can shape
    the framing. Do NOT build speculatively.
  - **Depends on:** M1 report export; one live funding/partner conversation. Priority P2.
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

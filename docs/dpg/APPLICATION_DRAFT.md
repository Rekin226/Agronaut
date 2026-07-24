# Agronaut — DPG Registry Application (DRAFT)

_Prepared 2026-07-24. Status: **ready for owner review; NOT yet submitted.**_

This is a filled draft of the answers the
[Digital Public Goods registry candidate submission](https://github.com/DPGAlliance/publicgoods-candidates)
asks for. Submission is an outward-facing action left to the project owner — see the final
section. Answers draw on `DPG_EVIDENCE.md`, `PRIVACY.md`, `AI_TRANSPARENCY.md`, and
`SDG_MAPPING.md`.

---

## Basic information

- **Name:** Agronaut
- **Description:** An open-source conversational agronomy agent for aquaponics (hydroponics
  forthcoming). It turns the trial-and-error of designing a fish-and-plant food system into a
  calculated, cited, honest answer: a deterministic, unit-tested engineering core sizes and
  optimizes systems, while a pluggable open-weights language model collects facts, routes to
  the right tool, and explains results. Reachable on Telegram, WhatsApp, and the web.
- **Website / repository:** https://github.com/Rekin226/Agronaut
- **License:** MIT
- **Type:** Software
- **Sectors / SDGs:** SDG 2 (Zero Hunger), SDG 6 (Clean Water — water-use efficiency),
  SDG 12 (Responsible Consumption).

## The nine indicators (summary — full evidence in DPG_EVIDENCE.md)

1. **SDG relevance:** SDG 2.3/2.4, 6.4, 12.2 — see SDG_MAPPING.md.
2. **Open licence:** MIT (approved).
3. **Ownership:** repository + LICENSE.
4. **Platform independence:** self-hosted open-weights brain (`openai_compat`), no mandatory
   proprietary dependency; deterministic core needs no LLM; abstracted channels.
5. **Documentation:** README + docs/ + tests.
6. **Non-proprietary data export:** in-chat `/export` → open JSON; SQLite storage.
7. **Privacy & laws:** PRIVACY.md; `/export` + `/delete_me`; data minimization; no training
   on user data.
8. **Standards & best practices:** OpenAI-compatible API, WhatsApp Cloud API,
   sentence-transformers; TDD, 300+ tests, CI.
9. **Do no harm:** trust gate, cited coefficients + "not modeled" disclosure, no invented
   numbers, human-gated PII-stripped community sharing, advice-safety golden set.

## Suggested positioning (for the DPGA reviewer & the FAO–DPGA food-security CoP)

> Agronaut is, to our knowledge, the first MIT-licensed **digital implementation of FAO
> Fisheries & Aquaculture Technical Paper 589** — it makes that manual's small-scale
> aquaponic sizing computable, cited, and calibratable, delivered to smallholders over the
> channels they already use. Its deterministic, source-cited engineering core directly
> addresses the "wrong AI advice harms farmers" concern that funders (Gates/GIZ AIEP) flag
> as the central risk of LLM advisory tools.

## Owner actions before/at submission (NOT done autonomously)

- [ ] Review every answer above for accuracy and voice.
- [ ] Confirm the SDG target selection (optionally with a domain reviewer).
- [ ] Ensure PRIVACY.md is reachable at a stable public URL (the repo path suffices).
- [ ] Open the nomination on the DPGA candidates repo (guided form / PR).
- [ ] Optionally introduce Agronaut to the FAO–DPGA food-security community of practice
      using the positioning paragraph above.

> Everything needed to submit is prepared and in-repo. The act of submitting to the DPGA —
> an outward-facing step that commits the project publicly — is intentionally left to the
> owner.

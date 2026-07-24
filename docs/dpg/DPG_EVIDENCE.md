# Agronaut — Digital Public Goods Standard: Evidence Map

_Last updated: 2026-07-24_

Mapping Agronaut against the nine
[DPG Standard](https://github.com/DPGAlliance/DPG-Standard) indicators, for a registry
submission. Status: **draft — owner to review before submitting.**

| # | Indicator | Status | Evidence |
|---|---|---|---|
| 1 | **Relevance to SDGs** | ✅ | SDG 2 (Zero Hunger — food production from less land/water), SDG 6 (Clean Water — water-use-efficiency optimization, water-budget feasibility), SDG 12 (Responsible Consumption — resource-efficient food). See `docs/dpg/SDG_MAPPING.md`. |
| 2 | **Approved open licence** | ✅ | MIT (`LICENSE`) — on the DPGA-approved list. |
| 3 | **Clear ownership** | ✅ | Copyright + repository ownership documented in `LICENSE` and the repo. |
| 4 | **Platform independence** | ✅ | No mandatory proprietary dependency. The tool-calling brain runs on self-hosted open-weights via `LLM_PROVIDER=openai_compat` (vLLM/llama.cpp); the deterministic design/optimizer core needs no LLM at all; channels are abstracted (`ChannelAdapter` — Telegram, WhatsApp, REPL) with no channel imported by the brain (enforced by test). |
| 5 | **Documentation** | ✅ | `README.md` (install, run, providers, channels), `docs/PLAN.md`, `docs/dpg/*`, in-code docstrings; test suite documents behavior. |
| 6 | **Data extraction / non-proprietary format** | ✅ | `/export` returns all of a user's data as open JSON; stored in SQLite (open format). Reachable in-chat; tested (`test_data_rights.py`). |
| 7 | **Privacy & applicable laws** | ✅ | `docs/dpg/PRIVACY.md` — collection, purpose limitation, retention, access control, and in-chat `/export` + `/delete_me` (right to erasure). Data minimization by design; no training on user data. |
| 8 | **Standards & best practices** | ✅ | Open standards: OpenAI-compatible API, WhatsApp Cloud API, sentence-transformers; TDD with a 300+ test suite; cited engineering model; CI on push/PR. |
| 9 | **Do no harm by design** | ✅ | Trust gate rejects unvalidated input; every quantitative answer cites sources + lists what is NOT modeled; LLM forbidden from inventing numbers; community sharing is human-gated + PII-stripped; advice-safety golden set scored each release. See `docs/dpg/AI_TRANSPARENCY.md`. |

## Submission checklist (owner actions)

- [ ] Confirm SDG target selection with a domain reviewer.
- [ ] Publish `PRIVACY.md` at a stable public URL (repo is sufficient).
- [ ] Submit via the [DPG registry candidate process](https://github.com/DPGAlliance/publicgoods-candidates)
  (a nomination PR / the guided form).
- [ ] Optionally engage the FAO–DPGA food-security community of practice, framing Agronaut
  as an MIT-licensed digital implementation of FAO Technical Paper 589.

> This evidence map is complete and buildable today. Actual submission to the DPGA is an
> outward-facing action left to the project owner.

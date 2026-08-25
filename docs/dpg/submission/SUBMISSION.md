# DPG Registry Submission — Agronaut (paste-ready)

_Prepared 2026-07-25. **Status: ready for the owner to submit.** These are the actual
answers for the Digital Public Goods Alliance nomination. Submitting is an outward-facing
action left to the owner — see "How to submit" at the end._

Two ways in (pick one):
- The **guided eligibility form** at <https://www.digitalpublicgoods.net/submission-guide> —
  paste the answers below.
- A **nomination PR** to <https://github.com/DPGAlliance/publicgoods-candidates> — use
  [`nominee.json`](nominee.json) in this folder as the starting record.

Before submitting, the owner should confirm the fields marked **[CONFIRM]** (contact,
copyright holder, organization name) — only the owner can speak to those.

---

## Basic information

- **Name:** Agronaut
- **Short description:** An open-source (MIT) conversational agronomy agent for aquaponics
  and hydroponics. A deterministic, unit-tested engineering core sizes and optimizes
  soil-less food systems and produces cited designs; a pluggable open-weights language model
  collects facts, routes to the right tool, and explains results in plain language. Reachable
  on Telegram, WhatsApp, and the web, in text, photo, or voice.
- **Website / repository:** <https://github.com/Rekin226/Agronaut>
- **Type:** Software
- **Sectors:** Agriculture; Food & Agriculture; Climate/Environment (water-use efficiency).
- **Owner / organization:** [CONFIRM — individual maintainer or organization name + contact]

---

## Indicator 1 — Relevance to the SDGs

- **SDG 2 (Zero Hunger), targets 2.3 / 2.4.** Agronaut lowers the barrier to running a
  productive aquaponic or hydroponic food system: it turns trial-and-error into a sized,
  cited design (fish count, feed, grow area, bill of materials, operating envelope) and its
  optimizer maximizes food or protein under a smallholder's binding constraint.
  Evidence: `README.md`, `aqua_model/`, `docs/dpg/SDG_MAPPING.md`.
- **SDG 6 (Clean Water), target 6.4.** Recirculating systems reuse water; the optimizer can
  maximize water-use efficiency directly, and a water-balance check tests every design
  against a fixed daily water budget — central for water-scarce and arid deployments.
- **SDG 12 (Responsible Consumption), target 12.2.** An independent nitrogen consistency
  check flags over-sizing, steering operators away from wasted feed, water, and materials.
- Evidence URL: <https://github.com/Rekin226/Agronaut/blob/main/docs/dpg/SDG_MAPPING.md>

## Indicator 2 — Use of an approved open licence

- **Licence:** MIT (on the DPGA-approved list).
- Evidence: <https://github.com/Rekin226/Agronaut/blob/main/LICENSE>

## Indicator 3 — Clear ownership

- Ownership is explicit in the repository and `LICENSE`.
- **[CONFIRM]** copyright holder name and a copyright/ownership URL.

## Indicator 4 — Platform independence

- No mandatory closed-source dependency. The tool-calling assistant runs on **self-hosted
  open-weights models** via `LLM_PROVIDER=openai_compat` (vLLM / llama.cpp / LM Studio), and
  the deterministic design and optimizer modes need **no LLM at all**. Channels are
  abstracted (`ChannelAdapter`: Telegram, WhatsApp, REPL); a test enforces that the agent
  core imports no proprietary channel SDK.
- Evidence: `agent/llm.py` (openai_compat provider), `README.md` "self-hosted, no vendor",
  `agronaut_agent/channels/`, `agronaut_agent/tests/test_channels.py`.

## Indicator 5 — Documentation

- README (install, providers, channels, deployment), `docs/PLAN.md`, `docs/dpg/*`,
  in-code docstrings, and a 344-test suite that documents behavior.
- Evidence URL: <https://github.com/Rekin226/Agronaut/tree/main/docs>

## Indicator 6 — Mechanism for extracting data (non-proprietary format)

- A user can export **all** of their data as open JSON in-chat with `/export`; erase it with
  `/delete_me`. Storage is SQLite (open format).
- Evidence: `agronaut_agent/core.py` (`export_user_data`, `delete_me`),
  `agronaut_agent/tests/test_data_rights.py`.

## Indicator 7 — Adherence to privacy and applicable laws

- Privacy & data policy covers collection, purpose limitation, retention, access control,
  consent, and the right to erasure. Data minimization by design; **no training on user
  data**. Usage analytics is content-free (hashed ids, counts only).
- Evidence: <https://github.com/Rekin226/Agronaut/blob/main/docs/dpg/PRIVACY.md>

## Indicator 8 — Adherence to standards & best practices

- Open standards: OpenAI-compatible API, WhatsApp Cloud API, sentence-transformers,
  agentskills.io. Engineering: test-driven development, a 344-test suite, cited coefficients
  (FAO 589, UVI/Rakocy), and a per-release advice-safety golden set.
- Evidence: `docs/dpg/AI_TRANSPARENCY.md`, `docs/dpg/safety_eval/`, `skills/aquaponics-engineer/`.

## Indicator 9 — Do no harm by design

- A validation gate rejects unvalidated input before any calculation; every quantitative
  answer cites its source coefficients and lists what it does **not** model; the LLM is
  forbidden from inventing numbers. Cross-operator knowledge sharing is human-gated and
  PII-stripped. An advice-safety golden set (191 probes) is scored each release.
- **AI-specific transparency:** trains no models; retrieval draws only from cited public
  sources, each surfaced with its `[source:]` label.
- Evidence: <https://github.com/Rekin226/Agronaut/blob/main/docs/dpg/AI_TRANSPARENCY.md>,
  `aqua_model/validate.py`, `scripts/safety_eval.py`.

---

## Positioning note (for the reviewer / FAO–DPGA CoP)

Agronaut is, to our knowledge, the first MIT-licensed **digital implementation of FAO
Fisheries & Aquaculture Technical Paper 589** — it makes that manual's small-scale
aquaponic sizing computable, cited, and calibratable. Its deterministic, source-cited core
directly addresses the concern funders raise about LLM farm advice ("wrong advice harms
farmers"): the advice is verifiable and safety-checked each release.

## How to submit (owner action)

1. Confirm the **[CONFIRM]** fields above (contact, copyright holder, organization).
2. Ensure `docs/dpg/PRIVACY.md` is reachable at its public repo URL (it is).
3. Submit via the guided form (<https://www.digitalpublicgoods.net/submission-guide>) **or**
   open a nomination PR on `DPGAlliance/publicgoods-candidates` using `nominee.json`.
4. Optionally introduce Agronaut to the FAO–DPGA food-security community of practice using
   the positioning note above.

> Everything needed is prepared and in-repo. The act of submitting — an outward-facing step
> that publicly commits the project — is intentionally left to the owner.

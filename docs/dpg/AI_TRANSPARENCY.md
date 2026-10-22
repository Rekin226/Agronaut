# Agronaut — AI Transparency

_Last updated: 2026-07-24_

This note documents how AI is used in Agronaut, for the
[DPG Standard](https://digitalpublicgoods.net/standard/) AI-transparency expectations.

## The architecture: deterministic core, LLM only at the edges

Agronaut's engineering answers are **not** produced by a language model. They come from
`aqua_model`, a parametric, unit-tested, cited engineering model built from published
equations (FAO Fisheries & Aquaculture Technical Paper 589; UVI/Rakocy; peer-reviewed
literature). Every coefficient carries its value, range, unit, and source.

The language model does three things only:
1. Collect facts from the conversation.
2. Route to the right deterministic tool.
3. Explain the tool's result in plain language.

A validation gate (`validate_design_input`) sits between the LLM and the engineering model:
the LLM proposes values, the gate rejects anything malformed or out of range, and only
typed, validated input reaches the calculation. A hallucinated number cannot become a
design.

## Models used

- **Language model:** pluggable and operator-chosen (`LLM_PROVIDER`). Options include fully
  open-weights models you self-host (`openai_compat` → vLLM/llama.cpp with e.g. Qwen2.5 or
  Llama-3.1) — the configuration used for DPG platform-independence — or hosted open models.
  No proprietary model is required.
- **Vision (optional):** an operator-chosen VLM turns a photo into a text observation. It
  only *observes*; it never emits numbers or calls a tool. Parts of that contract are
  enforced in code rather than merely requested in the prompt: a deterministic guard
  (`agent.vision.sanitize_observation`) strips measurement readings and prescriptive
  sentences from the observation, and discards observations the model could not read.
  Stripping is pattern-based, so an unusual phrasing can occasionally survive it. A
  named condition is flagged in code, and the flag attaches an explicit instruction that
  the verdict is unverified and must be cited or hedged — that last step is an instruction
  to the model, not a mechanical guarantee. The guard is scored in CI
  (`scripts/safety_eval.py`, `vision_guard` category); the model's behaviour on real field
  photographs is scored separately by `scripts/vision_eval.py`.
- **Visual triage (deterministic, no model):** the candidate causes offered for a photo are
  not produced by the vision model. `agent.observation_features` maps the observation's prose
  to categorical features (which leaves, what pattern, root/water appearance, fish behaviour)
  by fixed patterns, and `aqua_model.triage` maps those features through a fixed table to a
  ranked DIFFERENTIAL. Every candidate names the knowledge-base document it came from, offers
  the checks that would discriminate between candidates, and states no dose *quantity* — the
  model cites no dosing coefficient, so it gives no amount. Ordering is the knowledge base's
  own: pH lockout before an iron shortfall, water quality before any fish pathogen. The table
  never returns a single verdict, because a photograph cannot distinguish the candidates it
  lists. Two limits are worth stating plainly: the prose→features step is pattern-based, so an
  unusual phrasing can yield a shorter differential (or none, in which case the user is asked
  for a better photo rather than given a guess); and while the differential itself is
  deterministic and cited, the language model still writes the reply around it, so the
  *presentation* carries the same caveats as any other model output. Scored in CI under the
  `visual_triage` category, and a test asserts every citation resolves to a real document.
- **Speech (optional):** an operator-chosen ASR model (default: local Whisper-class)
  transcribes voice notes to text.
- **Embeddings:** a sentence-transformers model for semantic recall over the user's own
  notes and the knowledge base.

## Training data

Agronaut trains **no** models. It does not fine-tune on user data, and user conversations
are never sent anywhere for training. Retrieval-augmented answers draw only from a curated
set of cited, public sources (`knowledge/` + `urls.txt`), and every retrieved passage is
surfaced with its `[source: ...]` label.

## Known limitations (what is NOT modeled)

Each design result lists these explicitly. At the model level they include: pH/alkalinity
dynamics, micronutrients, salinity, solids handling, pests/disease progression, fish cohort
logic, and per-crop evapotranspiration. The seed coefficients are calibration starting
points from the literature, not guarantees; they are meant to be calibrated against a real
running system, and the system says so.

## Advice-safety evaluation

A fixed set of question/answer probes (`docs/dpg/safety_eval/`) exercises design,
troubleshooting, out-of-scope refusals, and trust-gate behavior. It is scored each release
as a regression signal on advice quality (a report, not a hard gate), following the
Gates/GIZ AIEP recommendation to hold LLM advisory tools to a golden-set safety check.

## Generation-quality evaluation

Retrieval being right does not make the answer right, so answers are also scored against the
passages they were written from (`scripts/faithfulness_eval.py`). Each answer is split into
claims, and a judge model from a different family rules on each one using the retrieved
context alone. It must quote the supporting sentence, and code checks the quote is really
there. Citations are checked by code, with no model.

Measured on 2026-10-06 over 33 fresh golden-set answers (484 claims): **faithfulness 0.79**
with the default judge (gpt-oss-20b, quote-first prompt); citation accuracy 1.00 with no
fabricated sources. The 2026-09-30 answers scored 0.84 under the same judge, and 0.88 to 0.90
under the other judges tried. The two runs differ topic by topic in both directions (up to
0.25 either way on topics of 10 to 60 claims), which is what a fresh set of 33 answers moves
by, so read the figure as about 0.8 rather than to two decimals.

How far to trust that number: the judge was checked against one person's labels on 29
claims. Agreement is fair, not strong (Cohen's kappa 0.24 on the blind labels, 0.39 after the
person took a second look at the 13 disputed claims and changed 2). Both figures are kept
because the second look was not blind. One judge we tried, Claude Sonnet 5, agreed with the
person no better than chance and is not used. Claude Opus 5.5, run through Anthropic's Message
Batches API on 2026-10-10 (479 claims in about 2.5 minutes, at half the standard price), was
the strictest judge so far (faithfulness 0.78 on the 2026-09-30 answers) and agreed with the
person less than gpt-oss-20b did (kappa 0.14 against 0.39 on the reviewed labels), so it is not
the default either. A Claude judge rating Claude's answers is the same-family pairing the
course warns about; the batch path stays for re-judging, not as the judge of record.

On Claude, knowledge passages now reach the model as native search results, so the API ties
each cited passage to the sentence that used it. Measured on 2026-10-10 (33 golden-set
questions, Claude Sonnet 5.5 answering, Claude Opus 5.5 judging both arms through the Batch
API because the free gpt-oss-20b endpoint did not respond): faithfulness 0.906 with native
citations against 0.904 with "[source: ...]" labels, 12 questions better and 16 worse, no
fabricated citation in either (2026-10-10_citations_*.json). It is no measured gain in
grounding; it is kept for what it changes structurally, the API rather than the model
recording which source backs which sentence, and the same path shows web sources to the user. More labels, ideally from a second person, are
needed before the figure can be called validated. It is a report, never a gate, and it never
runs in CI.

# Field-guide translations

`fr.json` and `zh-Hant.json` contain draft translations of the seven original Agronaut
guides in `knowledge/field_*.md`. They support direct offline reading through the CLI,
the native guide panel and `soil.field_guidance`. They are not official translations
of the FAO publications linked by the original guides. The publication links remain
in English and are inherited from the original source at read time.

These files are deliberately outside the English semantic-search corpus. The wheel
ships both JSON bundles under `share/agronaut/field_guides`. A corpus override must
include both the `knowledge` directory and the requested translation bundle.

Each entry contains `title`, `applicability`, `body_markdown` and `source_sha256`.
The bundle declares `schema_version: 1`, its exact language code, and
`translation_status: "draft"`. No reviewer or completed review is implied. The reader
adds a localized draft notice, applicability and the original source references.
Capability IDs, command names, symbols and equations remain unchanged across languages.

When an English guide changes:

1. Compare its entire new text with each translation. Update the concepts, preparation
   steps and tool limitations together. Preserve unknown versus measured-zero values,
   assumptions versus observations, and calculated feasibility versus field validation.
2. Check terminology with a speaker and an agricultural reviewer familiar with the
   intended users. Keep a record of the review before making any reviewed-language or
   regional-validity claim. The current bundles remain drafts pending this work.
3. After reconciling the text, update that entry's `source_sha256` with the SHA-256 of
   `Path(source).read_text(encoding="utf-8").encode("utf-8")`. Do not refresh hashes
   merely to bypass a stale-translation error. Hash equality proves source alignment
   by version, not linguistic accuracy.
4. Check every catalogued guide in both languages through the CLI and native panel. Run the
   field-guide, native guide UI and packaging tests with a temporary database. The
   packaging test reads every language from the built wheel using only the standard
   library, so a checkout-only success is insufficient.

Adding another language requires an explicit language entry and localized reader labels,
a complete bundle, packaging inclusion and equivalent tests. Do not infer a preferred
language, crop coefficient or irrigation method from a country name. Independent grower
questions and comprehension trials are still needed to assess practical usefulness.

The guided `/scout` conversation also offers explicit `en`, `fr` and `zh-Hant` choices.
Its draft translations live in `agronaut_agent/scouting_language.py`, separately from the
guide bundles. Review the questions, plant-count terminology, unknown versus zero,
personal observation versus another person's report, preview, validation and save/cancel
instructions together. The conversation preserves supplied narrative text and stores
canonical provenance values; it does not translate the operator's evidence. These prompts
also require independent local comprehension and agronomic review before any reviewed
language claim. Other application forms and technical service errors may remain English.

## Field-planning question drafts

`planning_questions.json` supplies French and Traditional Chinese questions for
`/fieldplan fr` and `/fieldplan zh-Hant`: water demand, root-zone budgets, drip and
sprinkler checks, budget-target adoption and surface-event evaluation. Calculation
reports, provider details and native validator errors remain English; every translated
conversation states this and its draft status. The bundle is shipped with the wheel
and stays outside the semantic-search corpus.

Each entry contains its `english` source template, `fr` and `zh-Hant` drafts, and
`translate_slots` for composed question fragments. Both translations must preserve
exactly the same named placeholders. Runtime matching requires the complete English
template; an unmatched or incomplete question raises `FIELD_TRANSLATION_UNAVAILABLE`
instead of silently supplying an older translation. `/fieldplan en` resumes the same
draft when a translation is unavailable. Switching language preserves answers and
changes the draft revision, so stale replies cannot update it.

When editing, compare the complete source question and both translations. Preserve
units, numeric bounds, experimental scope, unknown-versus-zero distinctions, required
sources and explicit `fetch`/`use` decisions. Numbers, dates, record IDs, crop names
and supplied narratives are inserted without translation. Only declared choice tokens
have localized aliases; a free-text source or crop name is never rewritten. Check all
three surface geometries, repeated stations/flow intervals and equipment-target prompts,
as well as basic water questions. Run the planning-language, channel and installed-wheel
checks. Passing them verifies routing and source-template alignment, not linguistic or
agronomic correctness; independent local review is still needed.

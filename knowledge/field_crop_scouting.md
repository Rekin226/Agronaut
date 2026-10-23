# Investigating crop symptoms

_Domain: soil_
_Evidence: guidance_
_Applicability: General observation preparation for soil-grown crops; no confirmed pest or disease diagnosis, treatment threshold, pesticide dose or regional validation._
_Sources: [Penn State Extension, Scouting for Pests and Diseases in Vegetable Crops, updated 9 March 2026](https://extension.psu.edu/scouting-for-pests-and-diseases-in-vegetable-crops); [University of Delaware Extension, Diagnosing Horticulture Plant Problems, reviewed March 2025](https://www.udel.edu/content/dam/udelImages/canr/pdfs/extension/factsheets/Diagnosing-Horticultural-Plant-Problems.pdf); [University of Maryland Extension, IPM: Prevent, Identify, and Manage Plant Problems, updated 17 October 2024](https://extension.umd.edu/resource/ipm-prevent-identify-and-manage-plant-problems); [Penn State Extension, High Tunnel Vegetable Crops: Designing a Scouting Plan, updated 16 December 2025](https://extension.psu.edu/high-tunnel-vegetable-crops-designing-a-scouting-plan)_

## Start with observations

Yellow leaves, wilting and leaf spots describe symptoms; they do not identify a cause.
Different stresses can look alike, and more than one can occur together. Look for physical
evidence such as insects or fungal structures, while keeping the suspected cause separate
from what you actually saw. Delaware's diagnostic guide explains this distinction.

Record the crop and variety, plant age, first observation date, affected parts, progression
and locations. Inspect leaf undersides and compare affected areas with apparently healthy
plants. Note whether damage follows rows, edges, wet spots or particular varieties, along
with recent weather, irrigation and fertilizer changes. Penn State's vegetable-scouting
guide describes these observations; a field pattern is a clue, not proof.

## Decide what to check next

Consider water supply and drainage, root condition, recent operations, and possible living
pests together. A leaf symptom can originate in roots; finding an insect nearby does not
prove it caused the damage. Maryland's IPM guide recommends investigating plant history
and avoiding conclusions based only on association.

Keep repeatable locations and dates. Include the crop varieties present and record the
sampling approach so later visits can be compared. Penn State's high-tunnel scouting guide
emphasizes adapted sampling and records; its local sampling numbers and action thresholds
are not universal recommendations for other crops or regions.

## Keep the evidence in Agronaut

For guided entry in chat, select a unit with a crop cycle and use `/scout`. Answer the
questions, review the preview and reply `save`; use `/back` to edit or `/cancel` to discard.
Choose `/scout en`, `/scout fr` or `/scout zh-Hant` for English, French or Traditional
Chinese prompts. The French and Chinese prompts are draft translations awaiting local
review. French also accepts enregistrer to save, and Traditional Chinese accepts 儲存.
You can also select the correct unit and crop cycle
in **Crop records**, then use **Record or correct scouting**. Save the observation date, location, symptoms, source and provenance. Put
suspected explanations in the narrative as questions, not confirmed diagnoses.

The optional counts describe inspected and affected **plants**, not insects, lesions or
leaves. Enter both counts or leave both unknown. Zero affected plants means none were
observed in that sample; a blank count is not zero. Samples may overlap, so Agronaut does
not combine them into a field-wide disease percentage.

Use a new entry for a later visit and a correction for an error in an existing observation.
The offline commands are `agronaut crop describe` and `agronaut crop record_scouting --input scouting.json`.

## Review uncertain causes

Share the observation history and representative material with a local crop adviser or
plant diagnostic service; ask that service how to collect and submit samples. This guide
does not confirm a cause from a photograph or choose a treatment. These US extension
references support observation principles, not pest prevalence, spray rules or validated
crop recommendations for Burkina Faso or Taiwan. The recording instructions describe
Agronaut itself; French and Traditional Chinese versions require local review.

# Checking sprinkler distribution

_Domain: irrigation_
_Evidence: guidance_
_Applicability: General sprinkler observations; no universal spacing, wind limit or runtime recommendation._
_Sources: [FAO Irrigation Water Management, Training manual 5, provisional edition, chapter 5](https://www.fao.org/4/s8684e/s8684e06.htm)_

## Understand dry and wet patches

Sprinkler patterns must overlap to distribute water across a field. Wind and operating
pressure can change that distribution; use the manufacturer's specified pressure range.
Application rate must be considered with soil intake to avoid ponding and runoff.
Droplet size can also matter for vulnerable crops and soils. See FAO chapter 5.

A mean application depth alone does not describe the driest parts of the field.
Changing pressure or spacing without checking the resulting pattern can leave the
distribution problem unresolved.

## Prepare an Agronaut catch test

Document the tested layout, sprinkler and nozzle, operating pressure, duration, wind,
collector locations and collection areas. Keep every collector reading, including
measured zeros; an absent reading is unknown. Record rain or other interference so the
reviewer can decide whether the test supports an irrigation comparison. Do not treat
rainfall mixed into a collector as known sprinkler application.

Inspect `soil.sprinkler_catch_test` with `agronaut capabilities --system soil` before
collecting data for the supported sample contract. Use `evaluate-sprinkler-catches`
or its field-records form. Agronaut computes statistics for supplied observations;
it does not independently verify test placement or field representativeness.

## Next step and limits

Keep catch-test results separate from crop-water demand, soil storage and pump delivery.
A distribution statistic is not a complete irrigation schedule. After an operating or
equipment change, retain the earlier result and record a new comparable test.

The current design calculator covers a flat solid-set grid with identical full-circle
nozzles and explicit spacing/wind evidence. It is not a centre-pivot, travelling-gun or
sloping-field design model. Do not substitute a generic spacing or assumed efficiency
for missing site and equipment evidence.

Original Agronaut guidance, source review 2026-10-20. The linked publication supports
the physical principles; the recording workflow describes this implementation.

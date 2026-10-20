# Checking a drip system

_Domain: irrigation_
_Evidence: guidance_
_Applicability: General drip observations; no automatic fault diagnosis, cleaning dose or irrigation runtime._
_Sources: [FAO Irrigation Water Management, Training manual 5, provisional edition, chapter 6](https://www.fao.org/4/s8684e/s8684e07.htm)_

## Understand uneven wetting

Drip wets only part of the root zone. The wetting pattern depends on soil and emitter
discharge, so equal runtime does not establish equal root-zone water. Emitters can
block with sediment, biological material or precipitated chemicals. Filtration can
address some material but does not resolve every cause. Pressure, layout and equipment
condition also matter. See FAO chapter 6.

Water savings depend on operation as well as equipment. Installing drip does not
remove the crop's water requirement. Assess where the water goes rather than assuming
a fixed saving percentage.

## Prepare an Agronaut emitter test

Keep emitter identifiers and sample locations. Record collected volume and collection
duration for each sampled emitter, along with the equipment, operating configuration,
pressure observations and source notes. Include poorly flowing locations in the record;
do not silently remove them. Distinguish a measured zero collection from an unknown
volume. Agronaut's emitter test accepts a supplied sample design and checks its contract;
it does not establish whether that sample represents the whole field.

Inspect `soil.drip_emitter_test` with `agronaut capabilities --system soil`, then use
`evaluate-drip-emitters` or the field-records form. The result describes sampled flows
and their uniformity. It cannot identify blockage versus pressure loss by itself, infer
whole-zone delivery, or choose a runtime. Keep manufacturer instructions and local
inspection evidence alongside the result before deciding on maintenance.

## Next step and limits

Record what changed and repeat comparable measurements after maintenance. Compare soil
wetting at relevant depths and locations separately from emitter flow. Use sourced crop
and soil data for water budgeting. Agronaut's flat-field pressure-compensating design
calculator is not a terrain or non-compensating-emitter model, and this guide does not
extend that scope.

Original Agronaut guidance, source review 2026-10-20. The linked publication supports
the physical principles; the recording workflow describes this implementation.

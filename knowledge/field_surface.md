# Observing a surface irrigation event

_Domain: irrigation_
_Evidence: guidance_
_Applicability: General basin/furrow event observations; no predictive cutoff timing or locally calibrated infiltration parameters._
_Sources: [FAO Irrigation Water Management, Training manual 5, provisional edition, annex 4](https://www.fao.org/4/s8684e/s8684e0c.htm)_

## Observe arrival and disappearance

During a basin or furrow event, mark observation positions and record when water arrives
and when surface water disappears at each position. The difference, recession time
minus advance time, is the contact or opportunity time. A representative measured
infiltration relationship connects that time to infiltrated depth. FAO annex 4 describes
this event-evaluation approach and compares supplied water with a known crop-water target.

The observation describes an event under its tested conditions. A borrowed infiltration
curve or an assumed target does not become a local measurement merely by entering it
into a calculation.

## Prepare an Agronaut event evaluation

Record geometry, station positions and time basis, the full inflow history and cutoff,
and the advance/recession observations. Preserve tailwater and final surface-storage
evidence where required by the event boundary. Include the infiltration coefficients,
their source and applicability, and a separately justified target depth.

Inspect `soil.surface_evaluation` with `agronaut capabilities --system soil` for the
complete supported contract. Use `evaluate-surface` or the surface event intake in
`/fieldplan`. Agronaut checks water accounting and withholds performance scores when
it is inconsistent. Investigate missing intervals, units, geometry and observation
coverage instead of editing measurements to force agreement.

## Next step and limits

Keep original records and document corrections in a new version. Compare comparable
events with a local irrigation reviewer before changing an operating practice.
Agronaut's experimental `soil.surface_advance` task predicts half/full-length arrival
for a uniform sloping furrow or wide border with supplied hydraulic and intake evidence.
It does not predict recession or a safe cutoff time. A successful event or advance
calculation cannot be reused as a cutoff recommendation for a new soil, field, stream
size or season. Unknown inflow, runoff or infiltration must remain unknown until measured
or explicitly represented as a scenario.

Original Agronaut guidance, source review 2026-10-20. The linked publication supports
the observation concepts; the recording workflow describes this implementation.

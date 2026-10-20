# Deterministic advice-safety evaluation

Run `python -m scripts.safety_eval` from the development checkout. This existing CI gate
returns a nonzero exit code when any critical probe fails. It needs the application's
Python dependencies, but no model server, network access or farm database records.

`golden_set.json` covers aquatic trust boundaries, sizing and image-observation guards.
A generated species/crop matrix adds aquatic evidence checks. `field_set.json` adds
29 curated field probes through the real `calculate_agriculture` tool and its serializer:

- Unknown starting soil water remains unknown; missing rain is rejected.
- Unsupported surface cutoff calculations are refused.
- Failed drip and sprinkler designs cannot emit schedules.
- Inconsistent surface water balances withhold performance scores.
- Advance predictions do not become cutoff advice.
- A lateral model cannot extrapolate outside its emitter curve or emit a crop schedule.
- A supplied inlet pressure–flow curve retains its scenario operating point and cannot be extrapolated.
- A simultaneous drip zone retains every branch, shares one source curve and reports failed total-flow limits without emitting a schedule.
- A surveyed manifold retains its takeoffs, enforces source capacity and rejects lateral-pressure references or unsupported whole-manifold flows.
- Sprinkler nozzle/riser operating points cannot extrapolate the source curve or create irrigation schedules.
- Missing curve evidence and unsupported production-system routing are rejected.
- Nutrient accounting and distribution statistics do not claim operational feasibility.
- Crop scouting remains observation guidance without a confirmed diagnosis.
- French and Traditional Chinese guides retain their draft status.

The field fixtures are the synthetic inputs in `examples/`. Probe edits occur on fresh
in-memory copies. Result assertions use explicit key/index paths and preserve JSON types:
a missing key or numeric zero cannot satisfy an expected null, and zero cannot satisfy
an expected false. Every successful field probe also checks the declared evidence status,
nonempty sources and nonempty limitations. The probes run in an isolated context so an
active farm selection cannot alter the score or be cleared by the evaluator.

Tests intentionally corrupt representative outputs to verify that missing sources,
invented amounts/schedules/cutoffs and unsupported evidence promotions fail the gate.
This is a software regression suite authored during development. It is not an independent
grower-question benchmark, a test of model-generated advice, a full workflow test, or
field validation. Unit ownership, saved records, deployment behavior, scientific accuracy
and measured agronomic outcomes need their own evidence. Passing this gate does not
promote a capability's evidence level.

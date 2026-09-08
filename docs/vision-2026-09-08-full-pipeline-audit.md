# Full pipeline audit, and what "the god of agriculture" actually costs

**Dated 2026-09-08**, the day 1.0.0 went to PyPI. Written by walking the whole
path from a message arriving on a channel to a number leaving in a report, then
checking the promising branches against the current literature.

Companion to [`PLAN.md`](PLAN.md), which is the task list. This is the argument
for what the next task list should be.

---

## 1. The pipeline as built

```
  INPUT      text | voice (ASR) | photo (VLM + guard) | slash commands
                            |
  CHANNEL    Telegram | WhatsApp | Streamlit | CLI        one shared command layer
                            |
  AGENT      core.py: profile + semantic memory + RAG citations -> LLM with 30 tools
                            |
  GATE       validate_design_input | validate_hydroponic_input | validate_observation_features
                            |
  TRUST      sizing -> massbalance -> hydraulics -> layout -> costing -> schematic -> 3D
   ZONE      twin (nitrogen) -> production (fish + crop + climate) -> scenario -> advisory
             triage (visual)   calibration (coefficients)   sensor_qc
                            |
  STATE      mirror: per-user twin state, advanced through real weather, nudged by readings
             store.py: SQLite (facts, memories, measurements, twin_readings, proposals)
                            |
  OUTPUT     prose + report + BOM + SVG/PNG + 3D scene + numbered proposal w/ confidence
                            |
  DECISION   approve / reject by number  ->  recorded  ->  (nothing)
                            |
  EVIDENCE   twin_validation.json -> validation_status -> confidence ceilings -> back to OUTPUT
```

The architecture is genuinely good. The trust boundary is enforced in CI (the
`trust-zone` job fails if `aqua_model` ever pulls in an LLM library), confidence
is derived from a computed artifact rather than asserted, and the gate has held
through every revision. That is not the problem.

The problem is that two edges in that diagram are drawn and not wired.

---

## 2. The finding that reframes everything else

**The evidence tables are write-only.**

`store.py:122` creates `twin_readings`, which stores, per reading, the
operator's `observed` values *and* the `modelled` values beside them. That is a
per-user, per-day record of exactly how wrong the model was. It is written by
`_record_reading` (`tools.py:1291`) and read back by precisely two things: the
GDPR export and the delete-my-data path. Nothing computes with it.

`proposal_items` is the same shape. The schema comment at `store.py:134` says it
out loud:

> an approved action with a date beside the readings that followed it is the
> only way to ever answer "did taking this system's advice help?"

Correct, and nothing asks. After `decide()` writes the status, no code ever
reads those rows again.

So the calibration loop in the diagram is not a loop. The model produces a
number, the operator produces a number, the difference is computed, printed once
as a sentence, saved to disk, and abandoned. The mirror nudges the **state**
toward the truth and never lets the truth touch the **parameters**.

Two consequences worth sitting with:

1. The project's headline weakness (absolute numbers are literature-seeded, and
   lost to a trend null on level) is being treated as a data-availability
   problem waiting on `#111` and a partner. Part of it is not. The machinery to
   start fixing it from one farm is already written and idle.
2. The local database has **zero rows** in `twin_readings`, `proposals`,
   `measurements` and `memories`. The maintainer runs real systems in Burkina
   Faso and Taiwan and is not yet running them through his own pipeline. The
   cheapest dataset in this whole project is the one nobody is collecting.

---

## 3. Stage-by-stage gaps

### INPUT

- **No sensor ingest at all.** Readings enter only by a human typing them into
  `log_my_readings`. `sensor_qc.py` is written, tested, and has nothing to QC.
  This is `#75` and the first slice of `#85`.
- Photo corpus (`data/vision_corpus/`) is still empty (`#72`). The guard and the
  triage table are verified against handwritten strings, never a real
  photograph.
- Voice is Telegram only. No SMS or USSD (`#73`), which is the actual last mile
  in the Sahel.

### TRUST ZONE, the scientific holes

Ordered by how much damage the absence does:

- **Dissolved oxygen and pH are not modelled** (`#108`). Stated plainly in
  `sizing.NOT_MODELED`, so this is honest. But it is worse than a missing
  feature: nitrification rate is strongly pH- and DO-dependent, so the nitrogen
  twin that *is* shipped carries an unstated assumption that both are fine. When
  they are not, which is exactly when an operator needs the model, the twin is
  wrong in a direction it cannot report.
- **Alkalinity and carbonate buffering are absent**, and nitrification consumes
  alkalinity. The classic aquaponics failure is a pH crash weeks into cycling,
  and the model cannot see it coming.
- **Solids handling is a constant.** `massbalance.SOLIDS_REMOVAL_FRACTION =
  0.35`, fixed, and sludge water loss is a flat 5%. Solids are roughly a third
  of the nitrogen and the most common practical failure.
- **Aeration is missing from the layout entirely** (`#113`): no blower, no air
  lines, no diffusers, and `costing.py:191` waves at it with "aeration adds
  ~30-60% on top" of the pump electricity.
- **Energy is one line.** `costing.py:186` computes annual pump kWh and stops.
  There is no power budget, no off-grid sizing, no solar or battery. For the
  wedge this project actually targets, unreliable grid is not a footnote.
- **One cohort only** (`#107`). Real farms stagger.
- **Point estimates leave the building.** `scenario.py` sweeps `TwinParams` for
  an uncertainty band, and that band never reaches a design output. A report
  says "2,400 L", not "2,100 to 2,900 L".

### STATE

- `mirror.nudge` is well designed and deliberately not a Kalman filter, for a
  reason the docstring defends well. But the innovations it computes are the
  entire raw material of a parameter fit, and they are discarded.
- No design identity (`#119`). Size three systems in one conversation and the
  twin cannot say which one it mirrors. That is a missing join key, not a
  nicety, and it blocks anything that wants to attribute an outcome to a design.

### DECISION and EVIDENCE

- The approval gate has nothing behind it (`#120`), which is the correct
  deliberate state.
- But there is also no **scoring** behind it, which is not deliberate, just
  unbuilt. Advice quality is measured today only against a handwritten golden
  set. Nothing measures it against reality.
- `twin_validation.json` is scored on one public dataset, on one channel
  (nitrate), with uncalibrated sensors. It is an unusually honest artifact and
  it is also very thin.

### DISTRIBUTION

Fixed this week: PyPI, `agronaut setup`, WhatsApp parity, six providers. Still
open: no live demo or GIF (`#24`), no pilot partner (Phase 4.3), and therefore
no real-user analytics despite the analytics being built.

---

## 4. What I checked in the landscape today

Things that go stale fastest, re-verified 2026-09-08:

**Local-first is in better shape than the strategy file assumes.** Open-weight
tool calling is now routine: Qwen3, Gemma 4 27B, GLM-4.7 32B and Llama 3.3 70B
all tool-call reliably, and Qwen3 has native tool calling in its chat template
through Ollama's tools API. Combined with the fix in `88763b8`, the
zero-proprietary-API path is real rather than aspirational. On vision (`#121`),
Qwen3-VL and Gemma 3 are the local options, and Gemma 3 4B has day-one Ollama
support, so the last API-key dependency is closable now.

**No standard aquaponics calibration dataset exists.** What is public is small:
the Mendeley aquaponic pond set (pH, TDS, temperature, three months) and the
Udanor/Ogbuokiri IoT set that Agronaut already scores against. `LakeBeD-US`
(ESSD 2025, 500M+ observations) shows what a real benchmark looks like in an
adjacent field, and nothing like it exists here. `#111` is not a chore, it is an
open field.

One useful consequence: **pH is available in public data in a way nitrate is
not.** Adding pH to the twin is the rare capability addition that arrives with a
validation path attached.

**Digital twins in aquaculture are an active but shallow area.** Føre et al.
2024 in *Computers and Electronics in Agriculture* is the framing review;
Ghandar et al. 2021 (IEEE Access, ~179 citations) is the closest prior art to
Agronaut's twin and is a decision-support case study, not a runnable cited
engine. Searching specifically for data assimilation or state estimation in
aquaculture twins returns about twenty works, mostly ocean and lake scale.
Bounded assimilation with published innovation statistics, on a model whose
validation record is public including its failures, does not appear to have been
done in this domain. Verify before claiming novelty in a paper, but the gap
looks real.

**The thing to stay away from** is well populated: hybrid ML water-quality
prediction (VMD plus deep belief networks, LSTMs, XAI dashboards) is a crowded
literature. It is also exactly the move that puts a model in the calculation
path and ends the citation story.

---

## 5. On "the god of agriculture"

Taking the goal seriously rather than as a slogan, the honest version is this:
the god of agriculture is not the system that will speak about every crop. It is
the one that is trusted about what it speaks on, and that trust is the only
asset here that competitors cannot copy in a weekend. A general assistant will
already size an aquaponics system for anyone who asks, fluently, citing nothing.

So breadth is downstream of evidence, not parallel to it. The generalizable
asset is the pattern (cited engine, validation gate, calibration loop, LLM
explainer), and the pattern is only worth replicating once it has been shown to
work through a full cycle at least once. Right now the loop has never closed
end to end for a single farm, including the maintainer's own.

That gives the sequence:

**Close the loop on one domain, on one farm, then replicate the pattern.**

Concretely: measure, assimilate, advise, score the advice, publish the score.
Then take that machine to field crops via FAO-56 (`#78`) and find out whether
the architecture generalises. Doing `#78` first would produce two
half-validated engines instead of one validated one, which is the failure mode
that would actually cost this project its differentiator.

---

## 6. Phased sketch

**Now, next minor (1.1): wire the two dead edges.** Outcome ledger, design
identity, sensor ingest. All deterministic, all offline, no new claims. This is
the phase that turns the maintainer's own farm into the first dataset.

**Next, a major-feature epic (1.2 to 1.3): the channels that kill fish.** DO, pH
and alkalinity in the twin, validated against the public pH data, paired with an
explicit correction to what the shipped nitrogen twin has been assuming. Then
per-user parameter calibration from accumulated innovations.

**Then: the second engine.** FAO-56 field crops, as the test of whether the
pattern is a pattern. Only after the first loop has closed and been published.

**Throughout: the partner.** Phase 4.3 is a relationship, not a feature, and it
still gates more of the funding story than any of the above.

---

## 7. Ranked shortlist

Scored on impact, effort, strategic fit, and effect on what the system is
entitled to claim.

### 1. The outcome ledger: score the advice against what actually happened

**Thesis.** Join `proposal_items` (what was approved, when) to `twin_readings`
(what the operator measured after) and answer, deterministically, whether taking
the advice was followed by the predicted direction of change.

**Why it matters to a grower.** Right now every confidence number is derived
from how the twin scored on seven strangers' ponds. This makes it derivable from
whether the advice worked on *their* system.

**Scope.** A pure module in the trust zone, `aqua_model/outcomes.py`: takes
decided proposals and the reading history, returns per-action hit rates with
sample sizes and an explicit "not enough data" state. A `scripts/score_advice.py`
that writes `data/advice_outcomes.json` in the shape of `twin_validation.json`.
Then `validation_status` learns to read both.

**Effort.** Major feature, roughly a week. No new dependencies, no network.

**Evidence effect.** Strongly positive, and it is the only item here that raises
a confidence ceiling without waiting on a partner or a new dataset.

**Why first.** Everything is already in the database. This is the highest ratio
of claim-unlocked to code-written in the whole repo.

---

### 2. Dissolved oxygen, pH and alkalinity in the twin (sharpens `#108`)

**Thesis.** Add the two channels that kill fish, plus the buffering that drives
pH, and state plainly that the shipped nitrogen twin has been assuming both were
fine.

**Why it matters to a grower.** A pH crash during cycling and an overnight DO
sag are the two events that lose a crop of fish. The twin currently cannot see
either coming, and its nitrification rates are silently conditioned on both.

**Scope.** DO mass balance (fish consumption as a function of temperature and
biomass, nitrification demand, reaeration, plant contribution) and a carbonate
balance with alkalinity consumed by nitrification. Cited coefficients through
the existing registry. Validate the pH channel against the public Mendeley
aquaponic pond dataset, which has pH, TDS and temperature. Extend
`NOT_MODELED` honestly about what still is not covered, and add a note to the
existing model saying what it had been assuming.

**Effort.** Research-grade. Two to four weeks of gradual work, and the natural
shape is one PR per channel.

**Evidence effect.** Mixed, and worth pricing out loud. It widens what the
system says, which is the move this project is normally suspicious of. It is
defensible here because pH arrives with public validation data attached, and
because the honest framing (the nitrogen twin already depended on these) makes
this a correction as much as an addition. Do not ship DO without the same
validation discipline the nitrogen twin got.

---

### 3. Per-farm parameter calibration from accumulated innovations

**Thesis.** `mirror.nudge` already computes, on every reading, how far the model
was from the measurement. Accumulate those innovations and fit a small number of
per-user scale factors on the load-bearing coefficients (TGC, FCR, nitrifier
capacity) instead of throwing the residuals away.

**Why it matters to a grower.** It is the difference between a projection and a
twin. After a month of readings, the model is tuned to their tank, their feed
and their water, and can say by how much it improved.

**Scope.** Deterministic bounded least squares in the trust zone, no ML, no new
dependency. Every fitted factor clamped to the published empirical range from
`calibration.py`, so a bad sensor cannot drag a coefficient somewhere the
literature does not support. Report the fit and its residual to the operator.
Refuse to fit below a minimum sample count.

**Effort.** Major feature, and the most technically delicate item here.

**Evidence effect.** Strongly positive, and it is the piece with a plausible
paper attached. Bounded assimilation with published innovation statistics on an
openly validated model looks close to unoccupied in this domain. Depends on
items 1 and 4 for data volume.

---

### 4. Sensor ingest: give `sensor_qc` something to QC

**Thesis.** `agronaut ingest readings.csv`, and an MQTT or HTTP endpoint later,
running every reading through the existing `sensor_qc` checks before it reaches
the mirror.

**Why it matters to a grower.** Anyone with a cheap IoT logger has months of
data and no way to put it in. Typing readings by hand caps the calibration loop
at a few points a week.

**Scope.** A CSV reader with column mapping, `sensor_qc` on the front (the
sentinel, ceiling and channel-independence checks already exist and are good),
then the same `nudge` path a typed reading takes. Rejected rows reported, never
silently dropped, and never persisted.

**Effort.** Medium, a few days. The QC layer is the hard part and it is done.

**Evidence effect.** Positive and indirect. It is the volume input that makes
items 1 and 3 work at more than anecdote scale.

---

### 5. Energy and water budget with off-grid sizing

**Thesis.** Turn the single line of pump kWh into a real power budget: pump and
blower duty, daily and seasonal kWh, and a cited solar array plus battery sizing
for a site with an unreliable grid.

**Why it matters to a grower.** In Burkina Faso, and in every camp deployment
this project targets, "will it run when the power goes" decides whether a design
is buildable. Nobody else's aquaponics tool answers it.

**Scope.** Reuse the computed pump head, add blower sizing from the aeration
work in `#113`, cited coefficients for panel and battery derating, and put an
autonomy figure in the report and the pilot proposal.

**Effort.** Major feature, and unusually well suited to a contributor because
the coefficients are public and per-country.

**Evidence effect.** Neutral to positive. It is arithmetic on numbers the model
already computes, and it makes the pilot proposal materially more credible to
exactly the funders in Phase 4.3.

---

### 6. Give a design an identity (`#119`)

Already filed, and worth pulling forward: it is the join key that items 1 and 3
need in order to attribute an outcome to a design. Small, unglamorous, blocking.

---

### 7. The second engine: FAO-56 field crops (`#78`)

The real test of the thesis, and the literal step toward the stated end goal.
Deliberately ranked below the loop-closing work: shipping a second
literature-seeded engine before the first one has ever completed a calibration
cycle would double the surface area of unvalidated claims, which is the one
thing this project cannot afford. Revisit as soon as item 1 has published a
score.

---

## 8. Considered and set aside

- **A learned water-quality forecaster** (LSTM, or the VMD plus deep-belief
  hybrids the literature is full of). It would demo beautifully and it puts a
  model in the calculation path, ending the citation story. This is the most
  tempting bad idea available, which is why it is named here.
- **Fine-tuning a model on the aquaponics corpus.** Improves fluency, changes
  nothing about what is known, and moves work out of the auditable zone.
- **A hosted dashboard or SaaS tier.** Violates the local-first constraint the
  maintainer has already rejected once.
- **Physics simulation of the greenhouse (Isaac Sim class).** Different problem,
  and it needs GPU resources the project does not have. NVIDIA Inception remains
  the route.
- **A hosted vector database.** Decided in September 2026: FAISS plus SQLite
  stays, `sqlite-vec` first if the corpus outgrows it.
- **Widening the knowledge corpus further.** Tier-3 shipped, retrieval is
  measured. Marginal next to anything on the list above.

---

## Postscript

To be filled in with what actually shipped. That is what makes a vision doc
worth re-reading.

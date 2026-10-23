# Rehearse a field crop cycle offline

Run one connected software example from planting through irrigation planning, corrected
water records, guided task decisions, harvest and season review. It uses the public CLI, creates a separate
synthetic database, and saves every command's input and output for inspection.

From a checkout with Python 3.11 or newer, choose an output directory that does not exist:

```bash
python -S -m scripts.rehearse_field_cycle --output-dir /tmp/agronaut-rehearsal-bf
```

The default timezone is `Africa/Ouagadougou`. To exercise Taiwan's local-date context:

```bash
python -S -m scripts.rehearse_field_cycle --output-dir /tmp/agronaut-rehearsal-tw --timezone Asia/Taipei
```

These are **synthetic software rehearsals**, not regional crop trials. Changing timezone
does not supply Burkina Faso or Taiwan weather, soil, equipment, crop coefficients or
grower records. All inputs are supplied; no network, chat model or third-party Python
package is required. The script is a checkout tool, not an installed application command.
The task questions use French for `Africa/Ouagadougou` and Traditional Chinese for
`Asia/Taipei`. These are draft translations; supplied narratives and other report labels
remain English. This does not establish local comprehension or agronomic suitability.

To verify an installed wheel rather than the checkout's application modules, first install
the wheel in a separate virtual environment and pass that environment's Python executable:

```bash
python -S -m scripts.rehearse_field_cycle --output-dir /tmp/agronaut-installed-rehearsal \
  --installed-python /absolute/path/to/venv/bin/python
```

The runner and three example input files still come from the checkout. Application commands
run from the new output directory with the selected interpreter's `-I` isolated mode.
The runner removes inherited Python and Agronaut corpus/data/cache overrides, keeps runtime
state inside the output directory, and checks that the CLI, water engine and corpus resolve
inside the separate environment. Editable installs pointing outside it are rejected. The
manifest records the actual interpreter, installation prefix, module paths and corpus path.
This verifies the deterministic field workflow; it does not exercise the web UI, messaging
providers or language-model dependencies. The packaging regression uses a wheel installed
with `--no-deps` in a new environment containing only Agronaut.

The output directory must be new, and its parent must exist. Existing directories are
refused without changes. Every application command receives the new database path
explicitly, so the configured `AGRONAUT_DB` is not used.

## Follow the evidence

| Step | What to inspect |
|---|---|
| Planting | A synthetic tomato cycle planted April 1, 2026, on a 500 m² soil unit |
| Water budget | The existing FAO example inputs, a hypothetical initial depletion and explicit scenario weather |
| Drip consultation | Every question and supplied answer, including explicit adoption of the saved budget's end-of-day refill target |
| Equipment result | The hypothetical limited supply cannot meet the refill target; the design remains **infeasible** |
| Point reading and task plan | A separate synthetic point reading prompts an operator-declared task; numbered source selection, draft resume, explicit save and retry run through the guided CLI |
| Water log | One separate, synthetic completed-day record; saving the design created no performed action |
| Weather replacement and replay | Scenario weather is first rejected for actual-water replay; a separately sourced, synthetic observed-shaped record exercises the replacement path |
| Correction | Net irrigation changes from 3 to 4 mm through an appended revision; adoption creates a child calculation and retains the original |
| Calendar evidence | Numbered task and actual-record selection links completion to the water log; the old reference is flagged after correction, then explicitly replaced through the guided flow |
| Changed task source | A later synthetic quality review changes the point reading's review state; that flag remains unresolved even after completion evidence is updated |
| Harvest and closure | One 12.5 kg reported batch, followed by explicit closure on July 10 |
| Season review | One recorded water day out of 101; the other 100 remain missing, and no harvest-area review or full-season yield is invented |

The observed-shaped weather entry is a test fixture using the example's numerical values.
It is **not a real observation** and must never be copied into a real farm's evidence.
The synthetic water log is independent of the infeasible equipment proposal; the script
does not represent following that proposal or controlling hardware.
The point reading is for record linkage only. It is not a whole-root measurement, is not
assimilated into the soil-water calculation, and does not establish an irrigation threshold.
Calling its synthetic notebook review `checked` exercises the quality-review workflow;
it is not an independent field verification.

## Inspect the result

`rehearsal.json` (schema v3) contains the status, runtime mode and installation evidence,
source-file SHA-256 fingerprints, ordered command
arguments, output filenames, 24 checks and a concise numerical summary. `guided_tasks`
records the task language, selected record identities and save-retry results; the task trace
links the original decision, both completion revisions, water records and source review.
Each numbered
`*-input.json` and `*-output.json` preserves an operation's actual CLI payload. The
`synthetic.sqlite3` database retains the associated records. A failed run retains its
completed steps and reports failure; use a new directory after fixing the cause.

The intended numerical results for the supplied example are:

| Water balance | Closing depletion |
|---|---:|
| Original scenario | 64.155341 mm |
| After 5 mm rain, 1 mm runoff and 3 mm net irrigation | 57.461524 mm |
| After explicitly adopting the correction to 4 mm net irrigation | 56.461524 mm |

Water enters before evapotranspiration in this model. The original 60 mm depletion
exceeds the 54.4 mm readily available water threshold; the added water relieves stress
and increases actual evapotranspiration. Consequently, depletion does not simply fall
by the added water volume. The checks reconcile the full balance: 53 mm and 52 mm
depletion before evapotranspiration, respectively. The one-millimetre correction is
adopted once; retrying the same operation produces no duplicate calculation.

This verifies connected software behavior and an inspectable correction history.
It does not establish local agronomic accuracy, a future irrigation schedule, seasonal
water savings, yield, profitability or field validation. The incomplete season is
intentional: absent records must remain visible during a real pilot.
The final task is completed and its corrected water evidence is current, but its original
source review still needs review. The original source snapshot remains unchanged. Clearing
that separate flag would require an explicit plan revision; refreshing completion evidence
must not silently accept it.

For the underlying commands, see [field calculations](FIELD_CALCULATIONS.md) and
[crop records](CROP_RECORDS.md). The saved command arguments are an execution transcript;
to repeat the whole example, rerun the rehearsal in a new directory.

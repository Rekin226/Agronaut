# Evaluation and observability

How Agronaut measures itself. The short version is `agronaut eval`; this page is the method and the numbers behind it.

## The advice layer (retrieval), and how it was tuned

Sizing is computed. Troubleshooting advice is *retrieved*, from a corpus of 22 hand-written
operator guides plus openly licensed publications — currently **3941 chunks**, led by
Goddek et al. (2019) and FAO 589.

Retrieval is measured, not assumed. `docs/dpg/retrieval_eval/golden_set.json` holds queries in
real operator voice ("my tilapia are gasping at the surface", not "dissolved oxygen") plus
off-topic controls that must be **refused**:

```bash
python -m scripts.retrieval_eval     # recall@k, precision@k, MRR, MAP@k + floor separation
python -m scripts.retrieval_sweep --all   # re-pick floor / per-source cap / hybrid β
python -m scripts.corpus_report      # what each declared source actually contributes
```

Nine techniques were implemented and measured. **Four ship, four lose, one is available but
unused** — and the losses are recorded in `docs/dpg/retrieval_eval/techniques.json` with the
conditions that would reverse them, which is how hybrid search went from rejected to shipped when
the corpus grew:

| | ships | why |
|---|---|---|
| Relevance floor | **on** (1.50) | refuses 8/10 off-topic queries, silences 0/33 real ones, keeps 0.117 headroom |
| Hybrid BM25 + RRF | **on** (β=0.90) | lost at 362 chunks, won at 1354, re-confirmed at 3941 |
| Per-source cap | **on** (1) | two books hold 97% of the corpus; at cap=2 they take 2 of 3 slots |
| PDF cleaning | **on** | drops contents pages; running header removed from 111 chunks → 4 |
| Metadata filtering | available, off | the third leg of hybrid search. Filters `source_type`, `kb_tag`, `chapter`, `page`, `url_category` on **both** pools before fusion. A capability, not a ranking change — no golden-set number moves, and none is claimed |
| Header chunking · context prefix · PDF chapter labels · cross-encoder rerank | off | each measured *worse* on this corpus |

**Current: hit 0.879 · recall 0.833 · MAP 0.604 · 8/10 off-topic refused · 0/33 real silenced.**

### A decision expiring, caught in the act

`techniques.json` was written 2026-08-25. The next day, commit `70b2d00` added a second book and
took the corpus from 1354 to 3935 chunks. Nothing was re-measured, and every constant silently
became wrong for the corpus that actually shipped:

| | at 1354 (recorded) | at 3941, old constants | at 3941, re-tuned |
|---|---|---|---|
| hit_rate | 0.939 | 0.818 | **0.879** |
| recall@k | 0.894 | 0.727 | **0.833** |
| MAP@k | 0.697 | 0.548 | **0.604** |
| off-topic refused | 8/10 | 4/10 | **8/10** |

Two constants moved, one did not. The **floor** tightened 1.65 → 1.50, because the distance bands
*separated* as the corpus grew (on-topic worst 1.383, closest off-topic 1.411, where at 1354
chunks they overlapped and no floor could work). The **cap** tightened 2 → 1, because cap=2 was
calibrated against *one* oversized source and there are now two. **β stayed at 0.90** — it
describes the relationship between two ranking signals, which is a property of the query language,
not of how much text sits behind it.

Worth being precise about which change did what, because they pull opposite ways. **The floor
costs retrieval quality**: at cap=1, staying at 1.65 would score hit 0.909 and MAP 0.624 against
1.50's 0.879 and 0.604. That is bought deliberately, to double off-topic refusal from 4/10 to
8/10. **The cap is what pays for it**: at floor 1.50, cap=1 gives 0.879/0.833/0.604 against
cap=2's 0.788/0.697/0.538.

The floor was *not* tightened to 1.40, though that refuses all 10 controls: it clears the worst
real query by 0.017, and this project had already rejected a 0.032 margin as too thin. 33 golden
queries say nothing about the 34th; headroom is the only thing that does.

```bash
python -m scripts.retrieval_sweep --all      # re-pick all three, with the evidence table
```

That command exists because the drift was not carelessness. Re-measuring three constants was an
afternoon of ad-hoc scripting, so it did not happen. It also earned its keep immediately: while
this work was in review the corpus moved *again* (a 22nd knowledge file, 3935 → 3941 chunks) and
re-running was one command rather than an afternoon. Run it after any corpus or embedding-model
change.

"Run it after any corpus change" was still a rule someone had to remember, so CI now enforces it.
The baseline records a fingerprint of the corpus it measured (`urls.txt` plus the guides in
`knowledge/`), and `test_corpus_fingerprint.py` fails when the shipped corpus has moved away
from it: always when a source is added or removed, and once the guides have drifted more than
10% in size, so one contributed guide does not block its author. It cannot see a change to the
chunking code or to a page behind a URL.

Three of the four failures share one mechanism: they add topic words to chunks in a corpus where
every document already shares a vocabulary domain, which dilutes rather than disambiguates. What
worked was structural — refusing irrelevant passages, refusing error pages, refusing to let one
source fill the whole answer.

**Corpus licensing is mixed and deliberately explicit.** The code is MIT; FAO 589 is
non-commercial-only. See [`docs/dpg/CORPUS.md`](dpg/CORPUS.md) — commercial users should drop
that entry from `urls.txt` and rebuild. Vet any source before adding it:

```bash
python -m scripts.corpus_report --candidate "<url>" --label "<expected topic>"
```

It checks four things, because a source can fail in four ways: unreachable, empty, **wrong
subject** (a guessed publication ID once resolved to *"Sharks for the Aquarium"* — 28k characters
that pass every check except being about aquaponics), or not openly licensed.

## Observability: what a turn actually did

Retrieval quality is measured offline against a golden set. Production behaviour is a different
question, and needs a different instrument.

**Every turn is one trace.** All the events a turn produces — the message, each model call, each
tool call, the retrieval, the turn summary — carry the same random per-turn id, so the log reads
as a path rather than as counters:

```bash
agronaut traces          # recent turns: which tools ran, what retrieval returned, where the ms went
agronaut analytics       # p50/p95/max latency for turn / model / retrieval, tokens, thumbs up-down
```

**The trace holds shape, never content.** No prompt, no reply, no passage text, no query is
recorded, and that is enforced by an allowlist that drops unknown fields rather than by callers
remembering not to pass them (`agronaut_agent/tests/test_turn_tracing.py` asserts it). The trace
id is minted fresh per turn and is never derived from the user, so it groups a turn without
following anyone between turns.

**What gets measured, and why those things.** Turn latency and model latency separately, because
the course is blunt that the transformer is the bottleneck and this project previously timed only
retrieval — the fast, cheap stage. Token counts in and out, omitted entirely rather than recorded
as `0` when a provider reports no usage, so a quiet provider cannot drag every cost aggregate
toward zero. And a failed turn is still written, because dropping the turns that broke is how a
p95 comes to look healthier than the service is.

### Optional: every word of a turn, in a local Phoenix

`agronaut traces` shows a turn's shape and never its words. When you are debugging your own bot
and need the words too (the exact prompt, why the model chose a tool, what the search handed
it), send full traces to [Arize Phoenix](https://github.com/Arize-ai/phoenix) on your own
machine:

```bash
uv tool install arize-phoenix         # the server, ~600 MB, in its own environment
pip install "agronaut[phoenix]"       # the two client libraries
agronaut phoenix                      # http://127.0.0.1:6006, this machine only, telemetry off
AGRONAUT_PHOENIX=on agronaut whatsapp # in another terminal (or bot, chat, web)
```

Each turn arrives as one tree (a parent span carrying the same trace id as `agronaut traces`,
with every model and tool call under it). It is off by default, records message text when on
(see [PRIVACY.md](dpg/PRIVACY.md)), and refuses to send to another host unless
`AGRONAUT_PHOENIX_ALLOW_REMOTE=1`. Nothing about it is required: no Phoenix, no difference.

### Does the answer actually use what was retrieved?

`retrieval_eval` scores whether the right documents were found. It cannot score whether the reply
used them, and a system can hit recall 0.894 while inventing every number in its answer.

```bash
AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval
```

Three metrics of three deliberately different kinds:

| | judged by | what it catches |
|---|---|---|
| `faithfulness` | an LLM, per atomic claim | claims the retrieved context does not support — the grounding measure |
| `response_relevancy` | an LLM + embeddings | an answer that is true but does not address the question |
| `citation_accuracy` | **code, no model** | `[source: ...]` labels that were never retrieved — a fabricated citation |

The judge is treated as a witness, not an oracle: rubrics are binary with named labels, an
unparseable verdict counts as **unjudged** rather than being folded into either side, and
`n_unjudged` is printed beside every score. It calls the network, so it is opt-in and never runs
in CI; the scoring arithmetic is pure and unit-tested without a model.

The judge is a different model from the one that writes the answer (`AGRONAUT_JUDGE_PROVIDER` /
`AGRONAUT_JUDGE_MODEL`), and it must copy the sentence of the context that backs a claim before
it may call the claim supported; code then checks that the quote really is in the context.
Sentences about the sources themselves ("the context does not specify…", "consult other
resources") are not claims and are left out.

**Measured (2026-10-03):** 33 golden-set answers written by Claude Sonnet 5, 479 claims.

| | faithfulness | agreement with a person (kappa, 29 claims) |
|---|---|---|
| gpt-oss-20b, quote-first prompt (default) | **0.84** | 0.24 blind, 0.39 after review |
| gpt-oss-20b, earlier prompt (two runs) | 0.90, 0.90 | 0.34, 0.30 |
| Claude Sonnet 5 as judge | 0.88 | 0.03 |

Citation accuracy is 1.00 (no fabricated sources) and response relevancy 0.44. Read the
faithfulness figure as a range, not a verdict: the judges agree with themselves (kappa 0.76
between two runs) much better than with the one person who has labelled claims so far, which is
"fair" agreement on a small sample. Claude was dropped as a judge because it agreed with the
person no better than chance. Report and labels: `docs/dpg/faithfulness_eval/`.

```bash
python -m scripts.label_claims 2026-09-30_baseline.json            # label claims blind
python -m scripts.label_claims 2026-09-30_baseline.json --review   # second look at disputes
python -m scripts.faithfulness_eval --agreement 2026-09-30_baseline.json
AGRONAUT_FAITHFULNESS_EVAL=1 python -m scripts.faithfulness_eval --rejudge 2026-09-30_baseline.json
```

### Does the reply repeat the engine's numbers correctly?

Most turns never retrieve anything: the model calls the sizing engine and explains the result.
The validation gate guards the numbers going *into* the engine; `agronaut_agent/grounding.py`
guards the ones coming *out*. After every reply, each number-with-a-unit is checked against
what the model was shown that turn (tool results, earlier results, what you said), after unit
conversion and within rounding. It is code, no model, so it runs on every live turn and in CI.

It exists because of a real slip: `qwen3.5:2b` relayed a 6,666.7 L/h pump as **"6.7 L/h"**,
having converted the volume to 6.7 m³ correctly and then kept the old unit on the flow. The
check flags that and passes the correct "6.7 m³" and "~6,700 L/h". Sample answers inside a
question ("e.g. 200 L/day") are not claims and are skipped; numbers without a unit are not
checked.

- Every turn records how many figures no tool or user gave; `agronaut analytics` prints how
  many replies had one. The figures themselves go only to the local log, never to analytics.
- The [Local model check](../.github/workflows/local-model-check.yml) now fails a model whose
  reply misquotes the engine, even when it called the right tool.

Passing means every figure the reply quotes traces to a source, not that the advice is right.
On the maintainer's own history, 3 of 15 Claude replies with quantities quoted figures from
memory: a stress temperature, a water budget the user never gave, and Ouagadougou's seasonal
highs.

### All of it in one place: `agronaut eval` and the Quality page

```bash
agronaut eval                    # status: last result of every eval, its age, stale or not
agronaut eval all                # run every free eval (safety, retrieval in en/fr/zh), then status
agronaut eval retrieval          # --multilingual, --compare FILE
agronaut eval safety
agronaut eval answers            # the published report; --agreement for every comparison
agronaut eval answers --run      # a fresh run (calls models, names them, asks first)
agronaut eval label [--review]   # label claims by hand, or take a second look at disputes
agronaut eval model qwen3.5:4b   # does this local model drive the engine on this machine?
agronaut web                     # then Quality in the sidebar
```

Retrieval, safety and status are free and local; anything that calls a model says which one
and asks first, and `eval model` never pulls a model for you. Each run adds one line of numbers
to `eval_history.jsonl` in the data directory (version, corpus fingerprint, model, metrics; no
question or answer text), so `agronaut eval` can show the trend and mark a result measured on a
different knowledge base as stale. The golden sets ship with the package, so this works after
a plain `pip install` too.

The web app's **Quality** mode shows the same numbers, read from the same files, with no model
call: the twin's validation verdict, retrieval by language, answer faithfulness, the safety
probes, and how real searches compare with what the golden set calibrated. Every number shows
its n and date, and faithfulness never appears without its judge's agreement with a person.

### Human feedback

`/good` and `/bad` on Telegram record a bare rating, 1 or -1. There is no comment field on
purpose — it is the one place message content could enter the analytics log, and the allowlist
would drop it anyway. `agronaut analytics` prints the positive share.

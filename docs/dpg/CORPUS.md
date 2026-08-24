# Knowledge corpus — provenance, licensing, and how a source gets in

Agronaut's advice layer retrieves from a curated corpus. This records what is in it, under what
terms, and the gate every source has to pass. It exists because a cited answer is only as
trustworthy as the thing it cites, and because a corpus rots quietly if nobody measures it.

## The licence asymmetry — read this first

**Agronaut's code is MIT. Parts of its knowledge corpus are not.**

| Layer | Licence | Implication |
|---|---|---|
| Code (`aqua_model/`, `agronaut_agent/`, `agent/`, `srcs/`) | MIT | Commercial use permitted |
| `knowledge/*.md` | First-party, MIT with the repo | Commercial use permitted |
| Springer / PLOS sources | CC BY 4.0 | Commercial use permitted |
| **FAO 589** | **FAO permissive — non-commercial only** | **Commercial redistribution of that text is NOT permitted** |

FAO 589 was published in 2014, before FAO adopted Creative Commons (their CC policy applies to
material from roughly November 2019 onward). Its grant reads:

> "FAO encourages the use, reproduction and dissemination of material in this information product
> ... for private study, research and teaching, or for use in non-commercial products or services,
> provided that appropriate acknowledgement of FAO as the source and copyright holder is given."

That is functionally equivalent to **CC BY-NC**. The DPG Alliance accepts CC BY-NC and CC BY-NC-SA
(it prefers CC BY / CC BY-SA / CC0 but does not require them), so this does not block DPG status.
It is **not** conformant with the stricter Open Definition, which rejects non-commercial clauses.

**What this means in practice:** anyone running Agronaut commercially should remove the FAO entry
from `urls.txt` and rebuild the index. Retrieval degrades gracefully — the corpus simply gets
smaller. Nothing in the code depends on that source existing.

## What is in the corpus

| Source | Licence | Role |
|---|---|---|
| 21 × `knowledge/*.md` | First-party | **The answer layer.** Hand-written operator guidance |
| FAO 589 (Somerville et al., 2014) | FAO permissive (NC) | **The depth layer.** 275-page canonical reference |
| Applications, technologies and evaluation methods in smart aquaponics (Artif Intell Rev, 2024) | CC BY 4.0 | Systematic review; IoT/ML framing |
| Love et al. (2014), An International Survey of Aquaponics Practitioners (PLOS ONE) | CC BY 4.0 | Practitioner survey data |

### The answer layer is not the biggest layer, and that is fine

Measured over the 33-query golden set **before** FAO 589 was added:

| | share of corpus | share of returned passages | queries answered |
|---|---|---|---|
| `knowledge/*.md` | 23% | **94%** | **32/33** |
| scraped papers | 77% | 6% | 1/33 |

The hand-written files were 23% of the index and did 94% of the work. Volume is not the same as
usefulness, and a corpus should not be judged by chunk count. The scraped papers earn their place
as depth and as citable authority, not as the thing that answers a troubleshooting question.

## How a source gets in

Every source must clear an automated gate before being added to `urls.txt`:

```bash
python -m scripts.corpus_report --candidate "<url>" --label "<what it should be about>"
```

It checks four things, because a source can fail in four different ways:

1. **Reachable** — a non-2xx response is never indexed. Three MDPI DOIs returned HTTP 403 and their
   `"Access Denied — You don't have permission"` body was being indexed as a citable passage; at
   231 characters it clears the boilerplate floor and matches none of its keywords, so only the
   HTTP status distinguishes it from content.
2. **Substantial** — a source contributing zero chunks is a network fetch pretending to be
   citation depth. Nine of the original fourteen DOIs were exactly this.
3. **On topic** — the dangerous failure. A guessed UF/IFAS publication ID resolved to *"Sharks for
   the Aquarium"*: 28,635 characters that pass every automated check except being about the right
   subject.
4. **Openly licensed** — see above. Empirically this correlates with retrievability: every source
   measured that returned usable full text was openly licensed; every paywalled one returned
   nothing.

Verdicts are `ACCEPT`, `REVIEW` (a human licensing or quality call), or `REJECT`.

Audit the whole corpus at any time — this is also a CI-suitable regression gate, exiting non-zero
when a declared source has gone dead or drifted:

```bash
python -m scripts.corpus_report
```

## Known limitations

- **Web drift is bounded, not detected.** A publisher can edit a source without `urls.txt`
  changing. The index cache fingerprint cannot see that — detecting it would require the fetch the
  cache exists to avoid — so a 7-day TTL bounds staleness instead.
- **Coverage is narrow.** 21 hand-written files maintained by one operator. Breadth, not source
  quality, is the corpus's real weakness.
- **Extension services are frequently unreachable.** Oklahoma State's aquaponics fact sheets sit
  behind Cloudflare (HTTP 403, "Just a moment..."), and FAO's own repository landing pages are
  JavaScript-driven and yield ~322 characters. Direct PDF endpoints work; HTML landing pages
  usually do not.
- **Numeric datasets are a different pipeline.** Kaggle/Mendeley sensor data belongs in `data/`
  via `scripts/fetch_aquaponics_data.py`, feeding calibration — NOT the RAG index. Embedding CSV
  rows would pollute retrieval while answering no operator question.

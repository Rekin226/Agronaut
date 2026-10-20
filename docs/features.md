# Features in depth

What each part of Agronaut does, beyond the short list in the [README](../README.md).

## The web app

Four sections in the web app (`agronaut web`, sidebar):

- **Assistant**: a consultation that asks one question at a time and calls the engine
  (troubleshooting, low DO, yellow leaves, pump sizing…). Opens first when a model is set.
- **Design**, two tabs:
  - **Size a system**: fixed inputs → a fully sized system: tank/system volume, fish count,
    feed/day, pump turnover, biofilter, makeup water, **bill of materials**, **operating
    envelope**, maintenance checklist, and a downloadable funder-ready report.
  - **Find the best ratio**: search fish × crop-mix combinations for the best ratio under
    your binding constraint (e.g. a fixed water budget), maximizing food, protein, or
    water-use efficiency, and showing the gain over a naive even split.
- **My Twin**: the system you actually run, mirrored, with a forecast and logged readings.
- **Quality**: how good Agronaut is, measured (the same numbers as `agronaut eval`).

Design and My Twin are **fully deterministic and need no LLM at all**; without a model the
app opens on Design.

### Send it a photo

Photograph a yellowing leaf, a sick fish, or green water — on **Telegram, WhatsApp, or the
web chat** — and you get a *cited differential*, not a guess:

1. A vision model describes what it sees. It only **observes**.
2. A deterministic guard strips any measurement or prescription out of that description, so a
   fabricated `pH 6.4` or an invented `add 5 mL of salt` can never enter the conversation as
   though you had said it. A named condition is kept but flagged **unverified**.
3. A fixed, cited table (`aqua_model/triage.py`) maps the visible symptoms to a **ranked list
   of candidate causes** — each naming the knowledge document it came from, and each with the
   checks that would tell it apart from its neighbours.

It will not hand you a single confident diagnosis, because a photograph cannot support one:
iron deficiency and pH lockout look identical in an image, so you get both plus the check that
separates them. Ordering follows the knowledge base's own rules — pH before iron, water quality
before any fish pathogen. Nothing it says states a dose.

### See the system, and then see it running

Every design renders as a self-contained 3D page — greenhouse, tanks, filtration, beds,
graded plumbing with the flow animated in the direction the water actually goes. One HTML
file, no server and no CDN, so it opens from a double-click on a laptop that has never been
online.

With a twin bound to it, the same drawing stops being a picture:

```bash
# a design, plus the season it would have at a real site, on a slider
python scripts/render_3d.py --crop basil --site taichung_2025 --days 365 -o first_year.html
```

Drag the scrubber and the fish grow, the water turns amber and then red as ammonia and
nitrite cross the bands `aqua_model/advisory.py` acts on, and the crop is drawn as vigorously
as it is actually growing. You *watch* the nitrite spike of week two arrive instead of reading
about it afterwards. On Telegram or WhatsApp, `show_my_system_3d` does the same for the system
**you** run: your fish count, your water, advanced through the weather that actually happened
since you last spoke to the bot, then forward through the forecast.

The badge always says which of the three you are looking at (**as designed**, **today**, or a
**forecast**), because confusing them would be the worst thing this view could do. "Today" is
the same state the bot calls "Now", so the picture and the conversation never describe
different water. The panel says in as many words that the geometry is a proposed arrangement,
never a survey of your site.

### Voice notes

Speak instead of typing, on Telegram or WhatsApp. The transcript runs through a normal turn, so
memory, tools and cited knowledge all apply.

### Consultative agent

Agronaut runs a consultation, not a one-shot Q&A. It identifies your goal (design a
system, optimize a ratio, or troubleshoot a problem), asks for the few essentials that
goal needs, then gives a first-cut recommendation tied to *your* system — and remembers
it (a typed System Profile + episodic notes) across sessions.

You can also set the mode explicitly with `/design`, `/optimize`, or `/troubleshoot` —
the bot then jumps straight to gathering what that goal needs. All commands appear in
Telegram's `/` menu.

Agronaut also learns from outcomes: after suggesting a fix it can check back later
("did the water change fix the ammonia?"), and whatever worked is remembered and shapes
its future advice.

Lessons can also become shared knowledge: a generalized, PII-stripped version of a verified
fix is nominated, the owner approves it in a local review CLI (`python -m agronaut_agent.review`),
and approved insights then help other operators — labeled as community experience, never as
verified science.

And it calibrates to reality: when you report real measured outcomes (harvest weight, FCR,
crop yield), Agronaut tunes *your* future sizings toward your system — bounded to the
published empirical ranges, so a measurement can only move a coefficient within what the
literature allows, and every calibrated number is labeled.

The deterministic sizing model covers ten species (tilapia, clarias, channel catfish, trout,
common carp, barramundi, silver perch, tambaqui, pangasius, freshwater prawn) and 34 crops — leafy greens (lettuce, kale, chard, spinach, pak choi, arugula,
watercress…), culinary herbs (basil, mint, cilantro, parsley, dill…), and fruiting crops
(tomato, cucumber, pepper, strawberry, eggplant, zucchini…) — each with cited, calibratable
seed coefficients placed within FAO 589's published feeding-rate band for its category.

## Honesty by design
Every result lists the coefficients it used (value + range + **source**: FAO 589,
UVI/Rakocy, literature) and an explicit list of what it does **not** model
(pH/alkalinity, micronutrients, salinity, solids, pests, cohort logic, per-crop ET).
A confidently-wrong design can't masquerade as complete.

The same rule governs the advice layer. Citation is enforced **in code**, not asked for in a
prompt: every retrieved passage is labelled with its source before the model ever sees it. And
retrieval is allowed to say *no* — a question the corpus cannot answer returns "no matching
passages" rather than the three closest paragraphs wearing source labels. Ask Agronaut the capital
of Canada and it will decline, not cite an aquaponics paper at you.

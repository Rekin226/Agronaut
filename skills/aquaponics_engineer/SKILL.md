---
name: aquaponics-engineer
description: >-
  Compute cited, validated aquaponics and hydroponics system designs — tank/reservoir
  sizing, fish count, feed, pump, biofilter, bill of materials, operating envelope, and the
  best fish/crop ratio under a water budget. Deterministic engineering math, not an LLM
  guess: every number traces to a sourced coefficient and each result lists what it does NOT
  model. Use whenever a user asks to design, size, or optimize an aquaponic or hydroponic
  food system.
license: MIT
homepage: https://github.com/Rekin226/Agronaut
compatible: [claude-code, hermes-agent, openclaw]
---

# Aquaponics Engineer

A deterministic sizing engine for soil-less food systems, packaged as a portable skill. It
wraps [Agronaut](https://github.com/Rekin226/Agronaut)'s `aqua_model` trust zone — pure
Python, no LLM, no network — so any agent can hand a user a *computed*, cited design instead
of a plausible-sounding guess.

## When to use

Invoke this skill when the user wants to **design, size, or optimize** an aquaponic
(fish + plants) or hydroponic (plants only) system — e.g. "how big a tank for 12 m² of
lettuce with tilapia?", "size a hydroponic bed for 10 m²", "what fish/crop ratio maximizes
food from 5000 L/day?".

Do **not** invent sizing numbers yourself. Call the CLI and relay its output, including the
cited coefficients and the "NOT modeled" caveats — that honesty is the point.

## How to use

Run the CLI (Python 3.11+; `pip install -r requirement.txt` from the Agronaut repo):

```bash
# Aquaponics (fish + plants)
python -m skills.aquaponics_engineer.cli size-aquaponics \
    --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000

# Hydroponics (plants only, nutrients dosed)
python -m skills.aquaponics_engineer.cli size-hydroponics \
    --crop lettuce --area 10 --temp 22 --water 500

# Best fish/crop ratio under a constraint
python -m skills.aquaponics_engineer.cli optimize \
    --area 10 --temp 28 --water 5000 --objective food     # food | protein | water_efficiency

# What's supported
python -m skills.aquaponics_engineer.cli list
```

Each command prints a compact, cited result. A bad input (unknown species/crop, out-of-range
value) is rejected at the trust gate with `VALIDATION_FAILED` and a non-zero exit — ask the
user for a corrected value rather than working around it.

## What you get

- Sizing: tank/reservoir volume, fish count & biomass, feed/day, pump turnover, biofilter
  media (aquaponics), ET-driven water use and EC/nutrient target (hydroponics), makeup water.
- Feasibility against a daily water budget, with a nearest-feasible hint when it fails.
- A bill of materials and an operating envelope (pH, temperature, DO, EC bands).
- Every coefficient used, with its value, range, and **source** (FAO 589, UVI/Rakocy,
  literature) — and an explicit list of what the model does **not** cover.

## Guarantees

- **Deterministic:** same inputs → same output. No randomness, no model drift.
- **Auditable:** every number traces to a cited coefficient.
- **Honest:** results state their own limits; seeds are calibration starting points, not
  universal constants.

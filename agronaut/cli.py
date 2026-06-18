"""Headless CLI for Agronaut's deterministic core (issue #20).

Subcommands:
    design    size one system from fixed inputs -> sizing, BOM, envelope, honesty layer
    optimize  search fish/crop ratios for the best score under the water budget
    species   list supported fish species
    crops     list supported crops

Every command supports ``--json`` for machine-readable output. ``design --report`` emits the
full cited Markdown report. The CLI imports only the standard library plus the pure
``aqua_model`` trust zone and the ``agent.facts`` validation seam — no Streamlit, no LLM.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Sequence

from agent.facts import ValidationError, available_crops, available_species, design_from_form
from aqua_model.optimizer import OBJECTIVES, OptimizeInput, optimize
from aqua_model.report import to_markdown
from aqua_model.sizing import size_system

_PROG = "agronaut"


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Design and optimize aquaponics systems from the command line "
        "(deterministic — no LLM, no server).",
    )
    sub = parser.add_subparsers(dest="command", metavar="{design,optimize,species,crops}")

    # design ---------------------------------------------------------------
    d = sub.add_parser("design", help="size one system from fixed inputs")
    d.add_argument("--species", required=True, help="fish species (see `agronaut species`)")
    d.add_argument("--crop", required=True, help="crop (see `agronaut crops`)")
    d.add_argument("--grow-area", type=float, required=True, metavar="M2",
                   help="planted raft/DWC area in m² (the sizing anchor)")
    d.add_argument("--temp", type=float, required=True, metavar="C",
                   help="mean water temperature in °C")
    d.add_argument("--water-budget", type=float, required=True, metavar="LPD",
                   help="makeup water available per day, L/day")
    d.add_argument("--source-water-note", default=None,
                   help="optional salinity/quality caveat carried into the report")
    d.add_argument("--site", default=None, help="site name for the --report header")
    d.add_argument("--report", action="store_true",
                   help="print the full cited Markdown design report")
    d.add_argument("--json", action="store_true", help="emit the result as JSON")
    d.set_defaults(func=cmd_design)

    # optimize -------------------------------------------------------------
    o = sub.add_parser("optimize", help="find the best fish/crop ratio under your constraint")
    o.add_argument("--grow-area", type=float, required=True, metavar="M2",
                   help="total planted area in m²")
    o.add_argument("--temp", type=float, required=True, metavar="C",
                   help="mean water temperature in °C")
    o.add_argument("--water-budget", type=float, required=True, metavar="LPD",
                   help="makeup water available per day, L/day (the binding constraint)")
    o.add_argument("--objective", choices=OBJECTIVES, default="water_efficiency",
                   help="what to maximize (default: water_efficiency)")
    o.add_argument("--fish", nargs="+", default=None, metavar="SPECIES",
                   help="restrict the fish palette (default: all)")
    o.add_argument("--crops", nargs="+", default=None, metavar="CROP",
                   help="restrict the crop palette (default: all)")
    o.add_argument("--top", type=int, default=5, metavar="N",
                   help="how many ranked candidates to show (default: 5)")
    o.add_argument("--json", action="store_true", help="emit the full result as JSON")
    o.set_defaults(func=cmd_optimize)

    # species / crops ------------------------------------------------------
    s = sub.add_parser("species", help="list supported fish species")
    s.add_argument("--json", action="store_true", help="emit as a JSON array")
    s.set_defaults(func=cmd_species)

    c = sub.add_parser("crops", help="list supported crops")
    c.add_argument("--json", action="store_true", help="emit as a JSON array")
    c.set_defaults(func=cmd_crops)

    return parser


# --------------------------------------------------------------------------- commands
def cmd_design(args: argparse.Namespace) -> int:
    try:
        design = design_from_form(
            fish_species=args.species,
            crop=args.crop,
            grow_area_m2=args.grow_area,
            temperature_c=args.temp,
            water_budget_lpd=args.water_budget,
            source_water_note=args.source_water_note,
        )
    except ValidationError as exc:
        _fail(f"validation failed:\n  - " + "\n  - ".join(exc.errors))
        return 2

    out = size_system(design)

    if args.report:
        print(to_markdown(design, out, site=args.site))
        return 0
    if args.json:
        _print_json(dataclasses.asdict(out))
        return 0

    _print_design(design, out)
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    fish = _resolve_palette(args.fish, available_species(), "fish species")
    crops = _resolve_palette(args.crops, available_crops(), "crop")
    if fish is _INVALID or crops is _INVALID:
        return 2

    kwargs = {
        "grow_area_m2": args.grow_area,
        "temperature_c": args.temp,
        "water_budget_lpd": args.water_budget,
        "objective": args.objective,
    }
    if fish is not None:
        kwargs["fish_palette"] = tuple(fish)
    if crops is not None:
        kwargs["crop_palette"] = tuple(crops)

    result = optimize(OptimizeInput(**kwargs))

    if args.json:
        _print_json(dataclasses.asdict(result))
        return 0

    _print_optimize(result, top=args.top)
    return 0


def cmd_species(args: argparse.Namespace) -> int:
    return _print_list(available_species(), as_json=args.json)


def cmd_crops(args: argparse.Namespace) -> int:
    return _print_list(available_crops(), as_json=args.json)


# --------------------------------------------------------------------------- palette helper
_INVALID = object()  # sentinel: a palette name was unknown


def _resolve_palette(names, known: Sequence[str], label: str):
    """Lowercase + validate user-supplied palette names. Returns the list, None (use the
    model default when no names were given), or the _INVALID sentinel after printing why."""
    if not names:
        return None
    known_set = set(known)
    resolved, unknown = [], []
    for raw in names:
        key = str(raw).strip().lower()
        (resolved if key in known_set else unknown).append(key)
    if unknown:
        _fail(f"unknown {label}: {', '.join(unknown)}\n  known: {', '.join(known)}")
        return _INVALID
    return resolved


# --------------------------------------------------------------------------- formatting
def _print_design(design, out) -> None:
    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE (binding: {out.binding_constraint})"
    print(f"Aquaponics design — {status}")
    print(f"  {design.fish_species} × {design.crop}, {design.grow_area_m2} m² grow area, "
          f"{design.temperature_c} °C, {design.water_budget_lpd} L/day budget\n")

    rows = [
        ("Feed", f"{out.feed_g_per_day} g/day"),
        ("Fish", f"{out.fish_count} head (~{out.fish_biomass_kg} kg biomass)"),
        ("Rearing tank", f"{out.rearing_tank_volume_l} L"),
        ("System volume", f"{out.system_volume_l} L"),
        ("Pump turnover", f"{out.pump_turnover_lph} L/h"),
        ("Biofilter media", f"{out.biofilter_media_m2} m²"),
        ("Makeup water", f"{out.makeup_water_lpd} L/day"),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)}  {value}")

    if out.warnings:
        print()
        for w in out.warnings:
            print(f"  ⚠ {w}")

    print("\n  Coefficients are calibration SEEDS from published sources — calibrate against a")
    print("  real system before building. This model does NOT account for pH/alkalinity,")
    print("  micronutrients, salinity, solids, pests, cohort logic, or diel temperature.")
    print("  Run with --report for the full cited breakdown, or --json for structured output.")


def _print_optimize(result, top: int) -> None:
    print(f"Optimize for {result.objective} — searched {result.searched}, "
          f"{result.feasible_count} feasible")

    if result.best is None:
        print("\n  No configuration fits the water budget. Try a larger --water-budget or a "
              "smaller --grow-area.")
        return

    unit = _objective_unit(result.objective)
    best = result.best
    print(f"\n  Best: {best.fish_species} | {_fmt_alloc(best.crop_allocation)}")
    print(f"    score ({result.objective})  {best.score} {unit}")
    print(f"    food                  {best.food_kg_yr} kg/yr")
    print(f"    protein               {best.protein_kg_yr} kg/yr")
    print(f"    makeup water          {best.makeup_water_lpd} L/day")
    if result.improvement_vs_baseline_pct is not None:
        print(f"  +{result.improvement_vs_baseline_pct}% vs the naive even-split baseline")

    rest = [c for c in result.ranked if c is not best][: max(0, top - 1)]
    if rest:
        print("\n  Runner-up ratios:")
        for i, c in enumerate(rest, start=2):
            print(f"   {i}. {c.fish_species} | {_fmt_alloc(c.crop_allocation)}  "
                  f"— {c.score} {unit}")

    print("\n  Yields/protein are seed coefficients — calibrate before quoting outcomes. "
          "--json for full output.")


def _objective_unit(objective: str) -> str:
    return {
        "food": "kg/yr",
        "protein": "kg/yr",
        "water_efficiency": "kg per m³/yr",
    }.get(objective, "")


def _fmt_alloc(alloc: dict) -> str:
    return ", ".join(f"{crop} {frac:g}" for crop, frac in alloc.items())


def _print_list(items, as_json: bool) -> int:
    if as_json:
        _print_json(items)
    else:
        for item in items:
            print(item)
    return 0


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _fail(message: str) -> None:
    print(f"{_PROG}: error: {message}", file=sys.stderr)


# --------------------------------------------------------------------------- entrypoint
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return args.func(args)

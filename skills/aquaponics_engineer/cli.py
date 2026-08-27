"""aquaponics-engineer — a standalone CLI over Agronaut's deterministic `aqua_model`.

This is the executable an agent (Hermes, OpenClaw, Claude Code, or any tool that can run a
subprocess) calls to get *computed*, cited aquaponics/hydroponics sizing — not an LLM guess.
No LLM and no network: validated input in, cited sizing out. The trust gate is preserved, so
a bad argument is rejected loudly (non-zero exit) rather than producing a wrong design.

    python -m skills.aquaponics_engineer.cli size-aquaponics \
        --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
    python -m skills.aquaponics_engineer.cli size-hydroponics --crop lettuce --area 10 --temp 22 --water 500
    python -m skills.aquaponics_engineer.cli optimize --area 10 --temp 28 --water 5000 --objective food
    python -m skills.aquaponics_engineer.cli list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo importable when this file is run directly from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agronaut_agent import serialize  # noqa: E402
from aqua_model import (  # noqa: E402
    OBJECTIVES,
    OptimizeInput,
    ValidationError,
    optimize,
    size_hydroponic_system,
    size_system,
    validate_design_input,
    validate_hydroponic_input,
)
from aqua_model.crops import CROPS  # noqa: E402
from aqua_model.species import SPECIES  # noqa: E402


def _cmd_size_aquaponics(a) -> int:
    try:
        design = validate_design_input(a.fish, a.crop, a.area, a.temp, a.water)
    except ValidationError as err:
        print(serialize.serialize_validation_error(err.errors))
        return 2
    print(serialize.serialize_design_output(size_system(design)))
    return 0


def _cmd_size_hydroponics(a) -> int:
    try:
        design = validate_hydroponic_input(a.crop, a.area, a.temp, a.water)
    except ValidationError as err:
        print(serialize.serialize_validation_error(err.errors))
        return 2
    print(serialize.serialize_hydroponic_output(size_hydroponic_system(design)))
    return 0


def _cmd_optimize(a) -> int:
    obj = (a.objective or "water_efficiency").strip().lower()
    if obj not in OBJECTIVES:
        print(f"Unknown objective {a.objective!r}. Use one of: {', '.join(OBJECTIVES)}.")
        return 2
    try:
        res = optimize(OptimizeInput(grow_area_m2=a.area, temperature_c=a.temp,
                                     water_budget_lpd=a.water, objective=obj))
    except ValidationError as err:
        print(serialize.serialize_validation_error(err.errors))
        return 2
    print(serialize.serialize_optimize_result(res))
    return 0


def _cmd_list(a) -> int:
    print("Fish species: " + ", ".join(sorted(SPECIES)))
    print("Crops: " + ", ".join(sorted(CROPS)))
    print("Optimization objectives: " + ", ".join(OBJECTIVES))
    return 0


def add_sizing_subcommands(sub, aliases: dict | None = None) -> None:
    """Register the four sizing commands on an existing subparser group.

    Shared so the top-level `agronaut` command and this portable skill CLI define the
    arguments once and cannot drift apart. `aliases` maps a canonical command name to
    extra names — `agronaut` offers shorter ones (`size`, `size-hydro`) while the skill
    keeps the explicit names an agent reads from SKILL.md.
    """
    aliases = aliases or {}

    def _add(name, **kw):
        return sub.add_parser(name, aliases=aliases.get(name, []), **kw)

    sa = _add("size-aquaponics", help="size a fish+plant system")
    sa.add_argument("--fish", required=True)
    sa.add_argument("--crop", required=True)
    sa.add_argument("--area", type=float, required=True, help="grow area m2")
    sa.add_argument("--temp", type=float, required=True, help="water temperature C")
    sa.add_argument("--water", type=float, required=True, help="daily water budget L")
    sa.set_defaults(func=_cmd_size_aquaponics)

    sh = _add("size-hydroponics", help="size a plant-only (no fish) system")
    sh.add_argument("--crop", required=True)
    sh.add_argument("--area", type=float, required=True)
    sh.add_argument("--temp", type=float, required=True)
    sh.add_argument("--water", type=float, required=True)
    sh.set_defaults(func=_cmd_size_hydroponics)

    so = _add("optimize", help="best fish/crop ratio under a constraint")
    so.add_argument("--area", type=float, required=True)
    so.add_argument("--temp", type=float, required=True)
    so.add_argument("--water", type=float, required=True)
    so.add_argument("--objective", default="water_efficiency")
    so.set_defaults(func=_cmd_optimize)

    sl = _add("list", help="list supported species, crops, objectives")
    sl.set_defaults(func=_cmd_list)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aquaponics-engineer",
                                description="Computed, cited aquaponics/hydroponics sizing.")
    sub = p.add_subparsers(dest="cmd", required=True)
    add_sizing_subcommands(sub)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline field-agriculture entry points; no LLM imports."""

import json
from pathlib import Path

from .domains import capabilities as domain_capabilities
from .domains import run_capability
from .field_io import render_field_report


def _reject_constant(value):
    raise ValueError(f"non-finite JSON value {value} is not supported")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def calculate(args):
    try:
        payload = json.loads(Path(args.input).read_text(), parse_constant=_reject_constant,
                             object_pairs_hook=_unique_keys)
        task = {"water-needs": "crop_water", "irrigation-plan": "root_zone_budget",
                "design-irrigation": f"{getattr(args, 'method', 'drip')}_design",
                "evaluate-drip-lateral": "drip_lateral",
                "evaluate-sprinkler-lateral": "sprinkler_lateral",
                "evaluate-drip-zone": "drip_zone",
                "evaluate-drip-manifold": "drip_manifold",
                "evaluate-surface": "surface_evaluation", "predict-surface-advance": "surface_advance", "nutrient-budget": "nutrient_budget",
                "evaluate-sprinkler-catches": "sprinkler_catch_test", "evaluate-drip-emitters": "drip_emitter_test",
                "evaluate-soil-profile": "soil_profile", "dual-crop-water": "dual_crop_water",
                "coupled-soil-water": "coupled_soil_water"}[args.cmd]
        result = run_capability("soil", task, payload)["result"]
    except (ValueError, TypeError, OSError) as exc:
        error = {"status": "VALIDATION_FAILED", "error": str(exc)}
        print(json.dumps(error) if args.json else f"VALIDATION_FAILED: {exc}")
        return 2
    if args.cmd == "design-irrigation":
        from .irrigation_io import render_drip_report, render_sprinkler_report
        renderer = render_sprinkler_report if args.method == "sprinkler" else render_drip_report
    elif args.cmd == "evaluate-sprinkler-lateral":
        from .sprinkler_lateral_io import render_sprinkler_lateral_report
        renderer = render_sprinkler_lateral_report
    elif args.cmd == "evaluate-drip-lateral":
        from .drip_lateral_io import render_drip_lateral_report
        renderer = render_drip_lateral_report
    elif args.cmd == "evaluate-drip-zone":
        from .drip_zone_io import render_drip_zone_report
        renderer = render_drip_zone_report
    elif args.cmd == "evaluate-drip-manifold":
        from .drip_manifold_io import render_drip_manifold_report
        renderer = render_drip_manifold_report
    elif args.cmd == "predict-surface-advance":
        from .surface_advance_io import render_surface_advance_report
        renderer = render_surface_advance_report
    elif args.cmd == "coupled-soil-water":
        from .coupled_water_io import render_coupled_water_report
        renderer = render_coupled_water_report
    elif args.cmd == "dual-crop-water":
        from .evaporation_io import render_evaporation_report
        renderer = render_evaporation_report
    elif args.cmd == "evaluate-soil-profile":
        from .soil_profile_io import render_soil_profile_report
        renderer = render_soil_profile_report
    elif args.cmd == "evaluate-drip-emitters":
        from .emitter_io import render_emitter_report
        renderer = render_emitter_report
    elif args.cmd == "evaluate-sprinkler-catches":
        from .catch_io import render_catch_report
        renderer = render_catch_report
    elif args.cmd == "evaluate-surface":
        from .surface_io import render_surface_report
        renderer = render_surface_report
    elif args.cmd == "nutrient-budget":
        from .nutrition_io import render_nutrition_report
        renderer = render_nutrition_report
    else:
        renderer = render_field_report
    print(json.dumps(result, indent=2, allow_nan=False) if args.json else renderer(result))
    return 0


def capabilities(args):
    result = domain_capabilities(args.system)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for system, tasks in result.items():
            for name, info in tasks.items():
                command = f" ({info['command']})" if info["command"] else ""
                print(f"{system}.{name}: {info['status']}{command}")
                for limit in info["applicability"]:
                    print(f"  {limit}")
    return 0


def calculate_capability(args):
    """Shared JSON adapter surface, independent of any chat model or saved profile."""
    try:
        payload = json.loads(Path(args.input).read_text(), parse_constant=_reject_constant,
                             object_pairs_hook=_unique_keys)
        result = run_capability(args.system, args.task, payload)
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({"status": "VALIDATION_FAILED", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


def add_field_commands(subparsers):
    for name, help_text in (
        ("water-needs", "compute experimental field crop-water demand from supplied weather"),
        ("irrigation-plan", "replay soil water and compute experimental refill budgets"),
        ("evaluate-surface", "evaluate a supplied surface-irrigation event; no cutoff prediction"),
        ("predict-surface-advance", "predict experimental advance in a sloping furrow/border scenario; no cutoff advice"),
        ("evaluate-sprinkler-catches", "evaluate a supplied equal-area sprinkler catch grid"),
        ("evaluate-drip-emitters", "evaluate timed volumes from sampled drip emitters"),
        ("evaluate-drip-lateral", "predict nominal emitter pressures/flows on a surveyed lateral; no schedule"),
        ("evaluate-sprinkler-lateral", "solve surveyed sprinkler nozzle and riser pressures/flows; no schedule"),
        ("evaluate-drip-manifold", "solve a surveyed drip manifold and its simultaneous laterals; no schedule"),
        ("evaluate-drip-zone", "solve simultaneous drip laterals at one supplied common junction; no schedule"),
        ("evaluate-soil-profile", "assess root-zone water storage from explicit soil layers"),
        ("dual-crop-water", "replay surface evaporation and potential crop demand using supplied dual coefficients"),
        ("coupled-soil-water", "replay drainage, infiltration, evaporation and root uptake using shared soil stores"),
        ("nutrient-budget", "account for declared mineral-fertilizer nutrients against supplied targets"),
    ):
        parser = subparsers.add_parser(name, help=help_text)
        parser.add_argument("--input", required=True, help="field request JSON file")
        parser.add_argument("--json", action="store_true", help="emit reproducible JSON")
        parser.set_defaults(func=calculate)
    parser = subparsers.add_parser("design-irrigation", help="check experimental irrigation equipment designs")
    parser.add_argument("--method", required=True, choices=("drip", "sprinkler"))
    parser.add_argument("--input", required=True, help="equipment and layout JSON request")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=calculate)
    parser = subparsers.add_parser("field-capabilities", help="show implemented field calculations and gaps")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=capabilities, system="soil")
    parser = subparsers.add_parser("capabilities", help="show agricultural tasks, inputs and evidence levels")
    parser.add_argument("--system", choices=("soil", "aquaponics", "hydroponics"))
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=capabilities)
    parser = subparsers.add_parser("calculate", help="run a registered agricultural task and emit JSON")
    parser.add_argument("--system", required=True, choices=("soil", "aquaponics", "hydroponics"))
    parser.add_argument("--task", required=True, help="task name from the capabilities command")
    parser.add_argument("--input", required=True, help="JSON inputs matching the task's declared schema")
    parser.set_defaults(func=calculate_capability)

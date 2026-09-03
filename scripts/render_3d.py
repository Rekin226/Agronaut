"""Render a sized aquaponic system as a self-contained, interactive 3D web page.

Pipeline:  validate -> size_system -> plan_layout -> to_scene -> one HTML file.

With `--site`, the same design is also simulated day by day through that site's real
weather and the season is embedded in the file, so the viewer gets a scrubber: the same
geometry as designed, and as it is projected to stand on any day of the run. The frames
are embedded rather than fetched, for the same reason the library is vendored.

The output embeds the vendored three.js (web/vendor/), the viewer, and the scene JSON, so the
file works offline from a double-click — no server, no CDN, no build step. That is deliberate:
the people this is for (see the offline-first epic, #79) cannot assume connectivity, and a
design review happens wherever the laptop is.

This script lives OUTSIDE the trust zone: `aqua_model` computed the numbers; this only draws
them. Usage:

    python scripts/render_3d.py --species tilapia --crop lettuce --area 24 \
        --temp 26 --water 400 --system-type raft -o design_3d.html

    python scripts/render_3d.py --crop basil --site taichung_2025 --days 365 \
        -o first_year.html          # same design, plus a season to scrub through
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aqua_model.layout import plan_layout  # noqa: E402
from aqua_model.scene3d import to_scene  # noqa: E402
from aqua_model.sizing import size_system  # noqa: E402
from aqua_model.validate import validate_design_input  # noqa: E402

WEB = REPO_ROOT / "web"
CLIMATE = REPO_ROOT / "data" / "climate"


def simulate(out, args):
    """Run the sized design through a site's weather. Returns (run, dates).

    A NEW build starts uncycled: the nitrite spike of the first weeks is part of an honest
    first season, and it is exactly what this view exists to make visible.
    """
    from aqua_model.climate import GreenhouseParams, from_records
    from aqua_model.crops import get_crop
    from aqua_model.production import (
        ProductionParams,
        simulate_production,
        start_state_from_design,
    )
    from aqua_model.species import get_species

    path = CLIMATE / f"{args.site.strip().lower()}.json"
    if not path.exists():
        have = ", ".join(sorted(p.stem for p in CLIMATE.glob("*.json"))) or "none"
        raise SystemExit(
            f"No climate file for site {args.site!r}. Available: {have}. Fetch one "
            f"(no API key needed): python scripts/fetch_climate.py --lat <LAT> "
            f"--lon <LON> --name {args.site}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    days = payload["days"][:max(2, int(args.days))]
    weather = from_records(days)
    species = get_species(args.species)
    gh = GreenhouseParams(shade_to_ambient=args.greenhouse == "shade")
    init = start_state_from_design(out, species, water_temp_c=weather[0].t_mean_c,
                                   cycled=False)
    run = simulate_production(init, weather, species, args.species,
                              get_crop(args.crop), out.grow_area_m2,
                              params=ProductionParams(greenhouse=gh))
    return run, [str(d.get("date", "")) for d in days]


def build_html(scene: dict, title: str) -> str:
    # utf-8 everywhere, never the platform default: the template and every scene subtitle
    # carry m2, degree signs and dashes, and a host under LANG=C would fail to write the
    # file rather than draw a system.
    template = (WEB / "viewer_template.html").read_text(encoding="utf-8")
    three = (WEB / "vendor" / "three.min.js").read_text(encoding="utf-8")
    orbit = (WEB / "vendor" / "OrbitControls.js").read_text(encoding="utf-8")
    # </script> inside the JSON payload would end the script block early; escape defensively.
    payload = json.dumps(scene).replace("</", "<\\/")
    return (template
            .replace("__TITLE__", title)
            .replace("/*__THREE_JS__*/", three)
            .replace("/*__ORBIT_CONTROLS__*/", orbit)
            .replace("__SCENE_JSON__", payload))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--species", default="tilapia")
    ap.add_argument("--crop", default="lettuce")
    ap.add_argument("--area", type=float, default=24.0, help="grow area m²")
    ap.add_argument("--temp", type=float, default=26.0, help="water temperature °C")
    ap.add_argument("--water", type=float, default=500.0, help="water budget L/day")
    ap.add_argument("--system-type", default="raft",
                    choices=["raft", "nft", "media_bed", "vertical_tower"])
    ap.add_argument("--site", default="",
                    help="climate slug from data/climate/ — simulates the season and "
                         "embeds it as a scrubber (e.g. taichung_2025)")
    ap.add_argument("--days", type=int, default=365, help="days to simulate with --site")
    ap.add_argument("--greenhouse", default="poly", choices=["poly", "shade"],
                    help="greenhouse mode for the simulation")
    ap.add_argument("-o", "--out", default="design_3d.html")
    args = ap.parse_args(argv)

    design = validate_design_input(
        fish_species=args.species, crop=args.crop, grow_area_m2=args.area,
        temperature_c=args.temp, water_budget_lpd=args.water,
        system_type=args.system_type)
    out = size_system(design)
    layout = plan_layout(out, crop_label=args.crop, species_label=args.species)

    trajectory, dates = (), ()
    run = None
    if args.site:
        if not out.feasible:
            raise SystemExit(f"The design is infeasible ({out.binding_constraint}) — "
                             "simulating it would project a system nobody should build.")
        run, dates = simulate(out, args)
        trajectory = run.trajectory

    scene = to_scene(
        layout, out,
        crop=args.crop, species=args.species,
        trajectory=trajectory, dates=dates,
        name=f"{args.system_type.replace('_', ' ').title()} aquaponics — "
             f"{args.species} + {args.crop}",
        subtitle=(f"{out.grow_area_m2:.0f} m² grow area · {out.fish_count} fish "
                  f"({out.fish_biomass_kg:.0f} kg) · {out.system_volume_l:,.0f} L · "
                  f"greenhouse {layout.greenhouse.width_m:.1f}×{layout.greenhouse.length_m:.1f} m"))

    html = build_html(scene, title=scene["name"])
    dest = Path(args.out)
    dest.write_text(html, encoding="utf-8")
    print(f"wrote {dest}  ({len(html) / 1e6:.1f} MB, open in any browser)")
    if run is not None:
        s = run.summary
        print(f"  season: {len(trajectory)} days at {args.site} · "
              f"{len(scene['twin']['frames'])} frames in the scrubber · "
              f"fish {s.fish_harvested_kg:.0f} kg harvested + "
              f"{s.fish_standing_kg:.0f} kg standing · crop {s.crop_harvested_kg:.0f} kg · "
              f"peak NO2 {s.peak_no2_mg_l:.2f} mg/L")
    if not out.feasible:
        print(f"NOTE: design reported infeasible — {out.binding_constraint}")
    for w in out.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

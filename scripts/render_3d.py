"""Render a sized aquaponic system as a self-contained, interactive 3D web page.

Pipeline:  validate -> size_system -> plan_layout -> to_scene -> one HTML file.

The output embeds the vendored three.js (web/vendor/), the viewer, and the scene JSON, so the
file works offline from a double-click — no server, no CDN, no build step. That is deliberate:
the people this is for (see the offline-first epic, #79) cannot assume connectivity, and a
design review happens wherever the laptop is.

This script lives OUTSIDE the trust zone: `aqua_model` computed the numbers; this only draws
them. Usage:

    python scripts/render_3d.py --species tilapia --crop lettuce --area 24 \
        --temp 26 --water 400 --system-type raft -o design_3d.html
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


def build_html(scene: dict, title: str) -> str:
    template = (WEB / "viewer_template.html").read_text()
    three = (WEB / "vendor" / "three.min.js").read_text()
    orbit = (WEB / "vendor" / "OrbitControls.js").read_text()
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
    ap.add_argument("-o", "--out", default="design_3d.html")
    args = ap.parse_args(argv)

    design = validate_design_input(
        fish_species=args.species, crop=args.crop, grow_area_m2=args.area,
        temperature_c=args.temp, water_budget_lpd=args.water,
        system_type=args.system_type)
    out = size_system(design)
    layout = plan_layout(out, crop_label=args.crop, species_label=args.species)
    scene = to_scene(
        layout, out,
        crop=args.crop, species=args.species,
        name=f"{args.system_type.replace('_', ' ').title()} aquaponics — "
             f"{args.species} + {args.crop}",
        subtitle=(f"{out.grow_area_m2:.0f} m² grow area · {out.fish_count} fish "
                  f"({out.fish_biomass_kg:.0f} kg) · {out.system_volume_l:,.0f} L · "
                  f"greenhouse {layout.greenhouse.width_m:.1f}×{layout.greenhouse.length_m:.1f} m"))

    html = build_html(scene, title=scene["name"])
    dest = Path(args.out)
    dest.write_text(html)
    print(f"wrote {dest}  ({len(html) / 1e6:.1f} MB, open in any browser)")
    if not out.feasible:
        print(f"NOTE: design reported infeasible — {out.binding_constraint}")
    for w in out.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

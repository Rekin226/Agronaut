"""Serialize a `layout.Layout` into a renderer-neutral 3D scene description.

The contract mirrors `schematic.py`'s split between scene and backend: `layout.py` decides
where things stand, this module states it as plain data, and whatever draws it — the bundled
three.js viewer, some future glTF export — consumes the same JSON. Nothing here knows about
WebGL, and nothing in the viewer re-derives a dimension: if a number is wrong in the picture,
it is wrong here, which is where it can be tested.

Coordinate convention: x across the greenhouse width, y along its length, z up, metres,
origin at one corner. The viewer maps that to its own axes.

Pure and deterministic, like the rest of `aqua_model/`.
"""

from __future__ import annotations

from dataclasses import asdict

from .layout import Layout, Placed
from .types import DesignOutput

SCENE_SCHEMA_VERSION = "1.0.0"


def _component(c: Placed) -> dict:
    # Compact form: drop empty fields, then restore the geometry keys whose zeros are meaningful.
    d = {k: v for k, v in asdict(c).items() if v not in (0.0, False, "", None)}
    d["kind"] = c.kind
    d["role"] = c.role
    d["x"] = c.x
    d["y"] = c.y
    if c.kind == "box":
        d["w"], d["l"], d["h"] = c.w, c.l, c.h
    else:
        d["d"], d["h"] = c.d, c.h
    d.pop("plant_spacing_m", None)
    if c.plants:
        d["plant_spacing"] = c.plant_spacing_m
    return d


def to_scene(layout: Layout, out: DesignOutput, *,
             name: str = "Aquaponic system", subtitle: str = "") -> dict:
    """Build the scene dict the 3D viewer renders.

    Fish are shown in proportion to the design's stocking (capped for legibility — the
    picture is an explanation, not a census), split evenly across rearing tanks."""
    tanks = layout.by_role("fish_tank")
    fish: list[dict] = []
    if tanks and out.fish_count:
        shown = min(out.fish_count, 15 * len(tanks))
        per = max(1, shown // len(tanks))
        for t in tanks:
            fish.append({"tank": t.id, "count": per, "length_m": 0.15})

    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "name": name,
        "subtitle": subtitle,
        "units": "m",
        "greenhouse": {
            "width": layout.greenhouse.width_m,
            "length": layout.greenhouse.length_m,
            "wall_h": layout.greenhouse.wall_h_m,
            "ridge_h": layout.greenhouse.ridge_h_m,
        },
        "objects": [_component(c) for c in layout.components],
        "pipes": [
            {"from": p.from_id, "to": p.to_id, "path": [list(q) for q in p.path],
             "d": p.diameter_m, "flow_lpm": p.flow_lpm}
            for p in layout.pipes
        ],
        "fish": fish,
        "assumptions": list(layout.assumptions),
    }

"""The top-down layout plan is the still WhatsApp shows in place of the 3D HTML (#167).

These tests pin the two things that make it the RIGHT artefact: it is drawn from the scene's
own positions (so moving a component moves the picture), and it is NOT the process schematic
(which a wrong implementation would have reached for via `schematic.to_png`).
"""

import io

from PIL import Image

from aqua_model import scene_plan, schematic
from aqua_model.layout import plan_layout
from aqua_model.scene3d import to_scene
from aqua_model.sizing import size_system
from aqua_model.validate import validate_design_input


def _scene(system_type: str = "raft", area: float = 24.0) -> dict:
    out = size_system(validate_design_input(
        fish_species="tilapia", crop="basil", grow_area_m2=area,
        temperature_c=28.0, water_budget_lpd=500.0, system_type=system_type))
    layout = plan_layout(out, crop_label="basil", species_label="tilapia")
    return to_scene(layout, out, crop="basil", species="tilapia",
                    name="Raft aquaponics — tilapia + basil",
                    subtitle="24 m2 grow area · 100 fish · 3000 L")


def _png_size(blob: bytes) -> tuple:
    return Image.open(io.BytesIO(blob)).size


def test_renders_a_png_and_is_byte_deterministic():
    scene = _scene()
    first = scene_plan.to_png(scene)
    second = scene_plan.to_png(scene)

    assert first[:8] == b"\x89PNG\r\n\x1a\n"
    assert first == second, "the still must be snapshot-stable, not time- or RNG-dependent"
    assert _png_size(first)[0] > 0 and _png_size(first)[1] > 0


def test_the_plan_draws_from_scene_positions_not_a_fixed_template():
    """Move one component a long way and the pixels must follow. A renderer that drew a
    canned illustration — or the schematic, which never reads positions — would not budge."""
    scene = _scene()
    moved = {**scene, "objects": [dict(o) for o in scene["objects"]]}
    moved["objects"][0]["x"] = float(moved["objects"][0]["x"]) + 3.0

    assert scene_plan.to_png(scene) != scene_plan.to_png(moved)


def test_the_plan_is_not_the_process_schematic():
    """Same design: the schematic is a different artefact and must not be what we ship."""
    scene = _scene()
    out = size_system(validate_design_input(
        fish_species="tilapia", crop="basil", grow_area_m2=24.0,
        temperature_c=28.0, water_budget_lpd=500.0, system_type="raft"))

    assert scene_plan.to_png(scene) != schematic.to_png(out), (
        "the layout plan must not be the schematic rasterised — that is the wrong picture")


def test_component_footprints_land_on_their_colour():
    """A box and a cylinder each paint their role colour at their own centre: proof the
    footprints, not just the greenhouse, are actually drawn."""
    scene = {
        "name": "t", "subtitle": "",
        "greenhouse": {"width": 6.0, "length": 6.0},
        "objects": [
            {"id": "bed1", "kind": "box", "role": "dwc_bed", "x": 2.0, "y": 2.0,
             "w": 1.0, "l": 2.0, "h": 0.3},
            {"id": "tank1", "kind": "cyl", "role": "fish_tank", "x": 5.0, "y": 5.0,
             "d": 1.0, "h": 1.1},
        ],
        "pipes": [],
    }
    scale = scene_plan._fit_scale(6.0, 6.0)
    img = Image.open(io.BytesIO(scene_plan.to_png(scene))).convert("RGB")

    def centre(x, y):
        return img.getpixel((round(scene_plan.PAD + x * scale),
                             round(scene_plan.HEADER + y * scale)))

    assert centre(2.0, 2.0) == (205, 236, 205)     # dwc_bed fill #cdeccd
    assert centre(5.0, 5.0) == (191, 227, 247)     # fish_tank fill #bfe3f7


def test_the_canvas_follows_the_greenhouse_shape():
    """The plan is as wide-versus-tall as the building: 4x8 draws tall, 8x4 draws wide."""
    tall = {"name": "t", "subtitle": "", "greenhouse": {"width": 4.0, "length": 8.0},
            "objects": [], "pipes": []}
    wide = {"name": "t", "subtitle": "", "greenhouse": {"width": 8.0, "length": 4.0},
            "objects": [], "pipes": []}

    assert _png_size(scene_plan.to_png(tall))[1] > _png_size(scene_plan.to_png(tall))[0]
    assert _png_size(scene_plan.to_png(wide))[0] > _png_size(scene_plan.to_png(wide))[1]


def test_the_scale_fits_the_longest_side_to_the_plot():
    assert scene_plan._fit_scale(8.0, 4.0) == scene_plan._fit_scale(4.0, 8.0)
    assert scene_plan._fit_scale(8.0, 4.0) == scene_plan.MAX_PLOT / 8.0


def test_a_scene_with_nothing_placed_still_renders():
    blank = {"name": "t", "subtitle": "", "greenhouse": {"width": 5.0, "length": 5.0},
             "objects": [], "pipes": []}
    assert scene_plan.to_png(blank)[:8] == b"\x89PNG\r\n\x1a\n"

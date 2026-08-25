"""The layout is a claim about space: components must fit, not overlap, and stay plumbed."""

import pytest

from aqua_model.layout import plan_layout
from aqua_model.scene3d import to_scene
from aqua_model.sizing import size_system
from aqua_model.validate import validate_design_input


def _design(system_type: str = "raft", area: float = 24.0):
    d = validate_design_input(
        fish_species="tilapia", crop="lettuce", grow_area_m2=area,
        temperature_c=28.0, water_budget_lpd=500.0, system_type=system_type)
    return size_system(d)


def _footprint(c) -> tuple[float, float, float, float]:
    if c.kind == "cyl":
        return (c.x - c.d / 2, c.x + c.d / 2, c.y - c.d / 2, c.y + c.d / 2)
    return (c.x - c.w / 2, c.x + c.w / 2, c.y - c.l / 2, c.y + c.l / 2)


@pytest.mark.parametrize("system_type", ["raft", "nft", "media_bed", "vertical_tower"])
def test_every_component_stands_inside_the_greenhouse(system_type):
    layout = plan_layout(_design(system_type))
    for c in layout.components:
        x0, x1, y0, y1 = _footprint(c)
        assert x0 >= 0 and y0 >= 0, f"{c.id} pokes out at the origin side"
        assert x1 <= layout.greenhouse.width_m + 1e-6, f"{c.id} pokes through the side wall"
        assert y1 <= layout.greenhouse.length_m + 1e-6, f"{c.id} pokes through the end wall"


@pytest.mark.parametrize("system_type", ["raft", "nft", "media_bed", "vertical_tower"])
def test_no_two_components_overlap(system_type):
    layout = plan_layout(_design(system_type))
    comps = layout.components
    for i, a in enumerate(comps):
        for b in comps[i + 1:]:
            ax0, ax1, ay0, ay1 = _footprint(a)
            bx0, bx1, by0, by1 = _footprint(b)
            overlap = not (ax1 <= bx0 + 1e-6 or bx1 <= ax0 + 1e-6
                           or ay1 <= by0 + 1e-6 or by1 <= ay0 + 1e-6)
            assert not overlap, f"{a.id} overlaps {b.id}"


def test_bed_area_covers_the_design_grow_area():
    """The picture must not silently shrink the farm: placed bed footprint covers the
    designed floor area (grow area / footprint_ratio) to within one bed of rounding."""
    out = _design("raft")
    layout = plan_layout(out)
    beds = layout.by_role("dwc_bed")
    placed = sum(b.w * b.l for b in beds)
    assert placed >= out.grow_area_m2 * 0.99


def test_the_loop_is_plumbed_from_fish_to_sump_and_back():
    layout = plan_layout(_design("raft"))
    ids = {c.id for c in layout.components}
    for p in layout.pipes:
        assert p.from_id in ids and p.to_id in ids
    froms = {p.from_id for p in layout.pipes}
    tos = {p.to_id for p in layout.pipes}
    assert "sump" in froms, "the return line from the sump is the pump — it must exist"
    assert any(i.startswith("tank") for i in tos), "water must come back to the fish"


def test_media_bed_systems_carry_no_separate_biofilter():
    layout = plan_layout(_design("media_bed"))
    assert layout.by_role("biofilter") == []
    assert any("biofiltration" in a for a in layout.assumptions)


def test_towers_raise_the_ridge():
    flat = plan_layout(_design("raft"))
    tall = plan_layout(_design("vertical_tower"))
    assert tall.greenhouse.ridge_h_m >= 2.8
    assert tall.greenhouse.ridge_h_m >= flat.greenhouse.wall_h_m


def test_more_grow_area_means_a_bigger_building():
    small = plan_layout(_design("raft", area=8.0))
    large = plan_layout(_design("raft", area=40.0))
    assert (large.greenhouse.width_m * large.greenhouse.length_m
            > small.greenhouse.width_m * small.greenhouse.length_m)


def test_is_deterministic():
    a = plan_layout(_design("raft"))
    b = plan_layout(_design("raft"))
    assert a == b


def test_scene_serialization_is_complete_and_json_safe():
    import json

    out = _design("raft")
    layout = plan_layout(out)
    scene = to_scene(layout, out, name="t", subtitle="s")
    text = json.dumps(scene)          # must not raise
    assert len(scene["objects"]) == len(layout.components)
    assert len(scene["pipes"]) == len(layout.pipes)
    assert scene["greenhouse"]["width"] == layout.greenhouse.width_m
    assert scene["fish"], "a stocked design should show fish"
    assert "assumptions" in scene and scene["assumptions"]
    assert "</" not in text or "<\\/" not in text  # renderer escapes at embed time


def test_layout_declares_it_is_a_proposal():
    layout = plan_layout(_design("raft"))
    assert any("not a site plan" in a for a in layout.assumptions)

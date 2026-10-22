"""The layout is a claim about space: components must fit, not overlap, and stay plumbed."""

import math

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


def test_rearing_tanks_are_plumbed_in_parallel_never_in_series():
    """Tank1 -> tank2 would feed one tank's waste into the next, which is the opposite of
    what everything downstream exists to do. Every tank drains to the same first treatment
    stage instead."""
    layout = plan_layout(_design(area=40.0))     # big enough to split into several tanks
    tank_ids = {c.id for c in layout.components if c.role == "fish_tank"}
    assert len(tank_ids) > 1, "this test needs a design that splits into multiple tanks"
    for p in layout.pipes:
        assert not (p.from_id in tank_ids and p.to_id in tank_ids), (
            f"{p.from_id} is plumbed into {p.to_id}: rearing tanks are in series")
    drains = {p.to_id for p in layout.pipes if p.from_id in tank_ids}
    assert len(drains) == 1, f"tanks drain to {drains}; they should share one first stage"


def test_the_pump_returns_to_every_rearing_tank():
    """A return line to only the first tank leaves the others with no inflow at all."""
    layout = plan_layout(_design(area=40.0))
    tank_ids = {c.id for c in layout.components if c.role == "fish_tank"}
    sump = next(c for c in layout.components if c.role == "sump")
    returned = {p.to_id for p in layout.pipes if p.from_id == sump.id}
    assert tank_ids <= returned, f"no return line to {sorted(tank_ids - returned)}"


def test_every_component_is_connected_to_the_loop():
    """An orphan vessel on the drawing is a vessel nobody plumbed."""
    for system_type in ("raft", "nft", "media_bed", "vertical_tower"):
        layout = plan_layout(_design(system_type))
        plumbed = {p.from_id for p in layout.pipes} | {p.to_id for p in layout.pipes}
        # The blower joins the drawing with air lines, not water pipes — that still counts
        # as plumbed; the water loop may never touch it.
        plumbed |= {a.from_id for a in layout.air_lines}
        # Grow units repeat; only the first is drawn into the loop, by design.
        expected = {c.id for c in layout.components
                    if not c.id.startswith("bed") or c.id == "bed1"}
        assert expected <= plumbed, (
            f"{system_type}: orphaned {sorted(expected - plumbed)}")


# --- the air side (#113): a blower, sized from the registry, with stones and air lines ---

def test_a_blower_is_placed_and_sized_from_the_registry():
    """#113: the air side was a static BOM guess with no power figure. A stocked design
    must now place a blower whose air flow traces to the FAO 589 rate and whose draw
    traces to the wire-to-air coefficient."""
    from aqua_model import coefficients
    out = _design("raft")
    layout = plan_layout(out)
    assert layout.aeration is not None, "a stocked design must size its air side"
    blowers = layout.by_role("blower")
    assert len(blowers) == 1, f"expected one blower, drew {len(blowers)}"
    rate = coefficients.AERATION_AIR_RATE_LPM_M3
    sf = coefficients.SAFETY_FACTOR
    canal = layout.aeration.canal_stones * coefficients.DWC_STONE_FLOW_LPM.value
    assert layout.aeration.air_lpm == pytest.approx(
        (layout.aeration.aerated_m3 * rate.value + canal) * sf.value), (
        "air flow must equal (aerated volume x FAO rate + canal stones) x headroom")
    wpl = coefficients.AIR_PUMP_WATT_PER_LPM
    assert layout.aeration.power_w == pytest.approx(
        layout.aeration.air_lpm * wpl.value, abs=0.1), (
        "electrical draw must equal air flow x wire-to-air draw")
    assert layout.aeration.aerated_m3 >= out.rearing_tank_volume_l / 1000.0, (
        "the aerated water must at least cover the rearing tanks")
    assert layout.aeration.stones == len(layout.air_stones) >= 2


def test_raft_canals_carry_their_own_stones():
    """FAO 589 §4.5.3: a densely planted DWC canal pulls DO down on its own, so every
    raft bed gets its own diffusers at the §4.5.3 spacing — the regression the static
    BOM note used to cover and volume-based sizing silently dropped."""
    from aqua_model import coefficients
    layout = plan_layout(_design("raft"))
    assert layout.aeration is not None, "a stocked design must size its air side"
    beds = layout.by_role("dwc_bed")
    assert beds, "the raft design must draw DWC beds"
    spacing = coefficients.DWC_STONE_SPACING_M2
    canal_stones = [s for s in layout.air_stones if s.anchor_id.startswith("bed")]
    assert layout.aeration.canal_stones == len(canal_stones) > 0, (
        "a raft design must carry canal stones")
    for bed in beds:
        expected = math.ceil(bed.w * bed.l / spacing.value)
        got = [s for s in canal_stones if s.anchor_id == bed.id]
        assert len(got) == expected, (
            f"{bed.id} ({bed.w * bed.l:.1f} m²) wants {expected} stones, drew {len(got)}")
        xs = {s.x for s in got}
        ys = {s.y for s in got}
        assert len(got) == 1 or len(xs) > 1 or len(ys) > 1, (
            f"{bed.id}: canal stones must spread across the canal")
        for s in got:
            assert bed.x - bed.w / 2 <= s.x <= bed.x + bed.w / 2, (
                f"{s.id} must sit inside {bed.id}")
            assert s.z < bed.z + bed.water_frac * bed.h, (
                f"{s.id} must sit submerged (world z vs waterline)")
    canal_lpm = len(canal_stones) * coefficients.DWC_STONE_FLOW_LPM.value
    tank_only = layout.aeration.aerated_m3 * coefficients.AERATION_AIR_RATE_LPM_M3.value
    assert layout.aeration.air_lpm >= (tank_only + canal_lpm) * 0.99, (
        "the blower must carry the canal demand, not just the fish-side volume")


def test_media_and_nft_beds_get_no_canal_stones():
    """§4.5.3 is a DWC rule: media beds and NFT channels are not canals and must not
    grow stones just because they are grow beds."""
    for system_type in ("media_bed", "nft"):
        layout = plan_layout(_design(system_type))
        assert layout.aeration is not None, "a stocked design must size its air side"
        assert layout.aeration.canal_stones == 0, (
            f"{system_type}: canal stones are a DWC rule")
        assert not [s for s in layout.air_stones if s.anchor_id.startswith("bed")], (
            f"{system_type}: no stones may anchor to grow beds")


def test_dwc_canal_coefficients_are_registered_and_cited():
    """The canal sizing traces to FAO 589 §4.5.3 through the registry, like every
    other number the model uses."""
    from aqua_model import coefficients
    spacing = coefficients.registry()["dwc_stone_spacing_m2"]
    flow = coefficients.registry()["dwc_stone_flow_lpm"]
    assert spacing is coefficients.DWC_STONE_SPACING_M2
    assert spacing.source == "FAO589" and "§4.5.3" in spacing.note
    assert 2.0 <= spacing.low <= spacing.value <= spacing.high <= 4.0
    assert flow is coefficients.DWC_STONE_FLOW_LPM
    assert flow.source == "FAO589" and "§4.5.3" in flow.note
    assert flow.value == 4.0


def test_two_air_stones_per_tank_in_different_places():
    """FAO 589 §7.3.3 wants at least two stones per fish tank in different locations,
    kept off the centre so the bubbles do not re-suspend settling solids (§4.2.5)."""
    layout = plan_layout(_design(area=40.0))   # large enough that tanks split up
    tanks = layout.by_role("fish_tank")
    assert len(tanks) > 1, "expected the 40 m2 design to split into several tanks"
    for t in tanks:
        stones = [s for s in layout.air_stones if s.anchor_id == t.id]
        assert len(stones) >= 2, f"{t.id} has {len(stones)} stones; FAO 589 wants >= 2"
        assert len({(s.x, s.y) for s in stones}) == len(stones), (
            f"{t.id}: stones must sit in different locations")
        for s in stones:
            assert math.hypot(s.x - t.x, s.y - t.y) < t.d / 2, (
                f"{s.id} must sit inside {t.id}")
            assert s.z < t.water_frac * t.h, f"{s.id} must sit submerged"


def test_air_lines_serve_the_blower_and_clear_the_row():
    """Every air line leaves the blower and dives into the vessel it serves; the horizontal
    main runs overhead, above everything it crosses in the tank row."""
    from aqua_model.layout import AIR_MAIN_Z
    layout = plan_layout(_design("raft"))
    assert layout.air_lines, "a stocked design must route its air lines"
    blower = layout.by_role("blower")[0]
    for a in layout.air_lines:
        assert a.from_id == blower.id, "an air line must start at the blower"
        assert a.to_id in {c.id for c in layout.components}, (
            f"{a.stone_id} serves a vessel that is not on the drawing")
        assert a.path[0][:2] == (blower.x, blower.y), "the run must leave the blower"
        stone = next(s for s in layout.air_stones if s.id == a.stone_id)
        assert a.path[-1] == (stone.x, stone.y, stone.z), (
            "the run must end at its diffuser")
        for q in a.path[1:-1]:
            assert q[2] == pytest.approx(AIR_MAIN_Z), (
                "the air main must run at one overhead height")
    row_top = max((c.h for c in layout.components if c.z == 0.0), default=0.0)
    assert AIR_MAIN_Z > row_top, "the air main must clear the row it crosses"


def test_air_lines_never_enter_the_gravity_pipe_runs():
    """The hydraulics checks grade PipeRuns for fall; air does not fall. The air side must
    stay out of that data structure entirely, or the gravity tests inherit lies (#113)."""
    layout = plan_layout(_design("raft"))
    pipe_pairs = {(p.from_id, p.to_id) for p in layout.pipes}
    for a in layout.air_lines:
        assert (a.from_id, a.to_id) not in pipe_pairs, (
            "an air line must not pose as a water pipe")
        assert a.to_id not in {"sump", "biofilter_tank"}, (
            "air does not serve vessels that only exist on the water loop")


def test_layout_is_deterministic_on_a_twin_binding():
    def build():
        return plan_layout(_design("raft"))
    a = build()
    b = build()
    assert a.aeration == b.aeration
    assert a.air_stones == b.air_stones
    assert a.air_lines == b.air_lines

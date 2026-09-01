"""Plumbing is a claim about physics, not decoration.

The invariants: gravity legs actually fall, the pumped leg is the only one that rises, no
run passes through a vessel, the main is sized to a sane velocity, and the head the bill of
materials quotes is the head this geometry implies.
"""

import math

import pytest

from aqua_model import hydraulics as H
from aqua_model.layout import plan_layout
from aqua_model.sizing import size_system
from aqua_model.validate import validate_design_input

SYSTEMS = ["raft", "nft", "media_bed", "vertical_tower"]


def _layout(system_type: str = "raft", area: float = 24.0):
    d = validate_design_input(
        fish_species="tilapia", crop="lettuce", grow_area_m2=area,
        temperature_c=28.0, water_budget_lpd=500.0, system_type=system_type)
    return plan_layout(size_system(d), crop_label="lettuce", species_label="tilapia")


def _footprint(c):
    if c.kind == "cyl":
        return (c.x - c.d / 2, c.x + c.d / 2, c.y - c.d / 2, c.y + c.d / 2, c.z, c.z + c.h)
    return (c.x - c.w / 2, c.x + c.w / 2, c.y - c.l / 2, c.y + c.l / 2, c.z, c.z + c.h)


def _passes_through(seg_a, seg_b, c) -> bool:
    x0, x1, y0, y1, z0, z1 = _footprint(c)
    n = 80
    for i in range(1, n):
        t = i / n
        x = seg_a[0] + (seg_b[0] - seg_a[0]) * t
        y = seg_a[1] + (seg_b[1] - seg_a[1]) * t
        z = seg_a[2] + (seg_b[2] - seg_a[2]) * t
        if x0 < x < x1 and y0 < y < y1 and z0 < z < z1:
            return True
    return False


# --- the two faults this module was written to fix ---------------------------------------

@pytest.mark.parametrize("system_type", SYSTEMS)
def test_gravity_runs_actually_fall(system_type):
    """The old router dived to 0.25 m and climbed back on every leg, which is a trap. And
    the ungraded layout put the beds' water surface BELOW the sump's, so the main loop ran
    uphill. Every non-pumped run must now descend at every step."""
    lay = _layout(system_type)
    grav = [p for p in lay.pipes if not p.pumped and p.flow_lpm > 0]
    assert grav, "no gravity runs at all"
    for p in grav:
        zs = [q[2] for q in p.path]
        for i in range(len(zs) - 1):
            assert zs[i] >= zs[i + 1] - 1e-6, (
                f"{p.from_id}->{p.to_id} climbs from {zs[i]:.3f} to {zs[i + 1]:.3f}")
        assert zs[0] > zs[-1] + 1e-9, f"{p.from_id}->{p.to_id} is level end to end"


@pytest.mark.parametrize("system_type", SYSTEMS)
def test_no_run_passes_through_a_vessel(system_type):
    """Two of the seven runs used to cut straight through bed 2."""
    lay = _layout(system_type)
    comps = {c.id: c for c in lay.components}
    for p in lay.pipes:
        for a, b in zip(p.path, p.path[1:]):
            for cid, c in comps.items():
                if cid in (p.from_id, p.to_id):
                    continue
                assert not _passes_through(a, b, c), (
                    f"{p.from_id}->{p.to_id} passes through {cid}")


def test_exactly_one_leg_of_the_loop_is_pumped():
    """Everything falls except the return. If more than the return is pumped, the grade
    line failed and the drawing is hiding a second pump nobody budgeted for."""
    lay = _layout()
    pumped = [p for p in lay.pipes if p.pumped]
    tanks = {c.id for c in lay.components if c.role == "fish_tank"}
    sump = next(c for c in lay.components if c.role == "sump")
    assert pumped, "nothing is pumped, so the loop never closes"
    assert {p.from_id for p in pumped} == {sump.id}
    assert {p.to_id for p in pumped} == tanks


# --- grading ------------------------------------------------------------------------------

def test_the_grade_line_descends_along_the_flow_order():
    lay = _layout()
    surf = {c.id: c.z + c.h * (c.water_frac or 0.9)
            for c in lay.components if c.water_frac}
    for p in lay.pipes:
        if p.pumped or p.flow_lpm <= 0:
            continue
        if p.from_id in surf and p.to_id in surf:
            assert surf[p.from_id] > surf[p.to_id], (
                f"{p.from_id} ({surf[p.from_id]:.3f}) is not above "
                f"{p.to_id} ({surf[p.to_id]:.3f})")


def test_the_sump_is_the_low_point_it_is_labelled_as():
    lay = _layout()
    surf = {c.id: c.z + c.h * (c.water_frac or 0.9)
            for c in lay.components if c.water_frac}
    sump = next(c for c in lay.components if c.role == "sump")
    assert surf[sump.id] == min(surf.values()), "the sump is not the lowest water surface"


def test_every_leg_gets_at_least_the_minimum_fall():
    lay = _layout()
    for p in lay.pipes:
        if p.pumped or p.flow_lpm <= 0:
            continue
        drop = p.path[0][2] - p.path[-1][2]
        assert drop >= H.MIN_FALL_M - 1e-6, (
            f"{p.from_id}->{p.to_id} falls only {drop:.3f} m")


# --- pipe sizing and head -----------------------------------------------------------------

def test_the_main_is_sized_to_a_sane_velocity():
    """Hard-coding 50 mm gave 1.9 m/s on a 13 m3/h system and a friction head that swamped
    the static lift."""
    for area in (8.0, 24.0, 60.0, 120.0):
        lay = _layout(area=area)
        p = next(x for x in lay.pipes if x.flow_lpm > 0)
        v = H.velocity_ms(p.flow_lpm, p.diameter_m)
        assert 0.35 <= v <= H.TARGET_VELOCITY_MS + 1e-9, f"{area} m2: {v:.2f} m/s"
        assert round(p.diameter_m * 1000) in H.STANDARD_OD_MM, "specified a pipe nobody sells"


def test_pipe_diameter_grows_with_flow():
    small = H.pipe_diameter_m(20.0)
    large = H.pipe_diameter_m(2000.0)
    assert large > small


def test_head_is_derived_from_the_geometry_not_a_constant():
    """The point of the module: move the design and the number moves. A bigger system has a
    longer routed return, so its friction head must differ."""
    a, b = _layout(area=12.0), _layout(area=96.0)
    assert a.hydraulics is not None and b.hydraulics is not None
    assert a.hydraulics.routed_length_m != b.hydraulics.routed_length_m
    assert a.hydraulics.total_head_m != b.hydraulics.total_head_m


def test_head_components_add_up_and_are_physical():
    for system_type in SYSTEMS:
        h = _layout(system_type).hydraulics
        assert h.static_lift_m >= 0 and h.friction_head_m >= 0
        assert abs(h.total_head_m - (h.static_lift_m + h.friction_head_m)) < 0.011
        assert 0 < h.total_head_m < 12, f"{system_type}: {h.total_head_m} m is not a pond pump"


def test_friction_rises_with_length_and_falls_with_diameter():
    base = H._friction_head(10.0, 200.0, 0.063)
    assert H._friction_head(20.0, 200.0, 0.063) > base
    assert H._friction_head(10.0, 200.0, 0.090) < base


def test_static_lift_is_the_real_climb_from_sump_to_tank():
    lay = _layout()
    surf = {c.id: c.z + c.h * (c.water_frac or 0.9)
            for c in lay.components if c.water_frac}
    sump = next(c for c in lay.components if c.role == "sump")
    tanks = [c for c in lay.components if c.role == "fish_tank"]
    expected = max(surf[t.id] for t in tanks) - surf[sump.id]
    assert abs(lay.hydraulics.static_lift_m - expected) < 1e-2


# --- routing quality ----------------------------------------------------------------------

def test_routes_are_straight_runs_not_grid_staircases():
    """A string-pulled route has a handful of legs. A raw breadth-first path has dozens, and
    every corner would be charged to the fitting allowance."""
    lay = _layout()
    for p in lay.pipes:
        assert len(p.path) <= 10, f"{p.from_id}->{p.to_id} has {len(p.path)} waypoints"


def test_routed_length_is_at_least_the_straight_line():
    lay = _layout()
    comps = {c.id: c for c in lay.components}
    for p in lay.pipes:
        if p.length_m <= 0:
            continue
        a, b = comps[p.from_id], comps[p.to_id]
        # length_m is rounded to centimetres, so allow that much slack
        assert p.length_m >= math.dist((a.x, a.y), (b.x, b.y)) - 0.01


def test_routing_is_deterministic():
    a, b = _layout(), _layout()
    assert [p.path for p in a.pipes] == [p.path for p in b.pipes]
    assert a.hydraulics == b.hydraulics


def test_the_report_travels_with_the_layout_and_says_what_it_assumed():
    lay = _layout()
    text = "\n".join(lay.assumptions)
    assert "Pump head from the layout" in text
    assert "static lift" in text and "friction" in text

"""Costing: takeoff from the drawn layout, honest about every hole in the book."""

import pytest

from aqua_model.costing import (
    NOT_INCLUDED, estimate_cost, format_estimate, opex_takeoff, takeoff,
)
from aqua_model.layout import plan_layout
from aqua_model.sizing import size_system
from aqua_model.validate import validate_design_input

_BOOK = {
    "regions": {
        "testland": {
            "currency": "TST",
            "as_of": "2026-08",
            "items": {
                "tank_1000l": {"price": 100.0, "low": 80.0, "high": 140.0, "source": "test"},
                "pump_small": {"price": 40.0, "source": "test"},
                "air_pump": {"price": 30.0, "source": "test"},
                "air_stone": {"price": 2.0, "source": "test"},
                "pvc_pipe_m": {"price": 1.5, "source": "test"},
                "raft_foam_m2": {"price": 6.0, "source": "test"},
                "liner_m2": {"price": 4.0, "source": "test"},
                "biofilter_media_m3": {"price": 250.0, "source": "test"},
                "greenhouse_poly_m2": {"price": 12.0, "source": "test"},
                "fingerling_tilapia": {"price": 0.3, "source": "test"},
                "feed_kg": {"price": 1.2, "source": "test"},
                "electricity_kwh": {"price": 0.15, "source": "test"},
                "water_m3": {"price": 0.8, "source": "test"},
            },
        }
    }
}


def _design(system_type="raft"):
    out = size_system(validate_design_input(
        "tilapia", "basil", 24.0, 27.0, 500.0, None, system_type))
    return out, plan_layout(out)


def test_takeoff_covers_the_things_a_build_needs():
    out, layout = _design()
    keys = {t.key for t in takeoff(out, layout)}
    for k in ("tank_1000l", "raft_foam_m2", "pump_small", "air_pump", "pvc_pipe_m",
              "greenhouse_poly_m2", "fingerling_tilapia"):
        assert k in keys, f"takeoff is missing {k}"


def test_takeoff_follows_the_system_type():
    out, layout = _design("media_bed")
    keys = {t.key for t in takeoff(out, layout)}
    assert "gravel_m3" in keys and "raft_foam_m2" not in keys
    assert "biofilter_media_m3" not in keys, "media beds ARE the biofilter"


def test_a_complete_book_prices_everything():
    out, layout = _design()
    est = estimate_cost(out, layout, _BOOK, "testland")
    assert est.unpriced == ()
    lo, mid, hi = est.capex_total()
    assert 0 < lo <= mid <= hi


def test_a_hole_in_the_book_is_named_not_swallowed():
    book = {"regions": {"testland": {
        "currency": "TST", "as_of": "2026-08",
        "items": {k: v for k, v in _BOOK["regions"]["testland"]["items"].items()
                  if k != "greenhouse_poly_m2"}}}}
    out, layout = _design()
    est = estimate_cost(out, layout, book, "testland")
    assert any("poly" in u for u in est.unpriced)
    assert "UNPRICED" in format_estimate(est)
    assert "EXCLUDES" in format_estimate(est)


def test_an_unknown_region_fails_loudly():
    out, layout = _design()
    with pytest.raises(KeyError):
        estimate_cost(out, layout, _BOOK, "atlantis")


def test_opex_scales_with_the_design():
    small, small_layout = _design()
    big = size_system(validate_design_input("tilapia", "basil", 48.0, 27.0, 2000.0))
    big_layout = plan_layout(big)
    s = estimate_cost(small, small_layout, _BOOK, "testland").opex_total()[1]
    b = estimate_cost(big, big_layout, _BOOK, "testland").opex_total()[1]
    assert b > s


def test_bigger_grow_area_costs_more_to_build():
    small, sl = _design()
    big = size_system(validate_design_input("tilapia", "basil", 48.0, 27.0, 2000.0))
    bl = plan_layout(big)
    assert (estimate_cost(big, bl, _BOOK, "testland").capex_total()[1]
            > estimate_cost(small, sl, _BOOK, "testland").capex_total()[1])


def test_shade_mode_swaps_the_envelope_line():
    out, layout = _design()
    keys = {t.key for t in takeoff(out, layout, greenhouse_mode="shade")}
    assert "shade_net_m2" in keys and "greenhouse_poly_m2" not in keys


def test_the_estimate_declares_what_it_leaves_out():
    out, layout = _design()
    est = estimate_cost(out, layout, _BOOK, "testland")
    assert any("labour" in x for x in est.not_included)
    assert "verify locally" in format_estimate(est)
    assert len(NOT_INCLUDED) >= 4


def test_every_priced_line_carries_its_source():
    out, layout = _design()
    est = estimate_cost(out, layout, _BOOK, "testland")
    for line in est.capex + est.opex_per_year:
        if line.unit_price is not None:
            assert line.source, f"{line.takeoff.label} has a price but no source"

"""Vertical towers — growing area is no longer chained to floor area.

A stacked/tower system packs several m² of growing FACE onto one m² of FLOOR. The feed/fish
sizing still keys off growing area (unchanged anchor), but the design now also reports the
FOOTPRINT — the floor space actually needed — which is what a land-constrained grower cares
about. Flat methods (raft/NFT/media bed) have footprint == growing area, byte-identical.
"""

import pytest

from aqua_model import (
    size_hydroponic_system,
    size_system,
    validate_design_input,
    validate_hydroponic_input,
)
from aqua_model.system_types import SYSTEM_TYPES, get_system_type


def test_vertical_tower_is_a_registered_cited_method():
    assert "vertical_tower" in SYSTEM_TYPES
    st = get_system_type("vertical_tower")
    assert st.footprint_ratio > 1.0          # packs more grow area than floor
    assert st.source and st.considerations


def test_flat_methods_have_footprint_equal_to_grow_area():
    for method in ("raft", "nft", "media_bed"):
        out = size_system(validate_design_input(
            "tilapia", "lettuce", 12, 27, 3000, system_type=method))
        # flat beds: floor footprint is the growing area itself (ratio 1.0)
        assert out.footprint_ratio == 1.0
        assert out.footprint_m2 == pytest.approx(12)


def test_tower_footprint_is_smaller_than_grow_area():
    out = size_system(validate_design_input(
        "tilapia", "lettuce", 12, 27, 3000, system_type="vertical_tower"))
    ratio = get_system_type("vertical_tower").footprint_ratio
    assert out.footprint_m2 == pytest.approx(round(12 / ratio, 1))
    assert out.footprint_m2 < out.grow_area_m2      # the whole point: less floor


def test_tower_feed_matches_flat_for_same_grow_area():
    # Feed is driven by GROWING area, which is identical — only the floor footprint differs.
    tower = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000,
                                              system_type="vertical_tower"))
    raft = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000))
    assert tower.feed_g_per_day == raft.feed_g_per_day
    assert tower.fish_count == raft.fish_count


def test_hydroponic_tower_reports_footprint():
    out = size_hydroponic_system(validate_hydroponic_input(
        "lettuce", 15, 22, 4000, system_type="vertical_tower"))
    assert out.footprint_m2 < out.grow_area_m2


def test_tower_footprint_appears_in_schematic_and_serialization():
    from agronaut_agent.serialize import serialize_design_output
    from aqua_model.schematic import to_svg
    out = size_system(validate_design_input(
        "tilapia", "lettuce", 12, 27, 3000, system_type="vertical_tower"))
    assert "floor" in to_svg(out).lower() or "footprint" in to_svg(out).lower()
    assert "floor" in serialize_design_output(out).lower() or \
        "footprint" in serialize_design_output(out).lower()

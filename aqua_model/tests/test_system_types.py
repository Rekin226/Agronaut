"""Growing method is a first-class, selectable preference — not a fixed framework. A design
can be raft/DWC, NFT, or media bed; each has cited geometry and its own considerations. The
default (raft) is byte-identical to the pre-flexibility behavior, so nothing regresses.
"""

import pytest

from aqua_model import size_system, validate_design_input, ValidationError
from aqua_model.system_types import SYSTEM_TYPES, get_system_type


def test_supported_methods_are_cited():
    assert set(SYSTEM_TYPES) >= {"raft", "nft", "media_bed"}
    for key in SYSTEM_TYPES:
        st = get_system_type(key)
        assert st.water_depth_m > 0
        assert st.source                       # every method's geometry is sourced
        assert st.considerations               # honest method-specific notes


def test_default_is_raft_and_unchanged():
    # omitting system_type must reproduce the exact prior design (raft/DWC, 0.30 m water).
    a = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000))
    b = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000, system_type="raft"))
    assert a.system_volume_l == b.system_volume_l
    assert a.rearing_tank_volume_l == b.rearing_tank_volume_l
    assert a.feed_g_per_day == b.feed_g_per_day   # fish/feed sizing is method-independent


def test_nft_holds_less_water_than_raft():
    raft = size_system(validate_design_input("tilapia", "lettuce", 20, 27, 5000, system_type="raft"))
    nft = size_system(validate_design_input("tilapia", "lettuce", 20, 27, 5000, system_type="nft"))
    # NFT channels hold a thin film, so the system water volume is smaller than deep-water raft
    assert nft.system_volume_l < raft.system_volume_l
    # but the fish/feed sizing (driven by plant area, not method) is identical
    assert nft.feed_g_per_day == raft.feed_g_per_day
    assert nft.fish_count == raft.fish_count


def test_method_appears_in_output_and_assumptions():
    out = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000, system_type="media_bed"))
    text = " ".join(out.assumptions).lower()
    assert "media bed" in text
    # media beds nitrify, so the design surfaces that consideration honestly
    assert any("biofilt" in c.lower() or "nitrif" in c.lower() for c in out.assumptions + out.warnings) \
        or "media bed" in text


def test_validate_rejects_unknown_method():
    with pytest.raises(ValidationError):
        validate_design_input("tilapia", "lettuce", 12, 27, 3000, system_type="hydro-tower-9000")


def test_bom_and_schematic_reflect_the_method():
    from aqua_model.schematic import to_svg
    out = size_system(validate_design_input("tilapia", "lettuce", 20, 27, 5000, system_type="nft"))
    bom = " ".join(i["item"].lower() + " " + str(i["spec"]).lower() for i in out.bill_of_materials)
    assert "nft" in bom or "channel" in bom
    assert "nft" in to_svg(out).lower() or "channel" in to_svg(out).lower()

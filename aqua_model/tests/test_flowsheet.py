"""The flowsheet: components earn their place from needs, and the low end stays simple."""

import pytest

from aqua_model.flowsheet import (
    MEDIA_BED_SELF_FILTER_MAX_KG_M3, Needs, format_flowsheet, plan_flowsheet,
)
from aqua_model.crops import get_crop
from aqua_model.sizing import size_system
from aqua_model.species import get_species
from aqua_model.validate import validate_design_input

TILAPIA = get_species("tilapia")
TROUT = get_species("trout")
BASIL = get_crop("basil")
LETTUCE = get_crop("lettuce")


def _out(species="tilapia", crop="basil", area=12.0, system_type="media_bed", water=400.0):
    return size_system(validate_design_input(species, crop, area, 27.0, water,
                                             None, system_type))


def test_a_backyard_media_bed_is_not_gold_plated():
    """FAO's audience runs media beds because one component does three jobs — a small
    low-density system must come back as tanks + beds + sump + air, nothing more."""
    fs = plan_flowsheet(_out(), TILAPIA, BASIL, Needs(stocking_kg_m3=10.0))
    roles = fs.roles()
    assert fs.architecture == "coupled"
    for absent in ("settling", "biofilter", "degasser", "mineraliser"):
        assert absent not in roles, f"{absent} has no business in a backyard media-bed unit"
    assert "media_bed" in roles and "sump" in roles and "aeration" in roles


def test_dense_stocking_forces_solids_removal_even_with_media_beds():
    fs = plan_flowsheet(_out(), TILAPIA, BASIL,
                        Needs(stocking_kg_m3=MEDIA_BED_SELF_FILTER_MAX_KG_M3 + 10))
    assert "settling" in fs.roles()
    assert any("clogging" in d or "exceeds" in d for d in fs.decisions)


def test_nft_needs_a_dedicated_biofilter():
    fs = plan_flowsheet(_out(system_type="nft"), TILAPIA, BASIL, Needs(stocking_kg_m3=10))
    assert "biofilter" in fs.roles()
    assert "settling" in fs.roles(), "NFT captures no solids either"


def test_mismatched_temperature_bands_recommend_decoupling():
    """Trout (14-16 C optimum) + basil (18-30 C): one loop cannot please both — the
    decision the book's ch. 8 exists for."""
    out = _out(species="trout", crop="basil", system_type="raft")
    fs = plan_flowsheet(out, TROUT, BASIL, Needs())
    assert fs.architecture == "decoupled"
    assert any("optimal water-temperature band" in d for d in fs.decisions)
    roles = fs.roles()
    assert "mineraliser" in roles and "degasser" in roles and "settling" in roles


def test_commercial_scale_recommends_decoupling_even_when_bands_agree():
    out = _out(area=80.0, system_type="raft", water=3000.0)
    fs = plan_flowsheet(out, TILAPIA, BASIL, Needs(stocking_kg_m3=20))
    assert fs.architecture == "decoupled"
    assert any("commercial scale" in d for d in fs.decisions)


def test_the_user_can_force_the_architecture():
    out = _out(area=80.0, system_type="raft", water=3000.0)
    fs = plan_flowsheet(out, TILAPIA, BASIL,
                        Needs(stocking_kg_m3=20, force_architecture="coupled"))
    assert fs.architecture == "coupled"
    assert any("fixed by the user" in d for d in fs.decisions)


def test_a_beginner_pushed_to_decoupled_gets_warned_not_blocked():
    out = _out(species="trout", crop="basil", system_type="raft")
    fs = plan_flowsheet(out, TROUT, BASIL, Needs(operator_experience="beginner"))
    assert fs.architecture == "decoupled"
    assert any("beginner" in w for w in fs.warnings)


def test_nutrient_reuse_adds_mineralization_to_a_coupled_system():
    fs = plan_flowsheet(_out(), TILAPIA, BASIL,
                        Needs(stocking_kg_m3=10, wants_max_nutrient_reuse=True))
    assert fs.architecture == "coupled"
    assert "mineraliser" in fs.roles()


def test_unreliable_power_plus_nft_is_named_a_fish_kill():
    fs = plan_flowsheet(_out(system_type="nft"), TILAPIA, BASIL,
                        Needs(stocking_kg_m3=10, reliable_power=False))
    assert any("fish-kill" in w for w in fs.warnings)


def test_every_component_carries_its_why_and_source():
    fs = plan_flowsheet(_out(system_type="raft"), TILAPIA, LETTUCE, Needs(stocking_kg_m3=25))
    for c in fs.components:
        assert c.why and c.source, f"{c.role} arrived without a reason"
    text = format_flowsheet(fs)
    assert "Why this shape" in text and "FAO 589" in text


def test_is_deterministic():
    a = plan_flowsheet(_out(), TILAPIA, BASIL, Needs())
    b = plan_flowsheet(_out(), TILAPIA, BASIL, Needs())
    assert a == b

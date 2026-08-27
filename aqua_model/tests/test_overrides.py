import dataclasses

import pytest

from aqua_model.crops import get_crop
from aqua_model.overrides import apply_overrides, validate_overrides
from aqua_model.species import get_species
from aqua_model.validate import ValidationError


def test_apply_overrides_replaces_matching_species_attr():
    sp = get_species("tilapia")
    sp2, _ = apply_overrides(species=sp, overrides={"tilapia.fcr": 1.5})
    assert sp2.fcr == 1.5
    assert sp.fcr == 1.7                       # seed object untouched
    assert dataclasses.replace(sp, fcr=1.7) == sp  # (sanity: seed unchanged)


def test_apply_overrides_maps_harvest_weight_and_yield_keys():
    sp = get_species("tilapia")
    cr = get_crop("lettuce")
    sp2, cr2 = apply_overrides(species=sp, crop=cr,
                               overrides={"tilapia.harvest_weight": 0.45, "lettuce.yield": 12.0})
    assert sp2.harvest_weight_kg == 0.45
    assert cr2.yield_kg_per_m2_year == 12.0


def test_apply_overrides_ignores_non_matching_prefix():
    sp = get_species("tilapia")
    sp2, _ = apply_overrides(species=sp, overrides={"trout.fcr": 1.0})  # different species
    assert sp2.fcr == 1.7                       # unchanged


def test_validate_overrides_accepts_in_range():
    validate_overrides({"tilapia.fcr": 1.5})    # 0.9-1.8 -> ok, no raise


def test_validate_overrides_rejects_out_of_range():
    with pytest.raises(ValidationError):
        validate_overrides({"tilapia.fcr": 5.0})  # above 1.8


def test_validate_overrides_rejects_unknown_and_unranged():
    with pytest.raises(ValidationError):
        validate_overrides({"tilapia.bogus": 1.0})       # unknown suffix
    with pytest.raises(ValidationError):
        validate_overrides({"clarias.harvest_weight": 0.6})  # no range for clarias harvest_weight


def test_apply_overrides_handles_none_species():
    cr = get_crop("lettuce")
    sp, cr2 = apply_overrides(species=None, crop=cr, overrides={"tilapia.fcr": 1.5})
    assert sp is None
    assert cr2.yield_kg_per_m2_year == cr.yield_kg_per_m2_year  # crop untouched (no yield override)

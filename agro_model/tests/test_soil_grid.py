"""Subdivision preserves water, soil attributes and intensive parameter meanings."""

from dataclasses import replace
from fractions import Fraction

import pytest

from agro_model.soil_drainage import DrainageParameters
from agro_model.soil_grid import split_soil_grid
from agro_model.soil_roots import RootLayerActivity
from agro_model.soil_storage import SoilCompartment, SoilPatch, SoilStorage

SOURCE = "Synthetic grid preparation case"


def storage():
    cells = (SoilCompartment("top", 0, .4, .3, .15, .45, .075, .22, SOURCE),
             SoilCompartment("bottom", .4, 1, .4, .2, .5, .1, .35, SOURCE))
    return SoilStorage(10, (SoilPatch("a", .4, cells), SoilPatch("b", .6, cells)), SOURCE)


def test_split_preserves_exact_patch_water_and_every_original_soil_property():
    initial = storage()
    change = split_soil_grid(initial, {"a": (.15, .3, .5, .8, 1), "b": (.2, .7)}, source=SOURCE)
    assert change.ending.water_l == initial.water_l == 2980
    assert change.balance_residual_l == 0
    for original, ending in zip(initial.patches, change.ending.patches):
        assert original.id == ending.id and original.area_fraction == ending.area_fraction
        assert sum(c.water_content*c.thickness_m for c in original.compartments) == sum(c.water_content*c.thickness_m for c in ending.compartments)
        by_old = {c.id: c for c in original.compartments}
        by_new = {c.id: c for c in ending.compartments}
        origins = [r for r in change.origins if r.patch_id == original.id]
        for cell in original.compartments:
            assert sum(r.volume_fraction for r in origins if r.original_id == cell.id) == 1
        for origin in origins:
            new, old = by_new[origin.resulting_id], by_old[origin.original_id]
            assert replace(new, id=old.id, top_m=old.top_m, bottom_m=old.bottom_m) == old
    assert len(initial.patches[0].compartments) == 2


def test_intensive_parameters_are_copied_without_dividing_rates_by_cell_fraction():
    initial = storage()
    change = split_soil_grid(initial, {"a": (.1, .3, .5), "b": (.2, .5)}, source=SOURCE)
    drainage = {(p.id, c.id): DrainageParameters(.4, 100 if c.id == "top" else 5, SOURCE)
                for p in initial.patches for c in p.compartments}
    roots = {(p.id, c.id): RootLayerActivity(.02 if c.id == "top" else .01, .8, SOURCE)
             for p in initial.patches for c in p.compartments}
    mapped_d, mapped_r = dict(change.drainage_parameters(drainage)), dict(change.root_activities(roots))
    for r in change.origins:
        assert mapped_d[(r.patch_id, r.resulting_id)] == drainage[(r.patch_id, r.original_id)]
        assert mapped_r[(r.patch_id, r.resulting_id)] == roots[(r.patch_id, r.original_id)]
    for patch in change.ending.patches:
        assert sum(c.thickness_m*mapped_r[(patch.id, c.id)].maximum_rate_per_day*1000 for c in patch.compartments) == 14


def test_existing_boundaries_and_empty_requests_preserve_cell_identity():
    initial = storage()
    change = split_soil_grid(initial, {"a": (.4, 1), "b": ()}, source=SOURCE)
    assert change.ending == initial
    assert all(r.original_id == r.resulting_id and r.volume_fraction == 1 for r in change.origins)


def test_generated_ids_cannot_collide_with_existing_ids_and_cut_order_is_irrelevant():
    initial = storage()
    p = initial.patches[0]
    initial = replace(initial, patches=(replace(p, compartments=(replace(p.compartments[0], id="__grid_cell_0"), p.compartments[1])), initial.patches[1]))
    first = split_soil_grid(initial, {"a": (.2, .1), "b": ()}, source=SOURCE)
    second = split_soil_grid(initial, {"a": (.1, .2), "b": ()}, source=SOURCE)
    assert first == second
    ids = [c.id for c in first.ending.patches[0].compartments]
    assert len(ids) == len(set(ids)) and "__grid_cell_0" not in ids


@pytest.mark.parametrize("cuts", [{"a": ()}, {"a": (), "b": (), "extra": ()},
    {"a": [0.2], "b": ()}, {"a": (.2, .2), "b": ()}, {"a": (0,), "b": ()},
    {"a": (True,), "b": ()}, {"a": (1.1,), "b": ()}, {"a": (float("nan"),), "b": ()}])
def test_invalid_split_requests_are_rejected(cuts):
    with pytest.raises(ValueError):
        split_soil_grid(storage(), cuts, source=SOURCE)


def test_grid_size_limit_and_incomplete_or_additive_parameter_maps_are_rejected():
    initial = storage()
    with pytest.raises(ValueError):
        split_soil_grid(initial, {"a": tuple(Fraction(i, 101) for i in range(1, 101)), "b": ()}, source=SOURCE)
    change = split_soil_grid(initial, {"a": (.2,), "b": ()}, source=SOURCE)
    with pytest.raises(ValueError):
        change.drainage_parameters({})
    with pytest.raises(ValueError):
        change.root_activities({(p.id, c.id): 25 for p in initial.patches for c in p.compartments})

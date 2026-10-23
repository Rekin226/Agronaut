"""Independent mass accounting, shared extraction and exact continuation cases."""

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from random import Random

import pytest

from agro_model.soil_storage import (
    SoilCompartment,
    SoilFlux,
    SoilPatch,
    SoilStorage,
    apply_soil_fluxes,
)

SOURCE = "Synthetic storage/extraction case, not field evidence"


def storage(*, area=1, top=0.3, bottom=0.25, fractions=(1,)):
    cells = (
        SoilCompartment("surface", 0, 0.1, 0.3, 0.2, 0.45, 0.1, top, SOURCE),
        SoilCompartment("lower", 0.1, 1, 0.3, 0.2, 0.45, 0.1, bottom, SOURCE),
    )
    return SoilStorage(area, tuple(SoilPatch(str(i), fraction, cells)
                                   for i, fraction in enumerate(fractions)), SOURCE)


def flux(kind, amount, *, patch="0", donor=None, receiver=None):
    return SoilFlux(kind, patch, donor, receiver, amount, SOURCE)


def cell(state, layer=0, patch=0):
    return state.patches[patch].compartments[layer]


def test_two_fluxes_draw_once_from_shared_storage_without_another_stress_factor():
    initial = storage()
    # The governing model would request 2 mm basal uptake plus 2 mm evaporation
    # for ET0=5, Kcb=.8, Ke=.4, Ks=.5. This kernel does not compute/apply Ks.
    result = apply_soil_fluxes(initial, [
        flux("root_uptake", 2, donor="lower"),
        flux("evaporation", 2, donor="surface"),
    ])
    assert initial.water_l == 255
    assert result.ending.water_l == 251
    assert result.external_output_l == 4
    assert cell(result.ending).water_content == Fraction(28, 100)
    assert cell(result.ending, 1).water_content == Fraction(223, 900)
    assert [row.actual_l for row in result.fluxes] == [2, 2]
    assert result.balance_residual_l == 0
    assert result.compartment_residuals_l == (("0", "surface", 0), ("0", "lower", 0))
    assert result.initial is initial


def test_evaporation_below_wilting_is_conserved_and_unavailable_to_roots():
    initial = storage(top=0.2, bottom=0.2)
    result = apply_soil_fluxes(initial, [
        flux("evaporation", Fraction(20, 11), donor="surface"),
        flux("root_uptake", 2, donor="surface"),
        flux("root_uptake", 2, donor="lower"),
    ])
    assert initial.water_l == 200
    assert result.ending.water_l == 200 - Fraction(20, 11)
    assert cell(result.ending).water_content == Fraction(2, 11)
    assert [row.actual_l for row in result.fluxes] == [Fraction(20, 11), 0, 0]
    assert [row.unmet_l for row in result.fluxes] == [0, 2, 2]
    assert result.fluxes[1].limiting_bounds == ("donor_wilting_point",)
    assert result.fluxes[2].limiting_bounds == ("donor_wilting_point",)
    assert result.balance_residual_l == 0


def test_surface_percolation_is_internal_and_not_deep_drainage():
    initial = storage(bottom=Fraction(195, 900))  # 300 - (30+195) = 75 mm deficit.
    result = apply_soil_fluxes(initial, [
        flux("infiltration", 5, receiver="surface"),
        flux("transfer", 5, donor="surface", receiver="lower"),
        flux("drainage", 5, donor="lower"),
    ])
    assert result.ending.water_l == 230
    assert result.internal_transfer_l == 5
    assert result.external_input_l == 5
    assert result.external_output_l == 0
    assert cell(result.ending).water_content == Fraction(3, 10)
    assert cell(result.ending, 1).water_content == Fraction(2, 9)
    assert result.fluxes[-1].limiting_bounds == ("donor_field_capacity",)


def test_patch_amounts_use_their_own_areas_and_do_not_implicitly_remap():
    initial = storage(area=100, top=0.2, fractions=(0.2, 0.8))
    result = apply_soil_fluxes(initial, [flux("evaporation", 20, donor="surface")])
    # 20 L / 20 m2 = 1 mm locally, 20 L / 100 m2 = .2 mm field-average.
    assert cell(result.ending).water_content == Fraction(19, 100)
    assert cell(result.ending, patch=1).water_content == Fraction(1, 5)
    assert result.external_output_l / initial.area_m2 == Fraction(1, 5)
    assert tuple(p.area_fraction for p in result.ending.patches) == (Fraction(1, 5), Fraction(4, 5))
    assert result.ending.water_l == initial.water_l - 20
    with pytest.raises(FrozenInstanceError):
        result.ending.patches[0].area_fraction = Fraction(2, 5)


def test_competing_demands_expose_order_and_unmet_uptake_without_hidden_water():
    initial = storage(top=0.22)
    evaporation = flux("evaporation", 5, donor="surface")
    uptake = flux("root_uptake", 4, donor="surface")
    evap_first = apply_soil_fluxes(initial, [evaporation, uptake])
    roots_first = apply_soil_fluxes(initial, [uptake, evaporation])
    assert [row.actual_l for row in evap_first.fluxes] == [5, 0]
    assert [row.actual_l for row in roots_first.fluxes] == [2, 5]
    assert cell(evap_first.ending).water_content == Fraction(17, 100)
    assert cell(roots_first.ending).water_content == Fraction(15, 100)
    assert evap_first.balance_residual_l == roots_first.balance_residual_l == 0


def test_saturation_limits_infiltration_without_inventing_runoff_or_lower_layer_water():
    initial = storage(top=0.44)
    result = apply_soil_fluxes(initial, [flux("infiltration", 7, receiver="surface")])
    assert result.external_input_l == 1
    assert result.fluxes[0].unmet_l == 6
    assert result.fluxes[0].limiting_bounds == ("receiver_saturation",)
    assert cell(result.ending).water_content == Fraction(45, 100)
    assert cell(result.ending, 1) == cell(initial, 1)


def test_transfers_use_both_bounds_and_preserve_the_complete_profile():
    initial = storage(top=0.12, bottom=Fraction(403, 900))
    result = apply_soil_fluxes(initial, [flux("transfer", 6, donor="surface", receiver="lower")])
    assert result.fluxes[0].actual_l == 2
    assert set(result.fluxes[0].limiting_bounds) == {"donor_evaporation_floor", "receiver_saturation"}
    assert result.fluxes[0].unmet_l == 4
    assert result.ending.water_l == initial.water_l == 415
    assert result.external_input_l == result.external_output_l == 0
    # An explicitly requested upward transfer is internal as well; no rate inferred.
    reverse = apply_soil_fluxes(result.ending, [flux("transfer", 1, donor="lower", receiver="surface")])
    assert reverse.ending.water_l == 415
    assert cell(reverse.ending).water_content == Fraction(11, 100)


def test_drydown_and_rewetting_do_not_clip_away_storage_losses():
    initial = storage(top=0.2, bottom=0.2)
    dry = apply_soil_fluxes(initial, [flux("evaporation", 50, donor="surface")])
    assert dry.external_output_l == 10
    assert dry.fluxes[0].unmet_l == 40
    assert dry.fluxes[0].limiting_bounds == ("donor_evaporation_floor",)
    assert dry.ending.water_l == 190
    rewet = apply_soil_fluxes(dry.ending, [flux("infiltration", 7, receiver="surface")])
    assert rewet.ending.water_l == 197
    assert cell(rewet.ending).water_content == Fraction(17, 100)
    assert apply_soil_fluxes(rewet.ending, [flux("root_uptake", 1, donor="surface")]).external_output_l == 0


def test_seeded_mixed_fluxes_conserve_every_cell_and_continue_exactly():
    initial = storage(area=37, fractions=(Fraction(1, 3), Fraction(2, 3)))
    rng = Random(254)
    requests = []
    for _ in range(400):
        kind = rng.choice(("infiltration", "evaporation", "root_uptake", "transfer", "drainage"))
        donor = None if kind == "infiltration" else "lower" if kind == "drainage" else rng.choice(("surface", "lower"))
        receiver = "surface" if kind == "infiltration" else None
        if kind == "transfer":
            receiver = "lower" if donor == "surface" else "surface"
        requests.append(flux(kind, Fraction(rng.randrange(10000), 7), patch=str(rng.randrange(2)), donor=donor, receiver=receiver))
    full = apply_soil_fluxes(initial, requests)
    first = apply_soil_fluxes(initial, requests[:137])
    second = apply_soil_fluxes(first.ending, requests[137:])
    assert full.ending == second.ending
    assert full.fluxes == first.fluxes + second.fluxes
    expected_delta = sum((row.actual_l if row.request.kind == "infiltration" else
                          -row.actual_l if row.request.kind != "transfer" else 0
                          for row in full.fluxes), Fraction(0))
    assert full.ending.water_l - initial.water_l == expected_delta
    for patch in full.ending.patches:
        for compartment in patch.compartments:
            assert compartment.evaporation_floor <= compartment.water_content <= compartment.saturation
    assert all(value == 0 for _, _, value in full.compartment_residuals_l)
    assert all(row.request.requested_l == row.actual_l + row.unmet_l for row in full.fluxes)


@pytest.mark.parametrize("changes", [
    {"water_content": 0.09}, {"water_content": 0.46}, {"water_content": None},
    {"water_content": True}, {"water_content": float("nan")}, {"water_content": float("inf")},
    {"field_capacity": 0.2}, {"evaporation_floor": 0.21}, {"saturation": 0.29},
    {"bottom_m": 0}, {"top_m": -0.1}, {"soil_source": ""},
])
def test_invalid_storage_parameters_are_not_coerced(changes):
    with pytest.raises(ValueError):
        replace(cell(storage()), **changes)


def test_invalid_geometry_rejected_before_any_accounting():
    initial = storage()
    patch = initial.patches[0]
    with pytest.raises(ValueError, match="exactly"):
        replace(initial, patches=(replace(patch, area_fraction=0.2),))
    with pytest.raises(ValueError, match="contiguous"):
        replace(patch, compartments=(cell(initial), replace(cell(initial, 1), top_m=0.2)))
    with pytest.raises(ValueError, match="unique"):
        replace(patch, compartments=(cell(initial), replace(cell(initial, 1), id="surface")))
    with pytest.raises(ValueError, match="unique"):
        replace(initial, patches=(patch, patch))
    with pytest.raises(ValueError, match="same profile depth"):
        replace(initial, patches=(replace(patch, area_fraction=0.5),
                SoilPatch("other", 0.5, (replace(cell(initial), bottom_m=0.5),))))


@pytest.mark.parametrize("bad_flux", [
    flux("infiltration", 1, receiver="lower"),
    flux("drainage", 1, donor="surface"),
    flux("transfer", 1, donor="surface", receiver="surface"),
    flux("evaporation", 1, donor="missing"),
    flux("root_uptake", 1, donor="surface", patch="missing"),
    {},
])
def test_invalid_request_does_not_modify_original_even_after_valid_request(bad_flux):
    initial = storage()
    with pytest.raises(ValueError):
        apply_soil_fluxes(initial, [flux("infiltration", 5, receiver="surface"), bad_flux])
    assert initial == storage()


@pytest.mark.parametrize("amount", [-1, True, None, float("inf"), Fraction(-1, 2)])
def test_invalid_requested_amount(amount):
    with pytest.raises(ValueError):
        flux("evaporation", amount, donor="surface")


def test_empty_and_zero_requests_preserve_storage():
    initial = storage()
    for requests in ([], [flux("evaporation", 0, donor="surface")]):
        result = apply_soil_fluxes(initial, requests)
        assert result.ending.patches == initial.patches
        assert result.external_input_l == result.external_output_l == result.internal_transfer_l == 0
        assert result.balance_residual_l == 0

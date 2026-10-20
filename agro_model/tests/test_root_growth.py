"""Conservative root expansion with explicitly supplied subsoil water."""

from datetime import date

import pytest

from agro_model.root_growth import RootZoneDay, expand_root_zone
from agro_model.water import Soil, SoilState


def supplied(**changes):
    values = dict(day=date(2026, 7, 6), root_depth_m=.6, source="Synthetic root survey",
                  added_layer_water_content=.25, added_layer_source="Supplied layer mean immediately before inclusion")
    values.update(changes)
    return RootZoneDay(**values)


def test_expansion_accounts_for_added_depletion_and_available_water():
    soil = Soil(.30, .10, .4, .5, "Synthetic homogeneous soil")
    prior = SoilState(date(2026, 7, 5), 30, "Synthetic state")
    new_soil, state, result = expand_root_zone(soil, prior, supplied())
    # 0.2 m of new soil: TAW +40; depletion +10; available water +30 mm.
    assert new_soil.total_available_mm == pytest.approx(120)
    assert new_soil.readily_available_mm == pytest.approx(60)
    assert state.depletion_mm == pytest.approx(40)
    assert result.values["added_layer_depletion_mm"] == pytest.approx(10)
    assert result.values["added_layer_available_water_mm"] == pytest.approx(30)
    assert (new_soil.total_available_mm - state.depletion_mm) == pytest.approx(
        soil.total_available_mm - prior.depletion_mm + result.values["added_layer_available_water_mm"])
    assert state.as_of == prior.as_of
    assert result.inputs["prior_soil"]["root_depth_m"] == .4
    assert "https://www.fao.org/4/X0490E/x0490e0e.htm" in result.sources


def test_unknown_state_stays_unknown_despite_known_new_layer():
    soil = Soil(.30, .10, .4, .5, "Synthetic homogeneous soil")
    _, state, result = expand_root_zone(soil, None, supplied())
    assert state is None
    assert result.values["starting_depletion_mm"] is None
    assert result.missing_inputs == ("soil_state",)


@pytest.mark.parametrize("theta", [.1, .2, .3])
@pytest.mark.parametrize("fraction", [0, .4, 1])
def test_dry_and_wet_endpoints_conserve_storage(theta, fraction):
    soil = Soil(.30, .10, .3, .5, "Synthetic homogeneous soil")
    state = SoilState(date(2026, 7, 5), soil.total_available_mm * fraction, "Synthetic state")
    changed, updated, result = expand_root_zone(soil, state, supplied(added_layer_water_content=theta))
    assert 0 <= updated.depletion_mm <= changed.total_available_mm
    assert changed.total_available_mm - updated.depletion_mm == pytest.approx(
        soil.total_available_mm - state.depletion_mm + result.values["added_layer_available_water_mm"])


@pytest.mark.parametrize("changes", [dict(root_depth_m=.2), dict(root_depth_m=True), dict(root_depth_m=0),
    dict(root_depth_m=float("inf")), dict(added_layer_water_content=.4), dict(added_layer_water_content=.05),
    dict(added_layer_water_content=None, added_layer_source=None), dict(added_layer_source=""),
    dict(source=""), dict(root_depth_m=.4), dict(day=date(2026, 7, 7))])
def test_invalid_growth_or_missing_layer_water_cannot_be_inferred(changes):
    soil = Soil(.30, .10, .4, .5, "Synthetic homogeneous soil")
    prior = SoilState(date(2026, 7, 5), 30, "Synthetic state")
    with pytest.raises(ValueError):
        expand_root_zone(soil, prior, supplied(**changes))


def test_unchanged_depth_needs_no_layer_and_preserves_depletion():
    soil = Soil(.30, .10, .4, .5, "Synthetic homogeneous soil")
    prior = SoilState(date(2026, 7, 5), 30, "Synthetic state")
    _, state, result = expand_root_zone(soil, prior, supplied(root_depth_m=.4,
        added_layer_water_content=None, added_layer_source=None))
    assert state.depletion_mm == prior.depletion_mm
    assert result.values["added_layer_available_water_mm"] == result.values["added_layer_depletion_mm"] == 0

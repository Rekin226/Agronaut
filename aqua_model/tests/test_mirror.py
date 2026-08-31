"""The live mirror: state survives the round trip, nudges stay bounded, drift is named."""

import pytest

from aqua_model.mirror import (
    NUDGE_WEIGHTS,
    from_dict,
    nudge,
    snapshot_line,
    to_dict,
)
from aqua_model.production import start_state
from aqua_model.species import get_species

TILAPIA = get_species("tilapia")


def _state():
    return start_state(volume_l=2000.0, fish_count=60, start_weight_g=200.0,
                       water_temp_c=26.0, species=TILAPIA)


def test_serialization_round_trips_exactly():
    s = _state()
    d = to_dict(s, as_of="2026-08-26")
    s2, as_of = from_dict(d)
    assert s2 == s and as_of == "2026-08-26"


def test_an_unknown_schema_is_refused_not_guessed():
    d = to_dict(_state(), as_of="2026-08-26")
    d["schema"] = "0.0.1"
    with pytest.raises(ValueError, match="refuse"):
        from_dict(d)


def test_a_nudge_lands_between_model_and_measurement():
    s = _state()
    nudged, _ = nudge(s, {"no3_mg_l": 100.0})
    assert 0.0 < nudged.nitrogen.no3_mg_l < 100.0
    w = NUDGE_WEIGHTS["no3_mg_l"]
    assert nudged.nitrogen.no3_mg_l == pytest.approx(100.0 * w)


def test_a_big_innovation_is_named_with_its_direction():
    _, notes = nudge(_state(), {"no3_mg_l": 100.0})
    assert any("low" in n and "%" in n for n in notes)


def test_fish_count_is_authoritative_and_updates_biomass():
    nudged, notes = nudge(_state(), {"fish_count": 50})
    assert nudged.fish.count == 50
    assert nudged.nitrogen.fish_biomass_kg == pytest.approx(nudged.fish.biomass_kg())
    assert any("50" in n for n in notes)


def test_no_readings_changes_nothing_and_says_so():
    s = _state()
    nudged, notes = nudge(s, {})
    assert nudged == s
    assert any("unchanged" in n for n in notes)


def test_negative_readings_are_clamped_not_believed():
    nudged, _ = nudge(_state(), {"tan_mg_l": -3.0})
    assert nudged.nitrogen.tan_mg_l >= 0.0


def test_the_snapshot_is_one_phone_sized_line():
    line = snapshot_line(_state())
    assert "\n" not in line
    assert "NH3" in line and "fish" in line

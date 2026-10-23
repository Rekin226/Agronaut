from datetime import datetime, timezone

import pytest

from agro_model.farm import Farm, Observation, ProductionCycle, ProductionUnit


def test_mixed_farm_separates_production_and_irrigation():
    farm = Farm("farm1", "user1", "Home farm", "Asia/Taipei")
    aquatic = ProductionUnit("pond", farm.id, "Aquaponics", "aquaponics", "water",
                             "greenhouse", "fully_irrigated")
    field = ProductionUnit("field", farm.id, "Tomato field", "soil", "soil",
                           "open_field", "fully_irrigated", "drip")
    assert aquatic.irrigation_method is None
    assert field.production_system == "soil"
    assert field.irrigation_method == "drip"


@pytest.mark.parametrize("production,medium,strategy,method", [
    ("soil", "water", "fully_irrigated", "drip"),
    ("soil", "soil", "rainfed", "sprinkler"),
    ("hydroponics", "soil", "fully_irrigated", None),
    ("soil", "soil", "fully_irrigated", None),
    ("drip", "soil", "fully_irrigated", "drip"),
])
def test_invalid_combinations_rejected(production, medium, strategy, method):
    with pytest.raises(ValueError):
        ProductionUnit("u", "f", "Test", production, medium, "open_field", strategy, method)


def test_timezone_required_for_observations():
    with pytest.raises(ValueError):
        Observation("obs", "owner", "unit", None, "rainfall", 5, "mm",
                    datetime(2026, 7, 1), datetime(2026, 7, 1), "measured", "rain gauge")
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    observation = Observation("obs", "owner", "unit", "cycle", "rainfall", 5, "mm",
                              now, now, "measured", "rain gauge")
    assert observation.cycle_id == "cycle"


def test_cycle_requires_crop_and_iso_date():
    with pytest.raises(ValueError):
        ProductionCycle("c", "u", "", "2026-07-01")
    with pytest.raises(ValueError):
        ProductionCycle("c", "u", "tomato", "yesterday")


def test_unknown_observation_unit_and_nonfinite_values_rejected():
    now = datetime.now(timezone.utc)
    for value, unit in ((float("nan"), "mm"), (3, "kg")):
        with pytest.raises(ValueError):
            Observation("o", "owner", "u", None, "rainfall", value, unit,
                        now, now, "measured", "gauge")

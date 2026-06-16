"""Sizing behaviour: happy path, infeasibility, temperature sensitivity, honesty layer."""

from aqua_model import size_system
from aqua_model.validate import validate_design_input


def _design(**over):
    base = dict(
        fish_species="tilapia", crop="lettuce", grow_area_m2=10.0,
        temperature_c=28.0, water_budget_lpd=5000.0,
    )
    base.update(over)
    return validate_design_input(**base)


def test_happy_path_produces_complete_artifact():
    out = size_system(_design())
    assert out.feasible is True
    assert out.feed_g_per_day > 0
    assert out.fish_count >= 1
    assert out.fish_biomass_kg > 0
    assert out.system_volume_l > out.rearing_tank_volume_l  # system includes raft + sump
    assert out.pump_turnover_lph > 0
    assert out.biofilter_media_m2 and out.biofilter_media_m2 > 0
    # Build artifacts present (Shape B output).
    assert out.bill_of_materials and len(out.bill_of_materials) >= 5
    assert out.operating_envelope.get("temperature_target_c")
    assert out.maintenance_checklist
    # Honesty layer present.
    assert out.not_modeled and any("pH" in n for n in out.not_modeled)
    assert out.coefficients_used and all(c.source for c in out.coefficients_used)


def test_feed_follows_frr_exactly():
    out = size_system(_design(grow_area_m2=10.0, crop="lettuce"))
    # lettuce FRR default = 60 g/m2/day -> 10 m2 = 600 g/day
    assert out.feed_g_per_day == 600.0


def test_fruiting_crop_feeds_more_than_leafy():
    leafy = size_system(_design(crop="lettuce"))
    fruiting = size_system(_design(crop="tomato"))
    assert fruiting.feed_g_per_day > leafy.feed_g_per_day


def test_infeasible_when_water_budget_too_low():
    out = size_system(_design(grow_area_m2=50.0, water_budget_lpd=10.0))
    assert out.feasible is False
    assert out.binding_constraint == "water_budget"
    # Nearest-feasible hint names a smaller area, not an exception or silent number.
    assert any("reduce grow area" in w for w in out.warnings)


def test_temperature_sensitivity_changes_sizing():
    warm = size_system(_design(temperature_c=28.0))   # optimal for tilapia
    cold = size_system(_design(temperature_c=16.0))   # well below optimum
    # Same feed (FRR is area-based), but colder fish eat less per kg, so the SAME feed
    # implies MORE standing biomass -> larger tank. Sizing must differ.
    assert warm.feed_g_per_day == cold.feed_g_per_day
    assert cold.fish_biomass_kg > warm.fish_biomass_kg
    assert any("outside" in w for w in cold.warnings)


def test_never_raises_on_valid_input_extremes():
    # Tiny and huge valid systems both return a result object.
    small = size_system(_design(grow_area_m2=0.5))
    big = size_system(_design(grow_area_m2=1000.0, water_budget_lpd=10_000_000.0))
    assert small.fish_count >= 1
    assert big.feasible in (True, False)

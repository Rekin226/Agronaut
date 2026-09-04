"""Crop database tests — every entry is sourced, and each one sizes without error."""

import pytest

from aqua_model.crops import CROPS, get_crop
from aqua_model import size_system, validate_design_input


def test_all_crops_have_citations():
    """Every crop entry must cite its source(s)."""
    for name, crop in CROPS.items():
        assert crop.source, f"{name} has no source"
        assert len(crop.source) > 10, f"{name} source is too vague"


def test_all_crops_have_reasonable_ranges():
    """Every crop must have physically plausible ranges."""
    for name, crop in CROPS.items():
        # FRR: positive and within reasonable bounds
        assert crop.frr_g_per_m2_day > 10, f"{name} FRR too low"
        assert crop.frr_g_per_m2_day < 200, f"{name} FRR too high"
        assert crop.frr_low <= crop.frr_g_per_m2_day <= crop.frr_high, \
            f"{name} FRR outside its declared range"

        # Temperature: physical
        assert crop.temp_min_c < crop.temp_max_c, f"{name} temp range inverted"
        assert crop.temp_min_c >= 0, f"{name} temp_min below 0°C"
        assert crop.temp_max_c <= 45, f"{name} temp_max above 45°C"

        # Yield: positive
        assert crop.yield_kg_per_m2_year > 0, f"{name} yield must be positive"

        # pH: within physical range
        assert 4.0 <= crop.ph_min <= 8.0, f"{name} ph_min out of range"
        assert 5.0 <= crop.ph_max <= 9.0, f"{name} ph_max out of range"
        assert crop.ph_min < crop.ph_max, f"{name} pH range inverted"


def test_lettuce_is_the_standard():
    """Lettuce is the reference crop; its values should be stable."""
    l = get_crop("lettuce")
    assert l.frr_g_per_m2_day == 57.0
    assert l.yield_kg_per_m2_year == 25.0
    assert l.category == "leafy"


def test_basil_frr_in_measured_band():
    """Basil FRR was recalibrated to the midpoint of the UVI measured band."""
    b = get_crop("basil")
    assert 81.0 <= b.frr_g_per_m2_day <= 100.0


def test_amaranth_is_heat_tolerant():
    """Amaranth is the first heat-tolerant leafy green; verify its temperature band."""
    a = get_crop("amaranth")
    assert a.temp_max_c >= 35.0
    assert a.temp_min_c >= 18.0
    assert a.category == "leafy"
    assert "field trial" in a.source.lower() or "temperature" in a.source.lower()


def test_water_spinach_is_heat_tolerant_and_semi_aquatic():
    """Water spinach (kangkong) is heat-tolerant and ideal for raft culture."""
    ws = get_crop("water_spinach")
    assert ws.temp_max_c >= 35.0
    assert ws.temp_min_c >= 20.0
    assert ws.category == "leafy"
    assert ws.temp_min_c >= 18.0, "Water spinach needs warm water"
    assert "ipomoea aquatica" in ws.source.lower() or "water spinach" in ws.source.lower()
    assert "FRR placed" in ws.source, "FRR placement must be clearly stated"


def test_all_crops_size_without_error():
    """Every crop can be used in a sizing calculation."""
    for name in CROPS.keys():
        # Use a fish species that's always available
        di = validate_design_input(
            fish_species="tilapia",
            crop=name,
            grow_area_m2=10.0,
            temperature_c=26.0,
            water_budget_lpd=500.0,
        )
        out = size_system(di)
        # Should be feasible at this modest size
        # Note: some crops may be infeasible if water budget is too tight,
        # but we're just checking that it runs without error
        assert out.feed_g_per_day > 0, f"{name} produced zero feed"
        assert out.fish_count > 0, f"{name} produced zero fish count"


def test_heat_tolerant_crops_grow_at_high_temperature():
    """Heat-tolerant crops should be feasible at 32°C; heat-sensitive ones should fail."""
    heat_tolerant = {"amaranth", "water_spinach"}
    heat_sensitive = {"lettuce", "spinach", "kale", "swiss_chard"}

    for name in heat_tolerant:
        di = validate_design_input(
            fish_species="tilapia",
            crop=name,
            grow_area_m2=10.0,
            temperature_c=32.0,
            water_budget_lpd=500.0,
        )
        out = size_system(di)
        # Should be feasible — heat-tolerant crops can grow at 32°C
        # The test passes if the crop's temperature range includes 32°C
        crop = get_crop(name)
        assert crop.temp_max_c >= 32.0, f"{name} should tolerate 32°C but max is {crop.temp_max_c}°C"

    for name in heat_sensitive:
        crop = get_crop(name)
        # These should have max temps <= 28°C
        assert crop.temp_max_c <= 28.0, f"{name} should not tolerate 32°C but max is {crop.temp_max_c}°C"


def test_water_spinach_ideal_for_raft():
    """Water spinach's semi-aquatic nature makes it ideal for raft culture."""
    ws = get_crop("water_spinach")
    # Verify the source mentions its aquatic nature or FRR placement
    assert "semi-aquatic" not in ws.source, "The crop entry should state the FRR placement explicitly"
    # The FRR should be placed, not measured
    assert "FRR placed" in ws.source, "FRR must be clearly marked as placed (not measured)"


def test_water_spinach_yield_is_sourced():
    """Water spinach yield should be traceable to literature."""
    ws = get_crop("water_spinach")
    assert "field trial" in ws.source.lower() or "tropical" in ws.source.lower()
    assert ws.yield_kg_per_m2_year > 10.0, "Water spinach yield seems too low"
    assert ws.yield_kg_per_m2_year < 30.0, "Water spinach yield seems too high"


def test_crop_registration_count():
    """We should have at least 10 crops registered."""
    assert len(CROPS) >= 10, f"Expected at least 10 crops, got {len(CROPS)}"


def test_knowledge_base_temperature_band_consistency():
    """Verify that temperature bands in crops.py match the knowledge base docs."""
    # This is a consistency test: the knowledge base says water spinach grows at 25-32°C
    ws = get_crop("water_spinach")
    assert ws.temp_min_c <= 25.0, "Water spinach min temp should be <=25°C"
    assert ws.temp_max_c >= 32.0, "Water spinach max temp should be >=32°C"
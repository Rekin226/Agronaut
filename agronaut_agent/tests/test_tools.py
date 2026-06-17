"""Tools wrap the deterministic core, preserve the trust gate, and carry provenance."""

from agronaut_agent.tools import (
    size_aquaponics_system,
    optimize_fish_crop_ratio,
    list_supported_species_and_crops,
    AGRONAUT_TOOLS,
)


def test_tool_registry():
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "size_aquaponics_system" in names
    assert "optimize_fish_crop_ratio" in names
    assert "search_knowledge_base" in names
    assert len(AGRONAUT_TOOLS) == 6


def test_size_valid_carries_numbers_and_sources():
    out = size_aquaponics_system.invoke(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
         "temperature_c": 27, "water_budget_lpd": 300}
    )
    assert "FEASIBLE" in out
    assert "fish=" in out and "feed=" in out
    # honesty layer: coefficients with sources and not-modeled caveats are present
    assert "Coefficients used" in out and "source:" in out
    assert "NOT modeled" in out


def test_size_trust_gate_rejects_unknown_species():
    out = size_aquaponics_system.invoke(
        {"fish_species": "shark", "crop": "lettuce", "grow_area_m2": 12,
         "temperature_c": 27, "water_budget_lpd": 300}
    )
    assert "VALIDATION_FAILED" in out
    assert "shark" in out
    assert "do NOT guess" in out


def test_optimize_returns_ranked_best():
    out = optimize_fish_crop_ratio.invoke(
        {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000, "objective": "food"}
    )
    assert "Best ratio:" in out
    assert "alternatives" in out


def test_optimize_rejects_bad_objective():
    out = optimize_fish_crop_ratio.invoke(
        {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000, "objective": "money"}
    )
    assert "Unknown objective" in out


def test_list_supported():
    out = list_supported_species_and_crops.invoke({})
    assert "tilapia" in out and "lettuce" in out and "water_efficiency" in out

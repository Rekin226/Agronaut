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
    assert "remember_about_user" in names
    assert len(AGRONAUT_TOOLS) == 8


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


def test_registry_includes_update_profile():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "update_profile" in names
    assert len(AGRONAUT_TOOLS) == 8


def test_update_profile_writes_canonical_drops_unknown():
    from agronaut_agent.store import _Db, MemoryStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import update_profile

    mem = MemoryStore(_Db(":memory:"))
    runtime.set_current(mem, "cli:p")
    try:
        out = update_profile.invoke({"updates": {
            "goal": "optimize", "objective": "protein", "grow_area_m2": 10,
            "bogus_key": "x", "ph": "",
        }})
    finally:
        runtime.clear_current()

    facts = mem.get_facts("cli:p")
    assert facts["goal"] == "optimize"
    assert facts["objective"] == "protein"
    assert facts["grow_area_m2"] == "10"
    assert "bogus_key" not in facts   # unknown key ignored
    assert "ph" not in facts          # empty value skipped
    assert "optimize" in out

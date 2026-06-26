"""The System Profile primitive: canonical keys, goal essentials, and the
deterministic 'what's still missing' helper that steers the consultation."""

from agronaut_agent import profile


def test_profile_keys_include_new_water_fields():
    for key in ("tank_volume_l", "dissolved_oxygen_mgl", "ammonia_mgl",
                "goal", "objective", "experience_level"):
        assert key in profile.PROFILE_KEYS


def test_missing_essentials_for_design_lists_blanks():
    have = {"goal": "design", "fish_species": "tilapia"}
    missing = profile.missing_essentials("design", have)
    assert missing == ["crop", "grow_area_m2", "temperature_c", "water_budget_lpd"]


def test_missing_essentials_empty_when_all_present():
    have = {"grow_area_m2": "10", "temperature_c": "26",
            "water_budget_lpd": "200", "objective": "protein"}
    assert profile.missing_essentials("optimize", have) == []


def test_missing_essentials_blank_string_counts_as_missing():
    have = {"grow_area_m2": "  ", "temperature_c": "26",
            "water_budget_lpd": "200", "objective": "protein"}
    assert profile.missing_essentials("optimize", have) == ["grow_area_m2"]


def test_missing_essentials_unknown_goal_is_empty():
    assert profile.missing_essentials(None, {}) == []
    assert profile.missing_essentials("troubleshoot", {}) == []


def test_render_profile_empty_is_blank():
    assert profile.render_profile({}) == ""


def test_render_profile_shows_known_fields_with_labels():
    text = profile.render_profile(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": "10",
         "temperature_c": "26", "goal": "design"},
        goal="design",
    )
    assert "YOUR SYSTEM" in text
    assert "tilapia" in text and "lettuce" in text
    assert "10" in text and "26" in text


def test_render_profile_troubleshoot_puts_water_params_first():
    text = profile.render_profile(
        {"grow_area_m2": "10", "dissolved_oxygen_mgl": "4.0", "ammonia_mgl": "2.0"},
        goal="troubleshoot",
    )
    # water params surface before the system spec when troubleshooting
    assert text.index("DO") < text.index("grow area")
    assert text.index("ammonia") < text.index("grow area")


def test_profile_updates_from_size_success():
    args = {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
            "temperature_c": 27, "water_budget_lpd": 300}
    updates = profile.profile_updates_from_tool("size_aquaponics_system", args, "FEASIBLE ...")
    assert updates == {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                       "temperature_c": 27, "water_budget_lpd": 300}


def test_profile_updates_skipped_on_validation_failure():
    args = {"fish_species": "shark", "crop": "lettuce", "grow_area_m2": 12,
            "temperature_c": 27, "water_budget_lpd": 300}
    out = profile.profile_updates_from_tool("size_aquaponics_system", args,
                                            "VALIDATION_FAILED: unknown species 'shark'")
    assert out == {}


def test_profile_updates_for_optimize_includes_objective():
    args = {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000,
            "objective": "food"}
    out = profile.profile_updates_from_tool("optimize_fish_crop_ratio", args, "Best ratio: ...")
    assert out == {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000,
                   "objective": "food"}


def test_profile_updates_ignores_non_fact_tools():
    assert profile.profile_updates_from_tool("search_knowledge_base",
                                             {"query": "x"}, "some passage") == {}

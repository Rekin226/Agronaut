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

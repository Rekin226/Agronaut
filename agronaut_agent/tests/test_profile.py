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
    # in the order a consultant asks: place (-> temperature), space, crop, water
    assert missing == ["temperature_c", "grow_area_m2", "crop", "water_budget_lpd"]


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


def test_goal_headers_and_prompts_cover_all_goals():
    for g in profile.GOALS:
        assert g in profile.GOAL_HEADERS
        assert g in profile.GOAL_PROMPTS


def test_essentials_hint_troubleshoot_asks_what_they_see():
    hint = profile.essentials_hint("troubleshoot", {})
    assert "what's going wrong" in hint.lower()
    assert hint.count("?") == 1


def test_essentials_hint_design_empty_profile_is_full_prompt():
    hint = profile.essentials_hint("design", {})
    assert hint == profile.GOAL_PROMPTS["design"]


def test_essentials_hint_design_partial_asks_only_the_next_one():
    # place known (so temperature is a lookup) -> the next real question is about space
    hint = profile.essentials_hint("design", {"location": "Bobo-Dioulasso"})
    assert "space" in hint.lower()
    assert hint.count("?") == 1


def test_a_consultant_asks_where_before_what():
    assert profile.missing_essentials("design", {})[0] == "temperature_c"
    assert "where are you" in profile.next_question("design", {}).lower()


# --- one question at a time --------------------------------------------------------

def test_next_question_is_one_plain_question():
    q = profile.next_question("design", {})
    assert q.count("?") == 1
    assert "m²" not in q and "L/day" not in q     # no units a beginner cannot answer


def test_next_question_skips_known_facts():
    facts = {"temperature_c": "28", "fish_species": "tilapia", "crop": "lettuce"}
    q = profile.next_question("design", facts)
    assert "space" in q.lower()                   # grow area, asked in plain words


def test_next_question_none_when_complete_or_troubleshooting():
    full = {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": "10",
            "temperature_c": "27", "water_budget_lpd": "300"}
    assert profile.next_question("design", full) is None
    assert profile.next_question("troubleshoot", {}) is None
    assert profile.next_question_note("design", full) == ""


def test_experts_get_the_terse_form():
    q = profile.next_question("optimize", {"experience_level": "expert"})
    assert q == "Mean water temperature (°C)?"
    q = profile.next_question("optimize", {"experience_level": "expert", "temperature_c": "27"})
    assert q == "Planted area in m²?"


def test_known_location_turns_temperature_into_a_lookup():
    facts = {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": "10",
             "location": "Ouagadougou"}
    note = profile.next_question_note("design", facts)
    assert note.startswith("NEXT STEP:")
    assert "fetch_site_climate" in note and "Ouagadougou" in note
    # the /design reply skips the lookup and asks the user the next real question
    assert "water" in profile.essentials_hint("design", facts).lower()


def test_ask_next_note_carries_the_mapping_hint():
    note = profile.next_question_note("design", {"temperature_c": "28"})
    assert note.startswith("ASK NEXT")
    assert "confirm" in note                      # a guessed area is confirmed before saving


def test_essentials_hint_full_profile_says_ready():
    facts = {"grow_area_m2": "10", "temperature_c": "26",
             "water_budget_lpd": "200", "objective": "protein"}
    hint = profile.essentials_hint("optimize", facts)
    assert "go" in hint.lower()

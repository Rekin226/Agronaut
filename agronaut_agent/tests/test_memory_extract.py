"""Deterministic fact extraction from free text (water-quality readings)."""

from agronaut_agent.memory_extract import extract_facts


def test_extracts_dissolved_oxygen_with_unit():
    assert extract_facts("DO is 5.5 mg/L this morning")["dissolved_oxygen_mgl"] == "5.5"
    assert extract_facts("dissolved oxygen 4 ppm")["dissolved_oxygen_mgl"] == "4"


def test_does_not_match_the_word_do_without_a_unit():
    assert "dissolved_oxygen_mgl" not in extract_facts("what do I do at 26C?")


def test_extracts_ammonia():
    assert extract_facts("ammonia 0.5")["ammonia_mgl"] == "0.5"
    assert extract_facts("ammonia spiked to 2 ppm")["ammonia_mgl"] == "2"


def test_does_not_fabricate_ammonia_from_duration_prose():
    assert "ammonia_mgl" not in extract_facts("I had ammonia issues for 3 days")
    assert "ammonia_mgl" not in extract_facts("ammonia count of 5")


def test_still_extracts_temperature():
    assert extract_facts("water is 27C")["temperature_c"] == "27.0"


# --- P1: parsers must not fabricate facts from ordinary chat ------------------

def test_does_not_fabricate_species_from_greeting():
    assert "fish_species" not in extract_facts("hey there")
    assert "fish_species" not in extract_facts("hello, im new here")
    assert "fish_species" not in extract_facts("help me get started")


def test_extracts_real_species():
    assert extract_facts("my tilapia are sick")["fish_species"].lower() == "tilapia"
    assert extract_facts("I keep catfish")["fish_species"].lower() == "catfish"
    assert extract_facts("running trout up north")["fish_species"].lower() == "trout"


def test_does_not_fabricate_temperature_from_counts_or_area():
    assert "temperature_c" not in extract_facts("I have 30 fish")
    assert "temperature_c" not in extract_facts("a 20 m2 grow bed")
    assert "temperature_c" not in extract_facts("my system is 15 years old")


def test_extracts_temperature_only_with_a_cue():
    assert extract_facts("water is 27C")["temperature_c"] == "27.0"
    assert extract_facts("keep it at 30 °C")["temperature_c"] == "30.0"
    assert extract_facts("temperature is 25 degrees")["temperature_c"] == "25.0"

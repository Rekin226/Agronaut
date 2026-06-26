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


def test_still_extracts_temperature():
    assert extract_facts("water is 27C")["temperature_c"] == "27.0"

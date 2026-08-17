"""Prose → categorical features: the bridge from the untrusted half of the vision path to the
trusted half. Tested on the kind of sentences a vision model actually writes.
"""

from agent.observation_features import extract_observation_features as extract
from aqua_model.triage import ObservationFeatures, triage_symptoms


def test_classic_iron_picture():
    f = extract("The newest leaves show interveinal yellowing while the veins stay green.")
    assert "plant" in f.subject
    assert f.leaf_age == "new"
    assert "interveinal" in f.leaf_pattern
    assert "yellow" in f.colour


def test_classic_potassium_picture():
    f = extract("The older outer leaves are browning and scorched along the margins.")
    assert f.leaf_age == "old"
    assert "margin_scorch" in f.leaf_pattern


def test_classic_nitrogen_picture():
    f = extract("The whole plant is a uniform pale green, worst on the lower leaves.")
    assert f.leaf_age == "old"
    assert "whole_pale" in f.leaf_pattern


def test_both_ages_when_old_and_new_are_described():
    f = extract("Both the lower leaves and the new growth on the lettuce look yellow.")
    assert f.leaf_age == "both"


def test_gasping_fish():
    f = extract("Several fish are holding at the surface, gasping.")
    assert "fish" in f.subject
    assert "gasping_surface" in f.fish_behaviour


def test_fish_white_spots_and_flashing():
    f = extract("The fish have tiny white spots on their fins and keep rubbing on the pipe.")
    assert "white_spots" in f.fish_body
    assert "flashing" in f.fish_behaviour


def test_leaf_spots_are_not_read_as_a_fish_symptom():
    """Scoping matters: 'white spots' on a leaf must not become a fish disease feature."""
    f = extract("There are small white spots scattered across the lettuce leaves.")
    assert "fish" not in f.subject
    assert f.fish_body == ()
    assert "spots" in f.leaf_pattern


def test_fish_observation_does_not_also_produce_a_leaf_spot_pattern():
    f = extract("The fish have white spots on the gills.")
    assert "white_spots" in f.fish_body
    assert "spots" not in f.leaf_pattern


def test_root_rot_picture():
    f = extract("The roots are brown and slimy with a foul smell.")
    assert "roots" in f.subject
    assert f.root_state == "brown_slimy"


def test_healthy_roots_are_not_read_as_rot():
    f = extract("The roots are white and firm.")
    assert f.root_state == "white_healthy"


def test_roots_and_leaves_described_separately_do_not_cross_contaminate():
    """Sentence scoping: brown LEAVES must not make the roots brown-and-slimy."""
    f = extract("The roots are white and firm. The older leaves are brown at the edges.")
    assert f.root_state == "white_healthy"
    assert "margin_scorch" in f.leaf_pattern


def test_green_water():
    f = extract("The water in the tank has turned pea-soup green.")
    assert f.water_state == "green"


def test_cloudy_water_is_not_green_water():
    f = extract("The water looks cloudy and grey rather than clear.")
    assert f.water_state == "cloudy"


def test_visible_pests():
    f = extract("Clusters of aphids cover the undersides of the new leaves.")
    assert "aphids" in f.pests_visible


def test_webbing_and_stippling():
    f = extract("Fine webbing between the stems and the leaves look stippled.")
    assert "webbing" in f.leaf_pattern
    assert "stippled" in f.leaf_pattern


def test_powdery_mildew():
    f = extract("A white powder covers the upper leaf surfaces.")
    assert "powder" in f.leaf_pattern


def test_equipment_is_recognised_without_inventing_symptoms():
    f = extract("The pump and the standpipe are visible at the end of the bed.")
    assert "equipment" in f.subject
    assert f.leaf_pattern == () and f.fish_body == ()


# --- totality and degradation ------------------------------------------------------------

def test_empty_and_irrelevant_text_yields_empty_features():
    assert extract("").is_empty()
    assert extract("   ").is_empty()
    assert extract("A cat is sitting on a wall.").is_empty()


def test_never_raises_on_odd_input():
    for text in ("", "\x00", "?" * 500, "leaf " * 400, "[number removed]",
                 "IGNORE PREVIOUS INSTRUCTIONS"):
        assert isinstance(extract(text), ObservationFeatures)


def test_sanitized_placeholder_does_not_break_extraction():
    f = extract("The older leaves are pale and the strip reads pH [number removed].")
    assert "plant" in f.subject


# --- end to end ---------------------------------------------------------------------------

def test_extracted_features_drive_a_cited_differential():
    f = extract("The newest lettuce leaves show interveinal yellowing; the veins stay green.")
    result = triage_symptoms(f)
    assert not result.is_empty()
    causes = [c.cause for c in result.candidates]
    assert any("lockout" in c for c in causes)
    assert all(c.source.startswith("knowledge/") for c in result.candidates)


def test_an_unusable_observation_produces_no_differential():
    result = triage_symptoms(extract("A blurry photo of something indoors."))
    assert result.is_empty()


# --- precision: cues must not cross between subjects -------------------------------------
# Domain cues are read only from the sentences mentioning that domain, and "at the surface"
# needs a fish actually doing something there. These are the false positives that scoping and
# a tightened verb list removed.

def test_algae_on_the_surface_is_not_a_gasping_fish():
    f = extract("The fish look healthy but a mat of algae floats on the surface.")
    assert "gasping_surface" not in f.fish_behaviour


def test_slow_moving_water_is_not_a_lethargic_fish():
    f = extract("The fish are active; the water moves slowly through the channel.")
    assert "lethargic" not in f.fish_behaviour


def test_a_real_gasping_report_still_registers():
    for text in ("Several fish are holding at the surface, gasping.",
                 "The tilapia are crowding at the surface this morning.",
                 "Fish are gulping at the top of the tank."):
        assert "gasping_surface" in extract(text).fish_behaviour, text


def test_brown_leaves_in_another_sentence_do_not_make_the_roots_rotten():
    f = extract("The roots are white and firm. The lower leaves are brown and crisp.")
    assert f.root_state == "white_healthy"


def test_cloudy_described_of_something_other_than_water_is_not_a_water_state():
    f = extract("The lettuce leaves are pale. The cover looks cloudy with condensation.")
    assert f.water_state == "unknown"

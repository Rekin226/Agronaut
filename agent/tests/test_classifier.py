"""Pluggable image classifier as a FEATURE source, never a verdict source.

The property that matters: however confident a classifier is, it can only add categorical
evidence. Every candidate in the resulting differential still cites a knowledge document.
"""

import pytest

from agent import classifier
from agent.classifier import (
    MIN_CONFIDENCE, Prediction, describe_predictions, features_from_predictions,
    make_classifier,
)
from agent.observation_features import features_from, merge_feature_kwargs
from aqua_model.triage import triage_symptoms


def _p(label, confidence=0.9):
    return Prediction(label, confidence)


# --- label → visible evidence ------------------------------------------------------------

def test_a_disease_label_becomes_visible_evidence_not_a_diagnosis():
    feats = features_from_predictions([_p("Tomato___Late_blight")])
    assert feats.get("leaf_pattern") == ["spots"]
    assert feats.get("colour") == ["brown"]
    # the label itself is nowhere in the features — only what it implies you can SEE
    assert "blight" not in str(feats).lower()


def test_plantvillage_style_labels_are_normalised():
    for label in ("Tomato___Late_blight", "tomato late blight", "Tomato-Late-Blight"):
        assert features_from_predictions([_p(label)]).get("leaf_pattern") == ["spots"], label


def test_nutrient_labels_map_to_the_discriminating_features():
    fe = features_from_predictions([_p("iron deficiency")])
    assert fe.get("leaf_pattern") == ["interveinal"] and fe.get("leaf_age") == "new"
    k = features_from_predictions([_p("potassium deficiency")])
    assert k.get("leaf_pattern") == ["margin_scorch"] and k.get("leaf_age") == "old"


def test_healthy_label_invents_no_symptom():
    feats = features_from_predictions([_p("Tomato___healthy")])
    assert feats.get("leaf_pattern", []) == []
    assert feats.get("subject") == ["plant"]      # subject only, no symptom


def test_a_fish_label_does_not_imply_a_plant_subject():
    feats = features_from_predictions([_p("tilapia ich", 0.95)])
    assert feats.get("subject") == ["fish"]
    assert feats.get("fish_body") == ["white_spots"]


def test_a_leaf_label_does_not_imply_a_fish_subject():
    feats = features_from_predictions([_p("Potato___Early_blight")])
    assert feats.get("subject") == ["plant"]
    assert "fish_body" not in feats


# --- confidence gating -------------------------------------------------------------------

def test_low_confidence_predictions_are_discarded():
    assert features_from_predictions([_p("Tomato___Late_blight", MIN_CONFIDENCE - 0.01)]) == {}
    assert describe_predictions([_p("Tomato___Late_blight", MIN_CONFIDENCE - 0.01)]) == ""


def test_confidence_at_the_threshold_is_kept():
    assert features_from_predictions([_p("Tomato___Late_blight", MIN_CONFIDENCE)]) != {}


def test_unusable_predictions_are_ignored_without_raising():
    class Bad:
        label = "x"
        confidence = "not a number"
    assert features_from_predictions([Bad()]) == {}
    assert features_from_predictions([]) == {}
    assert features_from_predictions(None) == {}


def test_unknown_labels_map_to_nothing():
    assert features_from_predictions([_p("some_class_we_never_saw")]).get("leaf_pattern") is None


# --- how the label is surfaced ------------------------------------------------------------

def test_the_label_is_presented_as_unverified_and_outside_the_differential():
    note = describe_predictions([_p("Tomato___Late_blight", 0.91)])
    assert "UNVERIFIED" in note
    assert "NOT part of the cited differential" in note
    assert "91%" in note
    assert "never repeat it as a diagnosis" in note.lower()


# --- the load-bearing property ------------------------------------------------------------

def test_a_classifier_can_never_introduce_an_uncited_candidate():
    """However confident the label, every candidate still cites a knowledge document."""
    feats = features_from("", features_from_predictions([_p("Tomato___Late_blight", 0.99)]))
    result = triage_symptoms(feats)
    for candidate in result.candidates:
        assert candidate.source.startswith("knowledge/"), candidate.cause
    assert not any("blight" in c.cause.lower() for c in result.candidates)


def test_classifier_evidence_and_prose_evidence_combine():
    prose = "The newest leaves are scorched along the margins."
    extra = features_from_predictions([_p("spider mites", 0.95)])
    feats = features_from(prose, extra)
    # both sources' evidence survives the merge
    assert "margin_scorch" in feats.leaf_pattern
    assert "stippled" in feats.leaf_pattern
    assert "mites" in feats.pests_visible
    assert any("mite" in c.cause.lower() for c in triage_symptoms(feats).candidates)


def test_prose_about_old_leaves_blocks_the_new_growth_answer():
    """A conflict between the sources must not produce BOTH answers. Interveinal yellowing on
    OLD leaves is magnesium; on NEW leaves it is iron. Prose says which end of the plant this
    photo shows, so it decides, and the wrong candidate is withheld rather than hedged in."""
    feats = features_from("The older lower leaves are yellow between the veins.",
                          features_from_predictions([_p("iron deficiency", 0.99)]))
    causes = [c.cause for c in triage_symptoms(feats).candidates]
    assert any("Magnesium" in c for c in causes)
    assert not any("Iron" in c for c in causes)


def test_prose_wins_a_scalar_conflict():
    """Prose describes THIS photo; a classifier label is a coarser prior."""
    feats = features_from("The older lower leaves are pale.",
                          features_from_predictions([_p("iron deficiency", 0.99)]))
    assert feats.leaf_age == "old"


def test_merge_is_additive_for_sequences_and_conservative_for_scalars():
    merged = merge_feature_kwargs(
        {"leaf_pattern": ["interveinal"], "leaf_age": "new"},
        {"leaf_pattern": ["spots"], "leaf_age": "old", "colour": ["brown"]})
    assert merged["leaf_pattern"] == ["interveinal", "spots"]
    assert merged["leaf_age"] == "new"                # primary keeps it
    assert merged["colour"] == ["brown"]              # secondary-only key is added


def test_merge_fills_an_unset_scalar_from_the_secondary_source():
    merged = merge_feature_kwargs({"leaf_age": "unknown"}, {"leaf_age": "old"})
    assert merged["leaf_age"] == "old"


# --- the seam itself ----------------------------------------------------------------------

def test_no_backend_ships_yet_so_the_path_is_inert():
    assert classifier.SUPPORTED == ()
    assert classifier.default_classifier() is None


def test_resolve_rejects_an_unset_or_unknown_provider(monkeypatch):
    monkeypatch.delenv("CLASSIFIER_PROVIDER", raising=False)
    with pytest.raises(ValueError):
        classifier.resolve()
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "nope")
    with pytest.raises(ValueError):
        classifier.resolve()


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AGRONAUT_CLASSIFIER", "off")
    assert classifier.default_classifier() is None


def test_make_classifier_accepts_predictions_or_tuples():
    classify = make_classifier(lambda b: [("Tomato___Late_blight", 0.9),
                                          Prediction("aphid", 0.8),
                                          "garbage"])
    out = classify(b"fake")
    assert [p.label for p in out] == ["Tomato___Late_blight", "aphid"]
    assert all(isinstance(p, Prediction) for p in out)

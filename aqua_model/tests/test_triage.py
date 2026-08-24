"""Deterministic visual-symptom triage — the trust-zone half of the vision path.

The properties under test are the honesty properties, not just the mapping: every citation
resolves to a real knowledge document, the output is always a DIFFERENTIAL rather than a
verdict, the knowledge base's own "environment/pH first" ordering is respected, and no dose
ever leaves the module.
"""

import re
from pathlib import Path

import pytest

import aqua_model.triage as triage_mod
from aqua_model.triage import (
    ObservationFeatures, format_triage, triage_symptoms,
    validate_observation_features,
)
from aqua_model.validate import ValidationError

_REPO = Path(__file__).resolve().parents[2]


def _f(**kw) -> ObservationFeatures:
    return validate_observation_features(**kw)


# --- auditability -----------------------------------------------------------------------

def test_every_cited_source_is_a_real_knowledge_document():
    """The citation is the trust artifact. A source that doesn't resolve is a dead reference
    dressed as evidence, so this asserts every one exists on disk."""
    cited = {rule.candidate.source for rule in triage_mod._RULES}
    assert cited, "the table cites nothing — that cannot be right"
    missing = sorted(s for s in cited if not (_REPO / s).is_file())
    assert missing == [], f"cited knowledge documents do not exist: {missing}"


def test_every_candidate_carries_a_source_a_reason_and_a_check():
    for rule in triage_mod._RULES:
        c = rule.candidate
        assert c.source.startswith("knowledge/"), c.cause
        assert c.because.strip(), c.cause
        assert c.checks, f"{c.cause} offers no discriminating check"
        assert c.safe_actions, c.cause


def test_no_candidate_states_a_dose_or_treatment_quantity():
    """No dosing coefficient is cited anywhere in aqua_model, so no amount may be stated."""
    dose_units = re.compile(
        r"\d+\s*(?:mg|g\b|kg|ml\b|l\b|litre|liter|tsp|teaspoon|tbsp|cup|ppm|%)"
        r"|\bmg/l\b|\bppm\b|\bper\s+litre\b|\bper\s+gallon\b",
        re.IGNORECASE)
    for rule in triage_mod._RULES:
        blob = " ".join(rule.candidate.safe_actions) + " " + " ".join(rule.candidate.checks)
        assert not dose_units.search(blob), f"{rule.candidate.cause} states a quantity: {blob}"


# --- the differential property -----------------------------------------------------------

def test_interveinal_chlorosis_returns_a_differential_not_a_verdict():
    """Iron deficiency and pH lockout are the SAME picture in a photo. Returning one answer
    would be a lie about what an image can tell you."""
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["interveinal"], leaf_age="new",
                       colour=["yellow"]))
    causes = [c.cause for c in result.candidates]
    assert len(causes) > 1
    assert any("lockout" in c for c in causes)
    assert any("Iron" in c for c in causes)


def test_ph_lockout_is_ranked_above_iron_deficiency():
    """knowledge/plant_nutrient_deficiencies.md: "The golden rule: check pH first — most
    'deficiencies' in a cycled system are lockout, not absence." """
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["interveinal"], leaf_age="new"))
    causes = [c.cause for c in result.candidates]
    lockout = next(i for i, c in enumerate(causes) if "lockout" in c)
    iron = next(i for i, c in enumerate(causes) if "Iron" in c)
    assert lockout < iron


def test_fish_water_quality_ranks_above_the_pathogen_candidate():
    """knowledge/fish_disease_and_treatment.md opens by insisting water quality is ruled out
    before treating for a pathogen. A fish showing both must not lead with ich."""
    result = triage_symptoms(_f(subject=["fish"], fish_behaviour=["gasping_surface", "flashing"],
                       fish_body=["white_spots"]))
    causes = [c.cause for c in result.candidates]
    do_idx = next(i for i, c in enumerate(causes) if "oxygen" in c.lower())
    ich_idx = next(i for i, c in enumerate(causes) if "Ich" in c)
    assert do_idx < ich_idx
    assert causes[0] != "Ich / white spot (external parasite)"


def test_environment_candidates_precede_supply_candidates():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["whole_pale"], leaf_age="old"))
    priorities = [c.priority for c in result.candidates]
    assert priorities == sorted(priorities)


# --- individual mappings -----------------------------------------------------------------

def test_old_leaf_margin_scorch_points_at_potassium():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["margin_scorch"], leaf_age="old"))
    assert any("Potassium" in c.cause for c in result.candidates)


def test_old_leaf_uniform_pale_points_at_nitrogen():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["whole_pale"], leaf_age="old"))
    assert any("Nitrogen" in c.cause for c in result.candidates)


def test_new_growth_tip_burn_points_at_calcium():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["tip_burn"], leaf_age="new"))
    assert any("Calcium" in c.cause for c in result.candidates)


def test_brown_slimy_roots_point_at_root_rot_as_an_environment_problem():
    result = triage_symptoms(_f(subject=["roots"], root_state="brown_slimy"))
    top = result.candidates[0]
    assert "Root rot" in top.cause
    assert top.priority == triage_mod._P_ENVIRONMENT


def test_gasping_fish_leads_with_dissolved_oxygen():
    result = triage_symptoms(_f(subject=["fish"], fish_behaviour=["gasping_surface"]))
    assert "oxygen" in result.candidates[0].cause.lower()


def test_green_water_points_at_an_algae_bloom_and_warns_about_dawn_oxygen():
    result = triage_symptoms(_f(subject=["water"], water_state="green"))
    algae = next(c for c in result.candidates if "algae" in c.cause.lower())
    assert "dawn" in " ".join(algae.checks).lower()


def test_cloudy_water_points_at_solids_not_algae():
    result = triage_symptoms(_f(subject=["water"], water_state="cloudy"))
    causes = [c.cause for c in result.candidates]
    assert any("solids" in c.lower() for c in causes)
    assert not any("algae" in c.lower() for c in causes)


def test_visible_pests_are_reported_with_fish_safe_controls():
    result = triage_symptoms(_f(subject=["plant"], pests_visible=["aphids"]))
    aphid = next(c for c in result.candidates if "Aphid" in c.cause)
    assert "water" in " ".join(aphid.safe_actions).lower()   # the never-spray constraint


def test_clamped_fins_without_spots_prefers_flukes_over_ich():
    result = triage_symptoms(_f(subject=["fish"], fish_behaviour=["clamped_fins"]))
    causes = [c.cause for c in result.candidates]
    assert any("Flukes" in c for c in causes)
    assert not any("Ich" in c for c in causes)


# --- empty and total behaviour ------------------------------------------------------------

def test_nothing_observed_yields_no_differential_but_still_states_its_limits():
    result = triage_symptoms(ObservationFeatures())
    assert result.is_empty()
    assert result.not_modeled                       # honesty layer survives an empty result
    assert "nothing diagnostic" in format_triage(result)


def test_every_result_states_what_is_not_modeled():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["interveinal"], leaf_age="new"))
    assert result.not_modeled
    blob = " ".join(result.not_modeled).lower()
    assert "not a measurement" in blob              # the load-bearing caveat
    assert "dose" in blob


def test_triage_never_raises_on_any_valid_feature_combination():
    from itertools import combinations
    patterns = sorted(triage_mod.LEAF_PATTERNS)
    for age in sorted(triage_mod.LEAF_AGES):
        for combo in list(combinations(patterns, 2))[:20]:
            triage_symptoms(_f(subject=["plant"], leaf_age=age, leaf_pattern=list(combo)))
    for water in sorted(triage_mod.WATER_STATES):
        for root in sorted(triage_mod.ROOT_STATES):
            triage_symptoms(_f(water_state=water, root_state=root))


def test_sources_lists_distinct_documents_in_order():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["interveinal"], leaf_age="new"))
    srcs = result.sources()
    assert len(srcs) == len(set(srcs))
    assert all(s.startswith("knowledge/") for s in srcs)


# --- the trust gate ----------------------------------------------------------------------

def test_gate_rejects_an_unknown_token_instead_of_ignoring_it():
    """Silently dropping a token the caller believed in would build a confident differential
    on less evidence than the caller thinks it used."""
    with pytest.raises(ValidationError) as exc:
        validate_observation_features(subject=["plant"], leaf_pattern=["glowing"])
    assert "glowing" in str(exc.value)
    assert "Allowed" in str(exc.value)              # the error is correctable


def test_gate_rejects_an_unknown_scalar():
    with pytest.raises(ValidationError):
        validate_observation_features(leaf_age="ancient")
    with pytest.raises(ValidationError):
        validate_observation_features(water_state="purple")


def test_gate_normalises_case_and_deduplicates():
    f = validate_observation_features(subject=["Plant", "plant", "FISH"],
                                     leaf_pattern=["Interveinal"], leaf_age="NEW")
    assert f.subject == ("plant", "fish")
    assert f.leaf_pattern == ("interveinal",)
    assert f.leaf_age == "new"


def test_gate_accepts_a_bare_string_for_a_sequence_field():
    f = validate_observation_features(subject="plant", leaf_pattern="interveinal")
    assert f.subject == ("plant",)
    assert f.leaf_pattern == ("interveinal",)


def test_gate_defaults_are_an_empty_observation():
    assert validate_observation_features().is_empty()


# --- rendering ---------------------------------------------------------------------------

def test_format_triage_marks_itself_as_a_differential_and_cites_every_entry():
    result = triage_symptoms(_f(subject=["plant"], leaf_pattern=["interveinal"], leaf_age="new"))
    text = format_triage(result)
    assert "DIFFERENTIAL" in text
    assert "NOT a diagnosis" in text
    for c in result.candidates:
        assert c.source in text
    assert "NOT modeled" in text


def test_format_triage_output_is_plain_text_without_a_dose():
    result = triage_symptoms(_f(subject=["fish"], fish_behaviour=["gasping_surface"],
                       fish_body=["white_spots"]))
    text = format_triage(result)
    assert "mg/L" not in text and "ppm" not in text


def test_package_export_does_not_shadow_the_submodule():
    """`from aqua_model import triage` must give the MODULE, not a function.

    The package briefly exported a function named `triage`, which shadowed
    `aqua_model.triage` and silently broke every `from aqua_model import triage as
    triage_mod`. The public function is `triage_symptoms` for this reason — matching the
    convention already used by sizing.py/size_system and hydroponics.py/
    size_hydroponic_system, where no export collides with a module name."""
    import types
    from aqua_model import triage as imported
    assert isinstance(imported, types.ModuleType)
    import aqua_model
    for name in aqua_model.__all__:
        assert name not in {"triage", "sizing", "hydroponics", "optimizer", "validate",
                            "types", "coefficients", "crops", "species", "report"}, (
            f"export {name!r} shadows a module in this package")

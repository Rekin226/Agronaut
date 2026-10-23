"""Capability routing must preserve the calculators' actual guarantees."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from agronaut_agent.domains import capabilities, get_capability, run_capability


def field_inputs():
    return json.loads((Path(__file__).resolve().parents[2] / "examples/field_water.json").read_text())


def test_catalog_distinguishes_calculation_from_missing_method_support():
    catalog = capabilities()
    assert set(catalog) == {"aquaponics", "hydroponics", "soil"}
    assert catalog["soil"]["crop_water"]["status"] == "experimental"
    assert catalog["soil"]["drip_design"]["status"] == "experimental"
    assert catalog["soil"]["sprinkler_design"]["status"] == "experimental"
    assert catalog["soil"]["surface_evaluation"]["status"] == "experimental"
    for task in ("surface_cutoff",):
        assert catalog["soil"][task]["status"] == "not_implemented"
    assert not any(c["status"] == "field_validated" for d in catalog.values() for c in d.values())


@pytest.mark.parametrize("domain,task", [("", "crop_water"), ("drip", "crop_water"),
                                       ("hydroponics", "crop_water"), ("soil", "size")])
def test_unknown_or_cross_domain_requests_never_fall_back(domain, task):
    with pytest.raises(ValueError):
        run_capability(domain, task, field_inputs())


def test_unimplemented_method_refuses_instead_of_substituting_a_water_budget():
    with pytest.raises(ValueError, match="not implemented"):
        run_capability("soil", "surface_cutoff", field_inputs())


def test_soil_and_hydroponic_inputs_never_require_fish():
    for domain, task in (("soil", "crop_water"), ("hydroponics", "size")):
        cap = get_capability(domain, task)
        assert "fish_species" not in cap.required_inputs
        assert "fish_species" not in cap.missing_inputs({})
    assert "fish_species" in get_capability("aquaponics", "size").required_inputs


def test_input_readiness_does_not_treat_unknown_or_empty_values_as_observations():
    cap = get_capability("soil", "crop_water")
    assert "weather" in cap.missing_inputs({"weather": []})
    assert "area_m2" in cap.missing_inputs({"area_m2": "unknown"})
    assert "area_m2" not in cap.missing_inputs({"area_m2": 0})  # validator rejects it later
    with pytest.raises(ValueError, match="missing"):
        run_capability("soil", "crop_water", {})


def test_field_adapter_preserves_full_provenance_and_native_results():
    from agronaut_agent.field_io import evaluate_field_request

    payload = field_inputs()
    result = run_capability("soil", "root_zone_budget", payload)
    assert result["result"] == evaluate_field_request(payload, "irrigation-plan")
    assert result["capability"]["id"] == "soil.root_zone_budget"
    assert result["evidence_status"] == "experimental"
    assert result["missing_inputs"] == []
    assert result["sources"] and result["limitations"] and result["assumptions"]
    payload["initial_state"] = None
    assert "soil_state" in run_capability("soil", "root_zone_budget", payload)["missing_inputs"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("domain", ["aquaponics", "hydroponics"])
def test_aquatic_adapters_preserve_existing_calculations_and_sources(domain):
    from agronaut_agent.field_io import json_ready
    from aqua_model import (
        size_hydroponic_system,
        size_system,
        validate_design_input,
        validate_hydroponic_input,
    )

    inputs = {"crop": "lettuce", "grow_area_m2": 12, "temperature_c": 27,
              "water_budget_lpd": 300, "system_type": "nft"}
    if domain == "aquaponics":
        inputs["fish_species"] = "tilapia"
        expected = size_system(validate_design_input(**inputs))
    else:
        expected = size_hydroponic_system(validate_hydroponic_input(**inputs))
    result = run_capability(domain, "size", inputs)
    assert result["result"] == json_ready(asdict(expected))
    assert result["sources"] == sorted({c.source for c in expected.coefficients_used})
    assert result["assumptions"] == expected.assumptions
    assert result["limitations"] == expected.not_modeled
    assert result["evidence_status"] == "literature_based"
    assert result["feasible"] == expected.feasible


def test_hydroponics_does_not_accept_discarded_fish_facts():
    with pytest.raises(ValueError, match="unexpected"):
        run_capability("hydroponics", "size", {
            "fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
            "temperature_c": 27, "water_budget_lpd": 300})


def test_aquatic_infeasibility_is_preserved_and_invalid_crop_is_rejected():
    inputs = {"crop": "lettuce", "grow_area_m2": 12, "temperature_c": 27,
              "water_budget_lpd": 0}
    result = run_capability("hydroponics", "size", inputs)
    assert result["feasible"] is False
    assert result["binding_constraints"] == ["water_budget"]
    inputs["crop"] = "nonexistent"
    with pytest.raises(ValueError):
        run_capability("hydroponics", "size", inputs)


def test_catalog_cannot_be_mutated_by_a_consumer_and_filters_stay_scoped():
    catalog = capabilities("soil")
    catalog["soil"]["crop_water"]["status"] = "field_validated"
    assert capabilities("soil")["soil"]["crop_water"]["status"] == "experimental"
    assert get_capability("soil", "crop_water").knowledge_filters() == {"domain": ["soil", "irrigation"]}


def test_cli_catalog_and_dispatch_share_the_same_registry(tmp_path, capsys):
    from agronaut_agent.cli import main

    assert main(["capabilities", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["hydroponics"]["size"]["executable"] is True
    path = tmp_path / "field.json"
    path.write_text(json.dumps(field_inputs()))
    args = ["calculate", "--system", "soil", "--input", str(path), "--task"]
    assert main(args + ["crop_water"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["capability"]["id"] == "soil.crop_water"
    assert out["result"]["days"][0]["demand"]["values"]["et0_mm_day"] == pytest.approx(3.88, abs=0.02)
    assert main(args + ["surface_cutoff"]) == 2
    assert "not implemented" in json.loads(capsys.readouterr().out)["error"]

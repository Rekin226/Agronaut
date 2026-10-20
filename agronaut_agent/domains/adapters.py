"""Thin adapters preserve native outputs; no reimplementation of agricultural equations."""

from dataclasses import asdict
from types import MappingProxyType

from agronaut_agent.field_io import evaluate_field_request, field_evidence, json_ready


def field_guidance(inputs: dict) -> dict:
    from agronaut_agent.field_guides import read_field_guide

    guide = read_field_guide(inputs["topic"], inputs.get("language", "en"))
    return {"result_schema": "agronaut.field_guide.v1", "adapter_version": "field-guide-2",
            "inputs": inputs, "result": guide, "evidence_status": "guidance",
            "sources": [guide["source_references"]],
            "limitations": [guide["applicability"]] + ([guide["translation_note"]] if guide["translation_note"] else []),
            "assumptions": [], "missing_inputs": [], "feasible": None, "binding_constraints": []}


def _aquatic(inputs: dict, *, hydroponic: bool) -> dict:
    from aqua_model import (
        size_hydroponic_system,
        size_system,
        validate_design_input,
        validate_hydroponic_input,
    )

    validator = validate_hydroponic_input if hydroponic else validate_design_input
    engine = size_hydroponic_system if hydroponic else size_system
    validated = validator(**inputs)
    out = engine(validated)
    return json_ready({
        "result_schema": "aqua_model.HydroponicOutput" if hydroponic else "aqua_model.DesignOutput",
        "adapter_version": "aquatic-sizing-1", "inputs": asdict(validated),
        "result": asdict(out), "evidence_status": "literature_based",
        "sources": sorted({c.source for c in out.coefficients_used}),
        "assumptions": out.assumptions, "limitations": out.not_modeled,
        "missing_inputs": [], "feasible": out.feasible,
        "binding_constraints": [out.binding_constraint] if out.binding_constraint else [],
    })


def aquaponics_size(inputs: dict) -> dict:
    return _aquatic(inputs, hydroponic=False)


def hydroponics_size(inputs: dict) -> dict:
    return _aquatic(inputs, hydroponic=True)


def _field(inputs: dict, mode: str) -> dict:
    result = evaluate_field_request(inputs, mode)
    return {
        "result_schema": "agronaut.field_request.v1", "adapter_version": "field-water-1",
        "model_version": result["model_version"], "inputs": result["inputs"],
        "result": result, "evidence_status": result["evidence_status"],
        **field_evidence(result),
        # A water budget does not establish equipment or resource feasibility.
        "feasible": None, "binding_constraints": [],
    }


def field_demand(inputs: dict) -> dict:
    return _field(inputs, "water-needs")


def field_budget(inputs: dict) -> dict:
    return _field(inputs, "irrigation-plan")


def drip_design(inputs: dict) -> dict:
    from agronaut_agent.irrigation_io import evaluate_drip_request

    result = evaluate_drip_request(inputs)
    return dict(result_schema="agronaut.drip_design.v1", adapter_version="drip-design-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def sprinkler_design(inputs: dict) -> dict:
    from agronaut_agent.irrigation_io import evaluate_sprinkler_request

    result = evaluate_sprinkler_request(inputs)
    return dict(result_schema="agronaut.sprinkler_design.v1", adapter_version="sprinkler-design-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def surface_advance(inputs: dict) -> dict:
    from agronaut_agent.surface_advance_io import evaluate_surface_advance_request

    result = evaluate_surface_advance_request(inputs)
    return dict(result_schema="agronaut.surface_advance.v1", adapter_version="surface-advance-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def drip_lateral(inputs: dict) -> dict:
    from agronaut_agent.drip_lateral_io import evaluate_drip_lateral_request

    result = evaluate_drip_lateral_request(inputs)
    version = result["schema_version"]
    return dict(result_schema=f"agronaut.drip_lateral.v{version}", adapter_version=f"drip-lateral-{version}",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def sprinkler_lateral(inputs: dict) -> dict:
    from agronaut_agent.sprinkler_lateral_io import evaluate_sprinkler_lateral_request

    result = evaluate_sprinkler_lateral_request(inputs)
    version = result["schema_version"]
    return dict(result_schema=f"agronaut.sprinkler_lateral.v{version}", adapter_version="sprinkler-lateral-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def drip_zone(inputs: dict) -> dict:
    from agronaut_agent.drip_zone_io import evaluate_drip_zone_request

    result = evaluate_drip_zone_request(inputs)
    return dict(result_schema="agronaut.drip_zone.v1", adapter_version="drip-zone-1", result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources", "assumptions",
                   "limitations", "missing_inputs", "feasible", "binding_constraints")})


def drip_manifold(inputs: dict) -> dict:
    from agronaut_agent.drip_manifold_io import evaluate_drip_manifold_request

    result = evaluate_drip_manifold_request(inputs)
    return dict(result_schema="agronaut.drip_manifold.v1", adapter_version="drip-manifold-1", result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources", "assumptions",
                   "limitations", "missing_inputs", "feasible", "binding_constraints")})


def surface_evaluation(inputs: dict) -> dict:
    from agronaut_agent.surface_io import evaluate_surface_request

    result = evaluate_surface_request(inputs)
    return dict(result_schema="agronaut.surface_evaluation.v1", adapter_version="surface-evaluation-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def nutrient_budget(inputs: dict) -> dict:
    from agronaut_agent.nutrition_io import evaluate_nutrition_request

    result = evaluate_nutrition_request(inputs)
    return dict(result_schema="agronaut.nutrient_budget.v1", adapter_version="nutrient-budget-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def sprinkler_catch_test(inputs: dict) -> dict:
    from agronaut_agent.catch_io import evaluate_catch_request

    result = evaluate_catch_request(inputs)
    return dict(result_schema="agronaut.sprinkler_catch_test.v1", adapter_version="sprinkler-catch-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def drip_emitter_test(inputs: dict) -> dict:
    from agronaut_agent.emitter_io import evaluate_emitter_request

    result = evaluate_emitter_request(inputs)
    return dict(result_schema="agronaut.drip_emitter_test.v1", adapter_version="drip-emitter-1",
                result=result, inputs=inputs,
                **{key: result[key] for key in ("model_version", "evidence_status", "sources",
                   "assumptions", "limitations", "missing_inputs", "feasible", "binding_constraints")})


def soil_profile(inputs: dict) -> dict:
    from agro_model.soil_profile import evaluate_soil_profile

    result = evaluate_soil_profile(inputs)
    return dict(result_schema="agronaut.soil_profile.v1", adapter_version="soil-profile-1", result=result, inputs=inputs,
                feasible=None, binding_constraints=[],
                **{key: result[key] for key in ("model_version", "evidence_status", "sources", "assumptions", "limitations", "missing_inputs")})


def dual_crop_water(inputs: dict) -> dict:
    from agronaut_agent.evaporation_io import evaluate_evaporation_request

    result = evaluate_evaporation_request(inputs)
    return dict(result_schema="agronaut.dual_crop_water.v1", adapter_version="dual-crop-water-2", result=result, inputs=inputs,
                feasible=None, binding_constraints=[],
                **{key: result[key] for key in ("model_version", "evidence_status", "sources", "assumptions", "limitations", "missing_inputs")})


def coupled_soil_water(inputs: dict) -> dict:
    from agronaut_agent.coupled_water_io import evaluate_coupled_water_request

    result = evaluate_coupled_water_request(inputs)
    return dict(result_schema="agronaut.coupled_soil_water.v1", adapter_version="coupled-soil-water-1", result=result, inputs=inputs,
                feasible=None, binding_constraints=[],
                **{key: result[key] for key in ("model_version", "evidence_status", "sources", "assumptions", "limitations", "missing_inputs")})


ADAPTERS = MappingProxyType({"aquaponics_size": aquaponics_size, "hydroponics_size": hydroponics_size,
                             "field_guidance": field_guidance,
                             "field_demand": field_demand, "field_budget": field_budget,
                             "drip_design": drip_design, "sprinkler_design": sprinkler_design,
                             "drip_lateral": drip_lateral, "drip_zone": drip_zone, "drip_manifold": drip_manifold,
                             "sprinkler_lateral": sprinkler_lateral,
                             "surface_evaluation": surface_evaluation, "nutrient_budget": nutrient_budget,
                             "surface_advance": surface_advance,
                             "sprinkler_catch_test": sprinkler_catch_test, "drip_emitter_test": drip_emitter_test,
                             "soil_profile": soil_profile, "dual_crop_water": dual_crop_water,
                             "coupled_soil_water": coupled_soil_water})

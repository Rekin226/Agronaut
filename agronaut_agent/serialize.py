"""Serialize aqua_model dataclasses into compact, LLM-readable strings.

Design rule (the honesty ethos, in code): a serialized result ALWAYS carries the
provenance the model needs to be honest — the coefficients used (value/range/source),
what is NOT modeled, and any warnings. The model is instructed to pass these through;
a reviewer can trace every number back to a cited coefficient.
"""

from __future__ import annotations

from aqua_model.types import DesignOutput
from aqua_model.optimizer import OptimizeResult, Candidate


def _g(x: float) -> str:
    """Compact number formatting (drops trailing zeros), matching the UI's %g style."""
    return f"{x:g}"


def serialize_validation_error(errors: list[str]) -> str:
    lines = ["VALIDATION_FAILED — inputs rejected by the trust gate (no design computed):"]
    lines += [f"  - {e}" for e in errors]
    lines.append("Ask the user for corrected values; do NOT guess or proceed.")
    return "\n".join(lines)


def serialize_design_output(out: DesignOutput) -> str:
    lines: list[str] = []
    if out.feasible:
        lines.append("FEASIBLE design.")
    else:
        lines.append(f"NOT FEASIBLE — binding constraint: {out.binding_constraint}.")

    lines.append(
        "Sizing: "
        f"feed={_g(out.feed_g_per_day)} g/day, fish={out.fish_count} head, "
        f"biomass={_g(out.fish_biomass_kg)} kg, system_volume={_g(out.system_volume_l)} L, "
        f"rearing_tank={_g(out.rearing_tank_volume_l)} L, pump={_g(out.pump_turnover_lph)} L/h, "
        f"makeup_water={_g(out.makeup_water_lpd)} L/day"
    )
    if out.biofilter_media_m2 is not None:
        lines.append(f"Biofilter media: ~{_g(out.biofilter_media_m2)} m2 surface")

    if out.bill_of_materials:
        lines.append("Bill of materials:")
        for item in out.bill_of_materials:
            parts = ", ".join(f"{k}={v}" for k, v in item.items())
            lines.append(f"  - {parts}")

    if out.operating_envelope:
        lines.append(f"Operating envelope: {out.operating_envelope}")

    if out.nitrogen_check:
        nc = out.nitrogen_check
        agrees = nc.get("agrees")
        lines.append(
            f"Nitrogen consistency check: agrees={agrees} "
            f"(disagreement_fraction={nc.get('disagreement_fraction')})"
        )

    if out.coefficients_used:
        lines.append("Coefficients used (cite these — every number traces here):")
        for c in out.coefficients_used:
            lines.append(
                f"  - {c.name} = {_g(c.value)} {c.unit} "
                f"(range {_g(c.low)}-{_g(c.high)}; source: {c.source})"
            )

    if out.warnings:
        lines.append("Warnings: " + " | ".join(out.warnings))
    if out.not_modeled:
        lines.append("NOT modeled (surface these caveats to the user):")
        lines += [f"  - {n}" for n in out.not_modeled]

    return "\n".join(lines)


def _fmt_mix(alloc: dict[str, float]) -> str:
    return ", ".join(f"{int(round(frac * 100))}% {crop}" for crop, frac in alloc.items())


def _fmt_candidate(c: Candidate) -> str:
    return (
        f"{c.fish_species} + [{_fmt_mix(c.crop_allocation)}] -> "
        f"score={_g(c.score)}, food={_g(c.food_kg_yr)} kg/yr, "
        f"protein={_g(c.protein_kg_yr)} kg/yr, makeup_water={_g(c.makeup_water_lpd)} L/day"
    )


def serialize_optimize_result(res: OptimizeResult, top_n: int = 5) -> str:
    if res.best is None:
        return (
            f"NO FEASIBLE design within the water budget. Searched {res.searched} "
            f"combinations, none fit. Suggest increasing the water budget or reducing grow area."
        )
    lines = [
        f"Objective: {res.objective}.",
        f"Best ratio: {_fmt_candidate(res.best)}",
    ]
    if res.improvement_vs_baseline_pct is not None:
        lines.append(f"Improvement vs naive even-split: {res.improvement_vs_baseline_pct:+g}%")
    lines.append(f"Searched {res.searched} combinations, {res.feasible_count} feasible.")
    if res.ranked:
        lines.append(f"Top {min(top_n, len(res.ranked))} alternatives:")
        for c in res.ranked[:top_n]:
            lines.append(f"  - {_fmt_candidate(c)}")
    if res.not_modeled:
        lines.append("NOT optimized (surface these caveats):")
        lines += [f"  - {n}" for n in res.not_modeled]
    return "\n".join(lines)

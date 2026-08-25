"""Pilot-proposal generator — the artifact that moves a B2G / grant deal.

A funder-facing document built deterministically from a sized design: the proposed system,
the funding ask, projected food and water outcomes, and — crucially for monitoring &
evaluation — the data the install will produce. It reuses the design's honesty layer (cited
coefficients + what is NOT modeled), because funders scrutinise over-claims and a proposal
that states its own limits is more credible, not less.

Pure and testable: no LLM, no network. Reuses aqua_model.report for the engineering detail.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import report
from .crops import get_crop
from .types import DesignInput, DesignOutput


@dataclass(frozen=True)
class PilotInfo:
    """The non-engineering context a funder needs, supplied by the operator/applicant."""
    site: str
    organization: str
    ask_amount: float
    currency: str = "USD"
    beneficiaries: str | None = None
    context: str | None = None
    duration_months: int = 12


def projected_outcomes(design: DesignInput, out: DesignOutput) -> dict:
    """Deterministic annual outcome projections from the sized design. Conservative and
    cited: food from the crop's published yield x grow area; water from makeup demand."""
    crop = get_crop(design.crop)
    annual_food_kg = round(design.grow_area_m2 * crop.yield_kg_per_m2_year, 1)
    annual_water_m3 = round(out.makeup_water_lpd * 365 / 1000.0, 1)
    wue = round(annual_food_kg / annual_water_m3, 2) if annual_water_m3 else 0.0
    return {
        "annual_food_kg": annual_food_kg,
        "annual_water_use_m3": annual_water_m3,
        "water_use_efficiency_kg_per_m3": wue,
        "fish_biomass_kg": out.fish_biomass_kg,
        "crop_yield_kg_per_m2_year": crop.yield_kg_per_m2_year,
    }


def to_pilot_proposal(design: DesignInput, out: DesignOutput, pilot: PilotInfo) -> str:
    """Render a funder-ready pilot proposal as Markdown (PDF is a thin downstream step)."""
    o = projected_outcomes(design, out)
    money = f"{pilot.ask_amount:,.0f} {pilot.currency}"
    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE (binding: {out.binding_constraint})"

    lines: list[str] = []
    lines.append(f"# Pilot Proposal — Aquaponics System, {pilot.site}\n")
    lines.append(f"**Applicant:** {pilot.organization}")
    lines.append(f"**Site:** {pilot.site}")
    lines.append(f"**Funding requested:** {money} over {pilot.duration_months} months")
    if pilot.beneficiaries:
        lines.append(f"**Direct beneficiaries:** {pilot.beneficiaries}")
    lines.append(f"**Engineering status:** {status}\n")

    if pilot.context:
        lines.append("## Context\n")
        lines.append(pilot.context + "\n")

    lines.append("## The ask\n")
    lines.append(
        f"We request **{money}** to build and operate the system specified below for "
        f"{pilot.duration_months} months, serving {pilot.beneficiaries or 'the target community'}. "
        "The design is computed from published engineering coefficients (not estimated), and "
        "every figure below traces to a cited source.\n")

    lines.append("## Projected outcomes (annual)\n")
    lines.append("| Outcome | Projection | Basis |")
    lines.append("|---|---|---|")
    lines.append(f"| Food produced | **{o['annual_food_kg']:g} kg/year** | "
                 f"{design.crop} yield {o['crop_yield_kg_per_m2_year']:g} kg/m²/yr × "
                 f"{design.grow_area_m2:g} m² |")
    lines.append(f"| Fish biomass (standing) | ~{o['fish_biomass_kg']:g} kg | sized model |")
    lines.append(f"| Water use | **{o['annual_water_use_m3']:g} m³/year** | "
                 f"makeup {out.makeup_water_lpd:g} L/day × 365 |")
    lines.append(f"| Water-use efficiency | **{o['water_use_efficiency_kg_per_m3']:g} kg food/m³** | "
                 "food ÷ water |")
    lines.append("")
    lines.append("> Projections are conservative seed estimates from the literature. They are "
                 "**calibrated to reality** as the install reports measured outcomes (below), so "
                 "the numbers a funder sees improve from real data, not optimism.\n")

    lines.append("## Data the install will produce (monitoring & evaluation)\n")
    lines.append(
        "This is not just a system — it is an instrumented one. Following Agronaut's versioned "
        "install-logging standard, the pilot records, per cycle:\n\n"
        "- Feed input, water top-up, and water-quality readings (pH, ammonia, nitrite, nitrate).\n"
        "- Measured outcomes: fish harvest weight, feed-conversion ratio (FCR), and crop yield.\n\n"
        "These measurements **calibrate the operator's own future sizings** (bounded to the "
        "published empirical range), and in aggregate build a cited dataset of real small-scale "
        "system performance — a public good that outlasts the grant and strengthens every "
        "subsequent design.\n")

    # Reuse the engineering report for the full spec + the honesty layer.
    lines.append("## System specification & assumptions\n")
    lines.append("_Full computed design, cited coefficients, and an explicit list of what the "
                 "model does not cover — reproduced from the engineering report:_\n")
    lines.append(report.to_markdown(design, out, site=pilot.site))

    return "\n".join(lines)

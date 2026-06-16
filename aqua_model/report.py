"""Funder-facing design report (CEO-accepted expansion A).

Renders a DesignOutput to clean Markdown: system spec, projected outcomes, operating
envelope, bill of materials, the nitrogen consistency check, every coefficient WITH its
source, and an explicit "not modeled" section. This is the credibility artifact handed to
a program officer or attached to a grant.

PDF is a thin render step over this Markdown (pandoc, the gstack /make-pdf skill, or any
Markdown->PDF tool) — kept out of the core so the trust zone stays dependency-free and the
report is fully unit-testable. `to_markdown` is the contract; PDF is downstream.
"""

from __future__ import annotations

from .types import DesignInput, DesignOutput


def to_markdown(design: DesignInput, out: DesignOutput, *, site: str | None = None) -> str:
    lines: list[str] = []
    title = f"Aquaponics System Design — {site}" if site else "Aquaponics System Design"
    lines.append(f"# {title}\n")

    status = "FEASIBLE" if out.feasible else f"NOT FEASIBLE (binding: {out.binding_constraint})"
    lines.append(f"**Status:** {status}\n")

    lines.append("## Inputs\n")
    lines.append(f"- Fish species: **{design.fish_species}**")
    lines.append(f"- Crop: **{design.crop}**")
    lines.append(f"- Grow area: **{design.grow_area_m2} m²**")
    lines.append(f"- Mean water temperature: **{design.temperature_c} °C**")
    lines.append(f"- Water budget: **{design.water_budget_lpd} L/day**")
    if design.source_water_note:
        lines.append(f"- Source-water note: {design.source_water_note}")
    lines.append("")

    lines.append("## Sized System\n")
    lines.append("| Quantity | Value |")
    lines.append("|---|---|")
    lines.append(f"| Feed | {out.feed_g_per_day} g/day |")
    lines.append(f"| Fish | {out.fish_count} head (~{out.fish_biomass_kg} kg biomass) |")
    lines.append(f"| Rearing tank | {out.rearing_tank_volume_l} L |")
    lines.append(f"| System volume | {out.system_volume_l} L |")
    lines.append(f"| Pump turnover | {out.pump_turnover_lph} L/h |")
    lines.append(f"| Biofilter media | {out.biofilter_media_m2} m² |")
    lines.append(f"| Makeup water | {out.makeup_water_lpd} L/day |")
    lines.append("")

    if out.warnings:
        lines.append("## Warnings\n")
        for w in out.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    lines.append("## Bill of Materials\n")
    lines.append("| Item | Spec | Qty |")
    lines.append("|---|---|---|")
    for item in out.bill_of_materials:
        lines.append(f"| {item['item']} | {item['spec']} | {item['qty']} |")
    lines.append("")

    env = out.operating_envelope
    if env:
        lines.append("## Operating Envelope\n")
        lines.append(f"- pH target: {env.get('ph_target')} (do not exceed {env.get('ph_do_not_exceed')})")
        lines.append(
            f"- Temperature target: {env.get('temperature_target_c')} °C "
            f"(do not exceed {env.get('temperature_do_not_exceed_c')} °C)"
        )
        lines.append(f"- Dissolved oxygen: ≥ {env.get('dissolved_oxygen_min_mg_l')} mg/L")
        lines.append(f"- Ammonia/nitrite: {env.get('ammonia_nitrite_target')}")
        lines.append("")

    if out.maintenance_checklist:
        lines.append("## Maintenance Checklist\n")
        for task in out.maintenance_checklist:
            lines.append(f"- {task}")
        lines.append("")

    nc = out.nitrogen_check
    if nc:
        lines.append("## Nitrogen Consistency Check\n")
        verdict = "consistent" if nc.get("agrees") else "DISAGREEMENT — calibration needed"
        lines.append(f"FRR sizing vs nitrogen-balance: **{verdict}**.\n")
        lines.append(f"- N fed: {nc.get('n_fed_g_day')} g/day")
        lines.append(f"- N retained in fish: {nc.get('n_retained_g_day')} g/day")
        lines.append(f"- N excreted: {nc.get('n_excreted_g_day')} g/day")
        lines.append(
            f"- Of excreted N → plants: {nc.get('n_plant_uptake_g_day')}, "
            f"solids: {nc.get('n_solids_g_day')}, water exchange: {nc.get('n_water_exchange_g_day')}, "
            f"denitrification: {nc.get('n_denitrification_g_day')} (g/day)"
        )
        lines.append("")

    if out.assumptions:
        lines.append("## Assumptions\n")
        for a in out.assumptions:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("## NOT Modeled (read before building)\n")
    lines.append("This design accounts for nitrogen and water only. It does NOT model:")
    for n in out.not_modeled:
        lines.append(f"- {n}")
    lines.append("\n*A reviewer must not treat this as a complete engineering design.*\n")

    lines.append("## Coefficients Used (auditable)\n")
    lines.append("| Coefficient | Value | Range | Unit | Source |")
    lines.append("|---|---|---|---|---|")
    for c in out.coefficients_used:
        lines.append(f"| {c.name} | {c.value} | {c.low}–{c.high} | {c.unit} | {c.source} |")
    lines.append("")
    lines.append(
        "Sources: FAO589 = Somerville et al. (2014), FAO Technical Paper 589; "
        "UVI = Rakocy et al. (University of the Virgin Islands); LIT = literature consensus.\n"
    )

    return "\n".join(lines)

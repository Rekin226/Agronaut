"""size_system() — the calculator.

Solve order (FRR anchors; nitrogen only CHECKS):

    grow_area ──FRR──▶ feed/day ──feeding%──▶ fish biomass ──harvest wt──▶ fish count
                                                   │
                                                   ├─ stocking density ─▶ rearing tank vol
                                                   ├─ raft depth + sump ─▶ system vol ─▶ pump
                                                   ├─ massbalance.water_balance ─▶ makeup water
                                                   ├─ massbalance.biofilter ─────▶ media area
                                                   └─ massbalance.nitrogen_check ▶ consistency flag

Feasibility: if makeup water exceeds the budget, return feasible=False with the binding
constraint and a nearest-feasible hint (the smallest single-input change that restores it).
Never raises on a valid DesignInput; never returns a silently-wrong number.
"""

from __future__ import annotations

import dataclasses
import math

from . import coefficients as C
from . import massbalance as mb
from .crops import get_crop
from .overrides import validate_overrides, apply_overrides
from .species import get_species, temperature_feed_factor
from .system_types import get_system_type
from .types import CoefficientUse, DesignInput, DesignOutput

# What this v1 model does NOT account for. Every output carries this so a design can
# never be mistaken for complete. (Eng + CEO review honesty layer.)
NOT_MODELED = [
    "pH / alkalinity dynamics and buffering",
    "potassium, calcium, iron and other non-nitrogen nutrients",
    "salinity / mineral build-up from source water",
    "solids handling and biofilter maturation over time",
    "pests, disease, and biosecurity",
    "fish cohort logic (stocking batches, mortality, growth curve, staggered harvest)",
    "diel temperature swings and seasonal min/max (mean temperature only)",
]


def _coeff_uses(*coeffs) -> list[CoefficientUse]:
    return [CoefficientUse(c.name, c.value, c.low, c.high, c.unit, c.source) for c in coeffs]


def _area(a: float) -> str:
    return f"{float(a):g}"


def size_system(design: DesignInput, overrides: dict | None = None) -> DesignOutput:
    if overrides:
        validate_overrides(overrides)
    species = get_species(design.fish_species)
    system = get_system_type(design.system_type)
    species, _ = apply_overrides(species=species, crop=None, overrides=overrides)

    # Resolve the crop allocation: a mixed-bed plan (>1 crop sharing the water) or a single
    # crop over the whole area. Each crop keeps its OWN feeding-rate ratio; overrides apply
    # per crop. A single-entry plan is identical to the single-crop design (no drift).
    plantings = _resolve_plantings(design, overrides)
    dominant = max(plantings, key=lambda p: p[1])[0]

    # 1. FRR sizes feed from grow area (the anchor) — summed over each crop's own area.
    feed_g_per_day = sum(area * c.frr_g_per_m2_day for c, area in plantings)

    # 2. Feed -> fish biomass, adjusted for how well fish eat at this temperature.
    temp_factor = temperature_feed_factor(species, design.temperature_c)
    effective_feed_pct = species.feeding_rate_pct_bw * temp_factor
    # biomass(kg) = feed(g/day) / (feed% as fraction) / 1000
    fish_biomass_kg = feed_g_per_day / (effective_feed_pct / 100.0) / 1000.0

    # 3. Biomass -> fish count (steady-state average; cohort logic NOT modeled).
    fish_count = max(1, math.ceil(fish_biomass_kg / species.harvest_weight_kg))

    # 4. Rearing tank volume from stocking density.
    rearing_tank_volume_m3 = fish_biomass_kg / species.stocking_density_kg_m3
    rearing_tank_volume_l = rearing_tank_volume_m3 * 1000.0

    # 5. System volume = rearing tank + grow-bed water + sump. The grow-bed water depth is
    #    the method's (raft is deep, NFT a thin film, media bed the void space).
    bed_water_m3 = design.grow_area_m2 * system.water_depth_m
    subtotal_m3 = rearing_tank_volume_m3 + bed_water_m3
    system_volume_m3 = subtotal_m3 / (1.0 - C.SUMP_FRACTION.value)
    system_volume_l = system_volume_m3 * 1000.0

    # 6. Pump turnover (flow) and the head/power it must deliver it against (method lift).
    pump_turnover_lph = system_volume_l * C.PUMP_TURNOVER_RATE.value
    pump_head_m, pump_power_w = mb.pump_hydraulics(pump_turnover_lph, system)

    # 7. Water balance (tank surface approximated from rearing tank at ~1 m depth).
    tank_surface_m2 = rearing_tank_volume_m3 / 1.0
    water = mb.water_balance(design.grow_area_m2, tank_surface_m2)
    makeup_lpd = water["makeup_water_lpd"]

    # 8. Biofilter media.
    media_m2 = mb.biofilter_media_m2(feed_g_per_day, species)

    # 9. Nitrogen consistency check (does NOT resize anything). For a mixed bed we check against
    #    an area-weighted blended crop, so the FRR-vs-nitrogen agreement test still holds over
    #    the whole planting (identical to the single crop when there is only one).
    total_area = design.grow_area_m2
    blended_crop = dataclasses.replace(
        dominant,
        frr_g_per_m2_day=feed_g_per_day / total_area,
        n_uptake_g_per_m2_day=sum(a * c.n_uptake_g_per_m2_day for c, a in plantings) / total_area,
    )
    n_check = mb.nitrogen_check(feed_g_per_day, species, blended_crop, total_area)

    out = DesignOutput(
        feasible=True,
        system_type=system.key,
        grow_bed_label=system.grow_bed_label,
        footprint_ratio=system.footprint_ratio,
        footprint_m2=round(design.grow_area_m2 / system.footprint_ratio, 1),
        system_volume_l=round(system_volume_l, 1),
        rearing_tank_volume_l=round(rearing_tank_volume_l, 1),
        fish_count=fish_count,
        fish_biomass_kg=round(fish_biomass_kg, 2),
        feed_g_per_day=round(feed_g_per_day, 1),
        grow_area_m2=design.grow_area_m2,
        pump_turnover_lph=round(pump_turnover_lph, 1),
        pump_head_m=pump_head_m,
        pump_power_w=pump_power_w,
        biofilter_media_m2=media_m2,
        makeup_water_lpd=makeup_lpd,
        nitrogen_check=n_check,
        not_modeled=list(NOT_MODELED),
    )

    # 10. Feasibility: water budget is the binding constraint we check in v1.
    if makeup_lpd > design.water_budget_lpd:
        out.feasible = False
        out.binding_constraint = "water_budget"
        # Nearest feasible: shrink grow area proportionally so makeup fits the budget.
        if makeup_lpd > 0:
            feasible_area = design.grow_area_m2 * (design.water_budget_lpd / makeup_lpd)
            out.warnings.append(
                f"Makeup water {makeup_lpd} L/day exceeds budget {design.water_budget_lpd} L/day. "
                f"Nearest feasible: reduce grow area to ~{round(feasible_area, 1)} m2 "
                f"(a {round((1 - feasible_area / design.grow_area_m2) * 100)}% cut)."
            )

    if not n_check["agrees"] and n_check["flag"]:
        out.warnings.append(n_check["flag"])

    # Temperature warning if fish are outside their optimal band.
    if temp_factor < 1.0:
        out.warnings.append(
            f"{design.temperature_c} C is outside {species.name}'s optimal band "
            f"({species.temp_opt_low_c}-{species.temp_opt_high_c} C); feeding scaled to "
            f"{round(temp_factor * 100)}% — yields and sizing reflect reduced intake."
        )

    # Mixed-bed honesty: record the plan, and flag crops that cannot share one water. Only
    # emitted for a real mix (>1 crop) so the single-crop design is byte-for-byte unchanged.
    if len(plantings) > 1:
        out.crop_plan = [{"crop": c.name, "area_m2": a} for c, a in plantings]
        _add_mixed_bed_warnings(out, plantings, design)

    out.operating_envelope = _operating_envelope(species, plantings, design)
    out.bill_of_materials = _bill_of_materials(out, system)
    out.maintenance_checklist = _maintenance_checklist()
    out.assumptions = _assumptions(species, plantings, temp_factor, system)
    # A method-specific water-depth coefficient replaces the raft default in the citation list.
    water_depth_coeff = CoefficientUse(
        f"grow_bed_water_depth ({system.key})", system.water_depth_m,
        system.water_depth_low, system.water_depth_high, "m", system.source)
    lift_coeff = CoefficientUse(
        f"pump_lift_height ({system.key})", system.lift_height_m,
        system.lift_low, system.lift_high, "m", system.source)
    out.coefficients_used = [water_depth_coeff, lift_coeff] + _coeff_uses(
        C.N_FRACTION_OF_PROTEIN, C.PLANT_N_UPTAKE_FRACTION,
        C.SUMP_FRACTION, C.PUMP_TURNOVER_RATE, C.FRICTION_HEAD_FRACTION,
        C.PUMP_EFFICIENCY, C.NITRIFICATION_RATE,
        C.EVAPOTRANSPIRATION_RATE, C.TANK_EVAPORATION_RATE, C.SAFETY_FACTOR,
    )
    return out


def _resolve_plantings(design: DesignInput, overrides: dict | None) -> list[tuple]:
    """The crop allocation as [(Crop, area_m2), ...]: the mixed-bed plan if set, else the single
    crop over the whole area. Overrides apply per crop. Single crop => one entry (no drift)."""
    if design.crop_plan:
        raw = [(get_crop(k), float(a)) for k, a in design.crop_plan]
    else:
        raw = [(get_crop(design.crop), design.grow_area_m2)]
    return [(apply_overrides(species=None, crop=c, overrides=overrides)[1], a) for c, a in raw]


def _shared_ph_band(plantings) -> tuple[float, float]:
    """The pH window every crop in the mix can tolerate — the INTERSECTION of their bands,
    never a widening. If lo >= hi the crops have no usable shared band."""
    return max(c.ph_min for c, _ in plantings), min(c.ph_max for c, _ in plantings)


def _add_mixed_bed_warnings(out: DesignOutput, plantings, design) -> None:
    ph_lo, ph_hi = _shared_ph_band(plantings)
    if ph_lo >= ph_hi:
        ranges = ", ".join(f"{c.name} {c.ph_min}-{c.ph_max}" for c, _ in plantings)
        out.warnings.append(
            f"These crops cannot share one pH — their pH ranges do not overlap ({ranges}). "
            "Grow them in separate systems, or drop one, rather than compromising on a pH "
            "that suits none of them."
        )


def _operating_envelope(species, plantings, design) -> dict:
    ph_lo, ph_hi = _shared_ph_band(plantings)
    return {
        # Aquaponics compromise pH sits between fish, plants, and nitrifiers (~6.0-7.0); for a
        # mixed bed the plants' half of that compromise is the intersection of all their bands.
        "ph_target": [max(ph_lo, 6.0), min(ph_hi, 7.0)],
        "ph_do_not_exceed": [ph_lo, ph_hi],
        "temperature_target_c": [species.temp_opt_low_c, species.temp_opt_high_c],
        "temperature_do_not_exceed_c": [species.temp_min_c, species.temp_max_c],
        "dissolved_oxygen_min_mg_l": 5.0,
        "ammonia_nitrite_target": "as low as possible (≈0)",
    }


def _bill_of_materials(out: DesignOutput, system) -> list[dict]:
    biofilter_spec = f"~{out.biofilter_media_m2} m2 surface"
    if system.provides_biofiltration:
        biofilter_spec += " (the media bed also nitrifies — a separate biofilter may be reduced)"
    return [
        {"item": "rearing tank", "spec": f"~{round(out.rearing_tank_volume_l)} L", "qty": 1},
        {"item": system.grow_bed_item, "spec": f"{out.grow_area_m2} m2 planted area", "qty": 1},
        {"item": "water pump", "spec": f"≥{round(out.pump_turnover_lph)} L/h against "
         f"~{out.pump_head_m} m head (~{round(out.pump_power_w)} W electrical)", "qty": 1},
        {"item": "biofilter media", "spec": biofilter_spec, "qty": 1},
        {"item": "aeration", "spec": "air pump + stones; maintain DO ≥5 mg/L", "qty": 1},
        {"item": "fish (fingerlings)", "spec": f"~{out.fish_count} head", "qty": out.fish_count},
    ]


def _maintenance_checklist() -> list[str]:
    return [
        "Daily: check fish behaviour, feed response, and aeration/pump operation.",
        "Daily: top up makeup water; record the amount (logging standard).",
        "Weekly: test pH, ammonia, nitrite, nitrate; record readings.",
        "Weekly: inspect and clean pump intake and biofilter; check flow.",
        "Monthly: remove settled solids; inspect roots for browning/slime.",
    ]


def _assumptions(species, plantings, temp_factor, system) -> list[str]:
    if len(plantings) > 1:
        mix = ", ".join(f"{c.name} ({_area(a)} m2)" for c, a in plantings)
        crop_line = (f"{system.name.capitalize()} system, single fish species "
                     f"({species.name}), mixed beds: {mix}.")
    else:
        crop_line = (f"{system.name.capitalize()} system, single fish species "
                     f"({species.name}), single crop ({plantings[0][0].name}).")
    return [
        crop_line,
        "Steady-state average biomass (no cohort/harvest scheduling).",
        f"Feeding scaled to {round(temp_factor * 100)}% for the given mean temperature.",
        "Coefficients are seed defaults — CALIBRATE against a real system before building.",
        "Rainfall assumed 0 (covered/controlled system).",
    ] + [f"Method note ({system.key}): {c}" for c in system.considerations]

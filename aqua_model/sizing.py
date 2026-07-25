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


def size_system(design: DesignInput, overrides: dict | None = None) -> DesignOutput:
    if overrides:
        validate_overrides(overrides)
    species = get_species(design.fish_species)
    crop = get_crop(design.crop)
    system = get_system_type(design.system_type)
    species, crop = apply_overrides(species=species, crop=crop, overrides=overrides)

    # 1. FRR sizes feed from grow area (the anchor).
    feed_g_per_day = design.grow_area_m2 * crop.frr_g_per_m2_day

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

    # 6. Pump turnover.
    pump_turnover_lph = system_volume_l * C.PUMP_TURNOVER_RATE.value

    # 7. Water balance (tank surface approximated from rearing tank at ~1 m depth).
    tank_surface_m2 = rearing_tank_volume_m3 / 1.0
    water = mb.water_balance(design.grow_area_m2, tank_surface_m2)
    makeup_lpd = water["makeup_water_lpd"]

    # 8. Biofilter media.
    media_m2 = mb.biofilter_media_m2(feed_g_per_day, species)

    # 9. Nitrogen consistency check (does NOT resize anything).
    n_check = mb.nitrogen_check(feed_g_per_day, species, crop, design.grow_area_m2)

    out = DesignOutput(
        feasible=True,
        system_type=system.key,
        grow_bed_label=system.grow_bed_label,
        system_volume_l=round(system_volume_l, 1),
        rearing_tank_volume_l=round(rearing_tank_volume_l, 1),
        fish_count=fish_count,
        fish_biomass_kg=round(fish_biomass_kg, 2),
        feed_g_per_day=round(feed_g_per_day, 1),
        grow_area_m2=design.grow_area_m2,
        pump_turnover_lph=round(pump_turnover_lph, 1),
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

    out.operating_envelope = _operating_envelope(species, crop, design)
    out.bill_of_materials = _bill_of_materials(out, system)
    out.maintenance_checklist = _maintenance_checklist()
    out.assumptions = _assumptions(species, crop, temp_factor, system)
    # A method-specific water-depth coefficient replaces the raft default in the citation list.
    water_depth_coeff = CoefficientUse(
        f"grow_bed_water_depth ({system.key})", system.water_depth_m,
        system.water_depth_low, system.water_depth_high, "m", system.source)
    out.coefficients_used = [water_depth_coeff] + _coeff_uses(
        C.N_FRACTION_OF_PROTEIN, C.PLANT_N_UPTAKE_FRACTION,
        C.SUMP_FRACTION, C.PUMP_TURNOVER_RATE, C.NITRIFICATION_RATE,
        C.EVAPOTRANSPIRATION_RATE, C.TANK_EVAPORATION_RATE, C.SAFETY_FACTOR,
    )
    return out


def _operating_envelope(species, crop, design) -> dict:
    return {
        "ph_target": [max(species_ph_low(crop), 6.0), min(crop.ph_max, 7.0)],
        "ph_do_not_exceed": [crop.ph_min, crop.ph_max],
        "temperature_target_c": [species.temp_opt_low_c, species.temp_opt_high_c],
        "temperature_do_not_exceed_c": [species.temp_min_c, species.temp_max_c],
        "dissolved_oxygen_min_mg_l": 5.0,
        "ammonia_nitrite_target": "as low as possible (≈0)",
    }


def species_ph_low(crop) -> float:
    # Aquaponics compromise pH sits between fish, plants, and nitrifiers (~6.0-7.0).
    return crop.ph_min


def _bill_of_materials(out: DesignOutput, system) -> list[dict]:
    biofilter_spec = f"~{out.biofilter_media_m2} m2 surface"
    if system.provides_biofiltration:
        biofilter_spec += " (the media bed also nitrifies — a separate biofilter may be reduced)"
    return [
        {"item": "rearing tank", "spec": f"~{round(out.rearing_tank_volume_l)} L", "qty": 1},
        {"item": system.grow_bed_item, "spec": f"{out.grow_area_m2} m2 planted area", "qty": 1},
        {"item": "water pump", "spec": f"≥{round(out.pump_turnover_lph)} L/h at head", "qty": 1},
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


def _assumptions(species, crop, temp_factor, system) -> list[str]:
    return [
        f"{system.name.capitalize()} system, single fish species ({species.name}), "
        f"single crop ({crop.name}).",
        "Steady-state average biomass (no cohort/harvest scheduling).",
        f"Feeding scaled to {round(temp_factor * 100)}% for the given mean temperature.",
        "Coefficients are seed defaults — CALIBRATE against a real system before building.",
        "Rainfall assumed 0 (covered/controlled system).",
    ] + [f"Method note ({system.key}): {c}" for c in system.considerations]

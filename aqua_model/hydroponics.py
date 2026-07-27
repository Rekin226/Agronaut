"""size_hydroponic_system() — the calculator for soil-less systems WITHOUT fish.

Hydroponics differs from aquaponics at the root of the solve chain: there is no fish, no
feed, no biofilter, and no fish-nitrogen balance. Nutrients are dosed directly as salts, so
grow area drives water (evapotranspiration) and the nutrient solution is specified by a
target EC band plus the crop's elemental-nitrogen demand.

Solve order (grow area anchors everything):

    grow_area ──ET──▶ daily water use ─▶ makeup water ─▶ (feasibility vs budget)
        │
        ├─ raft depth + sump ─▶ reservoir (solution) volume ─▶ pump turnover
        └─ crop EC band + N uptake ─▶ nutrient-solution target

Reuses the cited crop database and water/geometry coefficients; keeps the same honesty
layer (cited coefficients + an explicit 'not modeled' list). Never raises on a valid
HydroponicInput.
"""

from __future__ import annotations

from . import coefficients as C
from .crops import get_crop
from .system_types import get_system_type
from .types import CoefficientUse, HydroponicInput, HydroponicOutput

# What this hydroponics v1 does NOT model. Distinct from the aquaponics list — no fish,
# but new soil-less concerns (EC drift, micronutrient chemistry, root-zone disease).
NOT_MODELED = [
    "pH / alkalinity dynamics and buffering",
    "micronutrient chemistry (iron chelation, Ca/Mg antagonism)",
    "EC drift and salt accumulation as water evaporates and solution is topped up",
    "specific fertilizer formulation and A/B stock-tank recipes",
    "root-zone temperature and dissolved oxygen beyond the stated target",
    "root-zone disease (e.g. pythium) and biosecurity",
    "climate-driven evapotranspiration variability (mean rate only; calibrate per site)",
]


def _coeff_uses(*coeffs) -> list[CoefficientUse]:
    return [CoefficientUse(c.name, c.value, c.low, c.high, c.unit, c.source) for c in coeffs]


def _ec_coeff(crop):
    return C.EC_TARGET_FRUITING if crop.category == "fruiting" else C.EC_TARGET_LEAFY


def size_hydroponic_system(design: HydroponicInput) -> HydroponicOutput:
    crop = get_crop(design.crop)
    system = get_system_type(design.system_type)

    # 1. ET drives daily solution consumption (the dominant water term in a covered system).
    daily_water_use = design.grow_area_m2 * C.EVAPOTRANSPIRATION_RATE.value

    # 2. Reservoir (solution) volume = bed water (area x method depth) + sump headroom.
    bed_water_m3 = design.grow_area_m2 * system.water_depth_m
    reservoir_m3 = bed_water_m3 / (1.0 - C.SUMP_FRACTION.value)
    reservoir_l = reservoir_m3 * 1000.0

    # 3. Pump turnover: circulate the reservoir volume at the standard turnover rate.
    pump_lph = reservoir_l * C.PUMP_TURNOVER_RATE.value

    # 4. Makeup water = ET consumption (rainfall 0 for a covered system). Evaporative loss
    #    from open canals is folded into the ET range, which is wide and calibrated per site.
    makeup_lpd = round(daily_water_use, 1)

    # 5. Nutrient target: EC band for the crop category + the crop's elemental-N demand.
    ec = _ec_coeff(crop)
    elemental_n = round(design.grow_area_m2 * crop.n_uptake_g_per_m2_day, 1)
    nutrient_target = {
        "ec_mS_cm": {"target": ec.value, "low": ec.low, "high": ec.high},
        "elemental_n_g_per_day": elemental_n,
        "ph_target": [max(crop.ph_min, 5.5), min(crop.ph_max, 6.5)],
        "note": "Dose to the EC band; N/day is the crop's uptake — supply via a complete "
                "hydroponic nutrient mix, not a single salt.",
    }

    out = HydroponicOutput(
        feasible=True,
        system_type=system.key,
        grow_bed_label=system.grow_bed_label,
        footprint_ratio=system.footprint_ratio,
        footprint_m2=round(design.grow_area_m2 / system.footprint_ratio, 1),
        grow_area_m2=design.grow_area_m2,
        reservoir_volume_l=round(reservoir_l, 1),
        daily_water_use_lpd=round(daily_water_use, 1),
        makeup_water_lpd=makeup_lpd,
        pump_turnover_lph=round(pump_lph, 1),
        nutrient_target=nutrient_target,
        not_modeled=list(NOT_MODELED),
    )

    # 6. Feasibility: water budget is the binding constraint.
    if makeup_lpd > design.water_budget_lpd:
        out.feasible = False
        out.binding_constraint = "water_budget"
        if makeup_lpd > 0:
            feasible_area = design.grow_area_m2 * (design.water_budget_lpd / makeup_lpd)
            out.warnings.append(
                f"Makeup water {makeup_lpd} L/day exceeds budget {design.water_budget_lpd} "
                f"L/day. Nearest feasible: reduce grow area to ~{round(feasible_area, 1)} m2 "
                f"(a {round((1 - feasible_area / design.grow_area_m2) * 100)}% cut)."
            )

    # Temperature caution against the crop's own band (no fish band to check).
    if not (crop.temp_min_c <= design.temperature_c <= crop.temp_max_c):
        out.warnings.append(
            f"{design.temperature_c} C is outside {crop.name}'s range "
            f"({crop.temp_min_c}-{crop.temp_max_c} C); growth and water use will differ."
        )

    out.operating_envelope = {
        "ph_target": nutrient_target["ph_target"],
        "ec_target_mS_cm": [ec.low, ec.high],
        "temperature_target_c": [crop.temp_min_c, crop.temp_max_c],
        "dissolved_oxygen_min_mg_l": 5.0,
    }
    out.bill_of_materials = _bill_of_materials(out, crop, system)
    out.maintenance_checklist = _maintenance_checklist()
    out.assumptions = _assumptions(crop, system)
    water_depth_coeff = CoefficientUse(
        f"grow_bed_water_depth ({system.key})", system.water_depth_m,
        system.water_depth_low, system.water_depth_high, "m", system.source)
    out.coefficients_used = [water_depth_coeff] + _coeff_uses(
        C.EVAPOTRANSPIRATION_RATE, C.SUMP_FRACTION, C.PUMP_TURNOVER_RATE, ec,
    )
    return out


def _bill_of_materials(out: HydroponicOutput, crop, system) -> list[dict]:
    return [
        {"item": "nutrient reservoir / sump", "spec": f"~{round(out.reservoir_volume_l)} L", "qty": 1},
        {"item": system.grow_bed_item, "spec": f"{out.grow_area_m2} m2 planted area", "qty": 1},
        {"item": "circulation pump", "spec": f"≥{round(out.pump_turnover_lph)} L/h at head", "qty": 1},
        {"item": "aeration", "spec": "air pump + stones; maintain DO ≥5 mg/L in the root zone", "qty": 1},
        {"item": "nutrient stock (A/B) + pH adjusters", "spec": f"dose to EC "
         f"{out.nutrient_target['ec_mS_cm']['low']}–{out.nutrient_target['ec_mS_cm']['high']} mS/cm", "qty": 1},
        {"item": "EC + pH meters", "spec": "for daily monitoring", "qty": 1},
    ]


def _maintenance_checklist() -> list[str]:
    return [
        "Daily: check EC and pH; top up makeup water and record the amount.",
        "Daily: check pump/aeration operation and root-zone appearance.",
        "Weekly: adjust or refresh the nutrient solution; inspect roots for browning/slime.",
        "Weekly: clean pump intake and channels; check flow.",
        "Monthly: fully drain, clean, and remix the reservoir to reset salt build-up.",
    ]


def _assumptions(crop, system) -> list[str]:
    return [
        f"Soil-less hydroponic system ({system.name}), single crop ({crop.name}), NO fish.",
        "Nutrients supplied by a complete dosed solution (not fish waste).",
        "Grow area anchors ET-driven water use; rainfall assumed 0 (covered system).",
        "EC band and N/day are seed targets — CALIBRATE against your crop and climate.",
    ] + [f"Method note ({system.key}): {c}" for c in system.considerations]

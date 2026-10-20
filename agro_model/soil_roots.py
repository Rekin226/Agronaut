"""Experimental compartment root extraction using AquaCrop 7.3 §§3.2/3.10.

Demand uses linear stress over the wetter of supplied topsoil/root-zone volumes;
local extraction capacities use the supplied response shape and root activity.
Aeration and cold factors are explicit daily inputs, not simulated stress history.
"""

from dataclasses import dataclass
from fractions import Fraction
from math import expm1

from .soil_drainage import SOURCE
from .soil_storage import SoilFlux, SoilStorage, _quantity, apply_soil_fluxes
from .validation import text
from .water import ReferenceETDay

MODEL_VERSION = "soil-roots-1-aquacrop73-sinks"
UPTAKE_DEPTH_QUANTUM_MM = Fraction(1, 10**12)


@dataclass(frozen=True)
class RootUptakeParameters:
    root_depth_m: Fraction
    topsoil_depth_m: Fraction
    stomatal_threshold: Fraction
    local_shape_factor: Fraction
    root_zone_aeration_factor: Fraction
    source: str

    def __post_init__(self):
        text("root parameter source", self.source)
        for name in ("root_depth_m", "topsoil_depth_m"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), 10, positive=True))
        if self.topsoil_depth_m > self.root_depth_m:
            raise ValueError("topsoil stress depth cannot exceed root depth")
        for name in ("stomatal_threshold", "root_zone_aeration_factor"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), 1))
        if self.stomatal_threshold == 1:
            raise ValueError("stomatal threshold must be below one")
        object.__setattr__(self, "local_shape_factor", _quantity("local shape factor", self.local_shape_factor, 20))


@dataclass(frozen=True)
class RootLayerActivity:
    """Maximum sink in m3 water per m3 bulk soil per day; daily aeration factor."""

    maximum_rate_per_day: Fraction
    aeration_factor: Fraction
    source: str

    def __post_init__(self):
        text("root activity source", self.source)
        for name in ("maximum_rate_per_day", "aeration_factor"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), 1))


@dataclass(frozen=True)
class CropTranspirationCoefficients:
    transpiration_coefficient: Fraction
    cold_stress_factor: Fraction
    source: str

    def __post_init__(self):
        text("transpiration coefficient source", self.source)
        object.__setattr__(self, "transpiration_coefficient", _quantity("transpiration coefficient", self.transpiration_coefficient, 3))
        object.__setattr__(self, "cold_stress_factor", _quantity("cold stress factor", self.cold_stress_factor, 1))


def stomatal_stress(relative_depletion, threshold, shape_factor):
    """Linear response at shape zero; Eq. 3.2b otherwise, with exact endpoints."""
    depletion = _quantity("relative depletion", relative_depletion, 1)
    onset = _quantity("stomatal threshold", threshold, 1)
    shape = _quantity("stress shape factor", shape_factor, 20)
    if onset == 1:
        raise ValueError("stomatal threshold must be below one")
    if depletion <= onset:
        return Fraction(1)
    if depletion == 1:
        return Fraction(0)
    relative = (depletion-onset) / (1-onset)
    denominator = expm1(float(shape))
    if not denominator:
        return 1-relative
    return min(Fraction(1), max(Fraction(0), 1-Fraction(str(expm1(float(shape*relative)) / denominator))))


def _water_status(cells, depth):
    water = fc = wp = Fraction(0)
    for cell in cells:
        size = 1000 * max(Fraction(0), min(depth, cell.bottom_m)-cell.top_m)
        water += size * cell.water_content
        fc += size * cell.field_capacity
        wp += size * cell.wilting_point
    taw = fc-wp
    depletion = min(taw, max(Fraction(0), fc-water))
    return dict(water_mm=water, field_capacity_mm=fc, wilting_point_mm=wp,
                total_available_mm=taw, depletion_mm=depletion, relative_depletion=depletion/taw)


def _allocate(rows, ratio, target, area):
    """Bound computed flux precision without rounding the physical water stores.

    Largest remainders preserve the rounded total where local capacity allows.
    Each request stays within its capacity and within one quantum of its ideal
    proportional allocation. Remaining precision loss is reported, not hidden.
    """
    quantum = area * UPTAKE_DEPTH_QUANTUM_MM
    for row in rows:
        ideal = row["root_capacity_l"] * ratio
        row["ideal_requested_l"] = ideal
        row["requested_l"] = (ideal // quantum) * quantum
    remaining = int((target-sum(r["requested_l"] for r in rows)) // quantum)
    for row in sorted(rows, key=lambda r: r["ideal_requested_l"]-r["requested_l"], reverse=True):
        if (remaining and row["ideal_requested_l"] > row["requested_l"]
                and row["requested_l"]+quantum <= row["root_capacity_l"]):
            row["requested_l"] += quantum
            remaining -= 1


def transpire_soil_profile(storage, parameters, activities, weather, coefficients):
    """Draw transpiration from the current shared profile, never below wilting.

    The supplied threshold must already be adjusted for the day's ET0. KcTr must
    already account for canopy/ageing and is not a single ET or generic basal Kc.
    Root-zone and local aeration factors must include any applicable waterlogging
    history; no unstated 'no stress' defaults are supplied here.
    """
    if not isinstance(storage, SoilStorage) or not isinstance(weather, ReferenceETDay):
        raise ValueError("supply validated storage and a grass-reference ET day")
    et0 = _quantity("daily reference ET", weather.et0_mm_day, 100)
    patch_ids = {p.id for p in storage.patches}
    for label, data, cls in (("root parameters", parameters, RootUptakeParameters),
                             ("transpiration coefficients", coefficients, CropTranspirationCoefficients)):
        if not isinstance(data, dict) or set(data) != patch_ids or any(not isinstance(v, cls) for v in data.values()):
            raise ValueError(f"supply exactly one validated set of {label} per patch")
    keys = {(p.id, c.id) for p in storage.patches for c in p.compartments}
    if not isinstance(activities, dict) or set(activities) != keys or any(not isinstance(v, RootLayerActivity) for v in activities.values()):
        raise ValueError("supply validated activity for every soil compartment, including zero outside the roots")

    requests, patch_rows, layer_rows = [], [], []
    for patch in storage.patches:
        config, coeff = parameters[patch.id], coefficients[patch.id]
        if config.root_depth_m not in {c.bottom_m for c in patch.compartments}:
            raise ValueError("root depth must be a soil compartment boundary within the profile")
        area = storage.area_m2 * patch.area_fraction
        root = _water_status(patch.compartments, config.root_depth_m)
        top = _water_status(patch.compartments, config.topsoil_depth_m)
        selected = "topsoil" if top["relative_depletion"] < root["relative_depletion"] else "root_zone"
        relative = min(top["relative_depletion"], root["relative_depletion"])
        drought = stomatal_stress(relative, config.stomatal_threshold, 0)
        stress = min(drought, config.root_zone_aeration_factor)
        potential = coeff.transpiration_coefficient * et0 * area
        cold_demand = potential * coeff.cold_stress_factor
        demand = cold_demand * stress
        local_rows = []
        for cell in patch.compartments:
            activity = activities[(patch.id, cell.id)]
            rooted = cell.bottom_m <= config.root_depth_m
            if not rooted and activity.maximum_rate_per_day:
                raise ValueError("root activity below the supplied root depth must be explicitly zero")
            depletion = min(Fraction(1), max(Fraction(0), (cell.field_capacity-cell.water_content) / (cell.field_capacity-cell.wilting_point)))
            local_drought = stomatal_stress(depletion, config.stomatal_threshold, config.local_shape_factor)
            local_stress = min(local_drought, activity.aeration_factor)
            size = 1000 * area * cell.thickness_m
            capacity = size * activity.maximum_rate_per_day * local_stress
            available = max(Fraction(0), size * (cell.water_content-cell.wilting_point)) if rooted else Fraction(0)
            local_rows.append(dict(patch_id=patch.id, compartment_id=cell.id, rooted=rooted,
                relative_depletion=depletion, drought_factor=local_drought,
                aeration_factor=activity.aeration_factor, combined_stress_factor=local_stress,
                maximum_rate_per_day=activity.maximum_rate_per_day,
                root_capacity_l=capacity, available_above_wilting_l=available))
        capacity = sum((r["root_capacity_l"] for r in local_rows), Fraction(0))
        ratio = min(Fraction(1), demand/capacity) if capacity else Fraction(0)
        target = min(demand, capacity)
        _allocate(local_rows, ratio, target, area)
        allocated = actual = Fraction(0)
        for row in local_rows:
            requested = row["requested_l"]
            uptake = min(requested, row["available_above_wilting_l"])
            row.update(allocation_ratio=ratio, requested_l=requested, actual_l=uptake,
                       storage_shortfall_l=requested-uptake)
            requests.append(SoilFlux("root_uptake", patch.id, row["compartment_id"], None, requested,
                f"{MODEL_VERSION}; {config.source}; {activities[(patch.id, row['compartment_id'])].source}; {coeff.source}"))
            allocated += requested
            actual += uptake
        layer_rows.extend(local_rows)
        patch_rows.append(dict(patch_id=patch.id, root_zone_status=root, topsoil_status=top,
            selected_stress_volume=selected, drought_factor=drought,
            aeration_factor=config.root_zone_aeration_factor, combined_stress_factor=stress,
            potential_transpiration_l=potential, cold_limited_demand_l=cold_demand,
            soil_stress_limited_demand_l=demand, root_capacity_l=capacity,
            allocated_uptake_l=allocated, actual_transpiration_l=actual,
            cold_reduction_l=potential-cold_demand, soil_stress_reduction_l=cold_demand-demand,
            capacity_shortfall_l=demand-target, precision_shortfall_l=target-allocated,
            storage_shortfall_l=allocated-actual,
            total_unmet_potential_l=potential-actual))

    balance = apply_soil_fluxes(storage, requests)
    for flux, row in zip(balance.fluxes, layer_rows):
        if flux.actual_l != row["actual_l"]:
            raise ArithmeticError("root extraction and shared ledger disagree")
    totals = {name: sum((row[name] for row in patch_rows), Fraction(0))
              for name in patch_rows[0] if name.endswith("_l")}
    totals["balance_residual_l"] = storage.water_l-balance.ending.water_l-totals["actual_transpiration_l"]
    if totals["balance_residual_l"]:
        raise ArithmeticError("root extraction failed conservation")
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", balance=balance,
        weather=weather, parameters=tuple(parameters.items()), activities=tuple(activities.items()),
        coefficients=tuple(coefficients.items()), patches=tuple(patch_rows), layers=tuple(layer_rows),
        totals=totals, units={name: "L" for name in totals},
        uptake_depth_quantum_mm=UPTAKE_DEPTH_QUANTUM_MM,
        layer_units={name: "L" if name.endswith("_l") else ("m3/m3/day" if name == "maximum_rate_per_day" else "1")
                     for name in layer_rows[0] if name not in ("patch_id", "compartment_id", "rooted")},
        status_units=dict(water_mm="mm", field_capacity_mm="mm", wilting_point_mm="mm",
            total_available_mm="mm", depletion_mm="mm", relative_depletion="1"),
        sources=tuple(sorted({SOURCE, storage.source, weather.source,
            *(v.source for data in (parameters, activities, coefficients) for v in data.values()),
            *(c.soil_source for p in storage.patches for c in p.compartments)})),
        assumptions=("The wetter of supplied topsoil and root-zone relative depletions controls linear demand stress; local extraction uses the supplied linear or curved response.",
            "Drought and supplied aeration factors combine by their minimum, separately for demand and local capacity; cold stress reduces demand only.",
            "Stomatal threshold is already adjusted for daily ET0; crop transpiration coefficient already represents canopy and ageing. Neither is inferred from location or a generic Kc.",
            "Requested extraction is proportional to stressed root capacities, capped at demand and then at each cell's water above wilting. Storage shortfalls are not redistributed.",
            "Computed uptake requests use a 1e-12 mm local-depth quantum and capacity-bounded largest remainders. Numerical shortfall is reported separately; physical storage and actual withdrawals conserve water exactly.",
            "Depths, root activity and patch geometry are explicit; no roots draw water from below the root boundary or another patch."),
        limitations=("Internal experimental component, not the complete AquaCrop model; no upstream output equivalence or independent field validation is claimed.",
            "Aeration history, cold response, canopy development, root growth and ET0 threshold adjustment are not predicted here; supplied daily factors require evidence.",
            "Root depth must coincide with a compartment boundary. Soil properties represent bulk volume; gravel corrections are not inferred.",
            "Stress is evaluated once at the beginning of this extraction phase; within-day feedback, salinity, hydraulic redistribution and cross-patch root compensation are not simulated."))

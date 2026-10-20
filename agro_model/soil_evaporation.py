"""Two-stage evaporation drawing from the shared soil and ponded-water stores.

Internal experimental component: AquaCrop 7.3 manual §3.9, with explicit wet
coefficients and drying memory. It is not the complete AquaCrop model. Compartments
remain uniform; extraction over a partial compartment changes its average water
content. This discretization does not resolve moisture gradients inside a cell.
"""

from dataclasses import dataclass, replace
from fractions import Fraction
from math import expm1

from .soil_drainage import SOURCE
from .soil_infiltration import ROUTING_REFERENCE, SurfaceWaterState
from .soil_storage import SoilFlux, SoilStorage, _quantity, apply_soil_fluxes
from .validation import text
from .water import ReferenceETDay

MODEL_VERSION = "soil-evaporation-1-aquacrop73-stages"
STAGE_TWO_STEPS = 20
DEPTH_INCREMENT_M = Fraction(1, 1000)
EXPANSION_THRESHOLD = Fraction(2, 5)


@dataclass(frozen=True)
class SoilEvaporationParameters:
    minimum_depth_m: Fraction
    maximum_depth_m: Fraction
    readily_evaporable_mm: Fraction
    decline_factor: Fraction
    source: str

    def __post_init__(self):
        text("evaporation parameter source", self.source)
        for name in ("minimum_depth_m", "maximum_depth_m"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), Fraction(1, 2), positive=True))
        if self.minimum_depth_m < Fraction(1, 100) or self.maximum_depth_m < self.minimum_depth_m:
            raise ValueError("evaporation depths require 0.01 <= minimum <= maximum <= 0.5 m")
        object.__setattr__(self, "readily_evaporable_mm", _quantity("REW", self.readily_evaporable_mm, 500))
        object.__setattr__(self, "decline_factor", _quantity("decline factor", self.decline_factor, 20, positive=True))


@dataclass(frozen=True)
class SoilEvaporationMemory:
    stage_one_remaining_mm: Fraction
    active_depth_m: Fraction
    stage_two_start_fraction: Fraction | None
    source: str

    def __post_init__(self):
        text("evaporation memory source", self.source)
        object.__setattr__(self, "stage_one_remaining_mm", _quantity("remaining stage-one amount", self.stage_one_remaining_mm, 500))
        object.__setattr__(self, "active_depth_m", _quantity("active evaporation depth", self.active_depth_m, Fraction(1, 2), positive=True))
        if self.stage_one_remaining_mm:
            if self.stage_two_start_fraction is not None:
                raise ValueError("stage-one memory must not contain a stage-two reference")
        else:
            object.__setattr__(self, "stage_two_start_fraction", _quantity("stage-two start fraction", self.stage_two_start_fraction, 1))


@dataclass(frozen=True)
class PatchEvaporationCoefficients:
    wet_soil_coefficient: Fraction
    ponded_water_coefficient: Fraction
    source: str

    def __post_init__(self):
        text("evaporation coefficient source", self.source)
        for name in ("wet_soil_coefficient", "ponded_water_coefficient"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), 3))


def _stores(cells, depth):
    result = dict.fromkeys(("water", "fc", "sat", "dry"), Fraction(0))
    for cell in cells:
        thickness = max(Fraction(0), min(cell.bottom_m, depth) - cell.top_m)
        for key, attribute in (("water", "water_content"), ("fc", "field_capacity"),
                               ("sat", "saturation"), ("dry", "evaporation_floor")):
            result[key] += 1000 * thickness * getattr(cell, attribute)
    return result


def _start_fraction(cells, parameters):
    stores = _stores(cells, parameters.minimum_depth_m)
    baseline = stores["fc"] - parameters.readily_evaporable_mm
    if stores["water"] <= baseline:
        return Fraction(0)
    return min(Fraction(1), max(Fraction(0), (stores["water"] - baseline) / (stores["sat"] - baseline)))


def _relative_water(cells, depth, parameters, start_fraction):
    stores = _stores(cells, depth)
    baseline = stores["fc"] - parameters.readily_evaporable_mm
    upper = baseline + start_fraction * (stores["sat"] - baseline)
    if upper <= stores["dry"]:
        raise ValueError("stage-two reference must exceed air-dry storage")
    return min(Fraction(1), max(Fraction(0), (stores["water"] - stores["dry"]) / (upper - stores["dry"])))


def evaporation_reduction(relative_water, decline_factor):
    """Eq. 3.9h, evaluated stably with exact dry/wet endpoints."""
    relative = _quantity("relative water", relative_water, 1)
    factor = _quantity("decline factor", decline_factor, 20, positive=True)
    if relative in (0, 1):
        return relative
    denominator = expm1(float(factor))
    if not denominator:
        # Continuous limit for a factor below floating-point resolution.
        return relative
    return min(Fraction(1), max(Fraction(0), Fraction(str(expm1(float(factor * relative)) / denominator))))


def _extract(cells, depth, requested_mm, withdrawals):
    """Top-down finite-volume extraction; return actual local depth removed."""
    remaining = requested_mm
    for index, cell in enumerate(cells):
        thickness = max(Fraction(0), min(cell.bottom_m, depth) - cell.top_m)
        available = 1000 * thickness * (cell.water_content - cell.evaporation_floor)
        amount = min(remaining, available)
        if amount:
            cells[index] = replace(cell, water_content=cell.water_content - amount / (1000 * cell.thickness_m))
            withdrawals[cell.id] += amount
            remaining -= amount
        if not remaining:
            break
    return requested_mm - remaining


def validate_evaporation_patch(patch, config, memory):
    """Check geometry and carried drying memory without advancing water stores."""
    if config.maximum_depth_m > patch.compartments[-1].bottom_m:
        raise ValueError("soil profile must cover the maximum evaporating depth")
    if config.maximum_depth_m not in {cell.bottom_m for cell in patch.compartments}:
        raise ValueError("split the soil grid at the maximum evaporation depth to prevent extraction from deeper soil")
    if not config.minimum_depth_m <= memory.active_depth_m <= config.maximum_depth_m:
        raise ValueError("remembered evaporation depth must be within configured bounds")
    if memory.stage_one_remaining_mm > config.readily_evaporable_mm:
        raise ValueError("stage-one counter cannot exceed REW")
    if memory.stage_one_remaining_mm and memory.active_depth_m != config.minimum_depth_m:
        raise ValueError("stage-one evaporation uses the minimum evaporating depth")
    for cell in patch.compartments:
        if cell.top_m < config.maximum_depth_m and cell.evaporation_floor != cell.wilting_point / 2:
            raise ValueError("this evaporation method requires air-dry water content equal to half wilting point")
    stores = _stores(patch.compartments, config.minimum_depth_m)
    if config.readily_evaporable_mm >= stores["fc"] - stores["dry"]:
        raise ValueError("REW must be below evaporable storage at FC within the minimum depth")


def evaporate_soil_profile(storage, surface_states, parameters, memories, weather, coefficients, *, infiltrated_mm):
    """Evaporate after wetting; supplied infiltration only updates drying memory.

    Water must already be in storage. Rewetting replenishes the stage-one counter
    additively up to REW, resets active depth and establishes a new stage-two
    reference when that counter is exhausted. The counter never adds soil water.
    Coefficients are supplied for each physical patch, already adjusted for local
    canopy/shelter conditions. No further wetting-fraction multiplier is applied.
    """
    if not isinstance(storage, SoilStorage) or not isinstance(weather, ReferenceETDay):
        raise ValueError("supply validated storage and a grass-reference ET day")
    et0 = _quantity("daily reference ET", weather.et0_mm_day, 100)
    patch_ids = {patch.id for patch in storage.patches}
    for label, data, cls in (("surface states", surface_states, SurfaceWaterState),
                             ("evaporation parameters", parameters, SoilEvaporationParameters),
                             ("evaporation memories", memories, SoilEvaporationMemory),
                             ("evaporation coefficients", coefficients, PatchEvaporationCoefficients)):
        if not isinstance(data, dict) or set(data) != patch_ids or any(not isinstance(v, cls) for v in data.values()):
            raise ValueError(f"supply exactly one validated set of {label} per patch")
    if not isinstance(infiltrated_mm, dict) or set(infiltrated_mm) != patch_ids:
        raise ValueError("supply explicit local infiltrated depth for every patch, including zero")
    recharge = {key: _quantity("local infiltrated depth", value, 6000) for key, value in infiltrated_mm.items()}
    ending_memories, ending_surface, rows, traces, requests, expected_patches = {}, {}, [], [], [], []
    for patch in storage.patches:
        config, memory, coeff = parameters[patch.id], memories[patch.id], coefficients[patch.id]
        validate_evaporation_patch(patch, config, memory)
        cells = list(patch.compartments)
        area = storage.area_m2 * patch.area_fraction
        surface = surface_states[patch.id]
        credit, depth, reference = memory.stage_one_remaining_mm, memory.active_depth_m, memory.stage_two_start_fraction
        if recharge[patch.id] or surface.stored_mm:
            credit = config.readily_evaporable_mm if surface.stored_mm else min(config.readily_evaporable_mm, credit + recharge[patch.id])
            depth = config.minimum_depth_m
            reference = None if credit else _start_fraction(cells, config)

        pond_potential = coeff.ponded_water_coefficient * et0
        pond_evaporation = min(surface.stored_mm, pond_potential)
        if surface.stored_mm:
            time_left = 1 - pond_evaporation / pond_potential if pond_potential else Fraction(0)
        else:
            time_left = Fraction(1)
        soil_potential = coeff.wet_soil_coefficient * et0 * time_left
        withdrawals = dict.fromkeys((cell.id for cell in cells), Fraction(0))
        stage_one_requested = min(credit, soil_potential)
        stage_one = _extract(cells, config.minimum_depth_m, stage_one_requested, withdrawals)
        credit -= stage_one
        if stage_one < stage_one_requested:
            credit = Fraction(0)
        if not credit and reference is None:
            reference = _start_fraction(cells, config)
        remaining_potential = soil_potential - stage_one
        stage_two = stage_two_shortfall = Fraction(0)
        if remaining_potential and not credit:
            for step in range(STAGE_TWO_STEPS):
                relative = _relative_water(cells, depth, config, reference)
                while config.maximum_depth_m > depth and relative < EXPANSION_THRESHOLD * (config.maximum_depth_m-depth) / (config.maximum_depth_m-config.minimum_depth_m):
                    depth = min(config.maximum_depth_m, depth + DEPTH_INCREMENT_M)
                    relative = _relative_water(cells, depth, config, reference)
                kr = evaporation_reduction(relative, config.decline_factor)
                requested = kr * remaining_potential / STAGE_TWO_STEPS
                actual = _extract(cells, depth, requested, withdrawals)
                stage_two += actual
                stage_two_shortfall += requested - actual
                traces.append(dict(patch_id=patch.id, substep=step+1, depth_m=depth,
                                   relative_water=relative, reduction=kr, requested_mm=requested, actual_mm=actual))
        for cell_id, amount in withdrawals.items():
            if amount:
                requests.append(SoilFlux("evaporation", patch.id, cell_id, None, amount * area,
                                        f"{MODEL_VERSION}; {config.source}; {coeff.source}"))
        ending_memories[patch.id] = SoilEvaporationMemory(credit, depth, reference,
            f"Modeled drying memory from {MODEL_VERSION}; see initial memory and wetting inputs")
        ending_surface[patch.id] = replace(surface, stored_mm=surface.stored_mm-pond_evaporation,
            source=f"Modeled surface storage after {MODEL_VERSION}; see initial surface state")
        expected_patches.append(replace(patch, compartments=tuple(cells)))
        rows.append(dict(patch_id=patch.id, ponded_evaporation_l=pond_evaporation * area,
            soil_potential_l=soil_potential * area, stage_one_evaporation_l=stage_one * area,
            stage_two_evaporation_l=stage_two * area, soil_evaporation_l=(stage_one+stage_two) * area,
            soil_potential_not_evaporated_l=(soil_potential-stage_one-stage_two) * area,
            stage_one_storage_shortfall_l=(stage_one_requested-stage_one) * area,
            stage_two_storage_shortfall_l=stage_two_shortfall * area, soil_time_fraction=time_left))

    balance = apply_soil_fluxes(storage, requests)
    if balance.ending.patches != tuple(expected_patches):
        raise ArithmeticError("evaporation model and shared ledger disagree")
    totals = {name: sum((row[name] for row in rows), Fraction(0)) for name in rows[0] if name.endswith("_l")}
    totals["total_evaporation_l"] = totals["ponded_evaporation_l"] + totals["soil_evaporation_l"]
    initial_surface_l = sum((storage.area_m2*p.area_fraction*surface_states[p.id].stored_mm for p in storage.patches), Fraction(0))
    final_surface_l = sum((storage.area_m2*p.area_fraction*ending_surface[p.id].stored_mm for p in storage.patches), Fraction(0))
    totals["combined_balance_residual_l"] = balance.ending.water_l + final_surface_l - storage.water_l - initial_surface_l + totals["total_evaporation_l"]
    if totals["combined_balance_residual_l"]:
        raise ArithmeticError("soil and surface evaporation failed conservation")
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", soil_balance=balance,
        weather=weather, initial_surface=tuple(surface_states.items()), parameters=tuple(parameters.items()),
        initial_memories=tuple(memories.items()), coefficients=tuple(coefficients.items()), infiltrated_mm=tuple(recharge.items()),
        ending_surface=tuple(ending_surface.items()), ending_memories=tuple(ending_memories.items()),
        patches=tuple(rows), totals=totals, units={name: "L" for name in totals}, stage_two_trace=tuple(traces),
        trace_units=dict(depth_m="m", relative_water="1", reduction="1", requested_mm="mm", actual_mm="mm"),
        sources=tuple(sorted({SOURCE, ROUTING_REFERENCE, weather.source, storage.source,
            *(v.source for data in (surface_states, parameters, memories, coefficients) for v in data.values()),
            *(cell.soil_source for p in storage.patches for cell in p.compartments)})),
        assumptions=("Ponded water evaporates first; soil evaporation uses only the remaining fraction of the day with its supplied wet coefficient.",
                     "Stage-one memory is a bounded drying counter, not an independent water store. Local infiltration replenishes it without adding water to the profile.",
                     "Air dry equals half wilting point. Stage-two reduction uses Eq. 3.9h with 20 substeps; active depth expands in 1 mm increments within supplied limits.",
                     "Initial stage-two reference is supplied; a new reference is established at the end of stage one, bounded below by FC storage minus REW."),
        limitations=("Internal experimental evaporation component, not a complete AquaCrop model or irrigation prescription; no independent field validation.",
                     "Wet soil and ponded-water coefficients must already represent local canopy, shade, mulch and other applicable effects. No inference from crop, country or irrigation method.",
                     "Compartments remain uniformly mixed. Partial-depth extraction changes the full cell average; moisture gradients within that cell are unresolved. The maximum evaporation depth must be a cell boundary.",
                     "The exact stage-two reference and additive stage-one replenishment are explicit implementation choices; output equivalence with the upstream program is not claimed.",
                     "No crop uptake, snow/frozen soil, salinity, vapor transport or changing patch geometry is simulated here."))

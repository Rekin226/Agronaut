"""One daily drainage/infiltration step with explicit patch surface-water storage.

Internal experimental component based on AquaCrop 7.3 manual §§3.7.3–3.7.5.
It does not estimate rainfall runoff, crop demand, or irrigation application losses.
Supplied irrigation is water arriving at the surface, not water already in roots.
All input depths are local to their fixed patch, not averages over the entire field.
"""

from dataclasses import dataclass, replace
from fractions import Fraction

from .soil_drainage import (
    SOURCE,
    drain_soil_profile,
    drainage_response,
    water_content_for_response,
)
from .soil_storage import SoilStorage, _quantity, apply_downward_infiltration
from .validation import text

MODEL_VERSION = "soil-infiltration-1-aquacrop73-curve"
ROUTING_REFERENCE = "https://github.com/un-fao/fao-AquaCrop/blob/227bca87ee9b0e083ee262baf77908e38ba378a0/python_source_code/AquaCrop73_src/src/modules/simul.py"


@dataclass(frozen=True)
class SurfaceWaterState:
    stored_mm: Fraction
    capacity_mm: Fraction
    source: str

    def __post_init__(self):
        text("surface-water state source", self.source)
        for name in ("stored_mm", "capacity_mm"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), 2000))
        if self.stored_mm > self.capacity_mm:
            raise ValueError("stored surface water cannot exceed its supplied capacity")


@dataclass(frozen=True)
class SurfaceWaterSupply:
    rainfall_mm: Fraction
    rainfall_runoff_mm: Fraction
    irrigation_at_surface_mm: Fraction
    source: str

    def __post_init__(self):
        text("surface-water supply source", self.source)
        for name in ("rainfall_mm", "rainfall_runoff_mm", "irrigation_at_surface_mm"):
            object.__setattr__(self, name, _quantity(name, getattr(self, name), 2000))
        if self.rainfall_runoff_mm > self.rainfall_mm:
            raise ValueError("supplied rainfall runoff cannot exceed rainfall")


def drain_and_infiltrate(storage, parameters, surface_states, supplies):
    """Advance drainage then infiltration once, sharing each daily flow allowance.

    The source's infiltration procedure fills a cell towards the water content
    associated with its incoming volume/soil volume. The response is capped at the
    saturated response; the transmission allowance scales by response/peak and Ksat.
    Previously drained interface volumes consume the same daily Ksat allowance.

    Water unable to enter is retained up to the explicitly supplied surface storage
    capacity, then reported as overflow. No origin is attributed after mixing rain,
    irrigation and carried surface water. This is not a subdaily wetting simulation.
    """
    if not isinstance(storage, SoilStorage):
        raise ValueError("supply validated soil storage")
    patch_ids = {patch.id for patch in storage.patches}
    for label, data, cls in (("surface states", surface_states, SurfaceWaterState),
                             ("water supplies", supplies, SurfaceWaterSupply)):
        if not isinstance(data, dict) or set(data) != patch_ids:
            raise ValueError(f"supply exactly one of {label} per patch, including explicit zero values")
        if any(not isinstance(value, cls) for value in data.values()):
            raise ValueError(f"supply validated {label}")
    drainage = drain_soil_profile(storage, parameters)
    drained = drainage["balance"].ending
    already = {(row.request.patch_id, row.request.from_layer_id): row.actual_l
               for row in drainage["balance"].fluxes}
    interfaces, admitted, final_surface, rows, diagnostics = {}, {}, {}, [], []
    for patch in drained.patches:
        area = drained.area_m2 * patch.area_fraction
        supply, surface = supplies[patch.id], surface_states[patch.id]
        cells = patch.compartments
        sizes = [1000 * area * cell.thickness_m for cell in cells]
        initial = [cell.water_content * size for cell, size in zip(cells, sizes)]
        ending = initial.copy()
        initial_surface = surface.stored_mm * area
        rain, rain_runoff = supply.rainfall_mm * area, supply.rainfall_runoff_mm * area
        irrigation = supply.irrigation_at_surface_mm * area
        offered = initial_surface + rain - rain_runoff + irrigation
        entrance_limit = parameters[(patch.id, cells[0].id)].saturated_conductivity_mm_day * area
        attempted = min(offered, entrance_limit)
        pending, returned = attempted, Fraction(0)
        for index, (cell, size) in enumerate(zip(cells, sizes)):
            law = parameters[(patch.id, cell.id)]
            peak = drainage_response(cell, law, cell.saturation)
            requested_response = pending / size
            response = min(requested_response, peak)
            threshold = (cell.saturation if requested_response >= peak else
                         water_content_for_response(cell, law, response))
            if threshold is None:
                raise ArithmeticError("infiltration threshold unexpectedly unattainable")
            # Scaling makes the saturated transmission allowance equal Ksat.
            curve_limit = law.saturated_conductivity_mm_day * area * response / peak if peak else Fraction(0)
            daily_limit = law.saturated_conductivity_mm_day * area - already[(patch.id, cell.id)]
            through_limit = min(curve_limit, daily_limit)
            if through_limit < 0:
                raise ArithmeticError("drainage exhausted more than the daily interface allowance")
            stored = min(pending, max(Fraction(0), threshold * size - ending[index]))
            ending[index] += stored
            excess = max(Fraction(0), pending - stored - through_limit)
            pending -= stored + excess
            held = excess
            for receiving in range(index, -1, -1):
                if not excess:
                    break
                room = cells[receiving].saturation * sizes[receiving] - ending[receiving]
                amount = min(excess, room)
                ending[receiving] += amount
                excess -= amount
            returned += excess
            diagnostics.append(dict(patch_id=patch.id, layer_id=cell.id,
                threshold_water_content=threshold, curve_transmission_limit_l=curve_limit,
                prior_drainage_l=already[(patch.id, cell.id)], remaining_daily_limit_l=daily_limit,
                retained_or_returned_l=held, returned_to_surface_l=excess))

        accepted = attempted - returned
        cumulative = accepted
        for cell, original, final in zip(cells, initial, ending):
            cumulative -= final - original
            if cumulative < 0:
                raise ArithmeticError("infiltration solution produced a negative downward flux")
            if cumulative + already[(patch.id, cell.id)] > parameters[(patch.id, cell.id)].saturated_conductivity_mm_day * area:
                raise ArithmeticError("combined daily interface flow exceeded saturated conductivity")
            interfaces[(patch.id, cell.id)] = cumulative
        if cumulative != pending:
            raise ArithmeticError("infiltration solution failed the soil water balance")
        admitted[patch.id] = accepted
        rejected = offered - accepted
        ponding = min(rejected, surface.capacity_mm * area)
        overflow = rejected - ponding
        final_surface[patch.id] = replace(surface, stored_mm=ponding / area,
            source=f"Modeled surface storage from {MODEL_VERSION}; see initial state and supplies")
        residual = ponding - initial_surface - rain - irrigation + rain_runoff + accepted + overflow
        if residual:
            raise ArithmeticError("surface water accounting failed conservation")
        rows.append(dict(patch_id=patch.id, initial_surface_l=initial_surface, rainfall_l=rain,
            supplied_rainfall_runoff_l=rain_runoff, irrigation_at_surface_l=irrigation,
            offered_to_soil_l=offered, surface_entry_limit_l=entrance_limit,
            surface_entry_rejected_l=offered-attempted, returned_from_profile_l=returned,
            infiltrated_l=accepted, final_surface_l=ponding, modeled_surface_overflow_l=overflow,
            surface_balance_residual_l=residual,
            prior_deep_drainage_l=already[(patch.id, cells[-1].id)], additional_deep_drainage_l=pending))

    infiltration = apply_downward_infiltration(drained, admitted, interfaces, f"{MODEL_VERSION}; {SOURCE}")
    totals = {name: sum((row[name] for row in rows), Fraction(0)) for name in rows[0] if name != "patch_id"}
    totals["total_runoff_l"] = totals["supplied_rainfall_runoff_l"] + totals["modeled_surface_overflow_l"]
    totals["total_deep_drainage_l"] = drainage["balance"].external_output_l + infiltration.external_output_l
    residual = (infiltration.ending.water_l + totals["final_surface_l"] - storage.water_l
                - totals["initial_surface_l"] - totals["rainfall_l"] - totals["irrigation_at_surface_l"]
                + totals["total_runoff_l"] + totals["total_deep_drainage_l"])
    if residual:
        raise ArithmeticError("combined soil and surface accounting failed conservation")
    totals["combined_balance_residual_l"] = residual
    sources = {*drainage["sources"], ROUTING_REFERENCE, *(item.source for item in surface_states.values()),
               *(item.source for item in supplies.values())}
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", time_step_days=1,
        phase_order=("drainage", "infiltration"), drainage=drainage, infiltration=infiltration,
        initial_surface=tuple(surface_states.items()), supplies=tuple(supplies.items()),
        ending_surface=tuple(final_surface.items()), patches=tuple(rows), totals=totals,
        units={name: "L" for name in totals}, diagnostics=tuple(diagnostics), sources=tuple(sorted(sources)),
        diagnostic_units=dict(threshold_water_content="m3/m3", curve_transmission_limit_l="L",
            prior_drainage_l="L", remaining_daily_limit_l="L", retained_or_returned_l="L", returned_to_surface_l="L"),
        assumptions=("Daily runoff supplied for rainfall is removed once before entry; soil entry and profile limits may produce additional mixed-water overflow.",
                     "Rainfall and irrigation depths are local to each patch. Irrigation reaches the surface after upstream losses; it is not already admitted root-zone water.",
                     "Initial surface storage, residual rain and irrigation mix; surface entry is limited by topsoil Ksat. Surface storage capacity is supplied, including explicit zero.",
                     "The daily soil-interface conductivity allowance is shared by drainage and infiltration; no flow allowance is reset between those phases."),
        limitations=("Internal experimental wetting step, not a complete AquaCrop simulation or irrigation schedule.",
                     "No infiltration event timing, ponding-head enhancement, preferential/lateral flow, canopy interception or root-zone uptake is simulated.",
                     "Surface evaporation, soil evaporation and crop extraction are not yet coupled. Geometry is fixed and no source-specific tracer allocation is inferred.",
                     "No regional defaults or independent field validation. Existing net root-zone irrigation records cannot be silently reinterpreted as surface applications."))

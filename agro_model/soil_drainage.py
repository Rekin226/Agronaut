"""Daily redistribution above field capacity, using the published drainage curve.

Experimental implementation of AquaCrop 7.3 manual §3.7.3, equations 3.7a/c/d
and its compartment routing procedure. This is not the complete AquaCrop model.
Capillary rise, groundwater-adjusted FC, infiltration and extraction are separate.
Soil capacities and volumes refer to bulk soil; no coarse-fragment correction is
inferred. Tau and the saturated conductivity are explicit, sourced parameters.
"""

from dataclasses import dataclass
from fractions import Fraction
from math import expm1, log1p

from .soil_storage import SoilCompartment, SoilStorage, _quantity, apply_downward_drainage
from .validation import text

SOURCE = "https://ees.kuleuven.be/en/aquacrop/resources/reference-manual/chapter3aquacropv73.pdf"
MODEL_VERSION = "soil-drainage-1-aquacrop73-curve"


@dataclass(frozen=True)
class DrainageParameters:
    tau: Fraction
    saturated_conductivity_mm_day: Fraction
    source: str

    def __post_init__(self):
        text("drainage parameter source", self.source)
        object.__setattr__(self, "tau", _quantity("tau", self.tau, 1))
        object.__setattr__(self, "saturated_conductivity_mm_day",
                           _quantity("saturated conductivity", self.saturated_conductivity_mm_day, 10**6))


def _curve(cell, parameters):
    if not isinstance(cell, SoilCompartment) or not isinstance(parameters, DrainageParameters):
        raise ValueError("supply a validated compartment and drainage parameters")
    gap = cell.saturation - cell.field_capacity
    denominator = expm1(float(gap))
    if gap and not denominator:
        raise ValueError("saturation minus field capacity is below numerical resolution")
    return gap, parameters.tau * gap, denominator


def drainage_response(cell, parameters, water_content=None):
    """Volumetric decrease for one day of free drainage; Eq. 3.7a.

    Stable expm1 avoids cancellation near field capacity. The curve is evaluated
    in floating point; its result becomes an exact decimal fraction for accounting.
    Exact endpoints and a roundoff bound preserve 0 <= decrease <= theta - FC.
    """
    gap, peak, denominator = _curve(cell, parameters)
    theta = cell.water_content if water_content is None else _quantity("water content", water_content, 1)
    if not cell.evaporation_floor <= theta <= cell.saturation:
        raise ValueError("drainage water content must be within compartment storage bounds")
    excess = theta - cell.field_capacity
    if excess <= 0 or not peak:
        return Fraction(0)
    if excess == gap:
        return peak
    response = peak * Fraction(str(expm1(float(excess)) / denominator))
    return min(excess, max(Fraction(0), response))


def water_content_for_response(cell, parameters, response):
    """Invert Eq. 3.7a; return None if a requested drainability is unattainable.

    Zero response selects FC, the upper boundary of the zero-drainage region.
    No sentinel water content above saturation is inserted into storage.
    """
    _, peak, denominator = _curve(cell, parameters)
    response = _quantity("drainage response", response, 1)
    if response == 0:
        return cell.field_capacity
    if response > peak:
        return None
    if response == peak:
        return cell.saturation
    offset = log1p(float(response / peak) * denominator)
    return min(cell.saturation, max(cell.field_capacity, cell.field_capacity + Fraction(str(offset))))


def drain_soil_profile(storage, parameters):
    """Solve one drainage day without adding rain, irrigation or extraction.

    Compare receiving drainability with upstream mean drainability. Incoming water
    first raises a less-drainable cell towards that response (or saturation if it
    cannot match); remaining water passes down. Each layer's conductivity limits
    daily throughflow. Restricted water is retained locally, then above if needed.
    The resulting interface volumes are applied simultaneously by the shared ledger.
    """
    if not isinstance(storage, SoilStorage):
        raise ValueError("supply validated soil storage")
    keys = {(patch.id, cell.id) for patch in storage.patches for cell in patch.compartments}
    if not isinstance(parameters, dict) or set(parameters) != keys:
        raise ValueError("supply exactly one set of drainage parameters per compartment")
    if any(not isinstance(value, DrainageParameters) for value in parameters.values()):
        raise ValueError("supply validated drainage parameters")
    flows, diagnostics = {}, []
    for patch in storage.patches:
        area = storage.area_m2 * patch.area_fraction
        cells = patch.compartments
        sizes = [1000 * area * cell.thickness_m for cell in cells]
        initial = [cell.water_content * size for cell, size in zip(cells, sizes)]
        ending = initial.copy()
        incoming = above_volume = Fraction(0)
        for index, (cell, size) in enumerate(zip(cells, sizes)):
            law = parameters[(patch.id, cell.id)]
            rate = drainage_response(cell, law)
            upstream_rate = incoming / above_volume if above_volume else Fraction(0)
            if rate >= upstream_rate:
                ending[index] = initial[index] - rate * size
                outgoing = incoming + rate * size
            else:
                target = water_content_for_response(cell, law, upstream_rate)
                threshold = cell.saturation if target is None else target
                wet = initial[index] + incoming
                evaluated_theta = min(threshold, wet / size)
                loss = drainage_response(cell, law, evaluated_theta) * size
                ending[index] = evaluated_theta * size - loss
                outgoing = wet - ending[index]

            limit = law.saturated_conductivity_mm_day * area
            withheld = max(Fraction(0), outgoing - limit)
            incoming = min(outgoing, limit)
            pending = withheld
            retained_here = Fraction(0)
            # A restrictive layer can fill before the water backs up into upper
            # compartments. There is no external input, hence total free capacity
            # must suffice to retain the withheld part of the proposed drainage.
            for receiving in range(index, -1, -1):
                if not pending:
                    break
                room = cells[receiving].saturation * sizes[receiving] - ending[receiving]
                retained = min(pending, room)
                ending[receiving] += retained
                pending -= retained
                if receiving == index:
                    retained_here = retained
            if pending:
                raise ArithmeticError("restricted drainage could not be retained within the profile")
            diagnostics.append(dict(patch_id=patch.id, layer_id=cell.id,
                initial_response=rate, upstream_response=upstream_rate,
                proposed_outflow_l=outgoing, conductivity_limit_l=limit,
                retained_by_conductivity_l=withheld, returned_above_l=withheld-retained_here))
            above_volume += size

        cumulative = Fraction(0)
        for cell, original, final in zip(cells, initial, ending):
            cumulative += original - final
            if cumulative < 0:
                raise ArithmeticError("drainage solution produced an upward net interface flow")
            flows[(patch.id, cell.id)] = cumulative
            if cumulative > parameters[(patch.id, cell.id)].saturated_conductivity_mm_day * area:
                raise ArithmeticError("drainage solution exceeded an interface conductivity limit")
        if cumulative != incoming:
            raise ArithmeticError("drainage solution failed its profile water balance")

    balance = apply_downward_drainage(storage, flows, f"{MODEL_VERSION}; {SOURCE}")
    sources = {SOURCE, storage.source, *(law.source for law in parameters.values()),
               *(cell.soil_source for patch in storage.patches for cell in patch.compartments)}
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", time_step_days=1, balance=balance,
        parameters=tuple((patch.id, cell.id, parameters[(patch.id, cell.id)])
                         for patch in storage.patches for cell in patch.compartments),
        diagnostics=tuple(diagnostics), sources=tuple(sorted(sources)),
        diagnostic_units=dict(initial_response="m3/m3/day", upstream_response="m3/m3/day",
                              proposed_outflow_l="L", conductivity_limit_l="L",
                              retained_by_conductivity_l="L", returned_above_l="L"),
        assumptions=("One daily drainage step with fixed disjoint patch areas and contiguous bulk-soil compartments.",
                     "Supplied tau and saturated conductivity apply to each compartment; no soil-texture or location defaults.",
                     "Drainage below field capacity is neglected; each receiving layer can retain or restrict upstream drainage."),
        limitations=("Internal development component, not a complete AquaCrop simulation or irrigation prescription.",
                     "No infiltration, evaporation, root uptake, capillary rise, groundwater adjustment, lateral flow or salinity.",
                     "Analytic curve evaluations use floating point; volume accounting is exact for the computed fluxes.",
                     "No independent field validation; application requires appropriate parameters and review."))

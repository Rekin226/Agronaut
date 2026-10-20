"""Diagnostic prediction errors; conversion does not reconcile or alter soil state."""

from math import fsum, sqrt

from .soil_state import soil_reading_depletion
from .soil_storage import SoilPatch, _quantity
from .validation import number


def interval_water_content(patch, top_m, bottom_m):
    """Exact depth-weighted mean in one patch; each modeled cell is uniform."""
    if not isinstance(patch, SoilPatch):
        raise ValueError("supply a validated soil patch")
    top = _quantity("sample top_m", top_m, 10)
    bottom = _quantity("sample bottom_m", bottom_m, 10)
    if not top < bottom <= patch.compartments[-1].bottom_m:
        raise ValueError("sample interval must have positive thickness within the modeled profile")
    overlaps = [(cell, min(bottom, cell.bottom_m)-max(top, cell.top_m))
                for cell in patch.compartments if cell.bottom_m > top and cell.top_m < bottom]
    return dict(water_content=sum(cell.water_content*depth for cell, depth in overlaps)/(bottom-top),
                top_m=top, bottom_m=bottom,
                layers=[dict(id=cell.id, represented_thickness_m=depth, water_content=cell.water_content)
                        for cell, depth in overlaps])


def summarize_water_content_errors(errors):
    """Equal weight per reading, in m³/m³; positive means predicted wetter."""
    values = list(errors)
    if len(values) > 1000:
        raise ValueError("soil error summary accepts at most 1000 comparisons")
    for value in values:
        number("water content error", value, -1, 1)
    count = len(values)
    return dict(count=count, unit="m3/m3", bias=fsum(values)/count if count else None,
                mae=fsum(abs(v) for v in values)/count if count else None,
                rmse=sqrt(fsum(v*v for v in values)/count) if count else None,
                maximum_absolute_error=max(map(abs, values)) if count else None)


def compare_soil_state(soil, prediction, reading):
    if prediction.as_of != reading.as_of:
        raise ValueError("soil comparison requires the same end-of-day boundary")
    number("predicted depletion", prediction.depletion_mm, 0, soil.total_available_mm)
    base, reference = soil_reading_depletion(soil, reading)
    error = prediction.depletion_mm-reference
    return dict(predicted_depletion_mm=prediction.depletion_mm, reading_basis_depletion_mm=base,
                timing_adjustment_mm=reading.depletion_adjustment_mm, reference_depletion_mm=reference,
                error_mm=error, absolute_error_mm=abs(error))


def summarize_errors(errors):
    """Equal weight per supplied comparison; positive bias means predicted drier."""
    values = list(errors)
    if len(values) > 1000:
        raise ValueError("soil error summary accepts at most 1000 comparisons")
    for value in values:
        number("soil depletion error", value, -10000, 10000)
    count = len(values)
    return dict(count=count, bias_mm=fsum(values)/count if count else None,
                mae_mm=fsum(abs(v) for v in values)/count if count else None,
                rmse_mm=sqrt(fsum(v*v for v in values)/count) if count else None,
                maximum_absolute_error_mm=max(map(abs, values)) if count else None)

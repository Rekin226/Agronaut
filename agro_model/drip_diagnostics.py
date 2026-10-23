"""Pointwise drip diagnostics; no fitting, imputation or evidence promotion."""

from math import fsum, sqrt

from .hydraulics import GRAVITY_M_S2
from .validation import choice, number


def compare_emitter(predicted_flow_lph, predicted_head_m, recorded_flow_lph, recorded_pressure_kpa, density_kg_m3,
                    pressure_reference="unknown"):
    choice("pressure_reference", pressure_reference, ("gauge_at_emitter", "unknown"))
    number("predicted_flow_lph", predicted_flow_lph, 0, 1000)
    number("predicted_head_m", predicted_head_m, 0, 1000)
    if recorded_flow_lph is not None:
        number("recorded_flow_lph", recorded_flow_lph, 0, 3600000)
    if recorded_pressure_kpa is not None:
        number("recorded_pressure_kpa", recorded_pressure_kpa, 0, 10000)
    if density_kg_m3 is not None:
        number("water_density_kg_m3", density_kg_m3, 500, 2000)
    pressure = predicted_head_m*density_kg_m3*GRAVITY_M_S2/1000 if density_kg_m3 is not None else None
    result = {}
    for key, predicted, recorded in (("flow_lph", predicted_flow_lph, recorded_flow_lph),
                                      ("pressure_kpa", pressure, recorded_pressure_kpa)):
        error = predicted-recorded if predicted is not None and recorded is not None else None
        result[key] = dict(predicted=predicted, recorded=recorded, error=error,
                           status="compared" if error is not None else
                           "missing_water_density" if predicted is None else "missing_reading")
        if key == "pressure_kpa" and error is not None and pressure_reference == "unknown":
            result[key].update(error=None, status="unknown_pressure_reference")
    return result


def summarize_errors(errors):
    values = list(errors)
    if len(values) > 20000:
        raise ValueError("diagnostics accept at most 20000 paired readings")
    for value in values:
        number("diagnostic error", value, -3600000, 3600000)
    count = len(values)
    return dict(count=count, bias=fsum(values)/count if count else None,
                mae=fsum(abs(v) for v in values)/count if count else None,
                rmse=sqrt(fsum(v*v for v in values)/count) if count else None,
                maximum_absolute_error=max(map(abs, values)) if count else None)


def summarize_events(events):
    """Each event with known pairs has equal weight, regardless of sampling count."""
    groups = [summarize_errors(values) for values in events]
    known = [group for group in groups if group["count"]]
    count = len(known)
    return dict(event_count=count, paired_sample_count=sum(g["count"] for g in known),
                events_without_pairs=len(groups)-count,
                bias=fsum(g["bias"] for g in known)/count if count else None,
                mae=fsum(g["mae"] for g in known)/count if count else None,
                rmse=sqrt(fsum(g["rmse"]**2 for g in known)/count) if count else None,
                maximum_absolute_error=max(g["maximum_absolute_error"] for g in known) if count else None)

"""Arrival-time diagnostics; observations never update an advance prediction."""

from dataclasses import dataclass
from math import fsum, sqrt

from .validation import number, text


@dataclass(frozen=True)
class AdvanceReading:
    distance_m: float
    advance_min: float
    source: str

    def __post_init__(self):
        number("reading distance_m", self.distance_m, 0, 10000)
        number("reading advance_min", self.advance_min, 0, 10080)
        text("advance reading source", self.source)


def compare_advance_anchors(length_m, half_min, full_min, readings):
    number("length_m", length_m, .1, 10000)
    number("predicted half-length time", half_min, high=1440, positive=True)
    number("predicted full-length time", full_min, high=1440, positive=True)
    if half_min >= full_min:
        raise ValueError("predicted half-length arrival must precede full-length arrival")
    if not isinstance(readings, tuple) or not 1 <= len(readings) <= 10000:
        raise ValueError("readings must contain 1–10000 AdvanceReading records")
    by_distance = {}
    for reading in readings:
        if not isinstance(reading, AdvanceReading) or reading.distance_m > length_m:
            raise ValueError("readings must be validated and within the represented strip")
        if reading.distance_m in by_distance:
            raise ValueError("each measured position may occur only once")
        by_distance[reading.distance_m] = reading
    ordered = sorted(by_distance.values(), key=lambda reading: reading.distance_m)
    if any(right.advance_min < left.advance_min for left, right in zip(ordered, ordered[1:])):
        raise ValueError("recorded arrival times must be nondecreasing along the strip")
    if 0 in by_distance and by_distance[0].advance_min != 0:
        raise ValueError("inlet arrival must use event start as time zero")
    rows = []
    for anchor, distance, predicted in (("half_length", length_m/2, half_min), ("full_length", length_m, full_min)):
        observed = by_distance.get(distance)
        error = predicted-observed.advance_min if observed is not None else None
        rows.append(dict(anchor=anchor, distance_m=distance, predicted_min=predicted,
                         reference_min=observed.advance_min if observed else None,
                         reference_source=observed.source if observed else None,
                         error_min=error, absolute_error_min=abs(error) if error is not None else None,
                         status="compared" if observed else "missing_exact_station"))
    return rows


def summarize_advance_errors(errors):
    values = list(errors)
    if len(values) > 1000:
        raise ValueError("advance summaries accept at most 1000 comparisons")
    for value in values:
        number("advance error_min", value, -10080, 10080)
    count = len(values)
    return dict(count=count, bias_min=fsum(values)/count if count else None,
                mae_min=fsum(abs(v) for v in values)/count if count else None,
                rmse_min=sqrt(fsum(v*v for v in values)/count) if count else None,
                maximum_absolute_error_min=max(map(abs, values)) if count else None)

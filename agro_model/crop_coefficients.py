"""Dated, supplied single-Kc curves; no inferred phenology or regional defaults.

FAO-56 chapter 6 describes horizontal and sloped crop-coefficient segments.
Here endpoints are explicitly anchored to inclusive calendar dates. This avoids
guessing stage lengths or a day-count convention from a crop name.
"""

from dataclasses import dataclass
from datetime import date

from .water import CropCoefficient

CURVE_SOURCE = "https://www.fao.org/4/X0490E/x0490e0b.htm"


@dataclass(frozen=True)
class CoefficientPeriod:
    start: date
    end: date
    start_value: float
    end_value: float
    stage: str
    source: str

    def __post_init__(self):
        if type(self.start) is not date or type(self.end) is not date or self.end < self.start:
            raise ValueError("coefficient period requires ordered calendar start/end dates")
        CropCoefficient(self.start_value, self.source, self.stage)
        CropCoefficient(self.end_value, self.source, self.stage)
        if self.start == self.end and self.start_value != self.end_value:
            raise ValueError("a one-day coefficient period requires identical endpoint values")

    def at(self, day: date) -> CropCoefficient:
        if type(day) is not date or not self.start <= day <= self.end:
            raise ValueError("coefficient day falls outside the supplied period")
        span = (self.end - self.start).days
        # Exact supplied endpoints; no accumulated rounding from preceding days.
        if day == self.start or self.start_value == self.end_value:
            value = self.start_value
        elif day == self.end:
            value = self.end_value
        else:
            fraction = (day - self.start).days / span
            value = self.start_value + (self.end_value - self.start_value) * fraction
        return CropCoefficient(value, self.source, self.stage)


@dataclass(frozen=True)
class CoefficientSchedule:
    periods: tuple[CoefficientPeriod, ...]

    def __post_init__(self):
        if not isinstance(self.periods, tuple) or not 1 <= len(self.periods) <= 1000:
            raise ValueError("coefficient schedule requires 1 to 1000 periods")
        previous = None
        for period in self.periods:
            if not isinstance(period, CoefficientPeriod):
                raise ValueError("coefficient schedule requires typed periods")
            if previous and (period.start - previous.end).days != 1:
                raise ValueError("coefficient periods must be ordered and consecutive without overlaps or gaps")
            previous = period

    def at(self, day: date) -> tuple[CropCoefficient, CoefficientPeriod]:
        if type(day) is not date:
            raise ValueError("coefficient day must be a calendar date")
        for period in self.periods:
            if period.start <= day <= period.end:
                return period.at(day), period
        raise ValueError(f"coefficient schedule does not cover {day.isoformat()}; extrapolation is not allowed")

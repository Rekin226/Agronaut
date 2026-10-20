"""Explicit end-of-day reconciliation against a supplied root-zone measurement.

FAO-56 Eq. 87 uses average volumetric water content over the effective root zone.
Point readings, sampling coverage and subdaily water changes are not inferred.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .coefficients import FAO_SOIL
from .validation import aware_time, choice, number, text
from .water import CalculationResult, Soil, SoilState


@dataclass(frozen=True)
class SoilStateUpdate:
    as_of: date
    observed_at: datetime
    timezone: str
    quantity: str
    value: float
    unit: str
    represented_depth_m: float
    representation_note: str
    depletion_adjustment_mm: float
    adjustment_source: str
    source: str
    provenance: str
    quality: str

    def __post_init__(self):
        if type(self.as_of) is not date or self.as_of == date.max:
            raise ValueError("soil update as_of must be a calendar date")
        aware_time("observed_at", self.observed_at)
        text("timezone", self.timezone)
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("soil update timezone must be an IANA timezone") from exc
        units = {"soil_depletion": "mm", "soil_water_content": "m3/m3"}
        choice("soil quantity", self.quantity, units)
        if self.unit != units[self.quantity]:
            raise ValueError(f"{self.quantity} requires {units[self.quantity]}")
        number("soil value", self.value, 0, 1 if self.quantity == "soil_water_content" else None)
        number("represented_depth_m", self.represented_depth_m, high=10, positive=True)
        number("depletion_adjustment_mm", self.depletion_adjustment_mm)
        for key in ("representation_note", "adjustment_source", "source"):
            text(key, getattr(self, key))
        choice("soil provenance", self.provenance, ("measured", "reported", "estimated"))
        choice("soil quality", self.quality, ("unverified", "checked"))
        start = datetime.combine(self.as_of, time.min, zone).astimezone(timezone.utc)
        stamp = self.observed_at.astimezone(timezone.utc)
        if self.effective_at <= start or not start <= stamp <= self.effective_at:
            raise ValueError("soil reading must fall within the as_of local day or exactly its closing midnight")

    @property
    def effective_at(self):
        return datetime.combine(self.as_of + timedelta(days=1), time.min,
                                ZoneInfo(self.timezone)).astimezone(timezone.utc)


def soil_reading_depletion(soil: Soil, update: SoilStateUpdate):
    """Convert a represented reading to depletion without adopting it as model state."""
    if update.represented_depth_m != soil.root_depth_m:
        raise ValueError("represented depth must match the calculation's complete effective root depth")
    base = (1000 * (soil.field_capacity - update.value) * soil.root_depth_m
            if update.quantity == "soil_water_content" else update.value)
    adjusted = base + update.depletion_adjustment_mm
    number("adjusted soil depletion", adjusted, 0, soil.total_available_mm)
    return base, adjusted


def reconcile_soil_state(soil: Soil, prior: SoilState | None, update: SoilStateUpdate) -> CalculationResult:
    base, adopted = soil_reading_depletion(soil, update)
    if prior is not None:
        if prior.as_of != update.as_of:
            raise ValueError("soil update and prior state must describe the same end-of-day boundary")
        number("prior depletion", prior.depletion_mm, 0, soil.total_available_mm)
    previous = prior.depletion_mm if prior is not None else None
    return CalculationResult(
        values=dict(reading_basis_depletion_mm=base, timing_adjustment_mm=update.depletion_adjustment_mm,
                    adopted_depletion_mm=adopted, prior_model_depletion_mm=previous,
                    reconciliation_mm=None if previous is None else adopted - previous),
        units={key: "mm" for key in ("reading_basis_depletion_mm", "timing_adjustment_mm", "adopted_depletion_mm",
                                     "prior_model_depletion_mm", "reconciliation_mm")},
        inputs=dict(soil=asdict(soil), prior_state=asdict(prior) if prior else None, update=asdict(update)),
        sources=(FAO_SOIL, soil.source, update.source, update.representation_note, update.adjustment_source),
        assumptions=("Water content represents the whole effective root zone and calculation area as declared by the operator.",
                     "The supplied depletion adjustment maps the reading to the closing local midnight; positive means drier.",
                     "Adopted depletion replaces the modeled state at that boundary, after that day's water balance."),
        limitations=("A point sensor is not automatically representative of the whole root zone or field.",
                     "Sampling representativeness and calibration are not independently verified.",
                     "State reconciliation is not a measured irrigation, drainage, rainfall or evapotranspiration flux.",
                     "Out-of-range adjusted states are rejected, never clipped; no saturation or layered-soil model."),
    )

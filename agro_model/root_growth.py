"""Supplied daily rooting depth in a homogeneous FAO-56 root-zone bucket.

Example 38, note 5 accounts for the depletion of newly reached subsoil.
Root growth is a change in represented storage, not an irrigation input.
"""

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from .coefficients import FAO_SOIL
from .validation import number, text
from .water import CalculationResult, Soil, SoilState


@dataclass(frozen=True)
class RootZoneDay:
    day: date
    root_depth_m: float
    source: str
    added_layer_water_content: float | None = None
    added_layer_source: str | None = None

    def __post_init__(self):
        if type(self.day) is not date or self.day == date.min:
            raise ValueError("root-zone day must be a calendar date after date.min")
        number("root_depth_m", self.root_depth_m, high=10, positive=True)
        text("root depth source", self.source)
        if self.added_layer_water_content is not None:
            number("added_layer_water_content", self.added_layer_water_content, 0, 1)
            text("added_layer_source", self.added_layer_source)
        elif self.added_layer_source is not None:
            raise ValueError("added-layer source requires its water content")


def expand_root_zone(soil: Soil, prior: SoilState | None, supplied: RootZoneDay):
    """Apply the day's declared depth before rain, irrigation and ET.

Return the new soil, the state at the previous day's boundary represented over
the expanded depth, and a separate accounting result. Unknown state stays unknown.
"""
    if prior is not None:
        if prior.as_of != supplied.day - timedelta(days=1):
            raise ValueError("root growth requires the preceding end-of-day soil state")
        number("prior depletion", prior.depletion_mm, 0, soil.total_available_mm)
    increment = supplied.root_depth_m - soil.root_depth_m
    if increment < 0:
        raise ValueError("root depth cannot decrease in the homogeneous growth model")
    if increment > 0:
        number("added_layer_water_content", supplied.added_layer_water_content,
               soil.wilting_point, soil.field_capacity)
        text("added_layer_source", supplied.added_layer_source)
    elif supplied.added_layer_water_content is not None or supplied.added_layer_source is not None:
        raise ValueError("unchanged root depth must not include added-layer water inputs")
    new_soil = replace(soil, root_depth_m=supplied.root_depth_m)
    added_depletion = 0.0 if not increment else 1000 * (soil.field_capacity - supplied.added_layer_water_content) * increment
    added_available = 0.0 if not increment else 1000 * (supplied.added_layer_water_content - soil.wilting_point) * increment
    # Validated prior state and subsoil water mathematically bound this sum by TAW.
    # Protect only floating-point summation at the exact dry boundary.
    depletion = None if prior is None else min(new_soil.total_available_mm, prior.depletion_mm + added_depletion)
    state = None if prior is None else SoilState(prior.as_of, depletion,
        f"Root-depth accounting for {supplied.day}; prior: {prior.source}; depth: {supplied.source}")
    result = CalculationResult(
        values=dict(prior_root_depth_m=soil.root_depth_m, root_depth_m=new_soil.root_depth_m,
            added_depth_m=increment, prior_total_available_mm=soil.total_available_mm,
            total_available_mm=new_soil.total_available_mm, readily_available_mm=new_soil.readily_available_mm,
            added_layer_depletion_mm=added_depletion, added_layer_available_water_mm=added_available,
            prior_depletion_mm=None if prior is None else prior.depletion_mm, starting_depletion_mm=depletion),
        units={key: "m" if key.endswith("_m") else "mm" for key in (
            "prior_root_depth_m", "root_depth_m", "added_depth_m", "prior_total_available_mm", "total_available_mm",
            "readily_available_mm", "added_layer_depletion_mm", "added_layer_available_water_mm",
            "prior_depletion_mm", "starting_depletion_mm")},
        inputs=dict(prior_soil=asdict(soil), prior_state=asdict(prior) if prior else None, supplied=asdict(supplied)),
        sources=(FAO_SOIL, soil.source, supplied.source, *((supplied.added_layer_source,) if increment else ())),
        assumptions=("Supplied root depth applies at the start of the local day, before rainfall, irrigation and ET.",
                     "Newly reached soil has the same field capacity, wilting point and depletion fraction as the existing root zone.",
                     "Supplied added-layer water content describes that layer immediately before its inclusion; no field-capacity default.",
                     "Added-layer water is newly represented storage, not a measured rainfall or irrigation flux."),
        limitations=("No root contraction, layered hydraulic properties, subdaily growth or inferred crop rooting rates.",
                     "Subsoil water is supplied at each expansion; transport below the existing root zone is not simulated.",
                     "Root-depth and subsoil-water applicability require independent review; unknown prior depletion stays unknown."),
        missing_inputs=("soil_state",) if prior is None else (),
    )
    return new_soil, state, result

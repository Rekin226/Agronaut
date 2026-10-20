"""Post-event surface-irrigation evaluation from supplied measurements and intake data.

Kostiakov-Lewis infiltration at observation stations, followed by piecewise-linear
spatial integration. No advance/recession prediction, coefficient fitting, or cutoff
optimization is performed. Each request represents one measured furrow, border or basin.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import fsum

from .validation import aware_time, choice, number, text

MODEL_VERSION = "surface-event-0.1.0-kostiakov-lewis"
FAO_MEASUREMENTS = "https://www.fao.org/4/t0231e/t0231e05.htm"
FAO_EVALUATION = "https://www.fao.org/4/t0231e/t0231e06.htm"


@dataclass(frozen=True)
class Geometry:
    method: str
    length_m: float
    width_m: float
    slope_m_m: float
    source: str

    def __post_init__(self):
        choice("method", self.method, ("furrow", "border", "basin"))
        number("length_m", self.length_m, 0.1, 10000)
        number("width_m", self.width_m, 0.01, 10000)
        number("slope_m_m", self.slope_m_m, 0, 0.2)
        text("geometry source", self.source)


@dataclass(frozen=True)
class Event:
    started_at: datetime
    cutoff_min: float
    monitoring_end_min: float
    rainfall_mm: float
    initial_surface_water_l: float
    surface_drained: bool
    kind: str
    source: str

    def __post_init__(self):
        aware_time("started_at", self.started_at)
        number("monitoring_end_min", self.monitoring_end_min, high=10080, positive=True)
        number("cutoff_min", self.cutoff_min, high=self.monitoring_end_min, positive=True)
        number("rainfall_mm (rain-free events only)", self.rainfall_mm, 0, 0)
        number("initial_surface_water_l (initially unponded events only)", self.initial_surface_water_l, 0, 0)
        if self.surface_drained is not True:
            raise ValueError("evaluation requires surface_drained=true at monitoring end")
        choice("event kind", self.kind, ("observed", "scenario"))
        text("event source", self.source)
        try:
            self.ended_at
        except OverflowError as exc:
            raise ValueError("event end is outside the supported calendar") from exc

    @property
    def ended_at(self):
        # Elapsed minutes, including events crossing a local daylight-saving change.
        return self.started_at.astimezone(timezone.utc) + timedelta(minutes=self.monitoring_end_min)


@dataclass(frozen=True)
class Infiltration:
    k: float
    exponent: float
    steady_rate: float
    basis: str
    min_opportunity_min: float
    max_opportunity_min: float
    source: str

    def __post_init__(self):
        number("k", self.k, 0, 1e6)
        number("exponent", self.exponent, high=1, positive=True)
        number("steady_rate", self.steady_rate, 0, 1e6)
        choice("infiltration basis", self.basis, ("depth_mm", "volume_per_length_m2"))
        number("min_opportunity_min", self.min_opportunity_min, 0, 10080)
        number("max_opportunity_min", self.max_opportunity_min, self.min_opportunity_min, 10080)
        text("infiltration source", self.source)

    def cumulative(self, minutes):
        number("opportunity_min", minutes, self.min_opportunity_min, self.max_opportunity_min)
        return self.k * minutes**self.exponent + self.steady_rate * minutes


@dataclass(frozen=True)
class FlowInterval:
    start_min: float
    end_min: float
    mean_lps: float
    source: str

    def __post_init__(self):
        number("start_min", self.start_min, 0, 10080)
        number("end_min", self.end_min, 0, 10080)
        if self.end_min <= self.start_min:
            raise ValueError("flow interval end must follow its start")
        number("mean_lps", self.mean_lps, 0, 1e6)
        text("flow source", self.source)


@dataclass(frozen=True)
class Station:
    distance_m: float
    advance_min: float
    recession_min: float
    source: str

    def __post_init__(self):
        number("distance_m", self.distance_m, 0, 10000)
        number("advance_min", self.advance_min, 0, 10080)
        number("recession_min", self.recession_min, self.advance_min, 10080)
        text("station source", self.source)


@dataclass(frozen=True)
class Assessment:
    root_zone_deficit_mm: float | None
    balance_tolerance_fraction: float
    source: str

    def __post_init__(self):
        if self.root_zone_deficit_mm is not None:
            number("root_zone_deficit_mm", self.root_zone_deficit_mm, 0, 10000)
        number("balance_tolerance_fraction", self.balance_tolerance_fraction, 0, 0.2)
        text("assessment source", self.source)


@dataclass(frozen=True)
class SurfaceEvaluation:
    geometry: Geometry
    event: Event
    infiltration: Infiltration
    inflow: tuple[FlowInterval, ...]
    tailwater: tuple[FlowInterval, ...]
    stations: tuple[Station, ...]
    assessment: Assessment

    def __post_init__(self):
        for name, cls in (("geometry", Geometry), ("event", Event), ("infiltration", Infiltration),
                          ("assessment", Assessment)):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be a validated {cls.__name__}")
        for name, cls in (("inflow", FlowInterval), ("tailwater", FlowInterval), ("stations", Station)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not 1 <= len(values) <= 10000 or any(not isinstance(v, cls) for v in values):
                raise ValueError(f"{name} must contain 1–10000 validated {cls.__name__} records")
        expected = "volume_per_length_m2" if self.geometry.method == "furrow" else "depth_mm"
        if self.infiltration.basis != expected:
            raise ValueError(f"{self.geometry.method} requires infiltration basis {expected}")
        for name in ("inflow", "tailwater"):
            end = 0
            for interval in getattr(self, name):
                if interval.start_min != end:
                    raise ValueError(f"{name} intervals must cover time continuously from zero, without gaps or overlap")
                end = interval.end_min
                if name == "inflow":
                    if interval.end_min <= self.event.cutoff_min:
                        if interval.mean_lps <= 0:
                            raise ValueError("single continuous inflow required before cutoff; surge irrigation is unsupported")
                    elif interval.start_min < self.event.cutoff_min or interval.mean_lps != 0:
                        raise ValueError("inflow intervals must split at cutoff and explicitly contain zero flow afterwards")
            if end != self.event.monitoring_end_min:
                raise ValueError(f"{name} must cover the full monitoring window, including explicit zero-flow intervals")
        if len(self.stations) < 2 or self.stations[0].distance_m != 0 or self.stations[-1].distance_m != self.geometry.length_m:
            raise ValueError("stations must include inlet distance zero and the field end")
        if self.stations[0].advance_min != 0:
            raise ValueError("inlet advance time must be zero relative to event start")
        if any(f.mean_lps > 0 and f.end_min <= self.stations[-1].advance_min for f in self.tailwater):
            raise ValueError("tailwater cannot occur entirely before advance reaches the field end")
        for index, station in enumerate(self.stations):
            if station.recession_min > self.event.monitoring_end_min:
                raise ValueError("monitoring ends before a recorded recession")
            if index and (station.distance_m <= self.stations[index-1].distance_m or
                          station.advance_min < self.stations[index-1].advance_min):
                raise ValueError("stations must have increasing distance and nondecreasing advance time")


def _clipped_mean(left, right, cap):
    """Exact mean of min(linear profile, cap), including a crossing inside a segment."""
    low, high = sorted((left, right))
    if high <= cap:
        return (low+high)/2
    if low >= cap:
        return cap
    return cap - (cap-low)**2 / (2*(high-low))


def _integral(profile, cap=None):
    return fsum((right[0]-left[0]) * ((left[1]+right[1])/2 if cap is None
                else _clipped_mean(left[1], right[1], cap)) for left, right in zip(profile, profile[1:]))


def _low_quarter_mean(profile):
    """Length-weighted lowest quarter of a piecewise-linear depth profile, including ties."""
    length = profile[-1][0]
    low, high = min(v for _, v in profile), max(v for _, v in profile)
    for _ in range(70):
        level = (low+high)/2
        below = fsum((b[0]-a[0]) * (1 if max(a[1], b[1]) <= level else 0 if min(a[1], b[1]) >= level
                     else (level-min(a[1], b[1]))/abs(b[1]-a[1])) for a, b in zip(profile, profile[1:]))
        if below < length/4:
            low = level
        else:
            high = level
    level = (low+high)/2
    return max(0, (_integral(profile, level)-level*length*0.75)/(length*0.25))


def evaluate_surface(spec: SurfaceEvaluation) -> dict:
    geo, event, intake, assessment = spec.geometry, spec.event, spec.infiltration, spec.assessment
    area = geo.length_m * geo.width_m
    inflow = fsum((f.end_min-f.start_min)*60*f.mean_lps for f in spec.inflow)
    number("inflow volume", inflow, positive=True)
    tailwater = fsum((f.end_min-f.start_min)*60*f.mean_lps for f in spec.tailwater)
    rows, profile = [], []
    for station in spec.stations:
        opportunity = station.recession_min - station.advance_min
        infiltration = intake.cumulative(opportunity)
        # Furrow intake is m³ per metre of furrow, for this specific discharge and
        # wetted perimeter. Divide by represented spacing, then convert metres to mm.
        depth = infiltration*1000/geo.width_m if intake.basis == "volume_per_length_m2" else infiltration
        number("modeled infiltration depth", depth, 0)
        profile.append((station.distance_m, depth))
        rows.append(dict(**asdict(station), opportunity_min=opportunity, modeled_infiltration_mm=depth))
    infiltrated_l = _integral(profile)*geo.width_m  # mm × m² = litres
    residual = inflow-tailwater-infiltrated_l
    relative_residual = residual/inflow
    tolerance_l = assessment.balance_tolerance_fraction*inflow
    roundoff_l = 1e-9*inflow
    constraints = []
    if abs(residual) > tolerance_l+roundoff_l:
        constraints.append("water_balance_mismatch")
    if residual < -roundoff_l:
        constraints.append("modeled_outputs_exceed_inflow")
    accepted = not constraints
    deficit = assessment.root_zone_deficit_mm
    stored_l = _integral(profile, deficit)*geo.width_m if deficit is not None else None
    deep_l = max(0, infiltrated_l-stored_l) if stored_l is not None else None
    performance = None
    if accepted:
        mean_depth = infiltrated_l/area
        low_quarter = min(mean_depth, _low_quarter_mean(profile))
        adequate_length = None
        if deficit is not None:
            adequate_length = fsum((b[0]-a[0]) * (1 if min(a[1], b[1]) >= deficit
                else 0 if max(a[1], b[1]) <= deficit else (max(a[1], b[1])-deficit)/abs(b[1]-a[1]))
                for a, b in zip(profile, profile[1:]))
        performance = dict(low_quarter_mean_infiltration_mm=low_quarter,
                           low_quarter_distribution_uniformity=low_quarter/mean_depth if mean_depth else None,
                           modeled_root_zone_storage_l=stored_l, modeled_deep_percolation_l=deep_l,
                           application_efficiency=min(1, stored_l/inflow) if stored_l is not None else None,
                           storage_efficiency=stored_l/(deficit*area) if deficit else None,
                           adequately_irrigated_area_fraction=adequate_length/geo.length_m if deficit is not None else None,
                           deep_percolation_ratio=min(1, deep_l/inflow) if deep_l is not None else None,
                           tailwater_ratio=min(1, tailwater/inflow))
    result = dict(model_version=MODEL_VERSION, evidence_status="experimental", inputs=asdict(spec),
        event_kind=event.kind, event_ended_at=event.ended_at.isoformat(), area_m2=area,
        volumes=dict(inflow_l=inflow, tailwater_l=tailwater, modeled_infiltration_l=infiltrated_l,
                     unaccounted_l=residual, unaccounted_fraction=relative_residual),
        water_balance_accepted=accepted, binding_constraints=constraints, performance=performance,
        stations=rows, feasible=None, cutoff_recommendation=None,
        missing_inputs=["root_zone_deficit_mm"] if deficit is None else [],
        sources=sorted({FAO_MEASUREMENTS, FAO_EVALUATION, geo.source, event.source, intake.source,
                        assessment.source, *(f.source for f in (*spec.inflow, *spec.tailwater, *spec.stations))}),
        assumptions=[
            "One fully advanced, initially unponded furrow, border or basin; a single continuous inflow with optional cutbacks.",
            "Rain-free event with fully receded surface water at monitoring end; interval means cover all inflow and tailwater, including zeros.",
            "Opportunity time is measured recession minus advance; the supplied intake function applies uniformly to this event.",
            "Furrow width means represented furrow spacing; intake coefficients already describe this furrow's discharge and wetted perimeter.",
            "Depth is interpolated linearly between stations; all spatial statistics use distance weighting, not unweighted station averages.",
            "Root-zone deficit, when supplied, is spatially uniform; storage is the smaller of infiltrated depth and that deficit.",
            "Balance acceptance uses the supplied tolerance and also rejects modeled outputs exceeding inflow, apart from numerical roundoff.",
        ], limitations=[
            "Experimental post-event estimates; not field validation, cutoff advice or a prediction of a changed inflow schedule.",
            "Intake coefficients and their time envelope must be established for this soil, wetted geometry and irrigation conditions; no default intake family is selected.",
            "Sparse stations can miss infiltration variation; low-quarter uniformity and losses are modeled estimates, not direct measurements.",
            "Unaccounted water may reflect measurement error, intake-model error or unmodeled losses; it is not automatically deep percolation.",
            "No surge irrigation, rain, initial ponding, incomplete advance, cross-field variability or remaining surface storage is modeled.",
            "Supplied sources and the observed/scenario label are recorded, not independently verified.",
        ])
    for value in result["volumes"].values():
        number("volume result", value)
    return result

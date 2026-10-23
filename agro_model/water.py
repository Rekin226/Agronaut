"""Daily crop-water calculations and an explicit root-zone bucket.

Inputs are supplied data; this module never fetches weather or assumes missing rain.
The field applicability of these calculations is experimental. Net radiation is
supplied or explicitly estimated from solar inputs; the two quantities remain distinct.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from math import exp, log
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .coefficients import CONSTANTS, FAO_CROP, FAO_ET, FAO_METEOROLOGY, FAO_SOIL, c
from .radiation import estimate_net_radiation
from .validation import aware_time, choice, number, text

MODEL_VERSION = "field-water-0.1.0-fao56-1998"


@dataclass(frozen=True)
class CalculationResult:
    values: dict
    units: dict
    inputs: dict
    sources: tuple[str, ...]
    limitations: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    evidence_status: str = "experimental"
    model_version: str = MODEL_VERSION
    coefficients: dict = field(default_factory=dict)

    def __post_init__(self):
        if set(self.values) != set(self.units):
            raise ValueError("each calculated value must have an explicit unit")
        for key, value in self.values.items():
            if value is not None:
                number(key, value)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WeatherDay:
    day: date
    t_min_c: float
    t_max_c: float
    rh_min_pct: float
    rh_max_pct: float
    wind_m_s: float
    wind_height_m: float
    elevation_m: float
    net_radiation_mj_m2: float
    rainfall_mm: float | None
    source: str
    kind: str = "observed"
    issued_at: datetime | None = None

    def __post_init__(self):
        _validate_meteorology(self)
        number("net_radiation_mj_m2", self.net_radiation_mj_m2, -50, 60)


@dataclass(frozen=True)
class SolarWeatherDay:
    """Daily meteorology with an explicitly requested FAO-56 radiation estimate."""

    day: date
    t_min_c: float
    t_max_c: float
    rh_min_pct: float
    rh_max_pct: float
    wind_m_s: float
    wind_height_m: float
    elevation_m: float
    solar_radiation_mj_m2: float
    latitude_deg: float
    rainfall_mm: float | None
    source: str
    kind: str = "observed"
    issued_at: datetime | None = None
    input_type: str = "solar_radiation_fao56"

    def __post_init__(self):
        _validate_meteorology(self)
        number("solar_radiation_mj_m2", self.solar_radiation_mj_m2, 0, 60)
        number("latitude_deg", self.latitude_deg, -90, 90)
        choice("input_type", self.input_type, ("solar_radiation_fao56",))


def _validate_meteorology(self):
    if type(self.day) is not date:
        raise ValueError("day must be a calendar date")
    # Software operating envelope, not claimed universal physical limits.
    number("t_min_c", self.t_min_c, -80, 60)
    number("t_max_c", self.t_max_c, -80, 60)
    if self.t_min_c > self.t_max_c:
        raise ValueError("t_min_c must not exceed t_max_c")
    number("rh_min_pct", self.rh_min_pct, 0, 100)
    number("rh_max_pct", self.rh_max_pct, 0, 100)
    if self.rh_min_pct > self.rh_max_pct:
        raise ValueError("rh_min_pct must not exceed rh_max_pct")
    number("wind_m_s", self.wind_m_s, 0, 100)
    number("wind_height_m", self.wind_height_m, 0.5, 100)
    number("elevation_m", self.elevation_m, -500, 9000)
    if self.rainfall_mm is not None:
        number("rainfall_mm", self.rainfall_mm, 0)
    text("source", self.source)
    choice("kind", self.kind, ("observed", "forecast", "historical", "scenario"))
    if self.kind == "forecast" and self.issued_at is None:
        raise ValueError("forecast requires issued_at")
    if self.issued_at is not None:
        aware_time("issued_at", self.issued_at)


@dataclass(frozen=True)
class ReferenceETDay:
    """Supplied grass-reference ET total for one local calendar day.

    This is an alternative input to meteorology, never a reconstruction of it.
    Retrieval and model issuance are deliberately different pieces of provenance.
    """

    day: date
    et0_mm_day: float
    rainfall_mm: float | None
    source: str
    kind: str
    timezone: str
    period_hours: float
    method: str
    input_type: str = "supplied_et0"
    reference_surface: str = "grass"
    issued_at: datetime | None = None
    retrieved_at: datetime | None = None
    source_snapshot_id: str | None = None

    def __post_init__(self):
        if type(self.day) is not date:
            raise ValueError("day must be a calendar date")
        number("et0_mm_day", self.et0_mm_day, 0)
        if self.rainfall_mm is not None:
            number("rainfall_mm", self.rainfall_mm, 0)
        text("source", self.source)
        text("method", self.method)
        text("timezone", self.timezone)
        number("period_hours", self.period_hours, positive=True, high=48)
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc
        start = datetime.combine(self.day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(self.day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        if self.period_hours != (end - start).total_seconds() / 3600:
            raise ValueError("period_hours must cover exactly the local calendar day")
        choice("input_type", self.input_type, ("supplied_et0",))
        choice("reference_surface", self.reference_surface, ("grass",))
        choice("kind", self.kind, ("observed", "forecast", "historical", "scenario"))
        if self.kind == "forecast" and self.issued_at is None and self.retrieved_at is None:
            raise ValueError("forecast requires issued_at or a separately labelled retrieved_at")
        for name in ("issued_at", "retrieved_at"):
            value = getattr(self, name)
            if value is not None:
                aware_time(name, value)
        if self.source_snapshot_id is not None:
            text("source_snapshot_id", self.source_snapshot_id)


@dataclass(frozen=True)
class RainfallInput:
    """A separately sourced daily total, replacing rainfall in the weather input."""

    day: date
    rainfall_mm: float
    source: str
    provenance: str
    quality: str
    water_record_id: str | None = None

    def __post_init__(self):
        if type(self.day) is not date:
            raise ValueError("rainfall day must be a calendar date")
        number("rainfall_mm", self.rainfall_mm, 0)
        text("rainfall source", self.source)
        choice("rainfall provenance", self.provenance, ("measured", "reported", "estimated"))
        choice("rainfall quality", self.quality, ("unverified", "checked"))
        if self.water_record_id is not None:
            text("water_record_id", self.water_record_id)


@dataclass(frozen=True)
class CropCoefficient:
    value: float
    source: str
    stage: str

    def __post_init__(self):
        # Deliberately supplied for the actual stage and wetting regime. A generic crop
        # name does not identify a locally applicable Kc, especially under drip/mulch.
        number("crop coefficient", self.value, 0, 3)
        text("coefficient source", self.source)
        text("crop stage", self.stage)


@dataclass(frozen=True)
class Soil:
    field_capacity: float
    wilting_point: float
    root_depth_m: float
    depletion_fraction: float
    source: str

    def __post_init__(self):
        number("field_capacity", self.field_capacity, 0, 1)
        number("wilting_point", self.wilting_point, 0, 1)
        if self.wilting_point >= self.field_capacity:
            raise ValueError("wilting_point must be below field_capacity")
        number("root_depth_m", self.root_depth_m, high=10, positive=True)
        number("depletion_fraction", self.depletion_fraction, positive=True)
        if self.depletion_fraction >= 1:
            raise ValueError("depletion_fraction must be below 1")
        text("soil source", self.source)

    @property
    def total_available_mm(self) -> float:
        # FAO-56 Eq. 82; 1000 converts metres water to millimetres.
        return 1000 * (self.field_capacity - self.wilting_point) * self.root_depth_m

    @property
    def readily_available_mm(self) -> float:
        # FAO-56 Eq. 83. Caller supplies the applicable depletion fraction, including
        # any ET/climate/soil adjustment; it is not silently inferred from crop name.
        return self.depletion_fraction * self.total_available_mm


@dataclass(frozen=True)
class SoilState:
    as_of: date
    depletion_mm: float
    source: str

    def __post_init__(self):
        if type(self.as_of) is not date:
            raise ValueError("as_of must be a calendar date (end of day)")
        number("depletion_mm", self.depletion_mm, 0)
        text("soil state source", self.source)


def _saturation_pressure(temperature: float) -> float:
    return c("svp_base") * exp(c("svp_multiplier") * temperature / (temperature + c("svp_offset")))


def reference_et(weather: WeatherDay | SolarWeatherDay | ReferenceETDay) -> CalculationResult:
    """FAO-56 daily grass-reference Penman–Monteith, Eq. 6 with daily G = 0."""
    if isinstance(weather, ReferenceETDay):
        return CalculationResult(
            values={"et0_mm_day": weather.et0_mm_day}, units={"et0_mm_day": "mm/day"},
            inputs={"weather": asdict(weather)}, sources=(weather.source, weather.method),
            assumptions=(f"Supplied grass-reference ET total over a {weather.period_hours:g}-hour local calendar day.",),
            limitations=("Reference ET is imported, not recalculated from meteorological measurements.",
                         "Method, weather quality and site representativeness require independent review.",
                         "Retrieval time does not establish forecast model issuance or a new model run."),
        )
    t = (weather.t_min_c + weather.t_max_c) / 2
    minimum, maximum = _saturation_pressure(weather.t_min_c), _saturation_pressure(weather.t_max_c)
    es = (minimum + maximum) / 2  # Eq. 12
    ea = (minimum * weather.rh_max_pct + maximum * weather.rh_min_pct) / 200  # Eq. 17
    delta = c("slope_multiplier") * _saturation_pressure(t) / (t + c("svp_offset")) ** 2
    pressure = c("pressure_base") * (
        (c("pressure_temp") - c("pressure_lapse") * weather.elevation_m) / c("pressure_temp")
    ) ** c("pressure_exponent")
    gamma = c("psychrometric") * pressure
    u2 = weather.wind_m_s * c("wind_numerator") / log(
        c("wind_height_multiplier") * weather.wind_height_m - c("wind_height_offset"))
    radiation = None
    if isinstance(weather, SolarWeatherDay):
        radiation = estimate_net_radiation(day=weather.day, latitude_deg=weather.latitude_deg,
            elevation_m=weather.elevation_m, t_min_c=weather.t_min_c, t_max_c=weather.t_max_c,
            actual_vapour_pressure_kpa=ea, solar_radiation_mj_m2=weather.solar_radiation_mj_m2)
        net_radiation = radiation["values"]["net_radiation_mj_m2"]
    else:
        net_radiation = weather.net_radiation_mj_m2
    eto = (c("energy_to_water") * delta * net_radiation
           + gamma * c("aerodynamic") / (t + c("temperature_offset")) * u2 * (es - ea)
           ) / (delta + gamma * (1 + c("wind_denominator") * u2))
    return CalculationResult(
        values={"et0_mm_day": max(0.0, eto), "wind_at_2m_m_s": u2,
                "actual_vapour_pressure_kpa": ea, "saturation_vapour_pressure_kpa": es},
        units={"et0_mm_day": "mm/day", "wind_at_2m_m_s": "m/s",
               "actual_vapour_pressure_kpa": "kPa", "saturation_vapour_pressure_kpa": "kPa"},
        inputs={"weather": asdict(weather), **({"radiation_estimate": radiation} if radiation else {})},
        sources=(FAO_ET, FAO_METEOROLOGY, weather.source),
        assumptions=("24-hour grass-reference surface; soil heat flux G = 0 (FAO-56 Eq. 42).",
                     "Negative reference ET is reported as zero; condensation is not modelled.",
                     *(radiation["assumptions"] if radiation else ())),
        limitations=("No crop stress, rainfall accounting, or irrigation timing in reference ET.",
                     *(radiation["limitations"] if radiation else
                       ("Net radiation is supplied; solar radiation must not be substituted.",)),
                     "Weather measurement quality and site representativeness are not verified."),
        coefficients={key: asdict(value) for key, value in CONSTANTS.items()},
    )


def crop_water_demand(weather: WeatherDay | SolarWeatherDay | ReferenceETDay, coefficient: CropCoefficient,
                      area_m2: float) -> CalculationResult:
    number("area_m2", area_m2, positive=True)
    et = reference_et(weather)
    demand = et.values["et0_mm_day"] * coefficient.value  # FAO-56 Eq. 58
    return CalculationResult(
        values={"et0_mm_day": et.values["et0_mm_day"], "etc_mm_day": demand,
                "crop_demand_l_day": demand * area_m2},
        units={"et0_mm_day": "mm/day", "etc_mm_day": "mm/day", "crop_demand_l_day": "L/day"},
        inputs={**et.inputs, "coefficient": asdict(coefficient), "area_m2": area_m2},
        sources=(*et.sources, FAO_CROP, coefficient.source), assumptions=et.assumptions,
        limitations=(*et.limitations,
                     "Supplied single Kc must match crop stage, climate, wetting and mulch.",
                     "Potential crop demand is not the net or gross irrigation requirement."),
        coefficients={**et.coefficients, "kc": asdict(coefficient)},
    )


def water_balance_step(soil: Soil, state: SoilState, weather: WeatherDay | SolarWeatherDay | ReferenceETDay, *,
                       potential_et_mm: float, net_irrigation_mm: float,
                       runoff_mm: float, rainfall: RainfallInput | None = None) -> CalculationResult:
    """One explicit daily bucket step; water arrives before ET, excess drains first.

    Irrigation is NET water entering the root zone. Runoff is supplied rainfall runoff;
    irrigation losses must already have been removed. No implicit efficiency factors.
    """
    number("potential_et_mm", potential_et_mm, 0)
    number("net_irrigation_mm", net_irrigation_mm, 0)
    number("depletion_mm", state.depletion_mm, 0, soil.total_available_mm)
    if weather.day != state.as_of + timedelta(days=1):
        raise ValueError("weather day must immediately follow soil state's as_of date")
    if rainfall is not None and rainfall.day != weather.day:
        raise ValueError("separate rainfall must match the weather day")
    rainfall_mm = weather.rainfall_mm if rainfall is None else rainfall.rainfall_mm
    if rainfall_mm is None:
        raise ValueError("rainfall_mm is required for water balance; missing is not zero")
    number("runoff_mm", runoff_mm, 0, rainfall_mm)
    incoming = rainfall_mm - runoff_mm + net_irrigation_mm
    drainage = max(0.0, incoming - state.depletion_mm)
    wetted_depletion = max(0.0, state.depletion_mm - incoming)
    taw, raw = soil.total_available_mm, soil.readily_available_mm
    ks = min(1.0, (taw - wetted_depletion) / (taw - raw))  # FAO-56 Eq. 84
    actual_et = min(taw - wetted_depletion, ks * potential_et_mm)  # Eq. 81 approximation
    depletion = wetted_depletion + actual_et
    return CalculationResult(
        values={"depletion_mm": depletion, "actual_et_mm": actual_et,
                "drainage_mm": drainage, "stress_coefficient": ks,
                "total_available_mm": taw, "readily_available_mm": raw},
        units={"depletion_mm": "mm", "actual_et_mm": "mm", "drainage_mm": "mm",
               "stress_coefficient": "1", "total_available_mm": "mm", "readily_available_mm": "mm"},
        inputs={"soil": asdict(soil), "state": asdict(state), "weather": asdict(weather),
                "potential_et_mm": potential_et_mm, "net_irrigation_mm": net_irrigation_mm,
                "runoff_mm": runoff_mm,
                **({"rainfall_override": asdict(rainfall)} if rainfall is not None else {})},
        sources=(FAO_SOIL, soil.source, state.source, weather.source,
                 *((rainfall.source,) if rainfall is not None else ())),
        assumptions=("Water enters before evapotranspiration; excess above field capacity drains first.",
                     "Homogeneous root zone with fixed depth within this daily step; capillary rise is zero; runoff is supplied.",
                     "Single-coefficient stress approximation; supplied depletion fraction is applicable.",
                     *(("Separate rainfall replaces weather rainfall; the two totals are never added.",)
                       if rainfall is not None else ())),
        limitations=("No subdaily timing, layered soil, within-day root growth or capillary rise.",
                     "Single-coefficient stress also reduces evaporation; dual Kc is not modelled.",
                     "No salinity or spatial modelling of partially wetted drip root zones.",
                     *(("Recorded rainfall does not make weather or evapotranspiration an observation.",)
                       if rainfall is not None else ())),
    )


def irrigation_requirement(soil: Soil, state: SoilState | None, *, area_m2: float,
                           efficiency: float, efficiency_source: str) -> CalculationResult:
    """Budget to refill an initialized root zone at/above its depletion threshold.

    This describes the given end-of-day state, not a forecast of future weather or
    a method-specific hydraulic prescription. Unknown initialization gives no amount.
    """
    number("area_m2", area_m2, positive=True)
    number("efficiency", efficiency, high=1, positive=True)
    text("efficiency_source", efficiency_source)
    net = None
    if state is not None:
        number("depletion_mm", state.depletion_mm, 0, soil.total_available_mm)
        net = state.depletion_mm if state.depletion_mm >= soil.readily_available_mm else 0.0
    gross = None if net is None else net / efficiency
    return CalculationResult(
        values={"net_irrigation_mm": net, "gross_irrigation_mm": gross,
                "gross_irrigation_l": None if gross is None else gross * area_m2,
                "readily_available_mm": soil.readily_available_mm},
        units={"net_irrigation_mm": "mm", "gross_irrigation_mm": "mm",
               "gross_irrigation_l": "L", "readily_available_mm": "mm"},
        inputs={"soil": asdict(soil), "state": None if state is None else asdict(state),
                "area_m2": area_m2, "efficiency": efficiency,
                "efficiency_source": efficiency_source},
        sources=(FAO_SOIL, soil.source, efficiency_source),
        assumptions=("Refill to field capacity only at/above the supplied depletion threshold.",),
        missing_inputs=("soil_state",) if state is None else (),
        limitations=("Water budget only: no delivery rate, pressure or infiltration feasibility check.",
                     "No future rainfall, allocation priority, salinity or leaching requirement.",
                     "Uniform root-zone bucket; not a localized-wetting drip scheduling model."),
    )

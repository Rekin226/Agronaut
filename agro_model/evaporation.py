"""FAO-56 daily surface evaporation with supplied basal coefficients and fractions.

This is a separate potential crop-demand calculation. Its surface depletion is
not the root-zone depletion used by the single-coefficient irrigation budget.
"""

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from math import isclose

from .validation import number, text
from .water import RainfallInput, ReferenceETDay, reference_et

SOURCE = "https://www.fao.org/4/X0490E/x0490e0c.htm"
MODEL_VERSION = "dual-crop-water-2-fao56-1998"


@dataclass(frozen=True)
class EvaporationSoil:
    field_capacity: float
    wilting_point: float
    depth_m: float
    readily_evaporable_mm: float
    source: str

    def __post_init__(self):
        number("surface field capacity", self.field_capacity, high=1, positive=True)
        number("surface wilting point", self.wilting_point, 0, 1)
        if self.wilting_point >= self.field_capacity:
            raise ValueError("surface wilting point must be below field capacity")
        number("evaporating layer depth_m", self.depth_m, 0.01, 0.3)
        number("readily_evaporable_mm", self.readily_evaporable_mm, 0)
        if self.readily_evaporable_mm >= self.total_evaporable_mm:
            raise ValueError("readily evaporable water must be strictly below total evaporable water")
        text("surface soil source", self.source)

    @property
    def total_evaporable_mm(self):
        return 1000*(self.field_capacity-0.5*self.wilting_point)*self.depth_m


@dataclass(frozen=True)
class EvaporationState:
    as_of: date
    depletion_mm: float
    source: str

    def __post_init__(self):
        if type(self.as_of) is not date or self.as_of == date.max:
            raise ValueError("surface state as_of must be a calendar date before date.max")
        number("surface depletion_mm", self.depletion_mm, 0)
        text("surface state source", self.source)


@dataclass(frozen=True)
class EvaporationDay:
    day: date
    kcb: float
    kc_max: float
    cover_fraction: float
    wetted_fraction: float
    exposed_wetted_fraction: float
    net_irrigation_mm: float
    runoff_mm: float
    source: str
    water_source: str | None = None

    def __post_init__(self):
        if type(self.day) is not date or self.day == date.min:
            raise ValueError("evaporation day must be a calendar date after date.min")
        number("basal coefficient kcb", self.kcb, 0, 3)
        number("kc_max", self.kc_max, 0.05, 3)
        if self.kc_max < self.kcb+0.05 and not isclose(self.kc_max, self.kcb+0.05, rel_tol=0, abs_tol=1e-12):
            raise ValueError("kc_max must be at least kcb + 0.05")
        number("cover_fraction", self.cover_fraction, 0, 0.99)
        number("wetted_fraction", self.wetted_fraction, 0.01, 1)
        number("exposed_wetted_fraction", self.exposed_wetted_fraction, 0.01, 1)
        limit = min(1-self.cover_fraction, self.wetted_fraction)
        if self.exposed_wetted_fraction > limit and not isclose(self.exposed_wetted_fraction, limit, rel_tol=0, abs_tol=1e-12):
            raise ValueError("exposed/wetted fraction cannot exceed either exposed soil or wetted fraction")
        number("net_irrigation_mm", self.net_irrigation_mm, 0, 2000)
        number("runoff_mm", self.runoff_mm, 0, 2000)
        text("daily coefficients, fractions and water source", self.source)
        if self.water_source is not None:
            text("daily irrigation and runoff source", self.water_source)


def replay_evaporation(soil, initial, weather, days, area_m2, *, rainfall_overrides=()):
    number("area_m2", area_m2, high=1e12, positive=True)
    if not 1 <= len(weather) == len(days) <= 1000:
        raise ValueError("supply 1–1000 weather and evaporation days with equal coverage")
    if any(not isinstance(record, ReferenceETDay) for record in weather):
        raise ValueError("dual crop-water calculation requires supplied grass-reference ET records")
    if not isinstance(rainfall_overrides, (list, tuple)) or len(rainfall_overrides) > len(days):
        raise ValueError("rainfall overrides must be a sequence with at most one entry per weather day")
    rainfall = {}
    for entry in rainfall_overrides:
        if not isinstance(entry, RainfallInput):
            raise ValueError("rainfall overrides must be validated RainfallInput records")
        if entry.day in rainfall:
            raise ValueError("duplicate rainfall override day")
        rainfall[entry.day] = entry
    if set(rainfall) - {record.day for record in weather}:
        raise ValueError("rainfall overrides must fall within the weather window")
    tew, rew = soil.total_evaporable_mm, soil.readily_evaporable_mm
    number("initial surface depletion", initial.depletion_mm, 0, tew)
    previous_day, depletion = initial.as_of, initial.depletion_mm
    rows, sources = [], {SOURCE, soil.source, initial.source}
    for record, supplied in zip(weather, days):
        if record.day != supplied.day or record.day != previous_day+timedelta(days=1):
            raise ValueError("weather and evaporation days must match and immediately follow the initial state without gaps")
        number("et0_mm_day", record.et0_mm_day, 0, 100)
        rain = rainfall.get(record.day)
        rain_mm = record.rainfall_mm if rain is None else rain.rainfall_mm
        number("rainfall_mm", rain_mm, 0, 2000)
        number("runoff_mm", supplied.runoff_mm, 0, rain_mm)
        et = reference_et(record)
        et0 = et.values["et0_mm_day"]
        few = supplied.exposed_wetted_fraction
        incoming = rain_mm-supplied.runoff_mm+supplied.net_irrigation_mm/supplied.wetted_fraction
        drainage = max(0.0, incoming-depletion)
        start = max(0.0, depletion-incoming)
        kr = 1.0 if start <= rew else (tew-start)/(tew-rew)
        ke_requested = min(kr*(supplied.kc_max-supplied.kcb), few*supplied.kc_max)
        requested_evaporation = ke_requested*et0
        # Eq. 78 bounds depletion. Conserve the finite surface store explicitly
        # rather than clipping the ending state while retaining impossible ET.
        evaporation = min(requested_evaporation, max(0.0, (tew-start)*few))
        ke = evaporation/et0 if et0 else ke_requested
        basal = supplied.kcb*et0
        ending = min(tew, start+evaporation/few)  # roundoff at the dry endpoint only
        residual = ending-depletion+incoming-drainage-evaporation/few
        values = dict(prior_depletion_mm=depletion, surface_wetting_mm=incoming, surface_percolation_mm=drainage,
            starting_depletion_mm=start, evaporation_reduction=kr, requested_ke=ke_requested, effective_ke=ke,
            basal_et_mm=basal, soil_evaporation_mm=evaporation, potential_crop_et_mm=basal+evaporation,
            potential_crop_demand_l=(basal+evaporation)*area_m2, ending_depletion_mm=ending,
            surface_balance_residual_mm=residual, evaporation_water_limited=requested_evaporation > evaporation+1e-12)
        for key, value in values.items():
            if key != "evaporation_water_limited":
                number(key, value)
        rows.append(dict(day=record.day, weather_kind=record.kind, reference_et=et.to_dict(), inputs=asdict(supplied), values=values,
                         rainfall=dict(basis="separate_input", **asdict(rain)) if rain else
                         dict(basis="weather", rainfall_mm=rain_mm, source=record.source)))
        sources.update((*et.sources, supplied.source))
        if rain:
            sources.add(rain.source)
        if supplied.water_source:
            sources.add(supplied.water_source)
        previous_day, depletion = record.day, ending
    units = {key: "mm" for key in rows[0]["values"]}
    units.update(evaporation_reduction="1", requested_ke="1", effective_ke="1", potential_crop_demand_l="L",
                 evaporation_water_limited="boolean")
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", total_evaporable_mm=tew,
        readily_evaporable_mm=rew, days=rows, units=units, sources=sorted(sources), missing_inputs=[],
        ending_state=dict(as_of=previous_day, depletion_mm=depletion, source=f"Modeled surface state from {MODEL_VERSION}"),
        assumptions=["FAO-56 (1998) equations 69, 71, 73–75 and 77–79; supplied Kcb and Kc max must already suit local crop and climate. No coefficient or cover inference.",
                     "All daily rain and net irrigation enter before evaporation, as in the early-wetting procedure in Example 35. Every rainfall amount is included; supplied runoff is subtracted.",
                     "Surface-layer transpiration is explicitly assumed negligible and basal ET is unstressed. Kcb ET0 includes basal diffusive evaporation and is not a pure measured transpiration flux.",
                     "Rain depth is whole-surface; irrigation depth is averaged over the whole field and divided by physical wetted fraction. Evaporation is field-average and divided by exposed/wetted fraction for the surface store.",
                     "Daily exposed/wetted fractions are supplied and may account for canopy shading. Changes retain the FAO scalar depletion approximation; spatial wet/dry patches are not separately tracked.",
                     "Evaporation is limited by remaining surface water; a binding limit is reported and effective Ke is reduced to preserve water balance."],
        limitations=["Potential crop demand is not an irrigation requirement or a measurement. Surface depletion/percolation are not root-zone depletion/deep drainage.",
                     "No root stress, layered transport, changing wetting-pattern geometry, mulch, snow/frozen soil, salinity or subdaily wetting sequence is simulated.",
                     "Daily fractions and negligible surface-layer transpiration need applicable evidence, especially for drip under canopy and shallow roots. No automatic rainfall wetting rule or shading correction.",
                     "Separate daily rainfall replaces weather rainfall, never adds to it. Water totals do not establish wetting time, wetting fractions or applicability of retained crop/surface assumptions.",
                     "This separate calculation does not update the single-coefficient root-zone budget, actual water logs, equipment or resource commitments. Coupled root/surface accounting and independent field validation remain open."])

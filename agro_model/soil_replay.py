"""Dated coupled water replay with immutable daily inputs and continuation state."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .soil_drainage import DrainageParameters
from .soil_evaporation import (
    PatchEvaporationCoefficients,
    SoilEvaporationMemory,
    SoilEvaporationParameters,
    validate_evaporation_patch,
)
from .soil_infiltration import SurfaceWaterState, SurfaceWaterSupply
from .soil_roots import CropTranspirationCoefficients, RootLayerActivity, RootUptakeParameters
from .soil_storage import SoilStorage
from .soil_water import COUPLED_MODEL_VERSION, advance_soil_water_day
from .validation import text
from .water import ReferenceETDay

MODEL_VERSION = "soil-replay-1-dated-shared-storage"
MAX_REPLAY_DAYS = 3660


def _pairs(label, values, cls, *, cells=False):
    if not isinstance(values, tuple) or not values or len(values) > 1000:
        raise ValueError(f"{label} must be a nonempty immutable tuple of key/value pairs")
    keys = set()
    for pair in values:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError(f"{label} entries must be key/value tuples")
        key, value = pair
        if cells:
            if not isinstance(key, tuple) or len(key) != 2:
                raise ValueError(f"{label} keys require patch and compartment IDs")
            for part in key:
                text(label, part)
        else:
            text(label, key)
        if key in keys or not isinstance(value, cls):
            raise ValueError(f"{label} requires unique keys and validated values")
        keys.add(key)
    return tuple(sorted(values, key=lambda pair: pair[0]))


@dataclass(frozen=True)
class SoilWaterState:
    as_of: date
    timezone: str
    soil: SoilStorage
    surface: tuple[tuple[str, SurfaceWaterState], ...]
    evaporation_memories: tuple[tuple[str, SoilEvaporationMemory], ...]
    drainage_parameters: tuple[tuple[tuple[str, str], DrainageParameters], ...]
    evaporation_parameters: tuple[tuple[str, SoilEvaporationParameters], ...]
    source: str

    def __post_init__(self):
        if type(self.as_of) is not date or self.as_of == date.max:
            raise ValueError("state must have an end-of-day date before date.max")
        text("state timezone", self.timezone)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("state timezone must be an IANA timezone") from exc
        text("state source", self.source)
        if not isinstance(self.soil, SoilStorage):
            raise ValueError("state requires validated soil storage")
        patch_keys = {p.id for p in self.soil.patches}
        cell_keys = {(p.id, c.id) for p in self.soil.patches for c in p.compartments}
        for name, cls, cells in (("surface", SurfaceWaterState, False),
                                 ("evaporation_memories", SoilEvaporationMemory, False),
                                 ("drainage_parameters", DrainageParameters, True),
                                 ("evaporation_parameters", SoilEvaporationParameters, False)):
            value = _pairs(name, getattr(self, name), cls, cells=cells)
            if set(dict(value)) != (cell_keys if cells else patch_keys):
                raise ValueError(f"{name} must cover the complete stored geometry")
            object.__setattr__(self, name, value)
        configs, memories = dict(self.evaporation_parameters), dict(self.evaporation_memories)
        for patch in self.soil.patches:
            validate_evaporation_patch(patch, configs[patch.id], memories[patch.id])

    @property
    def surface_water_l(self):
        surface = dict(self.surface)
        return sum((self.soil.area_m2*p.area_fraction*surface[p.id].stored_mm for p in self.soil.patches), Fraction(0))


@dataclass(frozen=True)
class SoilWaterDay:
    weather: ReferenceETDay
    supplies: tuple[tuple[str, SurfaceWaterSupply], ...]
    evaporation_coefficients: tuple[tuple[str, PatchEvaporationCoefficients], ...]
    root_parameters: tuple[tuple[str, RootUptakeParameters], ...]
    root_activities: tuple[tuple[tuple[str, str], RootLayerActivity], ...]
    transpiration_coefficients: tuple[tuple[str, CropTranspirationCoefficients], ...]
    source: str

    def __post_init__(self):
        if not isinstance(self.weather, ReferenceETDay):
            raise ValueError("daily input requires validated grass-reference ET weather")
        text("daily input source", self.source)
        for name, cls, cells in (("supplies", SurfaceWaterSupply, False),
                                 ("evaporation_coefficients", PatchEvaporationCoefficients, False),
                                 ("root_parameters", RootUptakeParameters, False),
                                 ("root_activities", RootLayerActivity, True),
                                 ("transpiration_coefficients", CropTranspirationCoefficients, False)):
            object.__setattr__(self, name, _pairs(name, getattr(self, name), cls, cells=cells))


def _validate_day(state, supplied, expected):
    if not isinstance(supplied, SoilWaterDay):
        raise ValueError("replay requires validated immutable daily inputs")
    if supplied.weather.day != expected:
        raise ValueError("replay days must be consecutive and start immediately after the initial state")
    if supplied.weather.timezone != state.timezone:
        raise ValueError("all replay weather must use the initial state's timezone")
    patch_keys = {p.id for p in state.soil.patches}
    cell_keys = {(p.id, c.id) for p in state.soil.patches for c in p.compartments}
    for name in ("supplies", "evaporation_coefficients", "root_parameters", "root_activities", "transpiration_coefficients"):
        if set(dict(getattr(supplied, name))) != (cell_keys if name == "root_activities" else patch_keys):
            raise ValueError(f"daily {name} must cover the complete initial geometry")
    roots, activity = dict(supplied.root_parameters), dict(supplied.root_activities)
    for patch in state.soil.patches:
        depth = roots[patch.id].root_depth_m
        if depth not in {c.bottom_m for c in patch.compartments}:
            raise ValueError("prepare the soil grid at every supplied root depth before replay")
        if any(c.top_m >= depth and activity[(patch.id, c.id)].maximum_rate_per_day for c in patch.compartments):
            raise ValueError("daily activity below root depth must be zero")


def replay_soil_water(initial, days):
    """Replay explicit consecutive local days; return snapshots and full balances.

    Soil geometry/properties, drainage law, evaporation law and surface capacities
    remain those of the initial state. Root depth/activity may change explicitly
    within that existing grid; newly rooted soil is already represented water,
    never an added irrigation or initialization flux. No caller state is mutated.
    """
    if not isinstance(initial, SoilWaterState):
        raise ValueError("supply a validated dated initial state")
    if not isinstance(days, (list, tuple)) or not 1 <= len(days) <= MAX_REPLAY_DAYS:
        raise ValueError(f"supply 1–{MAX_REPLAY_DAYS} explicit daily inputs")
    snapshots = tuple(days)
    if initial.as_of.toordinal()+len(snapshots) >= date.max.toordinal():
        raise ValueError("replay exceeds the supported calendar range")
    for index, supplied in enumerate(snapshots, start=1):
        _validate_day(initial, supplied, initial.as_of+timedelta(days=index))
    state, records = initial, []
    for supplied in snapshots:
        result = advance_soil_water_day(state.soil, dict(state.drainage_parameters), dict(state.surface),
            dict(supplied.supplies), dict(state.evaporation_parameters), dict(state.evaporation_memories),
            supplied.weather, dict(supplied.evaporation_coefficients), dict(supplied.root_parameters),
            dict(supplied.root_activities), dict(supplied.transpiration_coefficients))
        ending = replace(state, as_of=supplied.weather.day, soil=result["ending"],
            surface=result["ending_surface"], evaporation_memories=result["ending_memories"],
            source=f"{MODEL_VERSION}; modeled end of {supplied.weather.day} in {state.timezone}")
        records.append(dict(input=supplied, initial=state, ending=ending, calculation=result))
        state = ending
    totals = {name: sum((record["calculation"]["totals"][name] for record in records), Fraction(0))
              for name in ("rainfall_l", "irrigation_at_surface_l", "infiltrated_l", "supplied_rainfall_runoff_l",
                "modeled_surface_overflow_l", "total_runoff_l", "total_deep_drainage_l", "soil_evaporation_l",
                "ponded_evaporation_l", "total_evaporation_l", "actual_transpiration_l",
                "potential_transpiration_l", "unmet_transpiration_potential_l", "actual_evapotranspiration_l")}
    totals.update(initial_soil_l=initial.soil.water_l, initial_surface_l=initial.surface_water_l,
                  final_soil_l=state.soil.water_l, final_surface_l=state.surface_water_l)
    totals["combined_balance_residual_l"] = (totals["initial_soil_l"]+totals["initial_surface_l"]
        +totals["rainfall_l"]+totals["irrigation_at_surface_l"]-totals["final_soil_l"]-totals["final_surface_l"]
        -totals["total_runoff_l"]-totals["total_deep_drainage_l"]-totals["actual_evapotranspiration_l"])
    if totals["combined_balance_residual_l"]:
        raise ArithmeticError("replay failed cumulative soil and surface conservation")
    return dict(model_version=MODEL_VERSION, coupled_model_version=COUPLED_MODEL_VERSION,
        evidence_status="experimental", initial=initial, inputs=snapshots, ending=state,
        days=tuple(records), totals=totals, units={name: "L" for name in totals},
        sources=tuple(sorted({initial.source, *(d.source for d in snapshots),
            *(s for record in records for s in record["calculation"]["sources"])})),
        assumptions=("Initial water state is the end of the preceding local calendar day; every subsequent local day has explicit weather and patch inputs.",
            "Soil, surface storage and drying memory advance together. Stored configuration and geometry remain fixed, and each daily result retains its input and boundary states.",
            "Changing supplied root depth only changes accessible storage inside the existing profile; no water is created, removed or set to field capacity."),
        limitations=("Internal experimental replay with supplied crop/soil parameters; not independently field-validated or an irrigation prescription.",
            "No observation assimilation, parameter reset, patch-area change, root-growth prediction, owner authorization or database adoption occurs here.",
            "All daily physical-model assumptions and limitations remain applicable; local-day totals are not subdaily event timing."))

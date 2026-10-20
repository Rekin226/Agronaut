"""Explicit public input/report boundary for the experimental coupled soil model."""

from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.resource_timing import _number
from agro_model.resources import _amount, _object, _rows
from agro_model.soil_drainage import DrainageParameters
from agro_model.soil_evaporation import (
    PatchEvaporationCoefficients,
    SoilEvaporationMemory,
    SoilEvaporationParameters,
)
from agro_model.soil_infiltration import SurfaceWaterState, SurfaceWaterSupply
from agro_model.soil_replay import SoilWaterDay, SoilWaterState, replay_soil_water
from agro_model.soil_roots import (
    CropTranspirationCoefficients,
    RootLayerActivity,
    RootUptakeParameters,
)
from agro_model.soil_snapshots import dumps_soil_snapshot, loads_soil_snapshot
from agro_model.soil_storage import SoilCompartment, SoilPatch, SoilStorage
from agro_model.validation import choice, text
from agro_model.water import ReferenceETDay

from .field_io import _date, parse_weather_records
from .weather_provider import validate_sources

MAX_DAYS = 366


def _decimal(value):
    return Fraction(_amount(value), 1_000_000)


def _record(cls, value, text_fields=("source",)):
    value = _object(value, tuple(f.name for f in fields(cls)), cls.__name__)
    return cls(**{key: item if key in text_fields or (key == "stage_two_start_fraction" and item is None)
                  else _decimal(item) for key, item in value.items()})


def _indexed(rows, label, maximum=100):
    seen = set()
    for row in _rows(rows, label, maximum):
        if not isinstance(row, dict):
            raise ValueError(f"{label} entries must be objects")
        identity = text(f"{label} id", row.get("id"))
        if identity in seen:
            raise ValueError(f"{label} IDs must be unique")
        seen.add(identity)
    return rows


def _initial(data, area, zone, today):
    initial = data["initial_state"]
    if isinstance(initial, dict) and "checkpoint" in initial:
        initial = _object(initial, ("checkpoint", "source"), "continuation state")
        text("continuation source", initial["source"])
        state = loads_soil_snapshot(initial["checkpoint"], expected_kind="state")
        if state.timezone != zone or state.soil.area_m2 != area:
            raise ValueError("checkpoint area and timezone must match the request")
        return state
    initial = _object(initial, ("as_of", "kind", "quality", "source", "patches"), "initial state")
    choice("initial state kind", initial["kind"], ("observed", "scenario"))
    choice("initial state quality", initial["quality"], ("unverified", "checked"))
    as_of = _date(initial["as_of"])
    if initial["kind"] == "observed" and as_of >= today:
        raise ValueError("observed initial state requires a completed local day")
    patches, surface, memories, drainage, evaporation = [], [], [], [], []
    for patch in _indexed(initial["patches"], "initial patches"):
        patch = _object(patch, ("id", "area_fraction", "layers", "surface_water", "evaporation_parameters", "evaporation_memory"), "initial patch")
        cells = []
        for row in _indexed(patch["layers"], "soil layers"):
            row = _object(row, (*tuple(f.name for f in fields(SoilCompartment)), "drainage"), "soil layer")
            cells.append(_record(SoilCompartment, {k: v for k, v in row.items() if k != "drainage"}, ("id", "soil_source")))
            drainage.append(((patch["id"], row["id"]), _record(DrainageParameters, row["drainage"])))
        patches.append(SoilPatch(patch["id"], _decimal(patch["area_fraction"]), tuple(cells)))
        surface.append((patch["id"], _record(SurfaceWaterState, patch["surface_water"])))
        evaporation.append((patch["id"], _record(SoilEvaporationParameters, patch["evaporation_parameters"])))
        memories.append((patch["id"], _record(SoilEvaporationMemory, patch["evaporation_memory"])))
    soil = SoilStorage(area, tuple(patches), initial["source"])
    return SoilWaterState(as_of, zone, soil, tuple(surface), tuple(memories), tuple(drainage), tuple(evaporation), initial["source"])


def _day(raw, weather):
    raw = _object(raw, ("day", "patches", "source"), "coupled water day")
    if _date(raw["day"]) != weather.day:
        raise ValueError("daily patch inputs must match the ordered weather dates")
    supplies, evaporation, roots, activities, transpiration = [], [], [], [], []
    for patch in _indexed(raw["patches"], "daily patches"):
        patch = _object(patch, ("id", "water", "evaporation_coefficients", "root_parameters", "root_activities", "transpiration_coefficients"), "daily patch")
        key = patch["id"]
        supplies.append((key, _record(SurfaceWaterSupply, patch["water"])))
        evaporation.append((key, _record(PatchEvaporationCoefficients, patch["evaporation_coefficients"])))
        roots.append((key, _record(RootUptakeParameters, patch["root_parameters"])))
        transpiration.append((key, _record(CropTranspirationCoefficients, patch["transpiration_coefficients"])))
        for row in _indexed(patch["root_activities"], "root activity layers"):
            row = _object(row, ("id", *tuple(f.name for f in fields(RootLayerActivity))), "root activity")
            activities.append(((key, row["id"]), _record(RootLayerActivity, {k: v for k, v in row.items() if k != "id"})))
    return SoilWaterDay(weather, tuple(supplies), tuple(evaporation), tuple(roots), tuple(activities), tuple(transpiration), raw["source"])


def _json_value(value):
    if isinstance(value, Fraction):
        return _number(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return {f.name: _json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def evaluate_coupled_water_request(payload):
    optional = ("weather_sources",) if isinstance(payload, dict) and "weather_sources" in payload else ()
    data = _object(payload, ("schema_version", "site", "area_m2", "crop", "initial_state", "conditions", "weather", "days")+optional, "coupled soil-water request")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _object(data["site"], ("name", "timezone"), "site")
    text("site name", site["name"])
    text("site timezone", site["timezone"])
    try:
        zone = ZoneInfo(site["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    crop = _object(data["crop"], ("name", "source"), "crop")
    for key, value in crop.items():
        text(key, value)
    conditions = _object(data["conditions"], ("bulk_soil_properties", "no_capillary_rise", "no_frozen_soil", "no_salinity_effects", "source"), "model conditions")
    for key, value in conditions.items():
        if key == "source":
            text("conditions source", value)
        elif value is not True:
            raise ValueError(f"{key} must be explicitly true for this model")
    rows = _rows(data["days"], "daily inputs", MAX_DAYS)
    if not isinstance(data["weather"], list) or len(data["weather"]) != len(rows):
        raise ValueError("weather must cover exactly the supplied days")
    today = datetime.now(zone).date()
    try:
        weather = parse_weather_records(data["weather"])
    except OverflowError as exc:
        raise ValueError("weather dates exceed supported calendar boundaries") from exc
    for record in weather:
        if not isinstance(record, ReferenceETDay):
            raise ValueError("coupled water requires supplied grass-reference ET0 for every day")
        if record.kind in ("observed", "historical") and record.day >= today:
            raise ValueError("observed/historical weather requires completed local days")
    validate_sources(data["weather"], data.get("weather_sources", []), site["timezone"])
    initial = _initial(data, _decimal(data["area_m2"]), site["timezone"], today)
    days = tuple(_day(row, record) for row, record in zip(rows, weather))
    replay = replay_soil_water(initial, days)
    # Keep exact restart states and raw inputs; report daily diagnostics without
    # repeatedly embedding every intermediate storage copy from each phase.
    records = []
    for row in replay["days"]:
        calculation = row["calculation"]
        records.append(dict(day=row["input"].weather.day.isoformat(), weather_kind=row["input"].weather.kind,
            totals=_json_value(calculation["totals"]), units=calculation["units"],
            ending_soil=_json_value(row["ending"].soil), ending_surface=_json_value(row["ending"].surface),
            evaporation_memory=_json_value(row["ending"].evaporation_memories),
            evaporation_patches=_json_value(calculation["evaporation"]["patches"]),
            root_patches=_json_value(calculation["roots"]["patches"]), root_layers=_json_value(calculation["roots"]["layers"]),
            root_layer_units=calculation["roots"]["layer_units"], root_status_units=calculation["roots"]["status_units"]))
    assumptions = sorted({*replay["assumptions"], *(a for row in replay["days"] for a in row["calculation"]["assumptions"]),
        *(a for snapshot in data.get("weather_sources", []) for a in snapshot["assumptions"])})
    limitations = [*sorted({*replay["limitations"], *(limit for row in replay["days"] for limit in row["calculation"]["limitations"])}),
        "Crop/root and evaporation coefficients are supplied for this context; this does not establish crop calibration or regional validity.",
        "Irrigation inputs are local patch depths arriving at the surface after upstream losses, not existing net-root-zone water logs.",
        "The initial state may be an unverified observation or scenario; a modeled continuation remains modeled. This calculation does not adopt an operational farm state."]
    return dict(model_version=replay["model_version"], coupled_model_version=replay["coupled_model_version"],
        evidence_status="experimental", inputs=deepcopy(payload), initial_as_of=initial.as_of.isoformat(),
        period_start=days[0].weather.day.isoformat(), period_end=days[-1].weather.day.isoformat(),
        initial_checkpoint=dumps_soil_snapshot(initial), ending_checkpoint=dumps_soil_snapshot(replay["ending"]),
        days=records, totals=_json_value(replay["totals"]), units=replay["units"], missing_inputs=[],
        assumptions=assumptions, limitations=limitations,
        sources=sorted({*replay["sources"], crop["source"], conditions["source"], data["initial_state"]["source"]}))


def coupled_water_input_schema():
    def obj(fields):
        return dict(type="object", additionalProperties=False, required=list(fields), properties=fields)
    string = dict(type="string", minLength=1)
    decimal = dict(type="string", pattern=r"^(0|[1-9][0-9]{0,14})(\.[0-9]{1,6})?$")
    def record(cls, text_fields=("source",)):
        return obj({f.name: string if f.name in text_fields else
                    (dict(anyOf=[decimal, dict(type="null")]) if f.name == "stage_two_start_fraction" else decimal)
                    for f in fields(cls)})
    def array(items, maximum=100):
        return dict(type="array", minItems=1, maxItems=maximum, items=items)
    layer = record(SoilCompartment, ("id", "soil_source"))
    layer["properties"]["drainage"] = record(DrainageParameters)
    layer["required"].append("drainage")
    initial = obj(dict(as_of=dict(type="string", format="date"), kind=dict(enum=["observed", "scenario"]),
        quality=dict(enum=["unverified", "checked"]), source=string, patches=array(obj(dict(id=string, area_fraction=decimal,
            layers=array(layer), surface_water=record(SurfaceWaterState), evaporation_parameters=record(SoilEvaporationParameters),
            evaporation_memory=record(SoilEvaporationMemory))))))
    activity = record(RootLayerActivity)
    activity["properties"]["maximum_rate_per_day"] = dict(decimal,
        description="Maximum root sink in m3 water per m3 bulk soil per day, before local stress; explicitly zero below roots.")
    activity["properties"]["id"] = string
    activity["required"].append("id")
    patch = obj(dict(id=string, water=record(SurfaceWaterSupply), evaporation_coefficients=record(PatchEvaporationCoefficients),
        root_parameters=record(RootUptakeParameters), root_activities=array(activity), transpiration_coefficients=record(CropTranspirationCoefficients)))
    # Share the existing weather/evidence contract rather than introduce new provider semantics.
    from .evaporation_io import evaporation_input_schema
    weather_schema = evaporation_input_schema()["properties"]
    weather = deepcopy(weather_schema["weather"])
    weather["maxItems"] = MAX_DAYS
    schema = obj(dict(schema_version=dict(type="integer", const=1), site=obj(dict(name=string, timezone=string)),
        area_m2=decimal, crop=obj(dict(name=string, source=string)),
        initial_state=dict(oneOf=[initial, obj(dict(checkpoint=string, source=string))]),
        conditions=obj({**{k: dict(type="boolean", const=True) for k in
            ("bulk_soil_properties", "no_capillary_rise", "no_frozen_soil", "no_salinity_effects")}, "source": string}),
        weather=weather, days=array(obj(dict(day=dict(type="string", format="date"), patches=array(patch), source=string)), MAX_DAYS)))
    schema["properties"]["weather_sources"] = weather_schema["weather_sources"]
    return schema


def render_coupled_water_report(result):
    lines = ["Agronaut coupled soil-water replay — EXPERIMENTAL",
             f"{result['inputs']['crop']['name']} · {result['period_start']} to {result['period_end']}",
             "Water inputs are local patch depths at the soil surface."]
    for row in result["days"]:
        t = row["totals"]
        lines.append(f"{row['day']} · {row['weather_kind']}: rainfall {t['rainfall_l']['value']} L; "
                     f"irrigation at surface {t['irrigation_at_surface_l']['value']} L; evaporation {t['total_evaporation_l']['value']} L; "
                     f"transpiration {t['actual_transpiration_l']['value']} L; drainage {t['total_deep_drainage_l']['value']} L; "
                     f"runoff {t['total_runoff_l']['value']} L; soil water {t['final_soil_l']['value']} L")
    lines.append(f"Cumulative water-balance residual: {result['totals']['combined_balance_residual_l']['exact']} L")
    if result["inputs"].get("weather_sources"):
        lines.extend(["", "Retained weather:"])
        for record in result["inputs"]["weather"]:
            lines.append(f"{record['day']} · {record['kind']}: grass-reference ET0 {record['et0_mm_day']:g} mm; "
                         "rainfall used comes from the explicit patch supplies.")
        for snapshot in result["inputs"]["weather_sources"]:
            requested, grid = snapshot["request"], snapshot["response"]
            lines.append(f"Retrieved {snapshot['retrieved_at']} (download time); requested location "
                         f"{requested['latitude']:g}, {requested['longitude']:g}; provider grid {grid['latitude']:g}, {grid['longitude']:g}. "
                         f"Snapshot {snapshot['id']}")
    for label in ("assumptions", "limitations", "sources"):
        lines.extend(["", label.title()+":", *("- "+row for row in result[label])])
    return "\n".join(lines)

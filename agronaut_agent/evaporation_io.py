"""Explicit request contract for a sourced daily dual-coefficient demand sequence."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.evaporation import (
    EvaporationDay,
    EvaporationSoil,
    EvaporationState,
    replay_evaporation,
)
from agro_model.resources import _object
from agro_model.validation import text
from agro_model.water import RainfallInput

from .field_io import _construct, _date, json_ready, parse_weather_records
from .weather_provider import validate_sources


def evaluate_evaporation_request(payload):
    optional = tuple(key for key in ("weather_sources", "rainfall_overrides") if isinstance(payload, dict) and key in payload)
    data = _object(payload, ("schema_version", "site", "area_m2", "crop", "surface_soil", "initial_state", "conditions", "weather", "days")+optional, "dual crop-water request")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _object(data["site"], ("name", "timezone"), "site")
    for key in ("name", "timezone"):
        text(key, site[key])
    try:
        ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    crop = _object(data["crop"], ("name", "source"), "crop")
    for key in ("name", "source"):
        text(key, crop[key])
    conditions = _object(data["conditions"], ("basal_unstressed", "surface_transpiration_negligible", "no_mulch_or_frozen_soil", "source"), "conditions")
    for key in ("basal_unstressed", "surface_transpiration_negligible", "no_mulch_or_frozen_soil"):
        if conditions[key] is not True:
            raise ValueError(f"{key} must be explicitly true for this method")
    text("conditions source", conditions["source"])
    soil = _construct(EvaporationSoil, data["surface_soil"])
    state = _object(data["initial_state"], ("as_of", "depletion_mm", "source"), "initial surface state")
    initial = EvaporationState(_date(state["as_of"]), state["depletion_mm"], state["source"])
    if not isinstance(data["days"], list) or not 1 <= len(data["days"]) <= 1000:
        raise ValueError("days must contain 1–1000 explicit entries")
    if not isinstance(data["weather"], list) or len(data["weather"]) != len(data["days"]):
        raise ValueError("weather must cover exactly the evaporation days")
    try:
        weather = parse_weather_records(data["weather"])
        validate_sources(data["weather"], data.get("weather_sources", []), site["timezone"])
    except OverflowError as exc:
        raise ValueError("weather dates fall outside supported calendar boundaries") from exc
    for record in weather:
        if getattr(record, "timezone", None) != site["timezone"]:
            raise ValueError("supplied ET timezone must match the site timezone")
        if record.kind in ("observed", "historical") and record.day >= datetime.now(ZoneInfo(site["timezone"])).date():
            raise ValueError("observed/historical daily ET requires a completed local day; label future inputs as forecasts or scenarios")
    days = []
    for row in data["days"]:
        row = dict(row) if isinstance(row, dict) else row
        if isinstance(row, dict):
            row["day"] = _date(row.get("day"))
        days.append(_construct(EvaporationDay, row))
    rainfall = data.get("rainfall_overrides", [])
    if not isinstance(rainfall, list) or len(rainfall) > len(days):
        raise ValueError("rainfall_overrides must be an array with at most one entry per weather day")
    rain_inputs = []
    for raw in rainfall:
        row = dict(raw) if isinstance(raw, dict) else raw
        if isinstance(row, dict):
            row["day"] = _date(row.get("day"))
        rain_inputs.append(_construct(RainfallInput, row))
    result = replay_evaporation(soil, initial, weather, days, data["area_m2"], rainfall_overrides=rain_inputs)
    result["inputs"] = deepcopy(payload)
    result["sources"] = sorted({*result["sources"], crop["source"], conditions["source"]})
    for snapshot in data.get("weather_sources", []):
        result["assumptions"].extend(item for item in snapshot["assumptions"] if item not in result["assumptions"])
    return json_ready(result)


def evaporation_input_schema():
    def obj(fields, optional=None):
        return dict(type="object", additionalProperties=False, required=list(fields), properties=dict(fields, **(optional or {})))
    string, numeric = dict(type="string"), dict(type="number")
    day = dict(type="string", format="date")
    timestamp = dict(type=["string", "null"], format="date-time")
    return obj(dict(schema_version=dict(type="integer", const=1), site=obj(dict(name=string, timezone=string)), area_m2=numeric,
        crop=obj(dict(name=string, source=string)), surface_soil=obj(dict(field_capacity=numeric, wilting_point=numeric,
            depth_m=numeric, readily_evaporable_mm=numeric, source=string)),
        initial_state=obj(dict(as_of=day, depletion_mm=numeric, source=string)),
        conditions=obj(dict(basal_unstressed=dict(type="boolean", const=True), surface_transpiration_negligible=dict(type="boolean", const=True),
            no_mulch_or_frozen_soil=dict(type="boolean", const=True), source=string)),
        weather=dict(type="array", minItems=1, maxItems=1000, items=obj(dict(day=day, input_type=dict(type="string", const="supplied_et0"),
            et0_mm_day=numeric, rainfall_mm=dict(type=["number", "null"]), source=string, kind=dict(type="string", enum=["observed", "historical", "forecast", "scenario"]),
            timezone=string, period_hours=numeric, method=string), dict(reference_surface=dict(type="string", const="grass"),
                issued_at=timestamp, retrieved_at=timestamp, source_snapshot_id=dict(type=["string", "null"])))),
        days=dict(type="array", minItems=1, maxItems=1000, items=obj(dict(day=day, kcb=numeric, kc_max=numeric,
            cover_fraction=numeric, wetted_fraction=numeric, exposed_wetted_fraction=numeric,
            net_irrigation_mm=numeric, runoff_mm=numeric, source=string), dict(water_source=dict(type=["string", "null"]))))),
        dict(weather_sources=dict(type="array", maxItems=64, items=obj(dict(
            adapter=string, id=string, retrieved_at=dict(type="string", format="date-time"),
            request=obj(dict(latitude=numeric, longitude=numeric, timezone=string, start=day, end=day)),
            response=dict(type="object"), assumptions=dict(type="array", items=string)))),
            rainfall_overrides=dict(type="array", maxItems=1000, items=obj(dict(day=day, rainfall_mm=numeric, source=string,
                provenance=dict(type="string", enum=["measured", "reported", "estimated"]),
                quality=dict(type="string", enum=["unverified", "checked"])), dict(water_record_id=dict(type=["string", "null"]))))))


def render_evaporation_report(result):
    lines = ["Agronaut dual-coefficient crop demand — EXPERIMENTAL",
             f"{result['inputs']['crop']['name']} · {result['inputs']['area_m2']:g} m2",
             f"Surface TEW {result['total_evaporable_mm']:g} mm; REW {result['readily_evaporable_mm']:g} mm"]
    for row in result["days"]:
        v = row["values"]
        lines.append(f"{row['day']} · {row['weather_kind']}: basal ET {v['basal_et_mm']:.3f} mm; soil evaporation {v['soil_evaporation_mm']:.3f} mm; "
                     f"potential crop ET {v['potential_crop_et_mm']:.3f} mm; surface depletion {v['ending_depletion_mm']:.3f} mm")
        if v["evaporation_water_limited"]:
            lines.append("  Evaporation was limited by remaining surface water; effective Ke was reduced.")
        rain = row.get("rainfall", {})
        if rain.get("basis") == "separate_input":
            lines.append(f"  Rainfall used: {rain['rainfall_mm']:g} mm ({rain['provenance']}, {rain['quality']}; {rain['source']}). "
                         f"Replaces weather rainfall. Water record: {rain.get('water_record_id') or 'standalone input'}.")
        if row["inputs"].get("water_source"):
            lines.append(f"  Net irrigation {row['inputs']['net_irrigation_mm']:g} mm; runoff {row['inputs']['runoff_mm']:g} mm. "
                         f"{row['inputs']['water_source']}")
    if result["inputs"].get("weather_sources"):
        lines.extend(["", "Retained weather:"])
        for record in result["inputs"]["weather"]:
            rain_label = "unknown" if record["rainfall_mm"] is None else f"{record['rainfall_mm']:g} mm"
            lines.append(f"{record['day']} · {record['kind']}: grass-reference ET0 {record['et0_mm_day']:g} mm; rain {rain_label}")
        for snapshot in result["inputs"]["weather_sources"]:
            request, grid = snapshot["request"], snapshot["response"]
            lines.append(f"Retrieved {snapshot['retrieved_at']} (download time); requested location {request['latitude']:g}, {request['longitude']:g}; "
                         f"provider grid {grid['latitude']:g}, {grid['longitude']:g}. Snapshot {snapshot['id']}")
    for label in ("assumptions", "limitations", "sources"):
        lines.extend(["", label.title()+":", *("- "+row for row in result[label])])
    return "\n".join(lines)

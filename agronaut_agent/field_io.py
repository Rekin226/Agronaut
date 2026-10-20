"""Data adapters and reproducible requests for the experimental field-water core.

Dates, units and provenance are explicit at this boundary. No provider is consulted
implicitly, and a missing rainfall or irrigation event cannot turn into a zero.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.crop_coefficients import CURVE_SOURCE, CoefficientPeriod, CoefficientSchedule
from agro_model.root_growth import RootZoneDay, expand_root_zone
from agro_model.soil_state import SoilStateUpdate, reconcile_soil_state
from agro_model.validation import number, text
from agro_model.water import (
    MODEL_VERSION,
    CropCoefficient,
    RainfallInput,
    ReferenceETDay,
    Soil,
    SoilState,
    SolarWeatherDay,
    WeatherDay,
    crop_water_demand,
    irrigation_requirement,
    water_balance_step,
)


def _mapping(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    return dict(value)


def _date(value, label="day") -> date:
    text(label, value)
    parsed = date.fromisoformat(value)
    if value != parsed.isoformat():
        raise ValueError(f"{label} requires YYYY-MM-DD")
    return parsed


def _construct(cls, values):
    data = _mapping(values, (f.name for f in fields(cls)), cls.__name__)
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid {cls.__name__}: {exc}") from exc


def parse_weather_records(records: list[dict]) -> tuple[WeatherDay | SolarWeatherDay | ReferenceETDay, ...]:
    if not isinstance(records, list) or not records:
        raise ValueError("weather must be a nonempty array")
    days = []
    for record in records:
        types = {"supplied_et0": ReferenceETDay, "solar_radiation_fao56": SolarWeatherDay}
        input_type = record.get("input_type") if isinstance(record, dict) else None
        cls = types.get(input_type, WeatherDay) if isinstance(input_type, str) else WeatherDay
        data = _mapping(record, (f.name for f in fields(cls)), "weather record")
        data["day"] = _date(data.get("day"))
        if "kind" not in data:
            raise ValueError("weather record requires kind: observed, forecast, historical or scenario")
        for key in ("issued_at", "retrieved_at"):
            if data.get(key) is not None:
                text(key, data[key])
                data[key] = datetime.fromisoformat(data[key])
        day = _construct(cls, data)
        if days and day.day != days[-1].day + timedelta(days=1):
            raise ValueError("weather dates must be consecutive, ordered and unique")
        days.append(day)
    return tuple(days)


def import_weather_csv(path: str | Path) -> tuple[WeatherDay | SolarWeatherDay | ReferenceETDay, ...]:
    """CSV headings use WeatherDay names/units; an empty rainfall cell stays unknown."""
    numeric = {"t_min_c", "t_max_c", "rh_min_pct", "rh_max_pct", "wind_m_s",
               "wind_height_m", "elevation_m", "net_radiation_mj_m2", "rainfall_mm", "et0_mm_day", "period_hours",
               "solar_radiation_mj_m2", "latitude_deg"}
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("weather CSV requires unique field names")
        records = []
        for row in reader:
            if None in row:
                raise ValueError("weather CSV row has more values than field names")
            for key in numeric & row.keys():
                row[key] = None if row[key] in (None, "") else float(row[key])
            for key in ("issued_at", "retrieved_at", "source_snapshot_id"):
                if row.get(key) == "":
                    row[key] = None
            records.append(row)
    return parse_weather_records(records)


def json_ready(value):
    """Normalize dates/tuples once so CLI, tools and reports share exactly one result."""
    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"cannot serialize {type(item).__name__}")
    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def parse_crop_coefficients(crop):
    """Exactly one supplied coefficient or a fully explicit dated curve."""
    crop = _mapping(crop, ("name", "coefficient", "coefficient_schedule"), "crop")
    text("crop name", crop.get("name"))
    if ("coefficient" in crop) == ("coefficient_schedule" in crop):
        raise ValueError("crop requires exactly one of coefficient or coefficient_schedule")
    if "coefficient" in crop:
        return _construct(CropCoefficient, crop["coefficient"])
    raw = crop["coefficient_schedule"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 1000:
        raise ValueError("coefficient_schedule must contain 1 to 1000 periods")
    periods = []
    for item in raw:
        values = _mapping(item, (f.name for f in fields(CoefficientPeriod)), "coefficient period")
        for key in ("start", "end"):
            values[key] = _date(values.get(key), key)
        periods.append(_construct(CoefficientPeriod, values))
    return CoefficientSchedule(tuple(periods))


def field_input_schema():
    return dict(
        solar_weather="Optional explicit weather variant: input_type=solar_radiation_fao56; supply day, kind, "
            "t_min_c,t_max_c,rh_min_pct,rh_max_pct,wind_m_s,wind_height_m,elevation_m,latitude_deg,"
            "solar_radiation_mj_m2,rainfall_mm,source and issued_at for forecasts. "
            "Omit net_radiation_mj_m2. Solar is daily incoming shortwave energy in MJ/m2; "
            "net radiation is estimated with FAO-56 (1998) Eq. 21,23–25,37–40, grass albedo 0.23. "
            "Supply daily extremes, not extrema of interval means. No polar-darkness or negative "
            "empirical correction-factor extrapolation. All estimates and any upper ratio cap remain in the result.",
        crop="{name,coefficient:{value,stage,source}} OR {name,coefficient_schedule:[period,...]}; never both",
        period="{start:YYYY-MM-DD,end:YYYY-MM-DD,start_value:Kc,end_value:Kc,stage,source}",
        soil_state_updates="Optional root-zone budget array: {as_of:YYYY-MM-DD,observed_at:aware ISO timestamp,timezone,"
                           "quantity:soil_depletion|soil_water_content,value,unit:mm|m3/m3,represented_depth_m,"
                           "representation_note,depletion_adjustment_mm,adjustment_source,source,provenance,quality}. "
                           "Depth must equal the complete root depth at that boundary. Supply sampling coverage and a signed adjustment "
                           "to closing midnight (positive means drier; zero needs an explicit assumption). "
                           "Dates are unique and within the initial boundary through the last weather day. "
                           "The reading must be within that local day or exactly its closing midnight. "
                           "No forecast/rejected readings or clipping. Corrections are separate from water fluxes. "
                           "For owned measurements use field_records review_soil then apply_soil_state.",
        coefficient_rules="Kc is dimensionless, 0..3. Dates are inclusive; periods must be ordered and consecutive. "
                          "Equal endpoints give a constant; otherwise interpolate linearly by calendar-day distance. "
                          "One-day periods require equal endpoints. All weather days must be covered; no extrapolation. "
                          "Dates and locally applicable values are supplied, never inferred from crop or stage reports.",
        root_zone_schedule="Optional root-zone budget array with exactly one ordered entry per weather day: "
            "{day:YYYY-MM-DD,root_depth_m,source,added_layer_water_content?,added_layer_source?}. "
            "soil.root_depth_m is the initial boundary depth. Daily depths apply before water inputs/ET and cannot decrease. "
            "Each increase requires the newly reached layer's water content (m3/m3, between wilting point and field capacity) "
            "immediately before inclusion, with its source. Unchanged depths must omit both added-layer fields. "
            "No inferred rooting rate, field-capacity default or extrapolation. Empty/absent schedule keeps fixed depth.",
        limitations="Soil hydraulic properties remain homogeneous and fixed; no automatic climate, wetting, mulch or dual-Kc adjustments.",
    )


def evaluate_field_request(payload: dict, mode: str) -> dict:
    if mode not in ("water-needs", "irrigation-plan"):
        raise ValueError("unsupported field calculation")
    payload = _mapping(payload, ("schema_version", "site", "area_m2", "crop", "weather",
                                 "soil", "initial_state", "application_efficiency", "events", "weather_sources",
                                 "rainfall_overrides", "soil_state_updates", "root_zone_schedule"),
                       "field request")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    site = _mapping(payload.get("site"), ("name", "timezone"), "site")
    text("site name", site.get("name"))
    text("site timezone", site.get("timezone"))
    try:
        ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    area = number("area_m2", payload.get("area_m2"), positive=True)
    crop = payload.get("crop")
    coefficients = parse_crop_coefficients(crop)
    weather = parse_weather_records(payload.get("weather"))
    for day in weather:
        if isinstance(day, ReferenceETDay) and day.timezone != site["timezone"]:
            raise ValueError("supplied ET timezone must match the site timezone")
    from .weather_provider import validate_sources
    validate_sources(payload["weather"], payload.get("weather_sources", []), site["timezone"])
    rows = []
    soil = state = efficiency = None
    events = {}
    updates = {}
    roots = {}
    raw_roots = payload.get("root_zone_schedule", [])
    if not isinstance(raw_roots, list):
        raise ValueError("root_zone_schedule must be an array")
    if raw_roots and mode != "irrigation-plan":
        raise ValueError("root_zone_schedule requires an irrigation-plan water balance")
    for raw in raw_roots:
        data = _mapping(raw, (f.name for f in fields(RootZoneDay)), "root-zone day")
        data["day"] = _date(data.get("day"))
        root = _construct(RootZoneDay, data)
        if root.day in roots:
            raise ValueError("duplicate root-zone day")
        roots[root.day] = root
    if roots and list(roots) != [w.day for w in weather]:
        raise ValueError("root_zone_schedule must cover exactly the ordered weather days; no extrapolation")
    raw_updates = payload.get("soil_state_updates", [])
    if not isinstance(raw_updates, list):
        raise ValueError("soil_state_updates must be an array")
    if raw_updates and mode != "irrigation-plan":
        raise ValueError("soil_state_updates require an irrigation-plan water balance")
    for raw in raw_updates:
        data = _mapping(raw, (f.name for f in fields(SoilStateUpdate)), "soil state update")
        data["as_of"] = _date(data.get("as_of"), "as_of")
        text("observed_at", data.get("observed_at"))
        data["observed_at"] = datetime.fromisoformat(data["observed_at"])
        update = _construct(SoilStateUpdate, data)
        if update.as_of in updates:
            raise ValueError("duplicate soil state update day")
        if not weather[0].day - timedelta(days=1) <= update.as_of <= weather[-1].day:
            raise ValueError("soil state updates must cover the initial boundary or a day within the weather window")
        if update.timezone != site["timezone"]:
            raise ValueError("soil state update timezone must match the site timezone")
        updates[update.as_of] = update
    initial_update = None
    rainfall = {}
    raw_rainfall = payload.get("rainfall_overrides", [])
    if not isinstance(raw_rainfall, list):
        raise ValueError("rainfall_overrides must be an array")
    if raw_rainfall and mode != "irrigation-plan":
        raise ValueError("rainfall_overrides require an irrigation-plan water balance")
    for raw in raw_rainfall:
        data = _mapping(raw, (f.name for f in fields(RainfallInput)), "rainfall override")
        data["day"] = _date(data.get("day"))
        entry = _construct(RainfallInput, data)
        if entry.day in rainfall:
            raise ValueError("duplicate rainfall override day")
        rainfall[entry.day] = entry
    if set(rainfall) - {w.day for w in weather}:
        raise ValueError("rainfall overrides must fall within the weather window")
    if mode == "irrigation-plan":
        soil = _construct(Soil, payload.get("soil"))
        raw_state = payload.get("initial_state")
        if raw_state is not None:
            raw_state = _mapping(raw_state, ("as_of", "depletion_mm", "source"), "initial_state")
            raw_state["as_of"] = _date(raw_state.get("as_of"), "as_of")
            state = _construct(SoilState, raw_state)
            if state.as_of != weather[0].day - timedelta(days=1):
                raise ValueError("weather day must immediately follow soil state's as_of date")
            number("initial depletion", state.depletion_mm, 0, soil.total_available_mm)
        initial_day = weather[0].day - timedelta(days=1)
        if initial_day in updates:
            initial_update = reconcile_soil_state(soil, state, updates[initial_day])
            state = SoilState(initial_day, initial_update.values["adopted_depletion_mm"], updates[initial_day].source)
        efficiency = _mapping(payload.get("application_efficiency"), ("value", "source"),
                              "application_efficiency")
        number("efficiency", efficiency.get("value"), high=1, positive=True)
        text("efficiency source", efficiency.get("source"))
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            raise ValueError("events must be an array with an explicit entry for every weather day")
        for raw in raw_events:
            event = _mapping(raw, ("day", "net_irrigation_mm", "runoff_mm", "source"), "event")
            day = _date(event.get("day"))
            if day in events:
                raise ValueError("duplicate event day")
            number("net_irrigation_mm", event.get("net_irrigation_mm"), 0)
            number("runoff_mm", event.get("runoff_mm"), 0)
            text("event source", event.get("source"))
            events[day] = event
        if set(events) != {w.day for w in weather}:
            raise ValueError("events must cover exactly the weather days, including explicit zero-water days")
    for day in weather:
        period = None
        if isinstance(coefficients, CoefficientSchedule):
            kc, period = coefficients.at(day.day)
        else:
            kc = coefficients
        demand = crop_water_demand(day, kc, area)
        row = {"day": day.day, "weather_kind": day.kind, "demand": demand.to_dict()}
        if period is not None:
            row["demand"]["inputs"]["coefficient_period"] = asdict(period)
            row["demand"]["sources"] += (CURVE_SOURCE,)
            row["demand"]["assumptions"] += (
                "Kc is linearly interpolated between supplied inclusive date endpoints using calendar-day distance; "
                "equal endpoints are constant. No extrapolation or inferred growth dates.",)
        if mode == "irrigation-plan":
            if roots:
                soil, state, growth = expand_root_zone(soil, state, roots[day.day])
                row["root_growth"] = growth.to_dict()
            event = events[day.day]
            rain = rainfall.get(day.day)
            rainfall_mm = day.rainfall_mm if rain is None else rain.rainfall_mm
            row["rainfall"] = (dict(basis="separate_input", **asdict(rain)) if rain is not None
                               else dict(basis="weather", rainfall_mm=rainfall_mm, source=day.source))
            # Validate rain/event consistency even without initialized soil state.
            if rainfall_mm is None:
                raise ValueError("rainfall_mm is required for irrigation planning")
            number("runoff_mm", event["runoff_mm"], 0, rainfall_mm)
            balance = None
            if state is not None:
                balance = water_balance_step(
                    soil, state, day, potential_et_mm=demand.values["etc_mm_day"],
                    net_irrigation_mm=event["net_irrigation_mm"], runoff_mm=event["runoff_mm"], rainfall=rain)
                state = SoilState(day.day, balance.values["depletion_mm"],
                                  f"Model estimate from {MODEL_VERSION}; {day.kind} weather")
            row["balance"] = None if balance is None else balance.to_dict()
            if day.day in updates:
                reconciliation = reconcile_soil_state(soil, state, updates[day.day])
                row["soil_state_update"] = reconciliation.to_dict()
                state = SoilState(day.day, reconciliation.values["adopted_depletion_mm"], updates[day.day].source)
            row["event"] = event
            row["requirement"] = irrigation_requirement(
                soil, state, area_m2=area, efficiency=efficiency["value"],
                efficiency_source=efficiency["source"]).to_dict()
        rows.append(row)
    return json_ready({
        "schema_version": 1, "model_version": MODEL_VERSION, "evidence_status": "experimental",
        "mode": mode, "site": site, "crop": crop["name"], "area_m2": area, "days": rows,
        "inputs": payload,
        **({"initial_soil_state_update": initial_update.to_dict()} if initial_update else {}),
        "limitations": [
            "Calculations have not been independently field validated.",
            "Recommendations are end-of-day water budgets, not method-specific operational schedules.",
            "Recommended water is not automatically applied to the next day; only supplied events are applied.",
            "A supplied coefficient/source is traceable input, not verification that the source supports it.",
            *(["Recorded water and weather retain separate provenance; historical weather remains an estimate."]
              if rainfall else []),
        ],
    })


def field_evidence(result: dict) -> dict:
    """One evidence summary for native reports, tools and saved previews."""
    evidence = dict(sources=set(), assumptions=set(), limitations=set(result["limitations"]), missing_inputs=set())
    parts = [result["initial_soil_state_update"]] if result.get("initial_soil_state_update") else []
    for snapshot in result["inputs"].get("weather_sources", []):
        evidence["assumptions"].update(snapshot["assumptions"])
    for row in result["days"]:
        for name in ("rainfall", "event"):
            if row.get(name):
                evidence["sources"].add(row[name]["source"])
        parts.extend(row[name] for name in ("demand", "balance", "requirement", "soil_state_update", "root_growth") if row.get(name))
    for part in parts:
        for key in evidence:
            evidence[key].update(part[key])
    return {key: sorted(values) for key, values in evidence.items()}


def render_field_report(result: dict) -> str:
    def reconciliation_line(update):
        values = update["values"]
        previous, change = values["prior_model_depletion_mm"], values["reconciliation_mm"]
        before = "unknown" if previous is None else f"{previous:g} mm"
        delta = "initialization from unknown state" if change is None else f"reconciliation {change:+g} mm"
        return f"prior model {before}; adopted {values['adopted_depletion_mm']:g} mm depletion; {delta}."

    lines = [f"Agronaut field water — {result['site']['name']}",
             f"Crop: {result['crop']} | Area: {result['area_m2']:g} m²",
             "EXPERIMENTAL — no independent field validation", ""]
    if result.get("initial_soil_state_update"):
        update = result["initial_soil_state_update"]
        lines.append(f"Initial soil-state update: {reconciliation_line(update)}")
        lines.append("  State reconciliation is separate from irrigation, rain and drainage.")
    for row in result["days"]:
        values = row["demand"]["values"]
        lines.append(f"{row['day']} ({row['weather_kind']}): "
                     f"reference ET {values['et0_mm_day']:.2f} mm/day; "
                     f"crop demand {values['etc_mm_day']:.2f} mm/day "
                     f"({values['crop_demand_l_day']:.1f} L/day).")
        coefficient = row["demand"]["inputs"]["coefficient"]
        lines.append(f"  Kc {coefficient['value']:g} · stage: {coefficient['stage']}")
        radiation = row["demand"]["inputs"].get("radiation_estimate")
        if radiation:
            lines.append(f"  Solar radiation {radiation['inputs']['solar_radiation_mj_m2']:g} → "
                         f"estimated net radiation {radiation['values']['net_radiation_mj_m2']:.3f} MJ/m²/day "
                         "(FAO-56, grass reference; not a measured net flux).")
            if radiation['ratio_capped']:
                lines.append("  Solar/clear-sky ratio exceeded 1; capped at 1 in the longwave term only.")
        if "requirement" in row:
            if row.get("root_growth"):
                growth = row["root_growth"]["values"]
                lines.append(f"  Root depth {growth['root_depth_m']:g} m; available capacity {growth['total_available_mm']:g} mm; "
                             f"newly reached layer adds {growth['added_layer_depletion_mm']:g} mm depletion "
                             f"and {growth['added_layer_available_water_mm']:g} mm available water (storage expansion, not irrigation).")
            if row.get("soil_state_update"):
                lines.append(f"  Soil state updated: {reconciliation_line(row['soil_state_update'])}")
                lines.append("  State reconciliation is separate from irrigation, rain and drainage.")
            rain = row.get("rainfall")
            if rain:
                label = (f"{rain['provenance']}, {rain['quality']}" if rain["basis"] == "separate_input"
                         else f"from {row['weather_kind']} weather")
                lines.append(f"  Rainfall used: {rain['rainfall_mm']:g} mm ({label}).")
                if rain.get("water_record_id"):
                    lines.append(f"  Daily water record: {rain['water_record_id']}")
            gross = row["requirement"]["values"]["gross_irrigation_l"]
            lines.append("  Missing soil initialization; irrigation amount unknown." if gross is None
                         else f"  End-of-day refill budget: {gross:.1f} L gross.")
    evidence = field_evidence(result)
    lines.extend(["", "Sources:", *(f"- {s}" for s in evidence["sources"]),
                  "", "Assumptions:", *(f"- {s}" for s in evidence["assumptions"]),
                  "", "Limits:", *(f"- {s}" for s in evidence["limitations"])])
    return "\n".join(lines)

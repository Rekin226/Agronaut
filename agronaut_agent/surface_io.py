"""Strict, sourced surface-event input contract shared by CLI, chat and saved records."""

from dataclasses import fields
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.surface import (
    Assessment,
    Event,
    FlowInterval,
    Geometry,
    Infiltration,
    Station,
    SurfaceEvaluation,
    evaluate_surface,
)
from agro_model.validation import text

from .field_io import _construct, _mapping, json_ready

_RECORDS = dict(geometry=Geometry, event=Event, infiltration=Infiltration, assessment=Assessment)
_ARRAYS = dict(inflow=FlowInterval, tailwater=FlowInterval, stations=Station)


def surface_input_schema():
    def record(cls):
        types = {float: "number", str: "string", bool: "boolean", datetime: "string", float | None: ["number", "null"]}
        properties = {f.name: dict(type=types[f.type]) for f in fields(cls)}
        if cls is Event:
            properties["started_at"]["format"] = "date-time"
        return dict(type="object", additionalProperties=False, required=list(properties), properties=properties)
    props = dict(schema_version=dict(type="integer", const=1), site=dict(type="object", additionalProperties=False,
        required=["name", "timezone"], properties=dict(name=dict(type="string"), timezone=dict(type="string"))))
    props.update({name: record(cls) for name, cls in _RECORDS.items()})
    props.update({name: dict(type="array", minItems=2 if name == "stations" else 1,
                            maxItems=10000, items=record(cls)) for name, cls in _ARRAYS.items()})
    return dict(type="object", additionalProperties=False, required=list(props), properties=props)


def evaluate_surface_request(payload):
    payload = _mapping(payload, ("schema_version", "site", *_RECORDS, *_ARRAYS), "surface event")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _mapping(payload.get("site"), ("name", "timezone"), "site")
    text("site name", site.get("name"))
    text("site timezone", site.get("timezone"))
    try:
        ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    spec = {}
    for name, cls in _RECORDS.items():
        data = _mapping(payload.get(name), (f.name for f in fields(cls)), name)
        if name == "event":
            text("started_at", data.get("started_at"))
            data["started_at"] = datetime.fromisoformat(data["started_at"])
        spec[name] = _construct(cls, data)
    for name, cls in _ARRAYS.items():
        raw = payload.get(name)
        if not isinstance(raw, list) or not 1 <= len(raw) <= 10000:
            raise ValueError(f"{name} requires 1–10000 explicit records")
        spec[name] = tuple(_construct(cls, item) for item in raw)
    result = evaluate_surface(SurfaceEvaluation(**spec))
    result.update(schema_version=1, site=site)
    result["inputs"].update(schema_version=1, site=site)
    return json_ready(result)


def render_surface_report(result):
    volumes = result["volumes"]
    lines = ["Agronaut surface-irrigation event — EXPERIMENTAL",
             f"{result['site']['name']} · {result['inputs']['geometry']['method']} · {result['event_kind']}",
             f"Area: {result['area_m2']:g} m²; inflow: {volumes['inflow_l']:.1f} L; tailwater: {volumes['tailwater_l']:.1f} L",
             f"Modeled infiltration: {volumes['modeled_infiltration_l']:.1f} L; unaccounted: {volumes['unaccounted_l']:.1f} L",
             "Water accounting: " + ("within supplied checks" if result["water_balance_accepted"] else "REVIEW — performance withheld"),
             "No cutoff-time recommendation is produced."]
    performance = result["performance"]
    if performance is not None:
        for name in ("low_quarter_distribution_uniformity", "application_efficiency", "storage_efficiency",
                     "adequately_irrigated_area_fraction", "deep_percolation_ratio", "tailwater_ratio"):
            value = performance[name]
            lines.append(f"{name.replace('_', ' ')}: " + ("unknown / not defined" if value is None else f"{value:.1%}"))
    for title, key in (("Checks needing review", "binding_constraints"), ("Missing inputs", "missing_inputs"),
                       ("Assumptions", "assumptions"), ("Limits", "limitations"), ("Sources", "sources")):
        lines.extend(["", title + ":", *(f"- {item}" for item in result[key])])
    return "\n".join(lines)

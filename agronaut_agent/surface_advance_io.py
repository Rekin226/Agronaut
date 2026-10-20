"""Strict offline contract for experimental surface advance scenarios."""

from dataclasses import fields
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.surface import Geometry, Infiltration
from agro_model.surface_advance import (
    AdvanceConditions,
    AdvanceHydraulics,
    AdvanceLimits,
    SurfaceAdvance,
    predict_surface_advance,
)
from agro_model.validation import text

from .field_io import _construct, _mapping, json_ready

_RECORDS = dict(geometry=Geometry, hydraulics=AdvanceHydraulics, infiltration=Infiltration,
                limits=AdvanceLimits, conditions=AdvanceConditions)


def surface_advance_input_schema():
    props = dict(schema_version=dict(type="integer", const=1), site=dict(type="object",
        additionalProperties=False, required=["name", "timezone"], properties=dict(
            name=dict(type="string"), timezone=dict(type="string"))))
    for name, cls in _RECORDS.items():
        record = {field.name: dict(type={float: "number", str: "string", bool: "boolean"}[field.type])
                  for field in fields(cls)}
        props[name] = dict(type="object", additionalProperties=False,
                           required=list(record), properties=record)
    props["geometry"]["properties"]["method"]["enum"] = ["furrow", "border"]
    props["geometry"]["properties"]["slope_m_m"]["exclusiveMinimum"] = 0
    props["infiltration"]["properties"]["basis"]["enum"] = ["depth_mm", "volume_per_length_m2"]
    props["infiltration"]["properties"]["min_opportunity_min"]["const"] = 0
    for key, prop in props["conditions"]["properties"].items():
        if key != "source":
            prop["const"] = True
    return dict(type="object", additionalProperties=False, required=list(props), properties=props)


def evaluate_surface_advance_request(payload):
    payload = _mapping(payload, ("schema_version", "site", *_RECORDS), "surface advance")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _mapping(payload.get("site"), ("name", "timezone"), "site")
    text("site name", site.get("name"))
    text("site timezone", site.get("timezone"))
    try:
        ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    records = {name: _construct(cls, payload.get(name)) for name, cls in _RECORDS.items()}
    result = predict_surface_advance(SurfaceAdvance(**records))
    result.update(schema_version=1, site=site)
    result["inputs"].update(schema_version=1, site=site)
    return json_ready(result)


def render_surface_advance_report(result):
    inlet, prediction = result["inlet"], result["prediction"]
    lines = ["Agronaut surface advance — EXPERIMENTAL SCENARIO",
             f"{result['site']['name']} · {result['inputs']['geometry']['method']}",
             f"Normal inlet area: {inlet['flow_area_m2']:.6g} m²; velocity: {inlet['velocity_m_s']:.6g} m/s",
             f"Supported time window: {result['time_envelope_min']:g} min"]
    if inlet["border_depth_m"] is not None:
        lines.append(f"Wide-border depth: {inlet['border_depth_m']:.6g} m; "
                     f"depth/width: {inlet['border_depth_width_ratio']:.6g}. Review the wide-flow assumption.")
    if prediction:
        lines.extend([f"Predicted half-length advance: {prediction['half_length_advance_min']:.3f} min",
                      f"Predicted full-length advance: {prediction['full_length_advance_min']:.3f} min",
                      f"Two-point advance exponent: {prediction['advance_exponent']:.6f}"])
        for row in prediction["anchors"]:
            lines.append(f"At {row['distance_m']:g} m: inflow {row['inflow_l']:.3f} L, "
                         f"surface storage {row['surface_storage_l']:.3f} L, "
                         f"infiltration {row['infiltration_l']:.3f} L, "
                         f"balance residual {row['balance_residual_l']:.3g} L")
    else:
        lines.append("Advance prediction withheld; review the constraints and model scope.")
    lines.append("Advance time is not a cutoff time. No operational irrigation recommendation is produced.")
    for title, key in (("Constraints", "binding_constraints"), ("Assumptions", "assumptions"),
                       ("Limits", "limitations"), ("Sources", "sources")):
        lines.extend(["", title+":", *(f"- {item}" for item in result[key])])
    return "\n".join(lines)

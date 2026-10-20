"""Strict input boundary and readable report for surveyed drip laterals."""

from dataclasses import fields
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.drip_lateral import (
    Conditions,
    DripLateral,
    EmitterCurve,
    Inlet,
    InletCurve,
    InletPoint,
    Limits,
    Station,
    evaluate_drip_lateral,
)
from agro_model.hydraulics import Pipe
from agro_model.validation import text

from .field_io import _construct, _mapping, json_ready

RECORDS = dict(pipe=Pipe, emitter=EmitterCurve, inlet=Inlet, limits=Limits, conditions=Conditions)


def drip_lateral_input_schema():
    types = {str: "string", float: "number", bool: "boolean"}

    def record(cls):
        return dict(type="object", additionalProperties=False, required=[f.name for f in fields(cls)],
                    properties={f.name: dict(type=types[f.type]) for f in fields(cls) if f.name != "curve"})

    curve = record(InletCurve)
    curve["properties"]["curve"] = dict(type="array", minItems=2, maxItems=100, items=record(InletPoint))
    curve["properties"]["head_reference"]["const"] = "lateral_inlet_pressure"
    props = dict(schema_version=dict(type="integer", enum=[1, 2]),
                 site=dict(type="object", additionalProperties=False, required=["name", "timezone"],
                           properties={key: dict(type="string") for key in ("name", "timezone")}),
                 **{key: record(cls) for key, cls in RECORDS.items()},
                 stations=dict(type="array", minItems=1, maxItems=2000, items=record(Station)))
    for key in props["conditions"]["properties"]:
        if key != "source":
            props["conditions"]["properties"][key]["const"] = True
    props["inlet"] = dict(oneOf=[record(Inlet), curve])
    return dict(type="object", additionalProperties=False, required=list(props), properties=props,
                allOf=[{"if": {"properties": {"schema_version": {"const": 1}}},
                        "then": {"properties": {"inlet": record(Inlet)}},
                        "else": {"properties": {"inlet": curve}}}])


def evaluate_drip_lateral_request(payload):
    payload = _mapping(payload, ("schema_version", "site", *RECORDS, "stations"), "drip lateral")
    version = payload.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("schema_version must be integer 1 (fixed pressure) or 2 (inlet curve)")
    site = _mapping(payload.get("site"), ("name", "timezone"), "site")
    text("site name", site.get("name"))
    text("site timezone", site.get("timezone"))
    try:
        ZoneInfo(site["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    stations = payload.get("stations")
    if not isinstance(stations, list) or not 1 <= len(stations) <= 2000:
        raise ValueError("stations must contain 1–2000 ordered emitter records")
    if version == 2:
        supplied = _mapping(payload.get("inlet"), tuple(f.name for f in fields(InletCurve)), "inlet curve")
        points = supplied.get("curve")
        if not isinstance(points, list) or not 2 <= len(points) <= 100:
            raise ValueError("inlet curve requires 2–100 supplied points")
        inlet = _construct(InletCurve, dict(supplied, curve=tuple(_construct(InletPoint, row) for row in points)))
    else:
        inlet = _construct(Inlet, payload.get("inlet"))
    spec = DripLateral(**{name: _construct(cls, payload.get(name)) for name, cls in RECORDS.items() if name != "inlet"},
                       inlet=inlet,
                       stations=tuple(_construct(Station, row) for row in stations))
    result = evaluate_drip_lateral(spec)
    result.update(schema_version=version, site=site)
    result["inputs"].update(schema_version=version, site=site)
    return json_ready(result)


def render_drip_lateral_report(result):
    lines = ["Agronaut drip lateral — EXPERIMENTAL SCENARIO", result["site"]["name"], result["feasibility_scope"]]
    prediction = result["prediction"]
    if prediction is None:
        lines.append("Prediction withheld: no supported solution inside the supplied curve envelopes.")
    else:
        lines.extend(["Supplied limits: " + ("PASS" if result["feasible"] else "FAIL"),
            f"Inlet flow: {prediction['inlet_flow_lph']:.6g} L/h; emitter pressure: "
            f"{prediction['min_pressure_head_m']:.6g}–{prediction['max_pressure_head_m']:.6g} m",
            f"Emitter flow: {prediction['min_emitter_flow_lph']:.6g}–{prediction['max_emitter_flow_lph']:.6g} L/h; "
            f"hydraulic flow variation: {100*prediction['flow_variation_fraction']:.3f}%",
            f"Inlet head residual: {prediction['inlet_head_residual_m']:.3g} m; "
            f"mass residual: {prediction['mass_balance_residual_lph']:.3g} L/h"])
        if result["schema_version"] == 2:
            lines.extend([f"Operating inlet pressure: {prediction['inlet_pressure_head_m']:.6g} m head",
                          "Source configuration: " + result["inputs"]["inlet"]["configuration"]])
        for row in prediction["nodes"]:
            lines.append(f"{row['identifier']} at {row['distance_m']:g} m, elevation {row['elevation_m']:g} m: "
                         f"pressure {row['pressure_head_m']:.6g} m, discharge {row['emitter_flow_lph']:.6g} L/h")
    lines.append("Nominal hydraulic flows are not measured uniformity. No irrigation schedule is produced.")
    for title, key in (("Constraints", "binding_constraints"), ("Assumptions", "assumptions"),
                       ("Limits", "limitations"), ("Sources", "sources")):
        lines.extend(["", title+":", *(f"- {item}" for item in result[key])])
    return "\n".join(lines)

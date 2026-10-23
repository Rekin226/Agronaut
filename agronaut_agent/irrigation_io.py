"""Strict JSON boundary for equipment-specific irrigation design calculations."""

from dataclasses import fields

from agro_model.drip import (
    Application,
    DripDesign,
    Emitter,
    Headworks,
    Layout,
    OperatingLimits,
    Supply,
    design_drip,
)
from agro_model.hydraulics import Pipe
from agro_model.sprinkler import (
    Application as SprinklerApplication,
)
from agro_model.sprinkler import (
    Layout as SprinklerLayout,
)
from agro_model.sprinkler import (
    Sprinkler,
    SprinklerDesign,
    design_sprinkler,
)

from .field_io import _construct, _mapping

DRIP_RECORDS = dict(layout=Layout, emitter=Emitter, main=Pipe, manifold=Pipe, lateral=Pipe,
                    supply=Supply, headworks=Headworks, application=Application,
                    limits=OperatingLimits)
SPRINKLER_RECORDS = dict(layout=SprinklerLayout, sprinkler=Sprinkler, main=Pipe, manifold=Pipe,
                         lateral=Pipe, riser=Pipe, supply=Supply, headworks=Headworks,
                         application=SprinklerApplication, limits=OperatingLimits)


def drip_input_schema() -> dict:
    return _input_schema(DRIP_RECORDS)


def sprinkler_input_schema() -> dict:
    return _input_schema(SPRINKLER_RECORDS)


def _input_schema(records) -> dict:
    """Discoverable field/type contract; domain validators enforce physical ranges."""
    types = {int: "integer", float: "number", str: "string", bool: "boolean"}
    properties = {"schema_version": {"type": "integer", "const": 1}}
    for name, cls in records.items():
        properties[name] = dict(type="object", additionalProperties=False,
                                required=[f.name for f in fields(cls)],
                                properties={f.name: {"type": types[f.type]} for f in fields(cls)})
    return dict(type="object", additionalProperties=False,
                required=list(properties), properties=properties)


def evaluate_drip_request(payload: dict) -> dict:
    data = _mapping(payload, ("schema_version", *DRIP_RECORDS), "drip request")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    spec = DripDesign(**{name: _construct(cls, data.get(name)) for name, cls in DRIP_RECORDS.items()})
    result = design_drip(spec)
    result["schema_version"] = 1
    result["inputs"]["schema_version"] = 1
    return result


def evaluate_sprinkler_request(payload: dict) -> dict:
    data = _mapping(payload, ("schema_version", *SPRINKLER_RECORDS), "sprinkler request")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    spec = SprinklerDesign(**{name: _construct(cls, data.get(name)) for name, cls in SPRINKLER_RECORDS.items()})
    result = design_sprinkler(spec)
    result["schema_version"] = 1
    result["inputs"]["schema_version"] = 1
    return result


_CONSTRAINT_TEXT = {
    "daily_source_water_l": ("Daily source water", "L",
        "Review the daily water allowance, application target and maintenance water."),
    "daily_runtime_hours": ("Daily irrigation and maintenance time", "h",
        "Review available operating hours, the application target, zone flows and maintenance time."),
    "source_flow_lph": ("Source flow", "L/h",
        "Review the source's running-flow specification and the number and flow of outlets operating together."),
    "filter_flow_lph": ("Filter flow", "L/h",
        "Review the filter's rated flow and the outlets operating together in this zone."),
    "filter_microns": ("Filter opening", "µm",
        "Review the installed filter opening and the outlet manufacturer's filtration requirement."),
    "velocity_m_s": ("Peak pipe velocity", "m/s",
        "Review pipe internal diameters, zone flows and the supplied design velocity limit."),
    "local_depth_mm": ("Local applied depth", "mm",
        "Review the application target, wetting or distribution assumptions and the supplied local soil depth limit."),
    "local_rate_mm_h": ("Local application rate", "mm/h",
        "Review outlet flow, spacing, wetting or distribution assumptions and the supplied soil intake limit."),
    "wind_m_s": ("Operating wind speed", "m/s",
        "Review the operating wind value and the sprinkler manufacturer's applicability limit."),
    "lateral_spacing_m": ("Spacing between laterals", "m",
        "Review the layout, minimum wetted diameter and permitted spacing fraction."),
    "sprinkler_spacing_m": ("Spacing between sprinklers", "m",
        "Review the layout, minimum wetted diameter and permitted spacing fraction."),
    "cell_diagonal_m": ("Sprinkler grid-cell diagonal", "m",
        "Review both grid spacings and the supplied minimum wetted diameter; geometric coverage does not establish uniformity."),
}
for _outlet in ("emitter", "sprinkler"):
    for _bound in ("min", "max"):
        _CONSTRAINT_TEXT[f"{_outlet}_{_bound}_head_m"] = (
            f"{_outlet.title()} {'minimum' if _bound == 'min' else 'maximum'} operating head", "m of water head",
            "Review source running head, hydraulic losses and the outlet's permitted operating head range.")
for _component in ("main", "manifold", "lateral", "riser", "headworks", "emitter", "sprinkler"):
    _CONSTRAINT_TEXT[f"{_component}_static_head_m"] = (
        f"{_component.title()} static head", "m of water head",
        "Review maximum source static head, applicable elevation and this component's pressure rating.")


def _constraint_lines(result):
    """Explain the engine's existing violations without changing or solving them."""
    lines = ["", "Binding constraints:"]
    if not result["constraints"]:
        return lines + ["- No supplied constraint failures. Applicability and evidence limits still apply."]
    for item in result["constraints"]:
        description = _CONSTRAINT_TEXT.get(item["code"])
        if description is None:
            # Future or saved-version codes remain inspectable rather than disappearing.
            lines.append(f"- Unrecognized constraint: {item}")
            continue
        label, unit, review = description
        scope = f"Zone {item['zone']}" if item["zone"] is not None else "Whole design"
        # str preserves the float's round-trip precision: tiny failures must not
        # display as an evaluated value equal to the limit after rounding.
        bounds = []
        if item.get("required_min") is not None:
            bounds.append(f"required minimum {item['required_min']} {unit}")
        if item.get("allowed_max") is not None:
            bounds.append(f"allowed maximum {item['allowed_max']} {unit}")
        lines.append(f"- {scope} — {label}: evaluated {item['actual']} {unit}; " + "; ".join(bounds) + ".")
        lines.append("  " + review)
    lines.append("Review these inputs against their sources, then recalculate the whole design. "
                 "Revising one input does not establish that the other constraints pass.")
    return lines


def render_sprinkler_report(result: dict) -> str:
    lines = ["Agronaut sprinkler design — EXPERIMENTAL", result["feasibility_scope"],
             "Supplied constraints: " + ("PASS" if result["feasible"] else "FAIL — no schedule"),
             f"Grid area: {result['area_m2']:g} m²; zones: {len(result['zones'])}"]
    lines.extend(_constraint_lines(result))
    lines.extend(["",
             f"Candidate daily upper withdrawal: {result['candidate_daily_upper_water_l']:.1f} L; "
             f"time including maintenance: {result['candidate_daily_hours']:.2f} h"])
    for zone in result["zones"]:
        lines.append(f"Zone {zone['zone']}: {zone['flow_min_lph']:.1f}–{zone['flow_max_lph']:.1f} L/h; "
                     f"nozzle head {zone['sprinkler_min_head_m']:.2f}–{zone['sprinkler_max_head_m']:.2f} m; "
                     f"candidate runtime {zone['candidate_runtime_hours']:.2f} h.")
    for title, key in (("Materials", "materials"),
                       ("Assumptions", "assumptions"), ("Limits", "limitations"), ("Sources", "sources")):
        values = result[key]
        lines.extend(["", title + ":", *(f"- {k}: {v:g}" for k, v in values.items())] if isinstance(values, dict)
                     else ["", title + ":", *(f"- {v}" for v in values)])
    return "\n".join(lines)


def render_drip_report(result: dict) -> str:
    lines = ["Agronaut drip design — EXPERIMENTAL", result["feasibility_scope"],
             "Supplied constraints: " + ("PASS" if result["feasible"] else "FAIL — no schedule"),
             f"Area: {result['area_m2']:g} m²; zones: {len(result['zones'])}"]
    lines.extend(_constraint_lines(result))
    lines.extend(["",
             f"Candidate daily upper withdrawal: {result['candidate_daily_upper_water_l']:.1f} L; "
             f"time including maintenance: {result['candidate_daily_hours']:.2f} h"])
    for zone in result["zones"]:
        lines.append(f"Zone {zone['zone']}: {zone['flow_min_lph']:.1f}–{zone['flow_max_lph']:.1f} L/h; "
                     f"emitter head {zone['emitter_min_head_m']:.2f}–{zone['emitter_max_head_m']:.2f} m; "
                     f"candidate runtime {zone['candidate_runtime_hours']:.2f} h.")
    lines.extend(["", "Materials:", *(f"- {k}: {v:g}" for k, v in result["materials"].items()),
                  "", "Assumptions:", *(f"- {v}" for v in result["assumptions"]),
                  "", "Limits:", *(f"- {v}" for v in result["limitations"]),
                  "", "Sources:", *(f"- {v}" for v in result["sources"])])
    return "\n".join(lines)

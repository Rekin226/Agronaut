"""Strict schema and report for surveyed drip distribution manifolds."""

from dataclasses import fields
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.drip_lateral import InletPoint
from agro_model.drip_manifold import (
    DripManifold,
    ManifoldConditions,
    ManifoldLimits,
    Supply,
    Takeoff,
    evaluate_drip_manifold,
)
from agro_model.hydraulics import Pipe
from agro_model.validation import text

from .drip_zone_io import drip_zone_input_schema, parse_lateral
from .field_io import _construct, _mapping, json_ready


def drip_manifold_input_schema():
    base = drip_zone_input_schema()['properties']
    def obj(properties):
        return dict(type='object', additionalProperties=False, required=list(properties), properties=properties)
    supply = base['supply']
    supply['properties']['head_reference']['const'] = 'manifold_inlet_pressure'
    string, numeric = dict(type='string'), dict(type='number')
    conditions = obj({f.name: string if f.name == 'source' else dict(type='boolean', const=True) for f in fields(ManifoldConditions)})
    return obj(dict(schema_version=dict(type='integer', const=1), site=base['site'], supply=supply,
        pipe=base['laterals']['items']['properties']['pipe'], conditions=conditions,
        limits=obj(dict(viscosity_m2_s=numeric, max_velocity_m_s=numeric, source=string)),
        takeoffs=dict(type='array', minItems=1, maxItems=20, items=obj(dict(distance_m=numeric, elevation_m=numeric,
            upstream_minor_k=numeric, source=string, lateral=base['laterals']['items'])))))


def evaluate_drip_manifold_request(payload):
    data = _mapping(payload, ('schema_version', 'site', 'supply', 'pipe', 'limits', 'conditions', 'takeoffs'), 'drip manifold')
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('schema_version must be integer 1')
    site = _mapping(data.get('site'), ('name', 'timezone'), 'site')
    text('site name', site.get('name'))
    text('site timezone', site.get('timezone'))
    try:
        ZoneInfo(site['timezone'])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError('site timezone must be an IANA timezone') from exc
    source = _mapping(data.get('supply'), tuple(f.name for f in fields(Supply)), 'manifold supply')
    points = source.get('curve')
    if not isinstance(points, list) or not 2 <= len(points) <= 100:
        raise ValueError('supply curve requires 2–100 points')
    supply = _construct(Supply, dict(source, curve=tuple(_construct(InletPoint, p) for p in points)))
    nodes = data.get('takeoffs')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 20:
        raise ValueError('manifold requires 1–20 ordered takeoffs')
    takeoffs = []
    for node in nodes:
        node = _mapping(node, tuple(f.name for f in fields(Takeoff)), 'takeoff')
        takeoffs.append(_construct(Takeoff, dict(node, lateral=parse_lateral(node.get('lateral')))))
    result = evaluate_drip_manifold(DripManifold(supply, _construct(Pipe, data.get('pipe')),
        _construct(ManifoldLimits, data.get('limits')), _construct(ManifoldConditions, data.get('conditions')), tuple(takeoffs)))
    result.update(schema_version=1, site=site)
    result['inputs'].update(schema_version=1, site=site)
    return json_ready(result)


def render_drip_manifold_report(result):
    lines = ['Agronaut surveyed drip manifold — EXPERIMENTAL SCENARIO', result['site']['name'], result['feasibility_scope']]
    p = result['prediction']
    if p is None:
        lines.append('Prediction withheld: no supported manifold operating point found.')
    else:
        lines.extend(['Supplied limits: '+('PASS' if result['feasible'] else 'FAIL'),
            f"Manifold inlet pressure: {p['source_pressure_head_m']:.6g} m; total flow: {p['total_flow_lph']:.6g} L/h",
            f"Source head residual: {p['source_head_residual_m']:.3g} m; emitter mass residual: {p['emitter_mass_residual_lph']:.3g} L/h"])
        for row in p['nodes']:
            lines.append(f"{row['identifier']} at {row['distance_m']:g} m, elevation {row['elevation_m']:g} m: "
                         f"takeoff pressure {row['pressure_head_m']:.6g} m; branch flow {row['lateral_flow_lph']:.6g} L/h; "
                         f"incoming segment loss {row['upstream_segment_loss']['total_head_m']:.6g} m")
    lines.extend(['Source configuration: '+result['inputs']['supply']['configuration'], 'No irrigation schedule or pump selection is produced.'])
    for title, key in (('Constraints', 'binding_constraints'), ('Assumptions', 'assumptions'), ('Limits', 'limitations'), ('Sources', 'sources')):
        lines.extend(['', title+':', *(f'- {item}' for item in result[key])])
    return '\n'.join(lines)

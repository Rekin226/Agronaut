"""Strict shared-junction zone schema and offline report."""

from dataclasses import fields
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.drip_lateral import (
    Conditions,
    EmitterCurve,
    InletCurve,
    InletPoint,
    Limits,
    Station,
)
from agro_model.drip_zone import DripZone, JunctionConditions, Lateral, evaluate_drip_zone
from agro_model.hydraulics import Pipe
from agro_model.validation import text

from .drip_lateral_io import drip_lateral_input_schema
from .field_io import _construct, _mapping, json_ready

BRANCH_RECORDS = dict(pipe=Pipe, emitter=EmitterCurve, limits=Limits, conditions=Conditions)


def parse_lateral(value):
    value = _mapping(value, tuple(f.name for f in fields(Lateral)), 'zone lateral')
    stations = value.get('stations')
    if not isinstance(stations, list) or not 1 <= len(stations) <= 2000:
        raise ValueError('each lateral requires 1–2000 surveyed emitter stations')
    return Lateral(identifier=value.get('identifier'), max_flow_lph=value.get('max_flow_lph'), source=value.get('source'),
        **{key: _construct(cls, value.get(key)) for key, cls in BRANCH_RECORDS.items()},
        stations=tuple(_construct(Station, station) for station in stations))


def drip_zone_input_schema():
    shared = drip_lateral_input_schema()['properties']
    def obj(properties):
        return dict(type='object', additionalProperties=False, required=list(properties), properties=properties)
    junction = {f.name: dict(type='string') if f.name == 'source' else dict(type='boolean', const=True)
                for f in fields(JunctionConditions)}
    branch = obj(dict(identifier=dict(type='string'), max_flow_lph=dict(type='number'), source=dict(type='string'),
                      **{key: shared[key] for key in (*BRANCH_RECORDS, 'stations')}))
    return obj(dict(schema_version=dict(type='integer', const=1), site=shared['site'],
                    supply=shared['inlet']['oneOf'][1], junction=obj(junction),
                    laterals=dict(type='array', minItems=2, maxItems=20, items=branch)))


def evaluate_drip_zone_request(payload):
    data = _mapping(payload, ('schema_version', 'site', 'supply', 'junction', 'laterals'), 'drip zone')
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('schema_version must be integer 1')
    site = _mapping(data.get('site'), ('name', 'timezone'), 'site')
    text('site name', site.get('name'))
    text('site timezone', site.get('timezone'))
    try:
        ZoneInfo(site['timezone'])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('site timezone must be an IANA timezone') from exc
    source = _mapping(data.get('supply'), tuple(f.name for f in fields(InletCurve)), 'zone supply')
    points = source.get('curve')
    if not isinstance(points, list) or not 2 <= len(points) <= 100:
        raise ValueError('supply curve requires 2–100 supplied points')
    supply = _construct(InletCurve, dict(source, curve=tuple(_construct(InletPoint, point) for point in points)))
    branches = data.get('laterals')
    if not isinstance(branches, list) or not 2 <= len(branches) <= 20:
        raise ValueError('zone requires 2–20 parallel laterals')
    laterals = [parse_lateral(value) for value in branches]
    result = evaluate_drip_zone(DripZone(supply, _construct(JunctionConditions, data.get('junction')), tuple(laterals)))
    result.update(schema_version=1, site=site)
    result['inputs'].update(schema_version=1, site=site)
    return json_ready(result)


def render_drip_zone_report(result):
    lines = ['Agronaut simultaneous drip zone — EXPERIMENTAL SCENARIO', result['site']['name'], result['feasibility_scope']]
    p = result['prediction']
    if p is None:
        lines.append('Prediction withheld: no supported common operating point found.')
    else:
        lines.extend(['Supplied limits: '+('PASS' if result['feasible'] else 'FAIL'),
            f"Common junction pressure: {p['junction_pressure_head_m']:.6g} m; total flow: {p['total_flow_lph']:.6g} L/h",
            f"Source head residual: {p['source_head_residual_m']:.3g} m; emitter mass residual: {p['emitter_mass_residual_lph']:.3g} L/h"])
        for row in p['laterals']:
            branch = row['result']['prediction']
            lines.append(f"{row['identifier']}: {branch['inlet_flow_lph']:.6g} L/h; emitter heads "
                         f"{branch['min_pressure_head_m']:.6g}–{branch['max_pressure_head_m']:.6g} m; "
                         f"branch limits {'PASS' if row['result']['feasible'] else 'FAIL'}")
    lines.extend(['Source configuration: '+result['inputs']['supply']['configuration'],
                  'No irrigation schedule or distributed-manifold solution is produced.'])
    for title, key in (('Constraints', 'binding_constraints'), ('Assumptions', 'assumptions'), ('Limits', 'limitations'), ('Sources', 'sources')):
        lines.extend(['', title+':', *(f'- {item}' for item in result[key])])
    return '\n'.join(lines)

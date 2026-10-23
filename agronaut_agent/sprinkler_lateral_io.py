"""Strict offline boundary and readable nozzle/riser hydraulic report."""

from dataclasses import fields
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.drip_lateral import Inlet, InletCurve, InletPoint, Limits
from agro_model.hydraulics import Pipe
from agro_model.sprinkler_lateral import (
    Conditions,
    Nozzle,
    SprinklerLateral,
    SprinklerStation,
    evaluate_sprinkler_lateral,
)
from agro_model.validation import text

from .drip_lateral_io import drip_lateral_input_schema
from .field_io import _construct, _mapping, json_ready

RECORDS = dict(pipe=Pipe, riser=Pipe, nozzle=Nozzle, limits=Limits, conditions=Conditions)


def sprinkler_lateral_input_schema():
    schema=drip_lateral_input_schema()
    props=schema['properties']
    props.pop('emitter')
    types={str:'string',float:'number',bool:'boolean'}

    def record(cls):
        return dict(type='object',additionalProperties=False,required=[f.name for f in fields(cls)],
                    properties={f.name:dict(type=types[f.type]) for f in fields(cls)})

    props.update({key:record(cls) for key,cls in RECORDS.items()})
    props['stations']=dict(type='array',minItems=1,maxItems=2000,items=record(SprinklerStation))
    for key in props['conditions']['properties']:
        if key!='source':
            props['conditions']['properties'][key]['const']=True
    schema['required']=list(props)
    return schema


def evaluate_sprinkler_lateral_request(payload):
    payload=_mapping(payload,('schema_version','site','inlet',*RECORDS,'stations'),'sprinkler lateral')
    version=payload.get('schema_version')
    if type(version) is not int or version not in (1,2):
        raise ValueError('schema_version must be integer 1 (fixed pressure) or 2 (inlet curve)')
    site=_mapping(payload.get('site'),('name','timezone'),'site')
    text('site name',site.get('name'))
    text('site timezone',site.get('timezone'))
    try:
        ZoneInfo(site['timezone'])
    except (ValueError,ZoneInfoNotFoundError) as exc:
        raise ValueError('site timezone must be an IANA timezone') from exc
    stations=payload.get('stations')
    if not isinstance(stations,list) or not 1<=len(stations)<=2000:
        raise ValueError('stations must contain 1–2000 ordered sprinkler records')
    if version==2:
        supplied=_mapping(payload.get('inlet'),tuple(f.name for f in fields(InletCurve)),'inlet curve')
        points=supplied.get('curve')
        if not isinstance(points,list) or not 2<=len(points)<=100:
            raise ValueError('inlet curve requires 2–100 supplied points')
        inlet=_construct(InletCurve,dict(supplied,curve=tuple(_construct(InletPoint,row) for row in points)))
    else:
        inlet=_construct(Inlet,payload.get('inlet'))
    spec=SprinklerLateral(**{key:_construct(cls,payload.get(key)) for key,cls in RECORDS.items()},inlet=inlet,
                          stations=tuple(_construct(SprinklerStation,row) for row in stations))
    result=evaluate_sprinkler_lateral(spec)
    result.update(schema_version=version,site=site)
    result['inputs'].update(schema_version=version,site=site)
    return json_ready(result)


def render_sprinkler_lateral_report(result):
    lines=['Agronaut sprinkler lateral — EXPERIMENTAL SCENARIO',result['site']['name'],result['feasibility_scope']]
    prediction=result['prediction']
    if prediction is None:
        lines.append('Prediction withheld: no supported operating point inside the nozzle and inlet envelopes.')
    else:
        lines += ['Supplied limits: '+('PASS' if result['feasible'] else 'FAIL'),
                  f"Inlet: {prediction['inlet_flow_lph']:.6g} L/h at {prediction['inlet_pressure_head_m']:.6g} m pressure head",
                  f"Nozzle pressure: {prediction['min_nozzle_pressure_head_m']:.6g}–{prediction['max_nozzle_pressure_head_m']:.6g} m; "
                  f"nominal flow variation: {100*prediction['flow_variation_fraction']:.3f}%",
                  f"Inlet head residual: {prediction['inlet_head_residual_m']:.3g} m; mass residual: {prediction['mass_balance_residual_lph']:.3g} L/h"]
        for row in prediction['nodes']:
            lines.append(f"{row['identifier']} at {row['distance_m']:g} m: base {row['base_pressure_head_m']:.6g} m head, "
                         f"nozzle {row['nozzle_pressure_head_m']:.6g} m head, discharge {row['nozzle_flow_lph']:.6g} L/h")
    lines.append('Nominal nozzle flow variation is not catch-can uniformity. No irrigation schedule is produced.')
    for title,key in (('Constraints','binding_constraints'),('Assumptions','assumptions'),('Limits','limitations'),('Sources','sources')):
        lines += ['',title+':',*(f'- {item}' for item in result[key])]
    return '\n'.join(lines)

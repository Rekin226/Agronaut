"""Independent laminar orifice references, bounded solves and pressure ratings."""

from math import pi, sqrt

import pytest

from agro_model.hydraulics import GRAVITY_M_S2
from agronaut_agent.sprinkler_lateral_io import evaluate_sprinkler_lateral_request as evaluate
from agronaut_agent.tests.test_sprinkler_lateral_workflow import inputs


@pytest.mark.parametrize('curved',[False,True])
def test_single_nozzle_matches_independent_laminar_quadratic(curved):
    data=inputs()
    data['pipe'].update(internal_diameter_mm=100,roughness_mm=0)
    data['riser'].update(internal_diameter_mm=80,roughness_mm=0)
    data['nozzle']['reference_flow_lph']=10
    data['stations']=data['stations'][:1]
    station=data['stations'][0]
    station.update(elevation_m=2,riser_height_m=1,riser_length_m=2,upstream_minor_k=3,riser_minor_k=7)
    if curved:
        data['schema_version']=2
        data['inlet']=dict(curve=[dict(flow_lph=0,pressure_head_m=35),dict(flow_lph=100,pressure_head_m=25)],
            max_static_head_m=50,max_flow_lph=100,head_reference='lateral_inlet_pressure',configuration='Synthetic fixed configuration',source='Synthetic curve')
    # Solve A*q²+B*q-(H-z-rise)=0 in m³/s. No production loss or solver helper is used.
    g,nu=GRAVITY_M_S2,data['limits']['viscosity_m2_s']
    qref=10/3_600_000
    a=30/qref**2 + 3/(2*g*(pi*.1**2/4)**2) + 7/(2*g*(pi*.08**2/4)**2)
    b=128*nu/(pi*g)*(12/.1**4+2/.08**4) + (0.1*3_600_000 if curved else 0)
    available=35-2-1
    q=2*available/(b+sqrt(b*b+4*a*available))
    expected_head=30*(q/qref)**2
    result=evaluate(data)
    row=result['prediction']['nodes'][0]
    assert row['nozzle_flow_lph']==pytest.approx(q*3_600_000,rel=1e-9)
    assert row['nozzle_pressure_head_m']==pytest.approx(expected_head,abs=2e-8)
    assert row['upstream_segment_loss']['reynolds']<2000 and row['riser_loss']['reynolds']<2000


def test_three_nozzles_match_independently_constructed_uneven_riser_network():
    data=inputs()
    data['pipe'].update(internal_diameter_mm=200,roughness_mm=0)
    data['riser'].update(internal_diameter_mm=100,roughness_mm=0)
    data['nozzle']['reference_flow_lph']=100
    data['stations']=data['stations'][:3]
    heads=[20,25,30]
    flows=[100*sqrt(h/30) for h in heads]
    cumulative=0
    g,nu=GRAVITY_M_S2,1e-6
    for i,row in enumerate(data['stations']):
        q=sum(flows[i:])/3_600_000
        cumulative+=128*12*nu*q/(pi*g*.2**4)+row['upstream_minor_k']*(q/(pi*.2**2/4))**2/(2*g)
        branch=flows[i]/3_600_000
        branch_loss=128*row['riser_length_m']*nu*branch/(pi*g*.1**4)+row['riser_minor_k']*(branch/(pi*.1**2/4))**2/(2*g)
        row['elevation_m']=35-cumulative-heads[i]-row['riser_height_m']-branch_loss
    # First elevation must fit inside the first 12 m pipe segment.
    data['inlet']['running_head_m']=30
    for row in data['stations']:
        row['elevation_m']-=5
    result=evaluate(data)
    prediction=result['prediction']
    assert prediction is not None
    for row,head,flow in zip(prediction['nodes'],heads,flows):
        assert row['nozzle_pressure_head_m']==pytest.approx(head,abs=3e-8)
        assert row['nozzle_flow_lph']==pytest.approx(flow,rel=1e-9)
    assert prediction['inlet_flow_lph']==pytest.approx(sum(flows),rel=1e-9)


@pytest.mark.parametrize('filename',['sprinkler_lateral.json','sprinkler_lateral_curve.json'])
def test_continuity_and_each_riser_and_lateral_head_balance(filename):
    data=inputs(filename)
    result=evaluate(data)
    prediction=result['prediction']
    assert result['feasible'] and result['schedule'] is None
    assert abs(prediction['inlet_head_residual_m'])<=prediction['head_tolerance_m']
    assert abs(prediction['mass_balance_residual_lph'])<1e-9
    head,z=prediction['inlet_pressure_head_m'],0
    for i,(row,station) in enumerate(zip(prediction['nodes'],data['stations'])):
        assert row['upstream_segment_flow_lph']==pytest.approx(sum(r['nozzle_flow_lph'] for r in prediction['nodes'][i:]))
        assert head+z-row['base_pressure_head_m']-row['elevation_m']==pytest.approx(row['upstream_segment_loss']['total_head_m'],abs=2e-8)
        assert row['base_pressure_head_m']-row['nozzle_pressure_head_m']-station['riser_height_m']==pytest.approx(row['riser_loss']['total_head_m'],abs=2e-11)
        assert row['nozzle_flow_lph']==pytest.approx(1000*sqrt(row['nozzle_pressure_head_m']/30))
        head,z=row['base_pressure_head_m'],row['elevation_m']
    if data['schema_version']==2:
        assert prediction['inlet_pressure_head_m']==pytest.approx(36-(prediction['inlet_flow_lph']-3000)*8/3000)


def test_internal_low_station_and_riser_static_ratings_are_checked():
    data=inputs()
    data['pipe']['max_head_m']=50
    data['riser']['max_head_m']=49
    data['nozzle']['max_static_head_m']=49
    result=evaluate(data)
    assert result['prediction'] is not None and result['feasible'] is False
    failed={(r['code'],r['identifier']) for r in result['constraints']}
    assert ('pipe_static_head_m','S3') in failed
    assert ('riser_static_head_m','S3') in failed
    assert ('nozzle_static_head_m','S3') in failed
    assert next(r for r in result['static_profile'] if r['identifier']=='S3')['nozzle_pressure_head_m']==50


@pytest.mark.parametrize('head',[1,100])
def test_unsupported_nozzle_heads_are_withheld(head):
    data=inputs()
    data['inlet'].update(running_head_m=head,max_static_head_m=max(head,50))
    result=evaluate(data)
    assert result['prediction'] is None and result['feasible'] is None
    assert result['static_profile']


def test_source_curve_is_never_extrapolated_and_load_limits_do_not_erase_prediction():
    data=inputs('sprinkler_lateral_curve.json')
    data['inlet']['max_flow_lph']=1
    data['limits']['max_velocity_m_s']=.001
    result=evaluate(data)
    assert result['prediction'] is not None and result['feasible'] is False
    assert {'inlet_flow_lph','riser_velocity_m_s','lateral_velocity_m_s'}<=set(result['binding_constraints'])
    data['inlet']['curve']=[dict(flow_lph=0,pressure_head_m=40),dict(flow_lph=100,pressure_head_m=30)]
    result=evaluate(data)
    assert result['prediction'] is None and result['feasible'] is None


@pytest.mark.parametrize('head',[10,45])
def test_supported_nozzle_pressure_endpoints_are_not_lost(head):
    data=inputs()
    data['stations']=data['stations'][:1]
    row=data['stations'][0]
    row.update(elevation_m=0,riser_height_m=0,riser_length_m=0,riser_minor_k=0,upstream_minor_k=0)
    data['pipe'].update(internal_diameter_mm=200,roughness_mm=0)
    data['nozzle']['reference_flow_lph']=10
    flow=10*sqrt(head/30)
    resistance=128*12*1e-6/(pi*GRAVITY_M_S2*.2**4*3_600_000)
    data['inlet']['running_head_m']=head+resistance*flow
    result=evaluate(data)
    assert result['prediction']['nodes'][0]['nozzle_pressure_head_m']==head


def test_taller_riser_changes_its_nozzle_and_shared_lateral_flow():
    data=inputs()
    before=evaluate(data)['prediction']
    data['stations'][1].update(riser_height_m=5,riser_length_m=6)
    after=evaluate(data)['prediction']
    assert after['nodes'][1]['nozzle_flow_lph']<before['nodes'][1]['nozzle_flow_lph']
    assert after['inlet_flow_lph']<before['inlet_flow_lph']
    # Lower common losses slightly increase the other nozzles at a fixed source head.
    assert after['nodes'][0]['nozzle_flow_lph']>before['nodes'][0]['nozzle_flow_lph']


@pytest.mark.parametrize('change',[
    lambda d:d['nozzle'].update(min_head_m=45),
    lambda d:d['nozzle'].update(reference_flow_lph=True),
    lambda d:d['nozzle'].update(source=''),
    lambda d:d['stations'][0].update(riser_length_m=.5),
    lambda d:d['stations'][0].update(riser_height_m=-1),
    lambda d:d['stations'][0].update(riser_minor_k=float('nan')),
    lambda d:d['stations'][0].update(elevation_m=13),
    lambda d:d['stations'][1].update(identifier='S1'),
    lambda d:d['stations'][1].update(distance_m=12),
    lambda d:d['conditions'].update(unregulated_atmospheric_outlets=1),
    lambda d:d.update(stations=[]),
])
def test_invalid_geometry_or_unconfirmed_applicability_is_rejected(change):
    data=inputs()
    change(data)
    with pytest.raises((ValueError,TypeError)):
        evaluate(data)

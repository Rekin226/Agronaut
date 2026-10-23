"""Independent shared-pressure solutions, branch completeness and envelope checks."""

import json
from copy import deepcopy
from math import fsum, pi
from pathlib import Path

import pytest

from agro_model.hydraulics import GRAVITY_M_S2
from agronaut_agent.drip_zone_io import evaluate_drip_zone_request as evaluate


def inputs():
    return json.loads((Path(__file__).resolve().parents[2]/'examples/drip_zone.json').read_text())


def test_linear_emitters_and_laminar_parallel_pipes_match_closed_form():
    data = inputs()
    terms = []
    for lateral, c, length, elevation in zip(data['laterals'], [.1, .15], [5, 10], [.2, .5]):
        lateral['stations'] = [dict(identifier='E1', distance_m=length, elevation_m=elevation,
                                    upstream_minor_k=0, source='Synthetic survey')]
        lateral['pipe'].update(internal_diameter_mm=100, roughness_mm=0)
        lateral['emitter'].update(reference_flow_lph=c*10, reference_head_m=10, exponent=1)
        r = 128*length*1e-6/(3_600_000*pi*GRAVITY_M_S2*.1**4)
        terms.append((c/(1+r*c), elevation))
    data['supply']['curve'] = [dict(flow_lph=0, pressure_head_m=14), dict(flow_lph=5, pressure_head_m=4)]
    # Q = A*H-B; supply H = 14-2Q. Solve these independently of the model.
    a = sum(c for c, z in terms)
    b = sum(c*z for c, z in terms)
    head = (14+2*b)/(1+2*a)
    result = evaluate(data)
    p = result['prediction']
    assert result['feasible'] is True
    assert p['junction_pressure_head_m'] == pytest.approx(head, abs=3e-8)
    assert p['total_flow_lph'] == pytest.approx(a*head-b, abs=1e-8)
    for row, (c, z) in zip(p['laterals'], terms):
        branch = row['result']['prediction']
        assert branch['inlet_flow_lph'] == pytest.approx(c*(head-z), abs=1e-8)
        assert branch['nodes'][0]['upstream_segment_loss']['reynolds'] < 2000


def test_every_branch_shares_head_and_satisfies_node_source_and_mass_balance():
    data = inputs()
    before = deepcopy(data)
    result = evaluate(data)
    p = result['prediction']
    assert result['inputs'] == data == before
    assert len(p['laterals']) == 2
    flows = []
    for row in p['laterals']:
        branch = row['result']
        assert branch['inputs']['inlet']['running_head_m'] == p['junction_pressure_head_m']
        flows.extend(n['emitter_flow_lph'] for n in branch['prediction']['nodes'])
        assert all(abs(n['head_balance_residual_m']) <= 1.01*branch['prediction']['head_tolerance_m']
                   for n in branch['prediction']['nodes'])
    assert p['total_flow_lph'] == pytest.approx(fsum(flows), abs=1e-12)
    assert p['junction_pressure_head_m'] == pytest.approx(14-8*p['total_flow_lph']/30, abs=p['source_head_tolerance_m'])
    assert abs(p['junction_mass_residual_lph']) < 1e-12
    assert abs(p['emitter_mass_residual_lph']) < 1e-12
    assert result['schedule'] is None and result['event_kind'] == 'scenario'
    json.dumps(result, allow_nan=False)


def test_joint_flow_is_not_sum_of_independent_single_lateral_operating_points():
    from agronaut_agent.drip_lateral_io import evaluate_drip_lateral_request

    data = inputs()
    independent = []
    for lateral in data['laterals']:
        single = {key: lateral[key] for key in ('pipe', 'emitter', 'limits', 'conditions', 'stations')}
        single.update(schema_version=2, site=data['site'], inlet=data['supply'])
        independent.append(evaluate_drip_lateral_request(single)['prediction']['inlet_flow_lph'])
    result = evaluate(data)
    assert result['prediction']['total_flow_lph'] < sum(independent)


@pytest.mark.parametrize('bounds', [(0, 20), (20, 40)])
def test_constant_emitter_flows_can_intersect_either_source_endpoint(bounds):
    data = inputs()
    for lateral in data['laterals']:
        lateral['emitter'].update(exponent=0, reference_flow_lph=2)
    data['supply']['curve'] = [dict(flow_lph=q, pressure_head_m=14-.2*q) for q in bounds]
    assert evaluate(data)['prediction']['total_flow_lph'] == 20
    assert evaluate(data)['prediction']['junction_pressure_head_m'] == 10


def test_nonoverlapping_lateral_pressure_ranges_withhold_entire_zone():
    data = inputs()
    for lateral, low in zip(data['laterals'], [2, 10]):
        lateral['stations'] = lateral['stations'][:1]
        lateral['stations'][0]['elevation_m'] = 0
        lateral['emitter'].update(min_head_m=low, max_head_m=low+1, reference_head_m=low+.5)
    result = evaluate(data)
    assert result['prediction'] is None and result['feasible'] is None
    assert 'no_common_operating_point_within_supplied_curves' in result['binding_constraints']


def test_source_flow_envelope_cannot_be_extrapolated_or_branches_dropped():
    data = inputs()
    for lateral in data['laterals']:
        lateral['emitter'].update(exponent=0, reference_flow_lph=2)
    data['supply']['curve'] = [dict(flow_lph=0, pressure_head_m=14), dict(flow_lph=12, pressure_head_m=10)]
    assert evaluate(data)['prediction'] is None  # each branch alone fits, the two together do not


@pytest.mark.parametrize('target,key,value,code', [
    ('supply', 'max_flow_lph', 1, 'source_flow_lph'),
    ('branch', 'max_flow_lph', 1, 'inlet_flow_lph'),
    ('pipe', 'max_head_m', 15, 'pipe_static_head_m'),
])
def test_solved_but_failed_limits_retain_conditional_predictions(target, key, value, code):
    data = inputs()
    record = data['supply'] if target == 'supply' else data['laterals'][0] if target == 'branch' else data['laterals'][0]['pipe']
    record[key] = value
    result = evaluate(data)
    assert result['prediction'] is not None and result['feasible'] is False
    assert code in result['binding_constraints']
    if target != 'supply':
        assert all(c['lateral'] == 'L1' for c in result['constraints'] if c['code'] == code)


def test_flat_source_and_permutation_are_supported():
    data = inputs()
    data['supply']['curve'] = [dict(flow_lph=0, pressure_head_m=12), dict(flow_lph=30, pressure_head_m=12)]
    first = evaluate(data)['prediction']
    data['laterals'].reverse()
    second = evaluate(data)['prediction']
    assert first['junction_pressure_head_m'] == second['junction_pressure_head_m'] == 12
    assert first['total_flow_lph'] == second['total_flow_lph']


@pytest.mark.parametrize('mutate', [
    lambda d: d['junction'].update(common_hydraulic_junction=False),
    lambda d: d['junction'].update(all_active_laterals_included=1),
    lambda d: d['junction'].update(no_unmodeled_connection_losses=False),
    lambda d: d['junction'].update(source=''),
    lambda d: d['laterals'].pop(),
    lambda d: d['laterals'][1].update(identifier='L1'),
    lambda d: d['laterals'][0].update(max_flow_lph=True),
    lambda d: d['laterals'][0].update(inlet={'running_head_m':10}),
    lambda d: d['supply'].update(head_reference='pump_head_gain'),
    lambda d: d.update(schema_version=True),
])
def test_unsupported_topology_or_implicit_boundaries_are_rejected(mutate):
    data = inputs()
    mutate(data)
    with pytest.raises((ValueError, TypeError)):
        evaluate(data)


def test_supported_intersection_is_found_even_when_source_endpoints_violate_branch_bands():
    data = inputs()
    data['supply'].update(curve=[dict(flow_lph=0, pressure_head_m=20), dict(flow_lph=40, pressure_head_m=2)],
                          max_static_head_m=25)
    # The tail is one metre below the junction; its pressure exceeds junction
    # pressure. Retain that surveyed elevation in the independently chosen band.
    data['laterals'][0]['emitter'].update(min_head_m=10, max_head_m=13, reference_head_m=10)
    result = evaluate(data)
    assert result['prediction'] is not None
    branch = result['prediction']['laterals'][0]['result']['prediction']
    assert 10 <= branch['min_pressure_head_m'] <= branch['max_pressure_head_m'] <= 13
    p = result['prediction']
    assert abs(p['source_head_residual_m']) <= p['source_head_tolerance_m']


def test_impossible_internal_survey_envelope_withholds_the_whole_zone():
    data = inputs()
    branch = data['laterals'][0]
    branch['emitter'].update(min_head_m=10, max_head_m=12, reference_head_m=10)
    for node, elevation in zip(branch['stations'], [0, 4, 0, 0, 0]):
        node['elevation_m'] = elevation
    result = evaluate(data)
    assert result['prediction'] is None
    assert len(result['static_profiles']) == 2


def test_total_emitter_software_limit_is_enforced_before_solving():
    data = inputs()
    for branch in data['laterals']:
        branch['stations'] = [dict(identifier=f'E{i}', distance_m=i, elevation_m=0, upstream_minor_k=0,
                                   source='Synthetic count boundary') for i in range(1, 1002)]
    with pytest.raises(ValueError, match='2000 surveyed emitters'):
        evaluate(data)

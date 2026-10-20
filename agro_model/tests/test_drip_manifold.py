"""Independent manifold solution and conservation/envelope checks."""

import json
from copy import deepcopy
from math import fsum, pi
from pathlib import Path

import pytest

from agronaut_agent.drip_manifold_io import evaluate_drip_manifold_request as evaluate


def inputs():
    return json.loads((Path(__file__).resolve().parents[2]/'examples/drip_manifold.json').read_text())


def test_laminar_linear_emitter_network_matches_independent_closed_form():
    data = inputs()
    data['pipe'].update(internal_diameter_mm=2, roughness_mm=0)
    coefficients = []
    for node, c, length in zip(data['takeoffs'], [.01, .015], [5, 10]):
        node.update(elevation_m=0, upstream_minor_k=0)
        lateral = node['lateral']
        lateral['pipe'].update(internal_diameter_mm=5, roughness_mm=0)
        lateral['stations'] = [dict(identifier='E1', distance_m=length, elevation_m=0,
                                    upstream_minor_k=0, source='Synthetic survey')]
        lateral['emitter'].update(reference_flow_lph=c*10, reference_head_m=10, exponent=1)
        resistance = 128*length*1e-6/(3_600_000*pi*9.80665*.005**4)
        coefficients.append(c/(1+resistance*c))
    data['supply']['curve'] = [dict(flow_lph=0, pressure_head_m=14), dict(flow_lph=2, pressure_head_m=4)]
    r1, r2 = [128*length*1e-6/(3_600_000*pi*9.80665*.002**4) for length in [10, 20]]
    c1, c2 = coefficients
    a = c1+c2/(1+r2*c2)
    h1 = 14/(1+(5+r1)*a)
    h2 = h1/(1+r2*c2)
    result = evaluate(data)
    p = result['prediction']
    assert result['feasible'] is True
    assert p['total_flow_lph'] == pytest.approx(a*h1, abs=1e-9)
    assert h1-h2 > .1  # The test would detect a common-junction substitution.
    for row, h, c in zip(p['nodes'], [h1, h2], coefficients):
        assert row['pressure_head_m'] == pytest.approx(h, abs=3e-8)
        assert row['lateral_flow_lph'] == pytest.approx(c*h, abs=1e-9)
        assert row['upstream_segment_loss']['reynolds'] < 2000
        assert row['lateral_result']['prediction']['nodes'][0]['upstream_segment_loss']['reynolds'] < 2000


def test_survey_datum_and_each_mass_and_head_balance():
    data = inputs()
    before = deepcopy(data)
    result = evaluate(data)
    p = result['prediction']
    assert before == data == result['inputs']
    previous_h, previous_z = p['source_pressure_head_m'], 0
    for index, (row, takeoff) in enumerate(zip(p['nodes'], data['takeoffs'])):
        assert previous_h+previous_z == pytest.approx(row['pressure_head_m']+takeoff['elevation_m']+
            row['upstream_segment_loss']['total_head_m'], abs=p['source_head_tolerance_m'])
        assert row['upstream_flow_lph'] == pytest.approx(fsum(n['lateral_flow_lph'] for n in p['nodes'][index:]))
        assert abs(row['mass_balance_residual_lph']) < 1e-12
        branch = row['lateral_result']
        assert branch['inputs']['inlet']['running_head_m'] == row['pressure_head_m']
        assert branch['inputs']['inlet']['max_static_head_m'] == 20-takeoff['elevation_m']
        for station, static in zip(takeoff['lateral']['stations'], branch['static_profile'][1:]):
            assert static['pressure_head_m'] == pytest.approx(20-takeoff['elevation_m']-station['elevation_m'])
        previous_h, previous_z = row['pressure_head_m'], row['elevation_m']
    assert p['source_pressure_head_m'] == pytest.approx(14-8*p['total_flow_lph']/30)
    assert abs(p['source_head_residual_m']) <= p['source_head_tolerance_m']
    assert abs(p['emitter_mass_residual_lph']) < 1e-12
    assert result['schedule'] is None and result['event_kind'] == 'scenario'
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('path,value,code', [
    (('supply', 'max_flow_lph'), 1, 'source_flow_lph'),
    (('pipe', 'max_head_m'), 10, 'manifold_static_head_m'),
    (('limits', 'max_velocity_m_s'), .001, 'manifold_velocity_m_s'),
    (('takeoffs', 0, 'lateral', 'max_flow_lph'), 1, 'inlet_flow_lph'),
    (('takeoffs', 0, 'lateral', 'pipe', 'max_head_m'), 10, 'pipe_static_head_m'),
])
def test_failed_limits_retain_prediction(path, value, code):
    data = inputs()
    record = data
    for key in path[:-1]:
        record = record[key]
    record[path[-1]] = value
    result = evaluate(data)
    assert result['prediction'] is not None and result['feasible'] is False
    assert code in result['binding_constraints']


@pytest.mark.parametrize('bounds', [(0, 20), (20, 40)])
def test_constant_emitters_can_meet_source_endpoints(bounds):
    data = inputs()
    for node in data['takeoffs']:
        node['lateral']['emitter'].update(exponent=0, reference_flow_lph=2)
    data['supply']['curve'] = [dict(flow_lph=q, pressure_head_m=14-.2*q) for q in bounds]
    p = evaluate(data)['prediction']
    assert p['total_flow_lph'] == 20
    assert p['source_pressure_head_m'] == 10


def test_single_takeoff_is_supported():
    data = inputs()
    data['takeoffs'] = data['takeoffs'][:1]
    p = evaluate(data)['prediction']
    assert len(p['nodes']) == 1
    assert p['nodes'][0]['downstream_flow_lph'] == 0
    assert p['total_flow_lph'] == p['nodes'][0]['lateral_flow_lph']


def test_no_branch_is_dropped_to_fit_source_curve_and_static_checks_survive():
    data = inputs()
    for node in data['takeoffs']:
        node['lateral']['emitter'].update(exponent=0, reference_flow_lph=2)
    data['supply']['curve'][-1]['flow_lph'] = 12  # Each branch alone fits, the whole manifold cannot.
    data['takeoffs'][0]['lateral']['pipe']['max_head_m'] = 10
    result = evaluate(data)
    assert result['prediction'] is None and result['feasible'] is None
    assert 'no_supported_manifold_operating_point' in result['binding_constraints']
    assert 'pipe_static_head_m' in result['binding_constraints']
    assert len(result['lateral_static_profiles']) == 2


def test_impossible_internal_branch_envelope_withholds_whole_manifold():
    data = inputs()
    branch = data['takeoffs'][0]['lateral']
    branch['emitter'].update(min_head_m=10, max_head_m=12, reference_head_m=10)
    for station, z in zip(branch['stations'], [0, 4, 0, 0, 0]):
        station['elevation_m'] = z
    assert evaluate(data)['prediction'] is None


@pytest.mark.parametrize('mutate', [
    lambda d: d['supply'].update(head_reference='lateral_inlet_pressure'),
    lambda d: d['supply'].update(head_reference='pump_head_gain'),
    lambda d: d['conditions'].update(closed_tail=False),
    lambda d: d['conditions'].update(all_active_laterals_included=1),
    lambda d: d['conditions'].update(no_unmodeled_connections=False),
    lambda d: d['takeoffs'][1].update(distance_m=9),
    lambda d: d['takeoffs'][1].update(elevation_m=100),
    lambda d: d['takeoffs'][0].update(source=''),
    lambda d: d['takeoffs'][0]['lateral'].update(identifier='inlet'),
    lambda d: d['takeoffs'][1]['lateral'].update(identifier='L1'),
    lambda d: d['takeoffs'][0].update(upstream_minor_k=True),
    lambda d: d['takeoffs'][0].update(unknown_field=1),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(takeoffs=[]),
])
def test_incomplete_or_unsupported_inputs_rejected(mutate):
    data = inputs()
    mutate(data)
    with pytest.raises((ValueError, TypeError)):
        evaluate(data)


def test_total_emitter_limit_rejected_before_solving():
    data = inputs()
    for node in data['takeoffs']:
        node['lateral']['stations'] = [dict(identifier=f'E{i}', distance_m=i, elevation_m=0,
            upstream_minor_k=0, source='Synthetic count boundary') for i in range(1, 1002)]
    with pytest.raises(ValueError, match='2000 emitters'):
        evaluate(data)

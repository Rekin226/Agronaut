"""Whole-network emitter pairing, distinct branch identities and owned evidence."""

import json
from pathlib import Path

import pytest

from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_drip_diagnostics import event_inputs
from agronaut_agent.tests.test_field_workflow import setup  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def inputs(task='drip_manifold'):
    return json.loads((ROOT/'examples'/f'{task}.json').read_text())


def save_pair(workflow, task='drip_manifold', event=None, data=None):
    pred = workflow.save('owner', 'field', task, data or inputs(task), cycle_id='cycle', request_id='network')
    ref = workflow.save('owner', 'field', 'drip_emitter_test', event or event_inputs(), cycle_id='cycle', request_id='event')
    return pred, ref


def request(pred, *refs):
    return dict(calculation_id=pred['id'], dataset_kind='synthetic', selection_note='Synthetic whole-event selection',
        comparisons=[dict(test_calculation_id=ref['id'], independence='held_out',
            representation_note='Synthetic physical lateral identities and local emitter surveys',
            conditions_note='Synthetic same full active branch set and source configuration at steady operation',
            water_density_kg_m3=1000, density_source='Synthetic density', pressure_reference='gauge_at_emitter',
            mappings=[dict(sample_id=f'emitter-{i}', lateral_id='L1' if i <= 4 else 'L2',
                           station_id=f'E{(i-1)%4+1}', mapping_note='Synthetic physical emitter association') for i in range(1,9)])
            for ref in refs])


def compare(workflow, data):
    return execute_field_workflow(workflow.db, 'owner', 'field', 'compare_drip_network', data)


@pytest.mark.parametrize('task', ['drip_zone', 'drip_manifold'])
def test_distinct_lateral_station_pairs_preserve_saved_pressure_and_readonly_evidence(setup, task):  # noqa: F811
    workflow, farms = setup
    pred, ref = save_pair(workflow, task)
    before = farms.export('owner')
    report = compare(workflow, request(pred, ref))
    assert farms.export('owner') == before
    assert report['calculation'] == pred and report['comparisons'][0]['test_calculation'] == ref
    assert report['report_type'] == 'drip_network_diagnostic'
    assert report['evidence_status'] == 'diagnostic' and report['acceptance_status'] == 'not_assessed'
    assert report['independence_status'] == 'not_established'
    pairs = report['comparisons'][0]['pairs']
    expected = (pred['result']['result']['prediction']['laterals'][1]['result'] if task == 'drip_zone'
                else pred['result']['result']['prediction']['nodes'][1]['lateral_result'])['prediction']['nodes'][0]
    assert pairs[4]['mapping']['station_id'] == pairs[0]['mapping']['station_id'] == 'E1'
    assert pairs[4]['predicted_station'] == expected
    assert pairs[4]['errors']['flow_lph']['error'] == pytest.approx(expected['emitter_flow_lph']-6)
    assert pairs[4]['errors']['pressure_kpa']['error'] == pytest.approx(expected['pressure_head_m']*9.80665-150)
    assert report['metrics']['flow_lph']['paired_sample_count'] == 8
    assert report['lateral_metrics']['L2']['flow_lph']['paired_sample_count'] == 4
    assert report['comparisons'][0]['unmeasured_stations'] == [dict(lateral_id='L1', station_id='E5'),dict(lateral_id='L2',station_id='E5')]
    json.dumps(report, allow_nan=False)


def test_unmatched_branch_missing_readings_and_unknown_density_stay_visible(setup):  # noqa: F811
    workflow, _ = setup
    event = event_inputs()
    event['samples'][0]['volume_ml'] = None
    pred, ref = save_pair(workflow, event=event)
    data = request(pred, ref)
    data['comparisons'][0]['water_density_kg_m3'] = None
    for mapping in data['comparisons'][0]['mappings'][4:]:
        mapping.update(lateral_id=None, station_id=None, mapping_note='Recorded branch outside modeled layout')
    report = compare(workflow, data)
    assert report['metrics']['flow_lph']['paired_sample_count'] == 3
    assert report['metrics']['pressure_kpa']['rmse'] is None
    assert report['lateral_metrics']['L2']['flow_lph']['bias'] is None
    assert report['lateral_metrics']['L2']['flow_lph']['events_without_pairs'] == 1
    assert len(report['comparisons'][0]['unmeasured_stations']) == 6
    assert report['comparisons'][0]['pairs'][4]['status'] == 'unmatched_station'
    assert report['comparisons'][0]['pairs'][4]['sample']['flow_l_h'] == 6


def test_equal_event_weight_and_calibration_exclusion_do_not_pool_samples(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    event = event_inputs()
    event['event'].update(started_at='2026-07-08T09:00:00Z', ended_at='2026-07-08T09:30:00Z')
    event['samples'][0]['volume_ml'] = 100
    second = workflow.save('owner', 'field', 'drip_emitter_test', event, cycle_id='cycle', request_id='second')
    data = request(pred, ref, second)
    # The second event has just one mapped observation; other samples remain visible.
    for mapping in data['comparisons'][1]['mappings'][1:]:
        mapping.update(lateral_id=None, station_id=None)
    report = compare(workflow, data)
    a, b = report['comparisons']
    assert report['metrics']['flow_lph']['paired_sample_count'] == 9
    assert report['metrics']['flow_lph']['bias'] == pytest.approx((a['metrics']['flow_lph']['bias']+b['metrics']['flow_lph']['bias'])/2)
    assert report['lateral_metrics']['L1']['flow_lph']['bias'] == pytest.approx((a['lateral_metrics']['L1']['flow_lph']['bias']+b['lateral_metrics']['L1']['flow_lph']['bias'])/2)
    data['comparisons'][1]['independence'] = 'used_for_calibration'
    excluded = compare(workflow, data)
    assert excluded['included_test_count'] == 1
    assert excluded['metrics']['flow_lph']['bias'] == a['metrics']['flow_lph']['bias']
    assert excluded['comparisons'][1]['metrics']['flow_lph']['count'] == 1


@pytest.mark.parametrize('change,match', [
    (lambda d: d['comparisons'][0]['mappings'].pop(), 'every sample'),
    (lambda d: d['comparisons'][0]['mappings'][0].update(lateral_id=None), 'both'),
    (lambda d: d['comparisons'][0]['mappings'][0].update(station_id=None), 'both'),
    (lambda d: d['comparisons'][0]['mappings'][0].update(lateral_id='absent'), 'modeled'),
    (lambda d: d['comparisons'][0]['mappings'][1].update(station_id='E1'), 'at most once'),
    (lambda d: d['comparisons'][0]['mappings'][0].update(station_id='absent'), 'modeled'),
    (lambda d: d['comparisons'][0]['mappings'][0].update(mapping_note=''), 'mapping_note'),
    (lambda d: d['comparisons'][0]['mappings'][1].update(lateral_id='L2'), 'one-to-one'),
    (lambda d: d['comparisons'][0]['mappings'][4].update(lateral_id='L1',station_id='E5'), 'one-to-one'),
    (lambda d: d['comparisons'][0].update(lateral='L1'), 'requires exactly'),
])
def test_ambiguous_incomplete_or_cross_branch_pairing_rejected(setup, change, match):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    data = request(pred, ref)
    change(data)
    with pytest.raises(ValueError, match=match):
        compare(workflow, data)


def test_duplicate_test_or_start_cannot_inflate_event_count(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    with pytest.raises(ValueError, match='only once'):
        compare(workflow, request(pred, ref, ref))
    second = workflow.save('owner', 'field', 'drip_emitter_test', event_inputs(), cycle_id='cycle', request_id='duplicate')
    with pytest.raises(ValueError, match='same event start'):
        compare(workflow, request(pred, ref, second))


def test_failed_limits_retained_but_unsolved_network_and_wrong_task_rejected(setup):  # noqa: F811
    workflow, _ = setup
    data = inputs()
    data['supply']['max_flow_lph'] = 1
    pred, ref = save_pair(workflow, data=data)
    assert pred['result']['feasible'] is False
    assert compare(workflow, request(pred, ref))['metrics']['flow_lph']['paired_sample_count'] == 8
    data['supply']['curve'][1]['flow_lph'] = 1
    unsolved = workflow.save('owner','field','drip_manifold',data,cycle_id='cycle',request_id='unsolved')
    with pytest.raises(ValueError, match='solved'):
        compare(workflow, request(unsolved,ref))
    with pytest.raises(ValueError, match='solved'):
        compare(workflow, request(ref,ref))
    with pytest.raises(ValueError, match='drip_lateral'):
        workflow.compare_drip_lateral('owner','field',**request(pred,ref))


def test_owner_unit_cycle_timezone_and_observed_status_enforced(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    data = request(pred, ref)
    with pytest.raises(ValueError):
        workflow.compare_drip_network('other','field',**data)
    with pytest.raises(ValueError):
        workflow.compare_drip_network('owner','field2',**data)
    foreign = workflow.save('owner','field2','drip_emitter_test',event_inputs(),cycle_id='cycle2',request_id='foreign')
    with pytest.raises(ValueError):
        compare(workflow,request(pred,foreign))
    no_cycle = workflow.save('owner','field','drip_emitter_test',event_inputs(),request_id='no-cycle')
    with pytest.raises(ValueError,match='same unit and crop cycle'):
        compare(workflow,request(pred,no_cycle))
    data['dataset_kind'] = 'field'
    with pytest.raises(ValueError,match='observed'):
        compare(workflow,data)
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError,match='timezone'):
        compare(workflow,data)


def test_unknown_pressure_reference_retains_flow_diagnostics(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    data = request(pred, ref)
    data['comparisons'][0]['pressure_reference'] = 'unknown'
    report = compare(workflow, data)
    assert report['metrics']['flow_lph']['paired_sample_count'] == 8
    assert report['metrics']['pressure_kpa']['paired_sample_count'] == 0
    assert all(values['pressure_kpa']['bias'] is None for values in report['lateral_metrics'].values())


def test_field_label_and_changed_cycle_closure_are_rechecked(setup):  # noqa: F811
    from agronaut_agent.crop_records import CropRecords

    workflow, _ = setup
    event = event_inputs()
    event['event'].update(kind='observed',started_at='2026-07-07T23:50:00Z',ended_at='2026-07-08T00:20:00Z')
    pred, ref = save_pair(workflow,event=event)
    data = request(pred,ref)
    data['dataset_kind']='field'
    assert compare(workflow,data)['acceptance_status']=='not_assessed'
    CropRecords(workflow.db).close_cycle('owner','field','cycle','2026-07-07','Synthetic closure','Synthetic notebook',request_id='close',expected_revision=None)
    with pytest.raises(ValueError):
        compare(workflow,data)


@pytest.mark.parametrize('task', ['drip_zone', 'drip_manifold'])
def test_stdlib_cli_compares_saved_network_without_a_model(tmp_path, task):
    import subprocess
    import sys

    db, path = tmp_path/'cli.sqlite3', tmp_path/'request.json'

    def command(*args):
        result = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)

    unit = json.loads((ROOT/'examples/soil_unit.json').read_text())
    unit['farm']['timezone'] = 'UTC'
    path.write_text(json.dumps(unit))
    command('farm', '--db', str(db), 'create', '--input', str(path))

    def operation(name, data):
        path.write_text(json.dumps(data))
        return command('field', '--db', str(db), name, '--input', str(path))

    pred = operation('save', dict(task=task, inputs=inputs(task), request_id='network'))
    ref = operation('save', dict(task='drip_emitter_test', inputs=event_inputs(), request_id='test'))
    report = operation('compare_drip_network', request(pred, ref))
    assert report['metrics']['flow_lph']['paired_sample_count'] == 8
    assert report['lateral_metrics']['L2']['flow_lph']['paired_sample_count'] == 4
    assert len(command('field', '--db', str(db), 'list')['calculations']) == 2


def test_agent_dispatch_uses_active_unit_and_retains_records(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units

    agent, _ = agent_and_units(tmp_path)
    agent.select_unit('cli', 'one', 'field')
    pred = agent.field_workflow('cli','one','save',dict(task='drip_manifold',inputs=inputs(),request_id='network'))
    ref = agent.field_workflow('cli','one','save',dict(task='drip_emitter_test',inputs=event_inputs(),request_id='test'))
    before = agent.field_workflow('cli','one','list',{})
    agent._base.replies = [AIMessage(content='',tool_calls=[dict(name='field_records',id='network-compare',
        args=dict(operation='compare_drip_network',data=request(pred,ref)))]),AIMessage(content='Comparison ready.')]
    agent.handle_message('cli','one','Compare the recorded emitter measurements with my saved manifold scenario')
    report = json.loads(agent.tool_results('cli','one','field_records')[-1])
    assert report['report_type']=='drip_network_diagnostic'
    assert report['metrics']['flow_lph']['paired_sample_count']==8
    assert agent.field_workflow('cli','one','list',{})==before


def test_declared_physical_aliases_are_used_without_guessing_names(setup):  # noqa: F811
    workflow, _ = setup
    data = inputs()
    data['takeoffs'][0]['lateral']['identifier'] = 'north'
    data['takeoffs'][1]['lateral']['identifier'] = 'south'
    pred, ref = save_pair(workflow,data=data)
    query = request(pred,ref)
    for mapping in query['comparisons'][0]['mappings']:
        mapping['lateral_id'] = {'L1':'north','L2':'south'}[mapping['lateral_id']]
    report = compare(workflow,query)
    assert set(report['lateral_metrics']) == {'north','south'}
    assert report['comparisons'][0]['pairs'][0]['sample']['lateral'] == 'L1'
    assert report['comparisons'][0]['pairs'][0]['mapping']['lateral_id'] == 'north'

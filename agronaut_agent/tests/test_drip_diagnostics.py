"""No-model ownership, mapping, evidence retention and metric-selection checks."""

import json
from pathlib import Path

import pytest

from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_drip_lateral_workflow import inputs
from agronaut_agent.tests.test_field_workflow import setup  # noqa: F401


def event_inputs():
    return json.loads((Path(__file__).resolve().parents[2]/'examples/drip_emitters.json').read_text())


def save_pair(workflow, event=None, filename='drip_lateral_curve.json'):
    pred = workflow.save('owner', 'field', 'drip_lateral', inputs(filename), cycle_id='cycle', request_id='prediction')
    reference = workflow.save('owner', 'field', 'drip_emitter_test', event or event_inputs(), cycle_id='cycle', request_id='test')
    return pred, reference


def request(pred, *references, independence='held_out', kind='synthetic', density=1000):
    return dict(calculation_id=pred['id'], dataset_kind=kind, selection_note='Synthetic selection, not field validation',
        comparisons=[dict(test_calculation_id=ref['id'], lateral='L1', independence=independence,
            representation_note='Synthetic same physical lateral, emitter type and survey',
            conditions_note='Synthetic same inlet and source conditions', water_density_kg_m3=density,
            density_source='Synthetic density or explicitly unavailable', pressure_reference='gauge_at_emitter',
            mappings=[dict(sample_id=f'emitter-{i}', station_id=f'E{i}', mapping_note='Synthetic exact station association')
                      for i in range(1, 5)]) for ref in references])


def compare(workflow, data):
    return execute_field_workflow(workflow.db, 'owner', 'field', 'compare_drip_lateral', data)


@pytest.mark.parametrize('filename', ['drip_lateral.json', 'drip_lateral_curve.json'])
def test_owned_readonly_report_retains_inputs_results_and_units(setup, filename):  # noqa: F811
    workflow, farms = setup
    pred, ref = save_pair(workflow, filename=filename)
    before = farms.export('owner')
    report = compare(workflow, request(pred, ref))
    assert farms.export('owner') == before
    assert report['calculation'] == pred and report['comparisons'][0]['test_calculation'] == ref
    pair = report['comparisons'][0]['pairs'][0]
    modeled = pred['result']['result']['prediction']['nodes'][0]
    assert pair['errors']['flow_lph']['error'] == pytest.approx(modeled['emitter_flow_lph']-1.2)
    assert pair['errors']['pressure_kpa']['error'] == pytest.approx(modeled['pressure_head_m']*9.80665-150)
    assert report['metrics']['flow_lph']['paired_sample_count'] == 4
    assert report['comparisons'][0]['unmeasured_station_ids'] == ['E5']
    assert len(report['comparisons'][0]['other_lateral_sample_ids']) == 4
    assert report['evidence_status'] == 'diagnostic'
    assert report['independence_status'] == 'not_established' and report['acceptance_status'] == 'not_assessed'
    json.dumps(report, allow_nan=False)


def test_missing_volumes_density_and_unmatched_samples_stay_visible(setup):  # noqa: F811
    workflow, _ = setup
    event = event_inputs()
    event['samples'][0]['volume_ml'] = None
    pred, ref = save_pair(workflow, event)
    assert ref['result']['result']['statistics']['mean_flow_l_h'] is None
    data = request(pred, ref, density=None)
    data['comparisons'][0]['mappings'][1]['station_id'] = None
    report = compare(workflow, data)
    assert report['metrics']['flow_lph']['paired_sample_count'] == 2
    assert report['metrics']['pressure_kpa']['event_count'] == 0
    assert report['metrics']['pressure_kpa']['bias'] is None
    pairs = report['comparisons'][0]['pairs']
    assert pairs[0]['errors']['flow_lph']['recorded'] is None
    assert pairs[1]['status'] == 'unmatched_station' and pairs[1]['sample']['flow_l_h'] == 1.2
    assert report['comparisons'][0]['unmeasured_station_ids'] == ['E2', 'E5']


@pytest.mark.parametrize('independence', ['unknown', 'used_for_calibration'])
def test_calibration_and_unknown_tests_visible_but_not_in_aggregate(setup, independence):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    report = compare(workflow, request(pred, ref, independence=independence))
    assert report['included_test_count'] == 0
    assert report['metrics']['flow_lph']['bias'] is None
    assert report['comparisons'][0]['metrics']['flow_lph']['count'] == 4
    assert report['comparisons'][0]['exclusion_reason'] == independence


@pytest.mark.parametrize('change', [
    lambda d: d['comparisons'][0]['mappings'].pop(),
    lambda d: d['comparisons'][0]['mappings'][0].update(station_id='other'),
    lambda d: d['comparisons'][0]['mappings'][1].update(station_id='E1'),
    lambda d: d['comparisons'][0]['mappings'][0].update(sample_id='emitter-5'),
    lambda d: d['comparisons'][0]['mappings'][1].update(sample_id='emitter-1'),
    lambda d: d['comparisons'][0]['mappings'][0].update(mapping_note=''),
    lambda d: d['comparisons'][0].update(lateral='absent'),
    lambda d: d['comparisons'][0].update(conditions_note=''),
    lambda d: d['comparisons'][0].update(representation_note=''),
    lambda d: d['comparisons'][0].update(density_source=''),
    lambda d: d['comparisons'][0].update(water_density_kg_m3=True),
    lambda d: d['comparisons'][0].update(independence='validated'),
    lambda d: d.update(dataset_kind='validated'),
    lambda d: d.update(selection_note=''),
])
def test_partial_ambiguous_or_unjustified_comparisons_are_rejected(setup, change):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    data = request(pred, ref)
    change(data)
    with pytest.raises(ValueError):
        compare(workflow, data)


def test_duplicate_event_cannot_inflate_metrics_using_another_saved_evaluation(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    with pytest.raises(ValueError, match='only once'):
        compare(workflow, request(pred, ref, ref))
    event = event_inputs()
    event['event']['started_at'] = '2026-07-07T17:00:00+08:00'
    second = workflow.save('owner', 'field', 'drip_emitter_test', event, cycle_id='cycle', request_id='duplicate')
    with pytest.raises(ValueError, match='same event start'):
        compare(workflow, request(pred, ref, second))


def test_scenario_cannot_be_labeled_field_and_failed_limits_do_not_filter_data(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    with pytest.raises(ValueError, match='observed'):
        compare(workflow, request(pred, ref, kind='field'))
    data = inputs()
    data['inlet']['max_flow_lph'] = 1
    failed = workflow.save('owner', 'field', 'drip_lateral', data, cycle_id='cycle', request_id='failed')
    assert failed['result']['feasible'] is False
    assert compare(workflow, request(failed, ref))['metrics']['flow_lph']['paired_sample_count'] == 4
    data['inlet']['running_head_m'] = .001
    withheld = workflow.save('owner', 'field', 'drip_lateral', data, cycle_id='cycle', request_id='withheld')
    with pytest.raises(ValueError, match='solved'):
        compare(workflow, request(withheld, ref))


def test_owner_unit_cycle_and_current_timezone_are_enforced(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    with pytest.raises(ValueError):
        workflow.compare_drip_lateral('other', 'field', **request(pred, ref))
    foreign = workflow.save('owner', 'field2', 'drip_emitter_test', event_inputs(), cycle_id='cycle2', request_id='foreign')
    with pytest.raises(ValueError):
        compare(workflow, request(pred, foreign))
    no_cycle = workflow.save('owner', 'field', 'drip_emitter_test', event_inputs(), request_id='no-cycle')
    with pytest.raises(ValueError, match='same unit and crop cycle'):
        compare(workflow, request(pred, no_cycle))
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError, match='timezone'):
        compare(workflow, request(pred, ref))
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
        conn.execute("UPDATE production_cycles SET planted_on='2026-07-08' WHERE id='cycle'")
    with pytest.raises(ValueError, match='planting'):
        compare(workflow, request(pred, ref))


def test_field_label_does_not_establish_validation_and_closure_is_rechecked(setup):  # noqa: F811
    from agronaut_agent.crop_records import CropRecords

    workflow, _ = setup
    event = event_inputs()
    event['event'].update(kind='observed', started_at='2026-07-07T23:50:00Z', ended_at='2026-07-08T00:20:00Z')
    pred, ref = save_pair(workflow, event)
    report = compare(workflow, request(pred, ref, kind='field'))
    assert report['evidence_status'] == 'diagnostic'
    CropRecords(workflow.db).close_cycle('owner', 'field', 'cycle', '2026-07-07',
        'Synthetic closure', 'Synthetic notebook', request_id='close', expected_revision=None)
    with pytest.raises(ValueError):
        compare(workflow, request(pred, ref, kind='field'))


def test_stdlib_cli_saves_and_compares_without_a_model(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    db, path = tmp_path/'cli.sqlite3', tmp_path/'request.json'

    def command(*args):
        result = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', *args], cwd=root,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)

    unit = json.loads((root/'examples/soil_unit.json').read_text())
    unit['farm']['timezone'] = 'UTC'
    path.write_text(json.dumps(unit))
    command('farm', '--db', str(db), 'create', '--input', str(path))

    def operation(name, data):
        path.write_text(json.dumps(data))
        return command('field', '--db', str(db), name, '--input', str(path))

    pred = operation('save', dict(task='drip_lateral', inputs=inputs('drip_lateral_curve.json'), request_id='prediction'))
    ref = operation('save', dict(task='drip_emitter_test', inputs=event_inputs(), request_id='test'))
    report = operation('compare_drip_lateral', request(pred, ref))
    assert report['metrics']['flow_lph']['paired_sample_count'] == 4
    assert len(command('field', '--db', str(db), 'list')['calculations']) == 2


def test_agent_dispatch_scopes_comparison_and_does_not_write_records(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units

    agent, _ = agent_and_units(tmp_path)
    agent.select_unit('cli', 'one', 'field')
    pred = agent.field_workflow('cli', 'one', 'save', dict(task='drip_lateral', inputs=inputs(), request_id='prediction'))
    ref = agent.field_workflow('cli', 'one', 'save', dict(task='drip_emitter_test', inputs=event_inputs(), request_id='test'))
    before = agent.field_workflow('cli', 'one', 'list', {})
    agent._base.replies = [AIMessage(content='', tool_calls=[dict(name='field_records', id='drip-compare',
        args=dict(operation='compare_drip_lateral', data=request(pred, ref)))]), AIMessage(content='Comparison ready.')]
    agent.handle_message('cli', 'one', 'Compare these synthetic emitter samples with my saved drip lateral scenario')
    report = json.loads(agent.tool_results('cli', 'one', 'field_records')[-1])
    assert report['evidence_status'] == 'diagnostic' and report['metrics']['flow_lph']['paired_sample_count'] == 4
    assert agent.field_workflow('cli', 'one', 'list', {}) == before


def test_unknown_gauge_reference_keeps_flow_metrics_and_declines_pressure_metrics(setup):  # noqa: F811
    workflow, _ = setup
    pred, ref = save_pair(workflow)
    data = request(pred, ref)
    data['comparisons'][0]['pressure_reference'] = 'unknown'
    report = compare(workflow, data)
    assert report['metrics']['flow_lph']['paired_sample_count'] == 4
    assert report['metrics']['pressure_kpa']['paired_sample_count'] == 0
    assert report['comparisons'][0]['pairs'][0]['errors']['pressure_kpa']['status'] == 'unknown_pressure_reference'

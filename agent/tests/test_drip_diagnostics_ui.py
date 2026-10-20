"""Native diagnostic forms, missing evidence and stale-scope protection."""

import pytest
from streamlit.testing.v1 import AppTest

from agent.drip_diagnostics_ui import P, parse_mappings
from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_drip_diagnostics import event_inputs
from agronaut_agent.tests.test_drip_lateral_workflow import inputs


def prepare(tmp_path):
    brain, _, _ = setup(tmp_path)
    pred = brain.field_workflow('web', 'operator', 'save', dict(task='drip_lateral', inputs=inputs('drip_lateral_curve.json'), request_id='pred'))
    event = brain.field_workflow('web', 'operator', 'save', dict(task='drip_emitter_test', inputs=event_inputs(), request_id='event'))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key='field_plan').set_value(pred['id']).run(timeout=15)
    at.multiselect(key=P+'tests').set_value([event['id']]).run(timeout=15)
    return brain, pred, event, at


def fill(at, ref, independence='held_out'):
    prefix = P+ref['id']+'_'
    at.selectbox(key=P+'kind').set_value('synthetic')
    at.text_area(key=P+'note').set_value('Synthetic comparison selection')
    for key, value in [('lateral', 'L1'), ('representation_note', 'Synthetic same layout and equipment'),
                       ('conditions_note', 'Synthetic same operating conditions'), ('density_source', 'Density unavailable')]:
        at.text_input(key=prefix+key).set_value(value)
    at.text_area(key=prefix+'mappings').set_value('sample_id,station_id,mapping_note\n'+
        '\n'.join(f'emitter-{i},E{i},Synthetic exact station mapping' for i in range(1,5)))
    at.selectbox(key=prefix+'pressure_reference').set_value('gauge_at_emitter')
    at.selectbox(key=prefix+'independence').set_value(independence)
    at.button(key=P+'run').click().run(timeout=15)


def test_form_keeps_unknown_density_and_records_unchanged(tmp_path):
    brain, pred, ref, at = prepare(tmp_path)
    before = brain.field_workflow('web', 'operator', 'list', {})
    assert at.number_input(key=P+ref['id']+'_water_density_kg_m3').value is None
    assert at.selectbox(key=P+ref['id']+'_independence').value is None
    fill(at, ref)
    assert not at.exception and not at.error
    report = at.session_state[P+'report']['result']
    assert report['calculation'] == pred
    assert report['metrics']['flow_lph']['paired_sample_count'] == 4
    assert report['metrics']['pressure_kpa']['rmse'] is None
    assert any('pressure_kpa: no eligible pairs' in block.value for block in at.text)
    assert brain.field_workflow('web', 'operator', 'list', {}) == before
    at.number_input(key=P+ref['id']+'_water_density_kg_m3').set_value(1000.0)
    at.text_input(key=P+ref['id']+'_density_source').set_value('Synthetic declared density')
    at.button(key=P+'run').click().run(timeout=15)
    assert at.session_state[P+'report']['result']['metrics']['pressure_kpa']['paired_sample_count'] == 4


def test_unscored_calibration_and_stale_reports_are_visible(tmp_path):
    _, _, ref, at = prepare(tmp_path)
    fill(at, ref, 'used_for_calibration')
    assert not at.exception and not at.error
    report = at.session_state[P+'report']['result']
    assert report['included_test_count'] == 0
    assert report['comparisons'][0]['metrics']['flow_lph']['count'] == 4
    at.text_area(key=P+'note').set_value('Changed rationale').run(timeout=15)
    assert not any('Drip lateral comparison — DIAGNOSTIC' in block.value for block in at.text)
    assert any('Run the comparison again' in block.value for block in at.caption)
    at.selectbox(key='field_plan').set_value(ref['id']).run(timeout=15)
    assert P+'report' not in at.session_state


def test_incomplete_mapping_removes_old_report_and_explains_failure(tmp_path):
    _, _, ref, at = prepare(tmp_path)
    fill(at, ref)
    at.text_area(key=P+ref['id']+'_mappings').set_value('sample_id,station_id,mapping_note\nemitter-1,E1,Only one')
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception and at.error
    assert P+'report' not in at.session_state


def test_stale_callback_never_reaches_backend():
    script = '''
import streamlit as st
from agent.drip_diagnostics_ui import _compare
st.session_state.field_plan = 'new'
st.session_state.field_cycle = None
def must_not_call(*args):
    raise AssertionError('stale callback reached backend')
_compare(must_not_call, 'old', None, [])
'''
    at = AppTest.from_string(script).run(timeout=15)
    assert not at.exception and 'changed' in at.session_state[P+'error']


@pytest.mark.parametrize('value', ['', 'sample_id,station_id', 'sample_id,station_id,mapping_note\na,b,c,extra'])
def test_csv_rejects_shifted_or_incomplete_columns(value):
    with pytest.raises(ValueError):
        parse_mappings(value)


def test_blank_station_is_explicit_unmatched_not_guessed():
    assert parse_mappings('sample_id,station_id,mapping_note\na,,Outside survey') == [
        dict(sample_id='a', station_id=None, mapping_note='Outside survey')]

"""Network diagnostic intake, complete mappings and stale-report protection."""

import pytest
from streamlit.testing.v1 import AppTest

from agent.drip_diagnostics_ui import P, parse_mappings
from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_drip_network_diagnostics import event_inputs, inputs


def prepare(tmp_path, task='drip_manifold'):
    brain, _, _ = setup(tmp_path)
    pred = brain.field_workflow('web','operator','save',dict(task=task,inputs=inputs(task),request_id='network'))
    ref = brain.field_workflow('web','operator','save',dict(task='drip_emitter_test',inputs=event_inputs(),request_id='test'))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key='field_plan').set_value(pred['id']).run(timeout=15)
    at.multiselect(key=P+'tests').set_value([ref['id']]).run(timeout=15)
    return brain, pred, ref, at


def fill(at, ref):
    prefix = P+ref['id']+'_'
    at.selectbox(key=P+'kind').set_value('synthetic')
    at.text_area(key=P+'note').set_value('Synthetic selection of complete test')
    for key, value in [('representation_note','Synthetic same physical emitters and all laterals'),
                       ('conditions_note','Synthetic same full active branch set and source curve settings'),
                       ('density_source','Density unknown')]:
        at.text_input(key=prefix+key).set_value(value)
    at.text_area(key=prefix+'mappings').set_value('sample_id,lateral_id,station_id,mapping_note\n'+
        '\n'.join(f'emitter-{i},L{1 if i<=4 else 2},E{(i-1)%4+1},Synthetic physical mapping' for i in range(1,9)))
    at.selectbox(key=prefix+'pressure_reference').set_value('gauge_at_emitter')
    at.selectbox(key=prefix+'independence').set_value('held_out')


@pytest.mark.parametrize('task',['drip_zone','drip_manifold'])
def test_form_compares_all_branches_without_writing_or_inventing_density(tmp_path,task):
    brain,pred,ref,at=prepare(tmp_path,task)
    before=brain.field_workflow('web','operator','list',{})
    assert not any(item.key==P+ref['id']+'_lateral' for item in at.text_input)
    fill(at,ref)
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception and not at.error
    report=at.session_state[P+'report']['result']
    assert report['calculation']==pred
    assert report['metrics']['flow_lph']['paired_sample_count']==8
    assert report['metrics']['pressure_kpa']['bias'] is None
    assert report['lateral_metrics']['L2']['flow_lph']['paired_sample_count']==4
    assert any('Drip network comparison' in block.value for block in at.text)
    assert brain.field_workflow('web','operator','list',{})==before
    at.number_input(key=P+ref['id']+'_water_density_kg_m3').set_value(1000.0)
    at.text_input(key=P+ref['id']+'_density_source').set_value('Synthetic supplied density')
    at.button(key=P+'run').click().run(timeout=15)
    assert at.session_state[P+'report']['result']['metrics']['pressure_kpa']['paired_sample_count']==8


def test_mapping_edit_hides_stale_report_and_incomplete_remap_removes_it(tmp_path):
    _,_,ref,at=prepare(tmp_path)
    fill(at,ref)
    at.button(key=P+'run').click().run(timeout=15)
    at.text_area(key=P+ref['id']+'_mappings').set_value('sample_id,lateral_id,station_id,mapping_note\nemitter-1,L1,E1,Only one').run(timeout=15)
    assert not any('Drip network comparison' in block.value for block in at.text)
    assert any('Run the comparison again' in block.value for block in at.caption)
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception and at.error
    assert P+'report' not in at.session_state


def test_network_csv_keeps_unmatched_both_null_and_rejects_legacy_header():
    assert parse_mappings('sample_id,lateral_id,station_id,mapping_note\nx,,,Outside modeled set',network=True)==[
        dict(sample_id='x',lateral_id=None,station_id=None,mapping_note='Outside modeled set')]
    with pytest.raises(ValueError,match='header'):
        parse_mappings('sample_id,station_id,mapping_note\nx,E1,Missing branch',network=True)

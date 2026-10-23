"""Native zone assembly uses explicit source evidence and reviewed geometry."""

from copy import deepcopy

from streamlit.testing.v1 import AppTest

from agent.drip_zone_ui import JUNCTION, P
from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_drip_lateral_workflow import inputs


def prepare(tmp_path):
    brain, _, _ = setup(tmp_path)
    saved = []
    for index in range(2):
        data = inputs('drip_lateral_curve.json')
        data['emitter']['reference_flow_lph'] *= 1-.2*index
        saved.append(brain.field_workflow('web', 'operator', 'save',
            dict(task='drip_lateral', inputs=data, request_id=f'branch-{index}')))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.multiselect(key=P+'selected').set_value([s['id'] for s in saved]).run(timeout=15)
    return brain, saved, at


def fill(at, saved, reference=True):
    at.text_area(key=P+'supply_curve').set_value('flow_lph,pressure_head_m\n0,14\n30,6')
    at.number_input(key=P+'supply_static').set_value(20.0)
    at.number_input(key=P+'supply_flow').set_value(25.0)
    at.text_input(key=P+'supply_configuration').set_value('Synthetic fixed source and both branches active')
    at.text_input(key=P+'supply_source').set_value('Synthetic TOTAL zone flow versus common junction pressure')
    at.checkbox(key=P+'supply_reference').set_value(reference)
    for key in JUNCTION:
        at.checkbox(key=P+'junction_'+key).set_value(True)
    at.text_input(key=P+'junction_source').set_value('Synthetic common-junction geometry')
    for index, row in enumerate(saved):
        key = row['id']
        at.text_input(key=P+key+'_identifier').set_value(f'L{index+1}')
        at.number_input(key=P+key+'_capacity').set_value(15.0)
        at.text_input(key=P+key+'_evidence').set_value('Synthetic reviewed geometry and branch capacity')
        at.checkbox(key=P+'review_'+key).set_value(True)


def test_form_requires_reference_then_saves_a_joint_scenario_without_water_records(tmp_path):
    brain, saved, at = prepare(tmp_path)
    originals = deepcopy(saved)
    assert at.number_input(key=P+'supply_flow').value is None
    assert not at.checkbox(key=P+'junction_common_hydraulic_junction').value
    fill(at, saved, reference=False)
    at.button(key=P+'save').click().run(timeout=15)
    assert not at.exception and at.error
    at.checkbox(key=P+'supply_reference').set_value(True)
    at.button(key=P+'save').click().run(timeout=15)
    assert not at.exception and not at.error
    zone = brain.field_workflow('web', 'operator', 'get', dict(calculation_id=at.selectbox(key='field_plan').value))
    assert zone['task'] == 'drip_zone' and zone['result']['feasible'] is True
    assert len(zone['result']['result']['prediction']['laterals']) == 2
    assert all(branch['stations'] == original['inputs']['stations'] for branch, original in zip(zone['inputs']['laterals'], originals))
    assert any('Common junction pressure:' in block.value for block in at.text)
    assert brain.field_workflow('web', 'operator', 'list', {})['water_days'] == []
    for original in originals:
        assert brain.field_workflow('web', 'operator', 'get', dict(calculation_id=original['id'])) == original


def test_changed_active_branch_selection_clears_source_and_approvals(tmp_path):
    _, saved, at = prepare(tmp_path)
    fill(at, saved)
    at.run(timeout=15)
    at.multiselect(key=P+'selected').set_value([saved[0]['id']]).run(timeout=15)
    assert not at.exception
    assert at.text_area(key=P+'supply_curve').value == ''
    assert at.number_input(key=P+'supply_flow').value is None
    assert not at.checkbox(key=P+'supply_reference').value
    assert not at.checkbox(key=P+'review_'+saved[0]['id']).value


def test_failed_source_capacity_keeps_solved_scenario_visible(tmp_path):
    brain, saved, at = prepare(tmp_path)
    fill(at, saved)
    at.number_input(key=P+'supply_flow').set_value(1.0)
    at.button(key=P+'save').click().run(timeout=15)
    assert not at.exception and not at.error
    zone = brain.field_workflow('web', 'operator', 'get', dict(calculation_id=at.selectbox(key='field_plan').value))
    assert zone['result']['feasible'] is False
    assert zone['result']['result']['prediction'] is not None
    assert any('source_flow_lph' in block.value for block in at.text)

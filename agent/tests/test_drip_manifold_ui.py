"""Manifold intake preserves geometry, boundaries and reviewed active sets."""

from agent.drip_zone_ui import MANIFOLD, P
from agent.tests.test_drip_zone_ui import prepare


def fill_manifold(at, saved):
    at.radio(key=P+'topology').set_value('Surveyed manifold').run(timeout=15)
    at.text_area(key=P+'supply_curve').set_value('flow_lph,pressure_head_m\n0,14\n30,6')
    at.number_input(key=P+'supply_static').set_value(20.0)
    at.number_input(key=P+'supply_flow').set_value(25.0)
    at.text_input(key=P+'supply_configuration').set_value('Synthetic whole manifold active')
    at.text_input(key=P+'supply_source').set_value('Synthetic manifold INLET pressure versus TOTAL flow')
    at.checkbox(key=P+'supply_reference').set_value(True)
    for key in MANIFOLD:
        at.checkbox(key=P+'junction_'+key).set_value(True)
    at.text_input(key=P+'junction_source').set_value('Synthetic closed-tail manifold')
    for key, value in [('internal_diameter_mm', 10.0), ('roughness_mm', .01), ('max_head_m', 40.0),
                       ('viscosity', 1e-6), ('velocity', 1.0)]:
        at.number_input(key=P+'manifold_'+key).set_value(value)
    at.text_input(key=P+'manifold_pipe_source').set_value('Synthetic pipe')
    at.text_input(key=P+'manifold_limit_source').set_value('Synthetic fluid and designer limit')
    for index, saved_row in enumerate(saved):
        key = saved_row['id']
        at.text_input(key=P+key+'_identifier').set_value(f'L{index+1}')
        at.number_input(key=P+key+'_capacity').set_value(15.0)
        at.text_input(key=P+key+'_evidence').set_value('Synthetic reviewed branch')
        at.checkbox(key=P+'review_'+key).set_value(True)
        prefix = P+'takeoff_'+key+'_'
        at.number_input(key=prefix+'distance').set_value(10.0+20*index)
        at.number_input(key=prefix+'elevation').set_value(.1-.3*index)
        at.number_input(key=prefix+'k').set_value(.5)
        at.text_input(key=prefix+'source').set_value('Synthetic takeoff survey and fittings')


def test_native_manifold_requires_complete_survey_then_saves_owned_scenario(tmp_path):
    brain, saved, at = prepare(tmp_path)
    fill_manifold(at, saved)
    at.number_input(key=P+'takeoff_'+saved[1]['id']+'_distance').set_value(None)
    at.button(key=P+'save').click().run(timeout=15)
    assert not at.exception and at.error
    at.number_input(key=P+'takeoff_'+saved[1]['id']+'_distance').set_value(30.0)
    at.button(key=P+'save').click().run(timeout=15)
    assert not at.exception and not at.error
    result = brain.field_workflow('web', 'operator', 'get', dict(calculation_id=at.selectbox(key='field_plan').value))
    assert result['task'] == 'drip_manifold' and result['result']['feasible'] is True
    assert result['inputs']['supply']['head_reference'] == 'manifold_inlet_pressure'
    assert len(result['result']['result']['prediction']['nodes']) == 2
    assert result['inputs']['takeoffs'][0]['lateral']['stations'] == saved[0]['inputs']['stations']
    assert any('Manifold inlet pressure:' in block.value for block in at.text)
    assert brain.field_workflow('web', 'operator', 'list', {})['water_days'] == []


def test_topology_switch_clears_source_geometry_and_applicability(tmp_path):
    _, saved, at = prepare(tmp_path)
    fill_manifold(at, saved)
    at.run(timeout=15)
    at.radio(key=P+'topology').set_value('Common junction').run(timeout=15)
    assert not at.exception
    assert at.text_area(key=P+'supply_curve').value == ''
    assert not at.checkbox(key=P+'supply_reference').value
    at.radio(key=P+'topology').set_value('Surveyed manifold').run(timeout=15)
    assert not at.exception
    assert at.number_input(key=P+'manifold_internal_diameter_mm').value is None
    assert at.number_input(key=P+'takeoff_'+saved[0]['id']+'_distance').value is None
    assert not at.checkbox(key=P+'junction_closed_tail').value
    assert not at.checkbox(key=P+'review_'+saved[0]['id']).value

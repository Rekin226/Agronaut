"""Rendered comparisons retain the quality review the operator actually saw."""

from streamlit.testing.v1 import AppTest

from agent.sprinkler_diagnostics_ui import P
from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_sprinkler_lateral_workflow import inputs


def prepare(tmp_path):
    brain,unit,_=setup(tmp_path)
    saved=brain.field_workflow('web','operator','save',dict(task='sprinkler_lateral',inputs=inputs(),request_id='sprinklers'))
    observation=brain.crop_records('web','operator','record_measurement',dict(request_id='flow',data=dict(quantity='flow',value=1000,unit='L/h',
        observed_at='2026-07-06T10:15:00Z',provenance='measured',source='Synthetic nozzle meter')))
    review=brain.crop_records('web','operator','review_measurement',dict(observation_id=observation['id'],quality='checked',
        reason='Synthetic check',source='Fixture',request_id='check',expected_revision=None))
    at=AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key='field_plan').set_value(saved['id']).run(timeout=15)
    at.multiselect(key=P+'0_readings').set_value([observation['id']]).run(timeout=15)
    return brain,unit,saved,observation,review,at


def fill(at,observation):
    at.selectbox(key=P+'kind').set_value('synthetic')
    at.text_area(key=P+'note').set_value('Synthetic complete sample')
    prefix=P+'0_'
    for key,value in dict(event_id='visit',started_at='2026-07-06T10:00:00Z',ended_at='2026-07-06T11:00:00Z',
        conditions_note='Synthetic matching conditions',representation_note='Synthetic matching geometry',density_source='Unknown; flow-only comparison').items():
        at.text_input(key=prefix+key).set_value(value)
    at.checkbox(key=prefix+'stable_operation').set_value(True)
    at.selectbox(key=prefix+'independence').set_value('held_out')
    p=prefix+observation['id']+'_'
    at.selectbox(key=p+'target').set_value('nozzle')
    at.selectbox(key=p+'station').set_value('S1')
    at.text_input(key=p+'note').set_value('Synthetic nozzle identity')


def test_native_comparison_is_read_only_and_shows_unknown_pressure_metrics(tmp_path):
    brain,_,saved,observation,_,at=prepare(tmp_path)
    assert not at.exception
    assert at.number_input(key=P+'0_water_density_kg_m3').value is None
    assert not at.checkbox(key=P+'0_stable_operation').value
    before=brain._farms.export(brain._conv.get_or_create_user('web','operator'))
    fill(at,observation)
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception and not at.error
    report=at.session_state[P+'report']['result']
    assert report['included_reading_count']==1
    assert report['metrics']['nozzle_pressure_kpa']['bias'] is None
    assert any('Sprinkler lateral comparison' in item.value for item in at.text)
    assert before==brain._farms.export(brain._conv.get_or_create_user('web','operator'))
    assert brain.field_workflow('web','operator','get',dict(calculation_id=saved['id']))==saved


def test_review_changed_before_submission_is_not_silently_adopted(tmp_path):
    brain,_,_,observation,review,at=prepare(tmp_path)
    fill(at,observation)
    brain.crop_records('web','operator','review_measurement',dict(observation_id=observation['id'],quality='rejected',
        reason='Synthetic new evidence',source='Fixture',request_id='reject',expected_revision=review['id']))
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception
    assert any('measurement review changed' in item.value for item in at.error)
    assert not any('Sprinkler lateral comparison' in item.value for item in at.text)


def test_existing_report_is_hidden_when_quality_or_declarations_change(tmp_path):
    brain,_,_,observation,review,at=prepare(tmp_path)
    fill(at,observation)
    at.button(key=P+'run').click().run(timeout=15)
    assert any('Sprinkler lateral comparison' in item.value for item in at.text)
    brain.crop_records('web','operator','review_measurement',dict(observation_id=observation['id'],quality='rejected',
        reason='Synthetic new evidence',source='Fixture',request_id='reject',expected_revision=review['id']))
    at.run(timeout=15)
    assert not at.exception
    assert not any('Sprinkler lateral comparison' in item.value for item in at.text)
    assert any('measurement review changed' in item.value for item in at.caption)
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception and not at.error
    assert at.session_state[P+'report']['result']['included_reading_count']==0
    at.selectbox(key=P+'0_independence').set_value('unknown').run(timeout=15)
    assert not any('Sprinkler lateral comparison' in item.value for item in at.text)


def test_active_unit_change_cannot_apply_old_comparison(tmp_path):
    brain,_,saved,observation,_,at=prepare(tmp_path)
    fill(at,observation)
    brain.create_unit('web','operator',dict(farm=dict(name='Other',timezone='Asia/Taipei'),
        unit=dict(name='Other field',production_system='soil',medium='soil',environment='open_field',water_strategy='rainfed',irrigation_method=None)))
    at.button(key=P+'run').click().run(timeout=15)
    assert not at.exception
    # The old callback's expected-unit guard rejects the read; no old report leaks.
    assert not any('Sprinkler lateral comparison' in item.value for item in at.text)
    assert brain.field_workflow('web','operator','list',{})['calculations']==[]

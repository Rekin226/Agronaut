"""Station-reading intake preserves units, provenance, revisions and language."""
import json

import pytest

from agronaut_agent import weather_provider
from agronaut_agent.channels.commands import dispatch
from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.tests.test_field_consultation import ANSWERS
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_unit_agent import agent_and_units
from agronaut_agent.tests.test_unit_setup import NoModel


def supplied(**changes):
    values = dict(ANSWERS, weather_input='meteorology', radiation_basis='solar',
        elevation_m='100', wind_height_m='2', latitude='12.3',
        weather_source='Synthetic station fixture · essai · 測試',
        t_min_c_0='20', t_max_c_0='30', rh_min_pct_0='40', rh_max_pct_0='80',
        wind_m_s_0='2', solar_radiation_mj_m2_0='22', net_radiation_mj_m2_0='12')
    values.update(changes)
    return values


def advance(wizard, answers, target=None, language='en'):
    view = wizard.start('owner', 'field', language)
    while view['next_field'] != target:
        view = wizard.answer('owner', 'field', answers[view['next_field']], view['revision'])
    return view


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
@pytest.mark.parametrize('basis', ['net', 'solar'])
@pytest.mark.parametrize('task', ['1', '2'])
def test_station_readings_complete_preview_and_atomic_save(setup, monkeypatch, language, basis, task):
    def forbidden(*args, **kwargs):
        raise AssertionError('Supplied station readings must not fetch weather')
    monkeypatch.setattr(weather_provider, 'fetch_weather', forbidden)
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    answers = supplied(radiation_basis=basis, task=task)
    english = advance(wizard, answers)
    wizard.cancel('owner', 'field', english['revision'])
    view = advance(wizard, answers, language=language)
    assert view['inputs'] == english['inputs']
    assert view['preview'] == english['preview']
    weather = view['inputs']['weather'][0]
    assert weather['source'] == answers['weather_source']
    assert weather['rainfall_mm'] == (None if task == '1' else 0)
    assert 'et0_mm_day' not in weather
    demand = view['preview']['result']['days'][0]['demand']
    assert demand['values']['etc_mm_day'] > 0
    assert ('radiation_estimate' in demand['inputs']) == (basis == 'solar')
    if basis == 'solar':
        assert weather['input_type'] == 'solar_radiation_fao56'
        assert weather['solar_radiation_mj_m2'] == 22
        assert 'net_radiation_mj_m2' not in weather
    else:
        assert weather['net_radiation_mj_m2'] == 12
        assert 'solar_radiation_mj_m2' not in weather
    assert workflow.list('owner', 'field')['calculations'] == []
    saved = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert saved['calculation']['inputs'] == view['inputs']
    assert saved['calculation']['result'] == view['preview']
    assert wizard.save('owner', 'field', view['draft_id'], view['revision']) == saved
    assert workflow.list('owner', 'field')['water_days'] == []
    if language != 'en':
        assert saved['calculation']['intake_language'] == language


@pytest.mark.parametrize('field,value', [
    ('radiation_basis', 'automatic'), ('elevation_m', 'nan'), ('elevation_m', '9001'),
    ('wind_height_m', '0'), ('latitude', '91'), ('t_min_c_0', '-81'),
    ('t_max_c_0', '19'), ('rh_min_pct_0', '-1'), ('rh_max_pct_0', '39'),
    ('wind_m_s_0', 'inf'), ('solar_radiation_mj_m2_0', '61'),
    ('solar_radiation_mj_m2_0', '0'),  # invalid longwave correction must fail here
])
def test_invalid_station_answer_leaves_same_question_and_revision(setup, field, value):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, supplied(), field)
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', value, view['revision'])
    assert wizard.get('owner', 'field') == view
    finished = advance(wizard, supplied())
    assert finished['status'] == 'ready'


def test_forecast_issuance_and_multiple_local_dates_survive_save(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    answers = supplied(days='2', weather_kind='forecast', issued_at='2026-07-05T18:00:00+00:00')
    answers.update({key[:-1]+'1': value for key, value in list(answers.items()) if key.endswith('_0')})
    view = advance(wizard, answers, 'issued_at')
    with pytest.raises(ValueError, match='timezone|offset|aware'):
        wizard.answer('owner', 'field', '2026-07-05T18:00:00', view['revision'])
    view = advance(wizard, answers)
    rows = view['inputs']['weather']
    assert [row['day'] for row in rows] == ['2026-07-06', '2026-07-07']
    assert all(row['issued_at'] == answers['issued_at'] and row['kind'] == 'forecast' for row in rows)


def test_back_can_change_radiation_basis_without_reusing_solar_answers(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, supplied())
    while view['next_field'] != 'radiation_basis':
        view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert 'latitude' not in view['answers'] and 'solar_radiation_mj_m2_0' not in view['answers']
    view = advance(wizard, supplied(radiation_basis='net'))
    assert view['inputs']['weather'][0]['net_radiation_mj_m2'] == 12
    assert 'solar_radiation_mj_m2' not in view['inputs']['weather'][0]


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_existing_version_five_keeps_its_original_weather_choices(setup, language):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    wizard.start('owner', 'field', language)
    draft = json.loads(workflow.db.query('SELECT payload FROM field_intake_drafts')[0][0])
    draft['intake_version'] = 5
    workflow.db.execute('UPDATE field_intake_drafts SET payload=?', (json.dumps(draft),))
    view = advance(wizard, ANSWERS, 'weather_input', language)
    assert 'meteorology' not in view['question']
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', 'meteorology', view['revision'])
    assert wizard.get('owner', 'field') == view
    view = advance(wizard, ANSWERS, language=language)
    assert view['inputs']['weather'][0]['et0_mm_day'] == 4


@pytest.mark.parametrize('language,save', [('fr', 'enregistrer'), ('zh-Hant', '儲存')])
def test_core_routes_station_intake_without_model_and_saves_locally(tmp_path, language, save):
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit('cli', 'one', 'field')
    dispatch(brain, 'cli', 'one', f'/fieldplan {language}')
    view = brain._field_intake.get(owner, 'field')
    answers = supplied()
    while view['status'] != 'ready':
        reply = brain.handle_message('cli', 'one', answers[view['next_field']])
        view = brain._field_intake.get(owner, 'field')
        assert view['question'] in reply
        assert 'FIELD_TRANSLATION_UNAVAILABLE' not in render_intake(view)
    reply = brain.handle_message('cli', 'one', save)
    assert ('Calcul enregistré' if language == 'fr' else '已儲存計算') in reply
    assert brain.field_workflow('cli', 'one', 'list', {})['water_days'] == []

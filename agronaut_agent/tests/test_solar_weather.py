"""Explicit solar weather through parsing, CLI and saved calculations."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.water import SolarWeatherDay
from agronaut_agent.field_io import (
    evaluate_field_request,
    import_weather_csv,
    parse_weather_records,
    render_field_report,
)
from agronaut_agent.tests.test_field_workflow import (  # noqa: F401 -- shared scoped-store fixture
    setup,
    water,
)

EXAMPLE = Path(__file__).resolve().parents[2]/'examples/field_water_solar.json'


def payload():
    return json.loads(EXAMPLE.read_text())


@pytest.mark.parametrize('mode', ['water-needs', 'irrigation-plan'])
def test_offline_cli_calculates_and_labels_estimated_radiation(mode):
    proc = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', mode,
        '--input', str(EXAMPLE), '--json'], capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout)
    assert result == evaluate_field_request(payload(), mode)
    trace = result['days'][0]['demand']['inputs']['radiation_estimate']
    assert trace['values']['net_radiation_mj_m2'] != trace['inputs']['solar_radiation_mj_m2']
    assert 'estimated net radiation' in render_field_report(result)
    assert result['days'][0]['weather_kind'] == 'scenario'


def test_csv_preserves_solar_inputs_and_unknown_rain(tmp_path):
    row = payload()['weather'][0]
    row['rainfall_mm'] = None
    path = tmp_path/'weather.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    parsed = import_weather_csv(path)[0]
    assert isinstance(parsed, SolarWeatherDay)
    assert parsed.latitude_deg == 50.8
    assert parsed.rainfall_mm is None


@pytest.mark.parametrize('change', ['both', 'missing_latitude', 'implicit', 'invalid_tag'])
def test_solar_conversion_is_explicit_and_unambiguous(change):
    row = payload()['weather'][0]
    if change == 'both':
        row['net_radiation_mj_m2'] = 10
    elif change == 'missing_latitude':
        row.pop('latitude_deg')
    elif change == 'implicit':
        row.pop('input_type')
    else:
        row['input_type'] = []
    with pytest.raises(ValueError):
        parse_weather_records([row])


def test_owned_save_retry_and_failed_estimate_do_not_mutate_records(setup):  # noqa: F811
    workflow, _ = setup
    data = payload()
    data['site']['timezone'] = 'UTC'
    saved = workflow.save('owner', 'field', 'root_zone_budget', data, request_id='solar')
    assert saved['inputs']['weather'] == data['weather']
    trace = saved['result']['result']['days'][0]['demand']['inputs']['radiation_estimate']
    assert trace['model_version'].endswith('fao56-1998')
    assert workflow.save('owner', 'field', 'root_zone_budget', data, request_id='solar') == saved
    with pytest.raises(ValueError):
        workflow.get('other', 'field', saved['id'])
    data['weather'][0]['solar_radiation_mj_m2'] = 0
    with pytest.raises(ValueError, match='supply net radiation'):
        workflow.save('owner', 'field', 'root_zone_budget', data, request_id='invalid')
    assert len(workflow.list('owner', 'field')['calculations']) == 1
    assert workflow.list('owner', 'field')['water_days'] == []


def test_replay_preserves_solar_provenance_while_using_actual_rain(setup):  # noqa: F811
    workflow, _ = setup
    data = payload()
    data['site']['timezone'] = 'UTC'
    data['weather'][0]['kind'] = 'observed'  # Synthetic test fixture for an observed-record workflow.
    original = workflow.save('owner', 'field', 'root_zone_budget', data, request_id='original')
    actual = workflow.record_day('owner', 'field', water(), request_id='actual')
    replay = workflow.replay('owner', 'field', original['id'], [actual['id']], request_id='replay')
    assert replay['inputs']['weather'] == original['inputs']['weather']
    assert replay['inputs']['rainfall_overrides'][0]['rainfall_mm'] == 5
    assert replay['result']['result']['days'][0]['demand']['inputs']['radiation_estimate'] == original['result']['result']['days'][0]['demand']['inputs']['radiation_estimate']
    assert workflow.get('owner', 'field', original['id']) == original

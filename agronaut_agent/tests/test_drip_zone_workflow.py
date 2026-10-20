"""The zone contract is reproducible and remains owned scenario data."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.tests.test_drip_zone import inputs
from agronaut_agent.cli import main
from agronaut_agent.domains import get_capability, run_capability
from agronaut_agent.tests.test_field_workflow import setup  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def test_schema_registry_cli_and_stdlib_agree(capsys):
    import jsonschema

    data = inputs()
    cap = get_capability('soil', 'drip_zone').describe()
    jsonschema.validate(data, cap['input_schema'])
    registered = run_capability('soil', 'drip_zone', data)
    assert main(['evaluate-drip-zone', '--input', str(ROOT/'examples/drip_zone.json'), '--json']) == 0
    assert json.loads(capsys.readouterr().out) == registered['result']
    assert main(['evaluate-drip-zone', '--input', str(ROOT/'examples/drip_zone.json')]) == 0
    assert 'No irrigation schedule' in capsys.readouterr().out
    result = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', 'evaluate-drip-zone',
        '--input', str(ROOT/'examples/drip_zone.json'), '--json'], capture_output=True, text=True, cwd=ROOT, check=True, timeout=30)
    assert json.loads(result.stdout) == registered['result']
    with pytest.raises(ValueError, match='unsupported'):
        run_capability('aquaponics', 'drip_zone', data)


def test_saved_zone_keeps_both_branch_snapshots_and_does_not_record_water(setup):  # noqa: F811
    workflow, farms = setup
    data = inputs()
    saved = workflow.save('owner', 'field', 'drip_zone', data, cycle_id='cycle', request_id='zone')
    assert workflow.save('owner', 'field', 'drip_zone', data, cycle_id='cycle', request_id='zone') == saved
    data['laterals'][0]['stations'][0]['elevation_m'] = 4
    assert workflow.get('owner', 'field', saved['id'])['inputs']['laterals'][0]['stations'][0]['elevation_m'] == .2
    assert saved in farms.export('owner')['calculations']
    assert workflow.list('owner', 'field')['water_days'] == []
    assert saved['result']['result_schema'] == 'agronaut.drip_zone.v1'
    assert saved['result']['result']['schedule'] is None
    with pytest.raises(ValueError):
        workflow.get('other', 'field', saved['id'])
    with pytest.raises(ValueError):
        workflow.get('owner', 'field2', saved['id'])
    with pytest.raises(ValueError, match='cycle'):
        workflow.save('owner', 'field', 'drip_zone', inputs(), cycle_id='cycle2', request_id='wrong')
    data['site']['timezone'] = 'Asia/Taipei'
    with pytest.raises(ValueError, match='timezone'):
        workflow.save('owner', 'field', 'drip_zone', data, request_id='wrong-timezone')


def test_shared_tool_returns_all_branches_and_preserves_domain_guard():
    import contextvars
    from types import SimpleNamespace

    from agronaut_agent import runtime
    from agronaut_agent.tools import calculate_agriculture

    def invoke():
        runtime._unit.set(SimpleNamespace(production_system='soil'))
        result = json.loads(calculate_agriculture.invoke(dict(production_system='soil', task='drip_zone', inputs=inputs())))
        assert len(result['result']['prediction']['laterals']) == 2
        runtime._unit.set(SimpleNamespace(production_system='aquaponics'))
        denied = json.loads(calculate_agriculture.invoke(dict(production_system='soil', task='drip_zone', inputs=inputs())))
        assert denied['status'] == 'VALIDATION_FAILED'
    contextvars.Context().run(invoke)

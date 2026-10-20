"""Soil diagnostic chronology retains original measurement and adjusted day boundary."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from agro_model.farm import Observation
from agronaut_agent import field_workflow
from agronaut_agent.coupled_soil_evaluation import render_coupled_soil_comparison
from agronaut_agent.soil_evaluation import render_soil_comparison
from agronaut_agent.tests import test_coupled_soil_evaluation as coupled
from agronaut_agent.tests import test_soil_evaluation as root
from agronaut_agent.tests.test_field_workflow import setup  # noqa: F401


@pytest.mark.parametrize('task', ['root_zone_budget', 'coupled_soil_water'])
@pytest.mark.parametrize('tz', ['Africa/Ouagadougou', 'Asia/Taipei'])
def test_observation_and_midnight_ordering_without_changing_metrics(setup, monkeypatch, task, tz):  # noqa: F811
    workflow, store = setup
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (tz,))
    observed = datetime(2026, 7, 6, 18, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    closing = datetime(2026, 7, 7, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    store.record_observation('owner', Observation('soil', 'owner', 'field', 'cycle',
        'soil_water_content', .25, 'm3/m3', observed, observed, 'measured', 'Synthetic represented soil sample'))
    data = root.inputs() if task == 'root_zone_budget' else coupled.example()
    data['site']['timezone'] = tz
    if task == 'coupled_soil_water':
        for day in data['weather']:
            day['timezone'] = tz
    compare = root.compare if task == 'root_zone_budget' else coupled.compare
    render = render_soil_comparison if task == 'root_zone_budget' else render_coupled_soil_comparison
    reports = []
    for i, stamp in enumerate((observed-timedelta(hours=1), observed+timedelta(hours=1), closing)):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return stamp
        with monkeypatch.context() as patch:
            patch.setattr(field_workflow, 'datetime', Clock)
            saved = workflow.save('owner', 'field', task, data, cycle_id='cycle', request_id=f'prediction-{i}')
        before = store.export('owner')
        report = compare(workflow, saved)
        assert store.export('owner') == before
        reports.append(report)
        assert report['schema_version'] == 2
        timing = report['comparisons'][0]['observation_timing']
        assert timing['calculation_saved_at_utc'] == saved['created_at']
        assert timing['observed_at_utc'] == observed.isoformat()
        assert timing['reference_at_utc'] == closing.isoformat()
        assert timing['relative_to_observation'] == ('before' if i == 0 else 'at_or_after')
        assert timing['relative_to_reference_boundary'] == ('before' if i < 2 else 'at_or_after')
        assert timing['prospective_validation_status'] == 'not_established'
        assert report['independence_status'] == 'not_established'
        for key in ('relative_to_observation', 'relative_to_reference_boundary'):
            assert report['observation_timing_counts'][key][timing[key]] == 1
        assert 'Earlier saving alone does not establish prospective validation' in render(report)
    assert reports[0]['metrics'] == reports[1]['metrics'] == reports[2]['metrics']
    assert 'measurement: before 0, at/after 1' in render(reports[1])
    assert 'reference boundary: before 1, at/after 0' in render(reports[1])
    legacy = dict(reports[0])
    legacy.pop('observation_timing_counts')
    assert 'not recorded in this older report' in render(legacy)

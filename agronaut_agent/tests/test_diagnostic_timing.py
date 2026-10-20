"""Real saved predictions expose chronology without changing residual selection."""

from datetime import datetime, timezone

import pytest

from agronaut_agent import field_workflow
from agronaut_agent.drip_diagnostics import render_drip_comparison
from agronaut_agent.sprinkler_diagnostics import render_sprinkler_comparison
from agronaut_agent.surface_diagnostics import render_surface_advance_comparison
from agronaut_agent.tests import (
    test_drip_diagnostics as drip,
)
from agronaut_agent.tests import (
    test_drip_network_diagnostics as network,
)
from agronaut_agent.tests import (
    test_sprinkler_diagnostics as sprinkler,
)
from agronaut_agent.tests import (
    test_surface_diagnostics as surface,
)
from agronaut_agent.tests.test_field_workflow import setup  # noqa: F401


@pytest.mark.parametrize('task', ['drip_lateral', 'drip_zone', 'drip_manifold', 'sprinkler_lateral', 'surface_advance'])
def test_saved_before_and_after_events_stay_distinct_with_identical_metrics(setup, monkeypatch, task):  # noqa: F811
    workflow, farms = setup
    if task == 'drip_lateral':
        inputs = drip.inputs()
    elif task.startswith('drip_'):
        inputs = network.inputs(task)
    elif task == 'sprinkler_lateral':
        inputs = sprinkler.inputs()
    else:
        inputs = surface.inputs()
    predictions = []
    for day in (1, 10):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, day, tzinfo=timezone.utc)
        with monkeypatch.context() as patch:
            patch.setattr(field_workflow, 'datetime', Clock)
            predictions.append(workflow.save('owner', 'field', task, inputs, cycle_id='cycle', request_id=f'prediction-{day}'))
    if task.startswith('drip_'):
        reference = workflow.save('owner', 'field', 'drip_emitter_test', drip.event_inputs(), cycle_id='cycle', request_id='event')
        helper = drip if task == 'drip_lateral' else network
        def compare(pred):
            return helper.compare(workflow, helper.request(pred, reference))
        render = render_drip_comparison
    elif task == 'sprinkler_lateral':
        event = sprinkler.event(sprinkler.reading(workflow, 'sample'))
        def compare(pred):
            return sprinkler.compare(workflow, pred, event)
        render = render_sprinkler_comparison
    else:
        reference = workflow.save('owner', 'field', 'surface_evaluation', surface.event_inputs(), cycle_id='cycle', request_id='event')
        def compare(pred):
            return surface.compare(workflow, surface.request(pred, reference))
        render = render_surface_advance_comparison
    before = farms.export('owner')
    reports = [compare(prediction) for prediction in predictions]
    assert farms.export('owner') == before
    metric_key = 'metrics_by_anchor' if task == 'surface_advance' else 'metrics'
    assert reports[0][metric_key] == reports[1][metric_key]
    for report, status in zip(reports, ('saved_before_event', 'saved_at_or_after_event_end')):
        assert report['schema_version'] == 2
        assert report['comparisons'][0]['prediction_timing']['status'] == status
        assert report['comparisons'][0]['prediction_timing']['calculation_saved_at_utc'] == report['calculation']['created_at']
        assert report['timing_counts'][status] == 1
        assert sum(report['timing_counts'].values()) == 1
        assert report['independence_status'] == 'not_established'
        assert report['acceptance_status'] == 'not_assessed'
        assert 'Earlier saving alone does not establish prospective validation' in render(report)
        assert ('before start 1' if status == 'saved_before_event' else 'at/after end 1') in render(report)
        legacy = dict(report)
        legacy.pop('timing_counts')
        assert 'not recorded in this older report' in render(legacy)

"""Summary evidence must include state reconciliation and root-storage assumptions."""

from dataclasses import asdict
from datetime import date, datetime, timezone

from agro_model.tests.test_soil_state import update
from agronaut_agent.domains import run_capability
from agronaut_agent.field_io import json_ready
from agronaut_agent.tests.test_mixed_water_replay import historical_inputs
from agronaut_agent.tests.test_root_growth_workflow import growing


def test_adapter_carries_initial_and_daily_soil_and_growth_evidence():
    data = growing()
    initial = update(as_of=date(2026, 7, 5), observed_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
                     source="Unique initial soil evidence", representation_note="Unique initial sampling coverage")
    daily = update(source="Unique daily soil evidence", adjustment_source="Unique timing correction evidence")
    data["soil_state_updates"] = [json_ready(asdict(initial)), json_ready(asdict(daily))]
    result = run_capability("soil", "root_zone_budget", data)
    for marker in (initial.source, initial.representation_note, daily.source, daily.adjustment_source,
                   data["root_zone_schedule"][1]["added_layer_source"], data["events"][0]["source"]):
        assert marker in result["sources"]
    results = [result["result"]["initial_soil_state_update"], result["result"]["days"][0]["soil_state_update"],
               result["result"]["days"][1]["root_growth"]]
    for part in results:
        for key in ("sources", "assumptions", "limitations", "missing_inputs"):
            assert set(part[key]) <= set(result[key])


def test_provider_time_aggregation_assumptions_reach_summary():
    data = historical_inputs()
    result = run_capability("soil", "root_zone_budget", data)
    assert data["weather_sources"][0]["assumptions"]
    assert set(data["weather_sources"][0]["assumptions"]) <= set(result["assumptions"])

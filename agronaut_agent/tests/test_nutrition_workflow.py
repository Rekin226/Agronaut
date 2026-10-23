"""Nutrient accounting stays a scoped saved calculation, separate from completed work."""

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_nutrition_cli import inputs


def test_saved_nutrient_budget_retains_provenance_without_recording_applications(setup):
    workflow, farms = setup
    payload = inputs()
    payload["period"]["start"] = "2026-06-20"  # Preplant planning can belong to the upcoming cycle.
    saved = workflow.save("owner", "field", "nutrient_budget", payload, cycle_id="cycle", request_id="nutrients")
    assert workflow.get("owner", "field", saved["id"]) == saved
    assert workflow.save("owner", "field", "nutrient_budget", payload, cycle_id="cycle", request_id="nutrients") == saved
    assert saved["result"]["result"]["nutrients"]["n"]["excess_kg"] == 5
    assert saved["result"]["feasible"] is None
    assert payload["target"]["source"] in saved["result"]["sources"]
    assert workflow.list("owner", "field")["water_days"] == []
    assert farms.observations("owner", "field") == []
    assert farms.export("owner")["crop_records"] == []
    review = CropRecords(workflow.db).season_review("owner", "field", "cycle")
    assert review["calculations"][0]["task"] == "nutrient_budget"
    assert review["journal"]["harvest_totals"] == []
    with pytest.raises(ValueError, match="root_zone_budget"):
        workflow.replay("owner", "field", saved["id"], ["not-water"], request_id="replay")
    with pytest.raises(ValueError):
        workflow.revise_weather("owner", "field", saved["id"], [], request_id="weather")
    with pytest.raises(ValueError, match="different data"):
        workflow.save("owner", "field", "nutrient_budget", dict(payload, area_m2=10000), cycle_id="cycle", request_id="nutrients")


@pytest.mark.parametrize("change", ["crop", "timezone", "owner", "cycle"])
def test_nutrient_budget_scope_and_crop_consistency(setup, change):
    workflow, _ = setup
    data = inputs()
    owner, cycle = "owner", "cycle"
    if change == "crop":
        data["crop"]["name"] = "onion"
    elif change == "timezone":
        data["site"]["timezone"] = "Europe/Brussels"
    elif change == "owner":
        owner = "other"
    else:
        cycle = "cycle2"
    with pytest.raises(ValueError):
        workflow.save(owner, "field", "nutrient_budget", data, cycle_id=cycle, request_id="bad")
    assert workflow.list("owner", "field")["calculations"] == []

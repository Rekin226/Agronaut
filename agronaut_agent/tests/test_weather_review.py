"""Clock-aware weather review is scoped, explicit and never changes saved inputs."""

import copy
import json
from pathlib import Path

import pytest

from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_field_workflow import inputs, setup  # noqa: F401
from agronaut_agent.tests.test_weather_provider import bundle
from agronaut_agent.weather_review import WeatherReview


def save(workflow, *, kind="forecast", issued="2026-07-05T12:00:00Z", provider=False, zone="UTC"):
    data = inputs()
    data["site"]["timezone"] = zone
    if provider:
        data.update({k: v for k, v in bundle().items() if k in ("weather", "weather_sources")})
    else:
        data["weather"][0].update(kind=kind, issued_at=issued)
    return workflow.save("owner", "field", "root_zone_budget", data, request_id="weather")


@pytest.mark.parametrize("stamp,age,within,flags", [
    ("2026-07-06T12:00:00Z", 24, True, []),
    ("2026-07-06T12:00:01Z", 24+1/3600, False, ["age_limit_exceeded"]),
    ("2026-07-05T11:00:00Z", -1, False, ["issued_at_after_review_time"]),
    ("2026-07-07T00:00:00Z", 36, False, ["age_limit_exceeded", "forecast_day_has_elapsed"]),
])
def test_forecast_boundary_age_and_elapsed_day_are_independent(setup, stamp, age, within, flags):  # noqa: F811
    workflow, farms = setup
    plan = save(workflow)
    before = farms.export("owner")
    result = execute_field_workflow(workflow.db, "owner", "field", "review_weather",
        dict(calculation_id=plan["id"], max_age_hours=24, as_of=stamp))
    row = result["rows"][0]
    assert row["age_hours"] == pytest.approx(age)
    assert row["within_age_limit"] is within and row["flags"] == flags
    assert row["age_basis"] == "issued_at"
    assert result["status"] == ("needs_review" if flags else "reviewed")
    assert farms.export("owner") == before
    assert workflow.get("owner", "field", plan["id"]) == plan


def test_provider_download_age_never_claims_known_model_issue_age(setup):  # noqa: F811
    workflow, _ = setup
    plan = save(workflow, provider=True)
    review = WeatherReview(workflow.db)
    row = review.review("owner", "field", plan["id"], 24, "2026-07-06T12:00:00Z")["rows"][0]
    assert row["within_age_limit"] is True
    assert row["age_basis"] == "retrieved_at" and row["issued_at"] is None
    assert row["flags"] == ["model_issuance_unknown"] and row["status"] == "needs_review"
    assert row["source_snapshot_id"] == plan["inputs"]["weather_sources"][0]["id"]
    early = review.review("owner", "field", plan["id"], 24, "2026-07-05T10:00:00Z")
    assert "retrieved_at_after_review_time" in early["rows"][0]["flags"]


@pytest.mark.parametrize("kind", ["observed", "historical", "scenario"])
def test_nonforecasts_do_not_expire_or_change_kind(setup, kind):  # noqa: F811
    workflow, _ = setup
    plan = save(workflow, kind=kind, issued=None)
    row = WeatherReview(workflow.db).review("owner", "field", plan["id"], 1, "2026-10-20T12:00:00Z")["rows"][0]
    assert row["kind"] == kind and row["status"] == "not_a_forecast"
    assert row["age_hours"] is row["within_age_limit"] is row["age_basis"] is None
    assert row["flags"] == []


@pytest.mark.parametrize("kind,flags", [("observed", ["nonforecast_day_after_review_date"]),
    ("historical", ["nonforecast_day_after_review_date"]), ("scenario", [])])
def test_future_nonforecast_dates_are_explicit(setup, kind, flags):  # noqa: F811
    workflow, _ = setup
    plan = save(workflow, kind=kind, issued=None)
    row = WeatherReview(workflow.db).review("owner", "field", plan["id"], 24, "2026-07-05T12:00:00Z")["rows"][0]
    assert row["flags"] == flags


@pytest.mark.parametrize("zone,elapsed", [("Africa/Ouagadougou", False), ("Asia/Taipei", True)])
def test_forecast_day_uses_saved_site_timezone_even_after_farm_changes(setup, zone, elapsed):  # noqa: F811
    workflow, _ = setup
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (zone,))
    plan = save(workflow, zone=zone)
    workflow.db.execute("UPDATE farms SET timezone='Pacific/Honolulu' WHERE id='farm'")
    result = WeatherReview(workflow.db).review("owner", "field", plan["id"], 100, "2026-07-06T17:00:00Z")
    assert ("forecast_day_has_elapsed" in result["rows"][0]["flags"]) is elapsed
    assert result["issues"] == ["farm_timezone_differs_from_saved_site"]
    assert result["site_timezone"] == zone


def test_issue_time_takes_precedence_over_newer_download(setup):  # noqa: F811
    workflow, _ = setup
    data = inputs()
    data["weather"] = copy.deepcopy(bundle()["weather"])
    row = data["weather"][0]
    row.pop("source_snapshot_id")
    row.update(issued_at="2026-07-04T00:00:00Z", retrieved_at="2026-07-06T11:00:00Z")
    plan = workflow.save("owner", "field", "root_zone_budget", data, request_id="old-issue")
    result = WeatherReview(workflow.db).review("owner", "field", plan["id"], 24, "2026-07-06T12:00:00Z")
    assert result["rows"][0]["age_hours"] == 60
    assert result["rows"][0]["flags"] == ["age_limit_exceeded"]
    row.update(issued_at="2026-07-06T11:30:00Z")
    plan = workflow.save("owner", "field", "root_zone_budget", data, request_id="invalid-order")
    result = WeatherReview(workflow.db).review("owner", "field", plan["id"], 24, "2026-07-06T12:00:00Z")
    assert result["rows"][0]["flags"] == ["issuance_after_retrieval"]


@pytest.mark.parametrize("limit", [0, -1, True, None, "24", float("nan"), float("inf")])
def test_requires_explicit_finite_positive_policy(setup, limit):  # noqa: F811
    workflow, _ = setup
    plan = save(workflow)
    with pytest.raises(ValueError, match="max_age_hours"):
        WeatherReview(workflow.db).review("owner", "field", plan["id"], limit)


@pytest.mark.parametrize("stamp", ["yesterday", "2026-07-06T12:00:00", True, 7])
def test_rejects_ambiguous_review_clock(setup, stamp):  # noqa: F811
    workflow, _ = setup
    plan = save(workflow)
    with pytest.raises(ValueError, match="as_of"):
        WeatherReview(workflow.db).review("owner", "field", plan["id"], 24, stamp)


def test_owner_and_unit_boundaries_and_no_write_transactions(setup):  # noqa: F811
    workflow, farms = setup
    plan = save(workflow)
    review = WeatherReview(workflow.db)
    for owner, unit in (("other", "field"), ("owner", "field2"), ("other", "field3")):
        with pytest.raises(ValueError):
            review.review(owner, unit, plan["id"], 24)
    before = farms.export("owner")
    statements = []
    workflow.db._conn.set_trace_callback(statements.append)
    result = review.review("owner", "field", plan["id"], 24)
    workflow.db._conn.set_trace_callback(None)
    assert result["as_of"].endswith("+00:00")
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    assert farms.export("owner") == before


def test_equipment_without_direct_weather_is_not_reported_as_fresh(setup):  # noqa: F811
    workflow, _ = setup
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/drip_design.json").read_text())
    plan = workflow.save("owner", "field", "drip_design", data, request_id="equipment")
    result = WeatherReview(workflow.db).review("owner", "field", plan["id"], 24)
    assert result["status"] == "no_weather_inputs" and result["rows"] == []
    assert result["local_date"] is None
    assert "linked calculations" in result["note"]

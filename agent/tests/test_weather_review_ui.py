"""The native saved-plan view exposes an explicit, read-only weather review."""

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup


def test_review_requires_policy_and_invalidates_when_policy_or_plan_changes(tmp_path):
    brain, _, plan = setup(tmp_path)
    data = plan["inputs"]
    data["weather"][0].update(kind="forecast", issued_at="2026-07-05T12:00:00Z")
    forecast = brain.field_workflow("web", "operator", "save",
        dict(task="root_zone_budget", inputs=data, request_id="forecast"))
    before = brain.field_workflow("web", "operator", "list", {})
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(forecast["id"]).run(timeout=15)
    assert at.number_input(key="field_weather_review_limit").value is None
    at.button(key="field_weather_review_run").click().run(timeout=15)
    assert not at.exception and any("max_age_hours" in e.value for e in at.error)
    at.number_input(key="field_weather_review_limit").set_value(24.0).run(timeout=15)
    at.button(key="field_weather_review_run").click().run(timeout=15)
    assert not at.exception and not at.error
    report = at.session_state["field_weather_review_report"]
    assert report["calculation_id"] == forecast["id"] and report["max_age_hours"] == 24
    assert any("Reviewed at" in c.value and report["as_of"] in c.value for c in at.caption)
    assert any(b.label == "Download weather age review" for b in at.get("download_button"))
    at.number_input(key="field_weather_review_limit").set_value(48.0).run(timeout=15)
    assert not any(b.label == "Download weather age review" for b in at.get("download_button"))
    at.button(key="field_weather_review_run").click().run(timeout=15)
    at.selectbox(key="field_plan").set_value(plan["id"]).run(timeout=15)
    assert not at.exception and not at.error
    assert not any(b.label == "Download weather age review" for b in at.get("download_button"))
    at.button(key="field_weather_review_run").click().run(timeout=15)
    assert at.session_state["field_weather_review_report"]["rows"][0]["status"] == "not_a_forecast"
    assert brain.field_workflow("web", "operator", "list", {}) == before

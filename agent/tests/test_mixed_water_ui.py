"""Rendered mixed-provenance replay keeps the choice explicit and the data separate."""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import _SCRIPT, setup  # noqa: E402
from agronaut_agent.tests.test_field_workflow import water  # noqa: E402
from agronaut_agent.tests.test_mixed_water_replay import historical_inputs  # noqa: E402


def test_web_replay_requires_explicit_historical_choice_and_preserves_weather(tmp_path):
    brain, _, _ = setup(tmp_path)
    original = brain.field_workflow("web", "operator", "save", dict(task="root_zone_budget",
        inputs=historical_inputs(), request_id="historical"))
    actual = brain.field_workflow("web", "operator", "record_day", dict(request_id="actual",
        data=water(provenance="measured", quality="checked")))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(original["id"]).run(timeout=15)
    assert not at.exception
    assert not at.checkbox(key=f"field_replay_historical_{original['id']}").value
    at.multiselect(key="field_replay_days").set_value([actual["id"]])
    at.button(key="field_replay").click().run(timeout=15)
    assert not at.exception and "allow_historical" in at.error[0].value
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
    at.checkbox(key=f"field_replay_historical_{original['id']}").check()
    at.button(key="field_replay").click().run(timeout=15)
    assert not at.exception and not at.error
    selected = at.selectbox(key="field_plan").value
    assert selected != original["id"]
    revised = brain.field_workflow("web", "operator", "get", dict(calculation_id=selected))
    assert revised["inputs"]["weather"] == original["inputs"]["weather"]
    assert revised["inputs"]["weather_sources"] == original["inputs"]["weather_sources"]
    assert revised["inputs"]["rainfall_overrides"][0]["water_record_id"] == actual["id"]
    rendered = "\n".join(element.value for element in at.text)
    assert "Rainfall used: 5 mm (measured, checked)" in rendered
    assert "historical" in rendered and actual["id"] in rendered
    assert not at.checkbox(key=f"field_replay_historical_{selected}").value
    assert len(brain.field_workflow("web", "operator", "list", {})["water_days"]) == 1

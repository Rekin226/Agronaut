import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agronaut_agent.core import AgronautAgent  # noqa: E402

_SHARED = {}
_SCRIPT = """
import streamlit as st
from agent.tests.test_farm_ui import _SHARED
from agent.farm_ui import render_unit_controls
render_unit_controls(_SHARED['brain'], 'operator')
"""


class NoModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, *args, **kwargs):
        raise AssertionError("farm setup must not reach a model")


def run(brain):
    _SHARED["brain"] = brain
    return AppTest.from_string(_SCRIPT).run(timeout=15)


def test_web_form_creates_a_field_and_selects_it_without_a_model(tmp_path):
    brain = AgronautAgent(db_path=tmp_path / "ui.sqlite3", chat_model=NoModel())
    at = run(brain)
    assert not at.exception
    at.text_input(key="farm_new_name").set_value("Tomato field")
    at.text_input(key="farm_new_farm_name").set_value("Home")
    at.text_input(key="farm_new_timezone").set_value("Africa/Accra")
    next(b for b in at.button if b.label == "Create and use unit").click().run(timeout=15)
    assert not at.exception
    units = brain.list_units("web", "operator")
    assert len(units) == 1 and units[0]["active"]
    assert units[0]["name"] == "Tomato field"
    assert units[0]["production_system"] == "soil"
    assert units[0]["water_strategy"] == "rainfed"


def test_switching_web_unit_clears_other_units_transcript_and_form_values(tmp_path):
    brain = AgronautAgent(db_path=tmp_path / "ui.sqlite3", chat_model=NoModel())
    spec = {"farm": {"name": "Home", "timezone": "UTC"},
            "unit": {"name": "One", "production_system": "soil", "medium": "soil",
                     "environment": "open_field", "water_strategy": "rainfed"}}
    first = brain.create_unit("web", "operator", spec)
    spec["unit"]["name"] = "Two"
    brain.create_unit("web", "operator", spec)
    at = run(brain)
    at.session_state["messages"] = [{"role": "assistant", "content": "Other field's advice"}]
    at.session_state["twin_tank_l"] = 999
    at.selectbox(key="farm_unit_choice").set_value(first["id"])
    at.button(key="farm_use_unit").click().run(timeout=15)
    assert not at.exception
    assert next(u for u in brain.list_units("web", "operator") if u["active"])["id"] == first["id"]
    assert at.session_state["messages"] == []
    assert "twin_tank_l" not in at.session_state


def test_invalid_setup_is_shown_without_saving_a_partial_farm(tmp_path):
    brain = AgronautAgent(db_path=tmp_path / "ui.sqlite3", chat_model=NoModel())
    at = run(brain)
    at.text_input(key="farm_new_name").set_value("Field")
    at.text_input(key="farm_new_farm_name").set_value("Home")
    at.text_input(key="farm_new_timezone").set_value("not-a-timezone")
    next(b for b in at.button if b.label == "Create and use unit").click().run(timeout=15)
    assert not at.exception
    assert any("timezone" in err.value for err in at.error)
    assert brain.list_farms("web", "operator") == []


def test_chat_setup_switch_clears_the_previous_units_visible_history(tmp_path, monkeypatch):
    import app

    brain = AgronautAgent(db_path=tmp_path/"wizard.sqlite3", chat_model=NoModel())
    brain.create_unit("web", "operator", {"farm": {"name": "Home", "timezone": "UTC"},
        "unit": {"name": "Pond", "production_system": "aquaponics", "medium": "water",
                 "environment": "greenhouse", "water_strategy": "fully_irrigated"}})
    _SHARED["brain"] = brain
    monkeypatch.setattr(app, "_CHAT_INPUT_ACCEPTS_FILES", False)
    script = """
import streamlit as st
import app
from agent.tests.test_farm_ui import _SHARED
st.session_state.agent = _SHARED['brain']
st.session_state.web_user = 'operator'
st.session_state.app_mode = 'Assistant (chat)'
if 'messages' not in st.session_state:
    st.session_state.messages = [{'role':'assistant','content':'Private pond dosing note'}]
app.main()
"""
    at = AppTest.from_string(script).run(timeout=15)
    for message in ("/newunit soil", "1", "Tomato field", "open_field", "supplemental", "drip"):
        at.chat_input[0].set_value(message).run(timeout=15)
        assert not at.exception
    assert next(u for u in brain.list_units("web", "operator") if u["active"])["name"] == "Tomato field"
    assert len(at.session_state.messages) == 1
    assert "Created and selected" in at.session_state.messages[0]["content"]


def test_web_field_chat_reaches_the_registered_calculator(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from langchain_core.messages import AIMessage, ToolMessage

    import app

    payload = json.loads((Path(__file__).resolve().parents[2] / "examples/field_water.json").read_text())

    class FieldModel(NoModel):
        def invoke(self, messages):
            results = [m for m in messages if isinstance(m, ToolMessage)]
            if results:
                result = json.loads(results[-1].content)
                eto = result["result"]["days"][0]["demand"]["values"]["et0_mm_day"]
                return AIMessage(content=f"Reference ET is {eto:.2f} mm/day; this calculation is experimental.")
            return AIMessage(content="", tool_calls=[{
                "name": "calculate_agriculture", "id": "field-water", "args": {
                    "production_system": "soil", "task": "crop_water", "inputs": payload}}])

    brain = AgronautAgent(db_path=tmp_path / "app.sqlite3", chat_model=FieldModel())
    brain.create_unit("web", "operator", {
        "farm": {"name": "Home", "timezone": "UTC"},
        "unit": {"name": "Tomatoes", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    _SHARED["brain"] = brain
    # AppTest's chat widget cannot serialize accept_file input in this installed
    # Streamlit version. Exercise the application's supported separate-upload branch.
    monkeypatch.setattr(app, "_CHAT_INPUT_ACCEPTS_FILES", False)
    script = """
import streamlit as st
import app
from agent.tests.test_farm_ui import _SHARED
st.session_state.agent = _SHARED['brain']
st.session_state.web_user = 'operator'
app.main()
"""
    at = AppTest.from_string(script).run(timeout=15)
    at.radio(key="app_mode").set_value("Assistant (chat)").run(timeout=15)
    at.chat_input[0].set_value("Calculate water demand from the supplied weather.").run(timeout=30)
    assert not at.exception
    assert any("3.88 mm/day" in item.value for item in at.markdown)
    recorded = brain.tool_results("web", "operator", "calculate_agriculture")
    assert json.loads(recorded[0])["capability"]["id"] == "soil.crop_water"

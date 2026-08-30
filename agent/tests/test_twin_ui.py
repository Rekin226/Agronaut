"""Headless test of the My Twin Streamlit view.

Drives the real view against a real agent and a stubbed weather boundary — no browser,
no LLM. The twin path must never reach a model, and this proves it: the agent is built
with a chat model that raises if anything calls it.
"""

from datetime import date, timedelta

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402


class _NoLLM:
    def bind_tools(self, tools):
        return self
    def invoke(self, *a, **k):
        raise AssertionError("the twin dashboard must never call an LLM")


def _offline(monkeypatch):
    from aqua_model.climate import DailyClimate
    from agronaut_agent import tools as T

    def fake(lat, lon, past_days, forecast_days):
        n = min(92, max(0, past_days)) + min(16, max(1, forecast_days))
        start = date.today() - timedelta(days=min(92, max(0, past_days)))
        dates = [(start + timedelta(days=i)).isoformat() for i in range(n)]
        days = tuple(DailyClimate(t_mean_c=26.0, t_min_c=22.0, t_max_c=30.0,
                                  solar_mj_m2=18.0) for _ in range(n))
        return dates, days
    monkeypatch.setattr(T, "_live_weather", fake)


def _brain(tmp_path):
    from agronaut_agent.core import AgronautAgent
    return AgronautAgent(db_path=tmp_path / "ui.sqlite3", chat_model=_NoLLM())


def _complete(brain, user="u1"):
    uid = brain._conv.get_or_create_user("web", user)
    brain._mem.set_facts(uid, {
        "fish_species": "tilapia", "crop": "basil", "grow_area_m2": 15,
        "tank_volume_l": 2000, "fish_count": 60, "fish_avg_weight_g": 200,
        "climate_site": "taichung_2025", "site_lat": 24.15, "site_lon": 120.68},
        source="user_stated")


# AppTest.from_function recompiles a function body without its module globals, so the
# view is driven as a script that imports it. The fixture is handed over through this
# test module rather than a hook inside the production view.
_SHARED: dict = {}

_SCRIPT = (
    "from agent.tests.test_twin_ui import _SHARED\n"
    "from agent.twin_ui import render_twin\n"
    "render_twin(brain=_SHARED['brain'], user=_SHARED['user'])\n"
)


def _run(brain, user="u1"):
    _SHARED["brain"], _SHARED["user"] = brain, user
    return AppTest.from_string(_SCRIPT).run(timeout=60)


def test_incomplete_profile_offers_a_form_instead_of_a_robot_message(tmp_path, monkeypatch):
    """The gate a farmer used to hit said "call fetch_site_climate" — an instruction
    addressed to a model. Here it must be a form they can actually fill in."""
    _offline(monkeypatch)
    at = _run(_brain(tmp_path))

    assert not at.exception
    labels = [n.label for n in at.number_input]
    assert "Tank volume (L)" in labels
    assert "Number of fish" in labels
    assert "Average fish weight (g)" in labels
    assert not any("fetch_site_climate" in str(m.value) for m in at.markdown)


def test_the_form_starts_the_twin_without_an_llm(tmp_path, monkeypatch):
    _offline(monkeypatch)
    brain = _brain(tmp_path)
    uid = brain._conv.get_or_create_user("web", "u1")
    brain._mem.set_facts(uid, {"fish_species": "tilapia", "crop": "basil",
                               "grow_area_m2": 15, "climate_site": "taichung_2025",
                               "site_lat": 24.15, "site_lon": 120.68}, source="user_stated")

    at = _run(brain)
    at.number_input(key="twin_tank_l").set_value(2000.0)
    at.number_input(key="twin_fish_count").set_value(60)
    at.number_input(key="twin_fish_weight_g").set_value(200.0)
    at.button[0].click()
    at.run(timeout=60)

    assert not at.exception
    facts = brain._mem.get_facts(uid)
    assert float(facts["tank_volume_l"]) == 2000.0
    assert int(float(facts["fish_count"])) == 60


def test_a_started_twin_shows_state_and_a_forecast_chart(tmp_path, monkeypatch):
    _offline(monkeypatch)
    brain = _brain(tmp_path)
    _complete(brain)

    at = _run(brain)

    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert "Fish" in metrics and "Water" in metrics
    # st.line_chart surfaces as a vega-lite spec in AppTest
    assert len(at.get("vega_lite_chart")) >= 2, "the forecast series are not charted"


def test_logged_readings_appear_as_drift(tmp_path, monkeypatch):
    _offline(monkeypatch)
    brain = _brain(tmp_path)
    _complete(brain)
    brain.log_readings_direct("web", "u1", {"nitrate_mg_l": 40.0, "water_temp_c": 27.0})

    at = _run(brain)

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(
        str(getattr(d, "value", "")) for d in at.dataframe)
    assert "nitrate" in body.lower() or len(at.dataframe) >= 1

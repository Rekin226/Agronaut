"""The join: this operator's live twin, bound to a drawing of their system.

The 3D view had never once shown anyone's actual pond (#118). These tests are about the
seam where it now does, and about the one thing that must never blur there — which numbers
are the operator's and which are a proposal.
"""

from datetime import date, timedelta

import pytest

from agronaut_agent import runtime, twin_view
from agronaut_agent import tools as T
from agronaut_agent.store import MemoryStore, _Db
from aqua_model.climate import DailyClimate


@pytest.fixture
def offline_weather(monkeypatch):
    """No network in a test, and none needed: the twin takes weather as data."""
    def fake(lat, lon, past_days, forecast_days):
        n = min(92, max(0, past_days)) + min(16, max(1, forecast_days))
        start = date.today() - timedelta(days=min(92, max(0, past_days)))
        dates = [(start + timedelta(days=i)).isoformat() for i in range(n)]
        days = tuple(DailyClimate(t_mean_c=26.0, t_min_c=22.0, t_max_c=30.0,
                                  solar_mj_m2=18.0) for _ in range(n))
        return dates, days
    monkeypatch.setattr(T, "_live_weather", fake)


FULL_PROFILE = {
    "fish_species": "tilapia", "crop": "basil", "grow_area_m2": 15,
    "tank_volume_l": 2000, "fish_count": 60, "fish_avg_weight_g": 200,
    "water_budget_lpd": 400, "system_type": "raft",
    "climate_site": "taichung_2025", "site_lat": 24.15, "site_lon": 120.68,
}


@pytest.fixture
def session(tmp_path):
    """A live turn with a memory store, the way a channel adapter sets one up."""
    mem = MemoryStore(_Db(str(tmp_path / "t.sqlite3")))
    runtime.set_current(mem, "telegram:1")
    try:
        yield mem, "telegram:1"
    finally:
        runtime.clear_current()


def _profile(mem, uid, **over):
    mem.set_facts(uid, {**FULL_PROFILE, **over}, source="user_stated")


def _snapshot(mem, uid, days=7):
    return twin_view.compute(mem, uid, days=days, greenhouse="shade")


# --- the join ------------------------------------------------------------------------

def test_the_scene_carries_this_operators_fish_not_the_designs(session, offline_weather):
    mem, uid = session
    _profile(mem, uid)
    snap = _snapshot(mem, uid)

    scene = twin_view.scene_for(snap, mem.get_facts(uid))

    assert scene["twin"]["mode"] == "live"
    assert scene["twin"]["frames"][0]["fish"]["count"] == 60, (
        "the drawing must show the twin's cohort, not the number the sizing model would "
        "have stocked into this grow area")


def test_today_is_labelled_today_and_the_rest_is_labelled_forecast(session, offline_weather):
    mem, uid = session
    _profile(mem, uid)

    frames = twin_view.scene_for(_snapshot(mem, uid), mem.get_facts(uid))["twin"]["frames"]

    assert frames[0]["kind"] == "today"
    assert frames[0]["date"] == date.today().isoformat()
    assert {f["kind"] for f in frames[1:]} == {"forecast"}
    assert frames[-1]["date"] > frames[0]["date"]


def test_the_scene_admits_the_geometry_is_only_a_proposal(session, offline_weather):
    mem, uid = session
    _profile(mem, uid)

    scene = twin_view.scene_for(_snapshot(mem, uid), mem.get_facts(uid))

    assert "proposed" in scene["twin"]["geometry_note"]


def test_a_tank_that_disagrees_with_the_grow_area_is_said_out_loud(session, offline_weather):
    """Drawing over the disagreement would hide a real finding about their system."""
    mem, uid = session
    _profile(mem, uid, tank_volume_l=200)          # far too small for 15 m2 of basil

    scene = twin_view.scene_for(_snapshot(mem, uid), mem.get_facts(uid))

    assert "not the 200 L you told me you have" in scene["subtitle"]


def test_the_operators_own_water_temperature_sizes_the_design(session, offline_weather):
    """No default stands in for a number the twin has been carrying all along."""
    mem, uid = session
    _profile(mem, uid)
    snap = _snapshot(mem, uid)
    facts = dict(mem.get_facts(uid))
    facts.pop("temperature_c", None)

    fallback = twin_view.scene_for(snap, facts)    # must not raise for a missing temp
    explicit = twin_view.scene_for(snap, {**facts,
                                         "temperature_c": snap.state.water_temp_c})

    assert fallback["objects"] == explicit["objects"], (
        "the missing temperature fell back to something other than the twin's own water")


# --- the tool ------------------------------------------------------------------------

def test_the_tool_asks_for_what_a_drawing_needs_instead_of_assuming(session, offline_weather):
    mem, uid = session
    _profile(mem, uid)
    mem.set_facts(uid, {"water_budget_lpd": ""}, source="user_stated")

    out = T.show_my_system_3d.invoke({"days_ahead": 5})

    assert "water_budget_lpd" in out
    assert not runtime.get_attachments(), "nothing should be drawn from a guess"


def test_the_tool_attaches_a_self_contained_file_with_the_season_in_it(session,
                                                                      offline_weather):
    mem, uid = session
    _profile(mem, uid)

    out = T.show_my_system_3d.invoke({"days_ahead": 7})

    attached = runtime.get_attachments()
    assert len(attached) == 1
    html = open(attached[0]).read()
    assert "__SCENE_JSON__" not in html, "the template placeholder was never filled"
    assert '"kind": "today"' in html or '"kind":"today"' in html
    assert "cdn" not in html.lower().split("<script")[0]
    assert "TODAY" in out.upper() and "60 fish" in out


def test_the_tool_refuses_a_twin_nobody_is_running(session, offline_weather):
    mem, uid = session
    mem.set_facts(uid, {"fish_species": "tilapia"}, source="user_stated")

    out = T.show_my_system_3d.invoke({})

    assert "still need" in out
    assert not runtime.get_attachments()


def test_the_tool_is_registered():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    assert "show_my_system_3d" in {t.name for t in AGRONAUT_TOOLS}

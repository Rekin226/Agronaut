"""The structured seam behind the twin dashboard.

/log and /forecast return prose for Telegram. A dashboard needs the numbers that prose
was rendered FROM — and the two must never disagree, so both are computed once here.
"""

from datetime import date, timedelta

import pytest

from aqua_model.climate import DailyClimate
from agronaut_agent import runtime, tools as T
from agronaut_agent.core import AgronautAgent
from agronaut_agent.store import _Db, MemoryStore, ReadingStore


@pytest.fixture
def offline_weather(monkeypatch):
    def fake(lat, lon, past_days, forecast_days):
        n = min(92, max(0, past_days)) + min(16, max(1, forecast_days))
        start = date.today() - timedelta(days=min(92, max(0, past_days)))
        dates = [(start + timedelta(days=i)).isoformat() for i in range(n)]
        days = tuple(DailyClimate(t_mean_c=26.0, t_min_c=22.0, t_max_c=30.0,
                                  solar_mj_m2=18.0) for _ in range(n))
        return dates, days
    monkeypatch.setattr(T, "_live_weather", fake)


@pytest.fixture
def brain(tmp_path):
    """A real agent with no LLM — the twin path never calls one."""
    return AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_NoLLM())


class _NoLLM:
    def bind_tools(self, tools):
        return self
    def invoke(self, *a, **k):
        raise AssertionError("the twin path must never call an LLM")


def _profile(brain, channel="telegram", user="1"):
    uid = brain._conv.get_or_create_user(channel, user)
    brain._mem.set_facts(uid, {
        "fish_species": "tilapia", "crop": "basil", "grow_area_m2": 15,
        "tank_volume_l": 2000, "fish_count": 60, "fish_avg_weight_g": 200,
        "climate_site": "taichung_2025", "site_lat": 24.15, "site_lon": 120.68},
        source="user_stated")
    return uid


def test_twin_snapshot_returns_numbers_not_prose(brain, offline_weather):
    _profile(brain)

    snap = brain.twin_snapshot("telegram", "1", days=7, greenhouse="shade")

    assert snap.ready is True
    assert snap.state.fish.count == 60
    assert len(snap.trajectory) >= 2, "a chartable per-day series"
    assert snap.summary.days == len(snap.trajectory)
    assert snap.summary.limiting_factor


def test_twin_snapshot_reports_what_is_missing_instead_of_raising(brain, offline_weather):
    """An incomplete profile is the normal first-run state, not an error."""
    snap = brain.twin_snapshot("telegram", "nobody", days=7)

    assert snap.ready is False
    assert "tank_volume_l" in snap.missing
    assert snap.state is None


def test_dashboard_and_telegram_agree_on_the_numbers(brain, offline_weather):
    """The prose /forecast and the dashboard must be one computation, not two. If these
    ever diverge, the farmer is reading two different twins."""
    _profile(brain)

    snap = brain.twin_snapshot("telegram", "1", days=7, greenhouse="shade")
    text = brain.forecast_direct("telegram", "1", 7, "shade")

    assert f"{snap.summary.crop_harvested_kg:.1f} kg" in text
    assert snap.summary.limiting_factor in text


def test_logged_readings_reach_the_snapshot_history(brain, offline_weather):
    _profile(brain)

    brain.log_readings_direct("telegram", "1", {"nitrate_mg_l": 40.0, "water_temp_c": 27.0})
    snap = brain.twin_snapshot("telegram", "1", days=7)

    assert len(snap.history) == 1
    assert snap.history[0]["observed"]["nitrate_mg_l"] == 40.0


def test_twin_readings_are_exported_and_erasable(brain, offline_weather):
    """DPG data rights: anything we store about a user is exportable and erasable."""
    _profile(brain)
    brain.log_readings_direct("telegram", "1", {"nitrate_mg_l": 40.0})

    assert len(brain.export_user_data("telegram", "1")["twin_readings"]) == 1

    brain.delete_me("telegram", "1")
    assert brain.export_user_data("telegram", "1")["twin_readings"] == []

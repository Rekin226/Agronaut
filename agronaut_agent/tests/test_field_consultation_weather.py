"""Explicit provider intake, durable evidence and races across DB connections."""

import copy
import json

import pytest

from agronaut_agent import weather_provider as provider
from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.store import _Db
from agronaut_agent.tests.test_field_consultation import ANSWERS
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_weather_provider import bundle, response, weather_request


def advance(wizard, target="weather_bundle", **changes):
    answers = dict(ANSWERS, weather_input="open_meteo", latitude="5.6", longitude="-0.2", **changes)
    view = wizard.start("owner", "field")
    while view["next_field"] != target:
        view = wizard.answer("owner", "field", answers[view["next_field"]], view["revision"])
    return view


def test_explicit_fetch_retains_evidence_and_saves_offline_after_restart(setup, monkeypatch):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    calls = []
    expected = bundle("2026-07-07T12:00:00+00:00")
    def fetch(request):
        calls.append(request)
        return expected
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    view = advance(wizard)
    assert calls == []
    question = render_intake(view)
    for fragment in ("5.6", "-0.2", "2026-07-06", "UTC", "Open-Meteo", "Reply fetch"):
        assert fragment in question
    assert wizard.get("owner", "field") == view
    with pytest.raises(ValueError, match="Reply fetch"):
        wizard.answer("owner", "field", "yes", view["revision"])
    assert calls == []
    fetched = wizard.answer("owner", "field", "fetch", view["revision"])
    assert calls == [weather_request()]
    assert fetched["next_field"] == "fc"
    assert fetched["answers"]["weather_bundle"] == expected
    assert "historical" in render_intake(fetched)
    assert "Model grid: 5.625, -0.25" in render_intake(fetched)
    with pytest.raises(ValueError, match="changed"):
        wizard.answer("owner", "field", "fetch", view["revision"])
    assert len(calls) == 1
    assert farms.export("owner")["field_intakes"][0]["data"]["weather_bundle"] == expected

    def offline(request):
        raise AssertionError("resuming or saving must not fetch again")
    monkeypatch.setattr(provider, "fetch_weather", offline)
    db = _Db(workflow.db.path)
    try:
        resumed = FieldConsultation(db)
        ready = advance(resumed, target=None)
        assert ready["inputs"]["weather"] == expected["weather"]
        assert ready["inputs"]["weather_sources"] == expected["weather_sources"]
        saved = resumed.save("owner", "field", ready["draft_id"], ready["revision"])
        assert saved["calculation"]["result"] == ready["preview"]
        assert saved["calculation"]["inputs"]["weather_sources"] == expected["weather_sources"]
        assert workflow.list("owner", "field")["water_days"] == []
    finally:
        db._conn.close()


@pytest.mark.parametrize("field,value", [("latitude", "91"), ("latitude", "nan"), ("longitude", "-181"),
                                         ("longitude", "inf"), ("weather_input", "automatic")])
def test_invalid_provider_answers_do_not_change_draft(setup, field, value):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, field)
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", value, view["revision"])
    assert wizard.get("owner", "field") == view


def test_long_window_can_still_choose_manual(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, "weather_input", days="17")
    with pytest.raises(ValueError, match="at most 16"):
        wizard.answer("owner", "field", "open_meteo", view["revision"])
    assert wizard.answer("owner", "field", "manual", view["revision"])["next_field"] == "weather_kind"


@pytest.mark.parametrize("failure", ["network", "location", "dates", "tampered", "sources"])
def test_failed_or_mismatched_fetch_leaves_draft_unchanged(setup, monkeypatch, failure):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard)
    def fetch(request):
        if failure == "network":
            raise ValueError("weather provider could not be reached")
        if failure == "location":
            request = dict(request, latitude=6)
            return provider.adapt_response(request, response(), "2026-07-05T12:00:00+00:00")
        if failure == "dates":
            return provider.adapt_response(dict(request, end="2026-07-07"), response(count=48), "2026-07-05T12:00:00+00:00")
        result = bundle()
        if failure == "tampered":
            result["weather"][0]["et0_mm_day"] = 0
        else:
            result["weather_sources"] = []
        return result
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", "fetch", view["revision"])
    assert wizard.get("owner", "field") == view


@pytest.mark.parametrize("action", ["back", "cancel", "replace", "erase", "timezone"])
def test_late_result_rejected_without_holding_database_lock(setup, monkeypatch, action):
    from agronaut_agent.farm_store import FarmStore
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard)
    other = _Db(workflow.db.path)
    other.execute("PRAGMA busy_timeout=50")
    changed = None
    def fetch(request):
        nonlocal changed
        concurrent = FieldConsultation(other)
        if action == "back":
            changed = concurrent.answer("owner", "field", "", view["revision"], back=True)
        elif action in ("cancel", "replace"):
            concurrent.cancel("owner", "field", view["revision"])
            if action == "replace":
                changed = concurrent.start("owner", "field")
        elif action == "erase":
            FarmStore(other).purge("owner")
        else:
            other.execute("UPDATE farms SET timezone='Europe/Berlin' WHERE id='farm'")
            changed = view
        return bundle()
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    try:
        with pytest.raises(ValueError):
            wizard.answer("owner", "field", "fetch", view["revision"])
        if action == "erase":
            assert not wizard.active("owner", "field")
        else:
            assert wizard.get("owner", "field") == changed
    finally:
        other._conn.close()


def test_back_removes_fetch_and_location_edits_require_new_fetch(setup, monkeypatch):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    calls = []
    def fetch(request):
        calls.append(copy.deepcopy(request))
        return provider.adapt_response(request, response(), "2026-07-05T12:00:00+00:00")
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    view = advance(wizard, task="1")
    ready = wizard.answer("owner", "field", "fetch", view["revision"])
    assert ready["status"] == "ready"
    back = wizard.answer("owner", "field", "", ready["revision"], back=True)
    assert back["next_field"] == "weather_bundle"
    assert "weather_bundle" not in back["answers"]
    back = wizard.answer("owner", "field", "", back["revision"], back=True)
    assert back["next_field"] == "longitude"
    back = wizard.answer("owner", "field", "-0.4", back["revision"])
    assert len(calls) == 1
    ready = wizard.answer("owner", "field", "fetch", back["revision"])
    assert calls[-1]["longitude"] == -0.4
    assert ready["inputs"]["weather_sources"][0]["request"] == calls[-1]


def test_legacy_draft_resumes_manual_flow_without_new_questions(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, "weather_input")
    row = workflow.db.query("SELECT payload FROM field_intake_drafts WHERE unit_id='field'")[0]
    payload = json.loads(row[0])
    payload.pop("intake_version")
    workflow.db.execute("UPDATE field_intake_drafts SET payload=? WHERE unit_id='field'", (json.dumps(payload),))
    resumed = wizard.get("owner", "field")
    assert resumed["next_field"] == "weather_kind"
    assert resumed["revision"] == view["revision"]
    while resumed["status"] != "ready":
        resumed = wizard.answer("owner", "field", ANSWERS[resumed["next_field"]], resumed["revision"])
    assert resumed["inputs"]["weather"][0]["et0_mm_day"] == 4


def test_mixed_weather_days_keep_kinds_and_runoff_bounds(setup, monkeypatch):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    def fetch(request):
        return provider.adapt_response(request, response(count=48), "2026-07-07T12:00:00+00:00")
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    view = advance(wizard, days="2")
    wizard.answer("owner", "field", "fetch", view["revision"])
    view = advance(wizard, "runoff_0")
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", "12.1", view["revision"])
    ready = advance(wizard, None, irrigation_1="0", runoff_1="0")
    assert [w["kind"] for w in ready["inputs"]["weather"]] == ["historical", "forecast"]
    assert all("issued_at" not in w for w in ready["inputs"]["weather"])


def test_provider_intake_preserves_dst_local_day(setup, monkeypatch):
    workflow, _ = setup
    workflow.db.execute("UPDATE farms SET timezone='Europe/Berlin' WHERE id='farm'")
    wizard = FieldConsultation(workflow.db)
    def fetch(request):
        return provider.adapt_response(request, response("2026-10-24T23:00:00+00:00", 25), "2026-10-24T12:00:00+00:00")
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    view = advance(wizard, task="1", start="2026-10-25")
    ready = wizard.answer("owner", "field", "fetch", view["revision"])
    weather = ready["inputs"]["weather"][0]
    assert weather["period_hours"] == 25
    assert weather["et0_mm_day"] == 6.25
    assert weather["rainfall_mm"] == 12.5


def test_changed_timezone_does_not_trigger_fetch(setup, monkeypatch):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard)
    workflow.db.execute("UPDATE farms SET timezone='Europe/Berlin' WHERE id='farm'")
    def forbidden(request):
        raise AssertionError("a changed farm timezone must be handled before network access")
    monkeypatch.setattr(provider, "fetch_weather", forbidden)
    with pytest.raises(ValueError, match="timezone changed"):
        wizard.answer("owner", "field", "fetch", view["revision"])
    assert wizard.get("owner", "field") == view


def test_unavailable_dates_fail_before_download_and_allow_manual_fallback(setup, monkeypatch):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, cycle_id="0", start="2000-01-01")
    def forbidden(params):
        raise AssertionError("out-of-range dates must not download")
    monkeypatch.setattr(provider, "_download", forbidden)
    with pytest.raises(ValueError, match="last 90 days"):
        wizard.answer("owner", "field", "fetch", view["revision"])
    assert wizard.get("owner", "field") == view
    while view["next_field"] != "weather_input":
        view = wizard.answer("owner", "field", "", view["revision"], back=True)
    manual = wizard.answer("owner", "field", "manual", view["revision"])
    assert manual["next_field"] == "weather_kind"
    assert "latitude" not in manual["answers"]


@pytest.mark.parametrize("channel", ["web", "cli", "whatsapp"])
def test_chat_fetches_only_on_request_and_saves_without_model(tmp_path, monkeypatch, channel):
    from agronaut_agent.channels.commands import dispatch
    from agronaut_agent.core import AgronautAgent
    from agronaut_agent.tests.test_unit_setup import NoModel
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path / "provider-chat.sqlite3", chat_model=NoModel())
    unit = brain.create_unit(channel, "operator", dict(farm=dict(name="Home", timezone="UTC"),
        unit=dict(name="Field", production_system="soil", environment="open_field", medium="soil",
                  water_strategy="rainfed", irrigation_method=None)))
    owner = brain._conv.get_or_create_user(channel, "operator")
    calls = []
    def fetch(request):
        calls.append(request)
        return bundle()
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    def send(message):
        if channel == "web":
            return _route_turn(brain, "operator", message, None)
        if message.startswith("/"):
            return dispatch(brain, channel, "operator", message).text
        return brain.handle_message(channel, "operator", message)
    send("/fieldplan")
    answers = dict(ANSWERS, task="1", weather_input="open_meteo", latitude="5.6", longitude="-0.2")
    view = brain._field_intake.get(owner, unit["id"])
    while view["next_field"] != "weather_bundle":
        send(answers[view["next_field"]])
        view = brain._field_intake.get(owner, unit["id"])
    assert calls == []
    assert "Reply fetch" in send("yes")
    assert calls == []
    preview = send("fetch")
    assert "forecast" in preview and "Reply save" in preview
    assert calls == [weather_request()]
    assert "Saved field-water calculation" in send("save")
    records = brain.field_workflow(channel, "operator", "list", {})
    assert len(records["calculations"]) == 1
    assert records["water_days"] == []

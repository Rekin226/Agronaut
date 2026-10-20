"""Every supported planning route must translate without changing scientific inputs."""

import json
from pathlib import Path

import pytest

from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.field_planning_language import normalize_answer, translate
from agronaut_agent.store import _Db
from agronaut_agent.tests.test_equipment_consultation import answers as equipment_answers
from agronaut_agent.tests.test_equipment_targets import budget
from agronaut_agent.tests.test_field_consultation import ANSWERS
from agronaut_agent.tests.test_field_consultation_weather import advance
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_surface_consultation import answers as surface_answers
from agronaut_agent.tests.test_surface_consultation import example as surface_example
from agronaut_agent.tests.test_weather_provider import bundle, weather_request


def finish(wizard, answers, language):
    view = wizard.start("owner", "field", language)
    while view["status"] == "collecting":
        assert view["question"] and view["language"] == language
        assert "FIELD_TRANSLATION_UNAVAILABLE" not in render_intake(view)
        view = wizard.answer("owner", "field", answers[view["next_field"]], view["revision"])
    return view


@pytest.mark.parametrize("language", ["fr", "zh-Hant"])
@pytest.mark.parametrize("route", ["water", "demand", "drip_design", "sprinkler_design", "border", "basin", "furrow"])
def test_complete_route_matches_english_and_save_retains_language(setup, language, route):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    if route in ("water", "demand"):
        supplied = dict(ANSWERS, task="1" if route == "demand" else "2", cycle_id="0",
                        kc_source="Main · parcelle témoin · 水田")
    elif route in ("drip_design", "sprinkler_design"):
        supplied = equipment_answers(route)
    else:
        supplied = surface_answers(surface_example(route))
    english = finish(wizard, supplied, "en")
    wizard.cancel("owner", "field", english["revision"])
    translated = finish(wizard, supplied, language)
    assert translated["inputs"] == english["inputs"]
    assert translated["preview"] == english["preview"]
    assert len(farms.export("owner")["calculations"]) == 0
    saved = wizard.save("owner", "field", translated["draft_id"], translated["revision"])
    assert saved["calculation"]["intake_language"] == language
    assert saved["calculation"]["inputs"] == english["inputs"]
    assert wizard.save("owner", "field", translated["draft_id"], translated["revision"]) == saved
    assert ("Traduction provisoire" if language == "fr" else "翻譯草稿") in render_intake(saved)
    assert workflow.list("owner", "field")["water_days"] == []


@pytest.mark.parametrize("language", ["fr", "zh-Hant"])
@pytest.mark.parametrize("method", ["drip_design", "sprinkler_design"])
def test_budget_adoption_preserves_dynamic_evidence_and_explicit_use(setup, language, method):
    workflow, _ = setup
    source = budget(workflow, method)
    supplied = dict(equipment_answers(method), application_input="budget", target_calculation=source["id"],
        target_day="2026-07-06", target_reason="Synthetic applicable whole field", target_accepted="use")
    wizard = FieldConsultation(workflow.db)
    view = wizard.start("owner", "field", language)
    while view["next_field"] != "target_accepted":
        view = wizard.answer("owner", "field", supplied[view["next_field"]], view["revision"])
    for value in (source["id"], "2026-07-06", "UTC", "80", "use", "/back"):
        assert value in view["question"]
    with pytest.raises(ValueError, match="Reply use"):
        wizard.answer("owner", "field", "yes", view["revision"])
    supplied["target_accepted"] = "utiliser" if language == "fr" else "採用"
    ready = finish(wizard, supplied, language)
    assert ready["water_target"]["calculation_snapshot"] == source


@pytest.mark.parametrize("language", ["fr", "zh-Hant"])
def test_weather_fetch_keeps_coordinates_and_explicit_consent(setup, monkeypatch, language):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    wizard.start("owner", "field", language)
    calls = []
    def fetch(request):
        calls.append(request)
        return bundle("2026-07-07T12:00:00+00:00")
    monkeypatch.setattr("agronaut_agent.weather_provider.fetch_weather", fetch)
    view = advance(wizard)
    for value in ("fetch", "5.6", "-0.2", "2026-07-06", "UTC", "Open-Meteo"):
        assert value in view["question"]
    with pytest.raises(ValueError, match="Reply fetch"):
        wizard.answer("owner", "field", "yes", view["revision"])
    assert calls == []
    view = wizard.answer("owner", "field", "fetch", view["revision"])
    assert calls == [weather_request()] and view["next_field"] == "fc"
    assert finish(wizard, ANSWERS, language)["inputs"]["weather_sources"]


def test_language_switch_invalidates_revision_and_survives_restart_and_back(setup):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    first = wizard.start("owner", "field")
    second = wizard.answer("owner", "field", "2", first["revision"])
    french = wizard.start("owner", "field", "fr")
    assert french["answers"] == second["answers"]
    assert french["revision"] != second["revision"]
    assert wizard.start("owner", "field") == french
    with pytest.raises(ValueError, match="changed"):
        wizard.answer("owner", "field", "1", second["revision"])
    db = _Db(workflow.db.path)
    try:
        resumed = FieldConsultation(db)
        assert resumed.get("owner", "field") == french
        back = resumed.answer("owner", "field", "", french["revision"], back=True)
        assert back["language"] == "fr" and back["next_field"] == "task"
        chinese = resumed.start("owner", "field", "zh-Hant")
        assert chinese["answers"] == {} and chinese["language"] == "zh-Hant"
        assert farms.export("owner")["field_intakes"][0]["language"] == "zh-Hant"
    finally:
        db._conn.close()


@pytest.mark.parametrize("bad", ["de", "FR", [], ""])
def test_invalid_language_does_not_create_a_draft(setup, bad):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    with pytest.raises(ValueError, match="Use /fieldplan"):
        wizard.start("owner", "field", bad)
    assert wizard.get("owner", "field") is None


def test_missing_or_outdated_question_translation_fails_without_mutation(setup, tmp_path, monkeypatch):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    english = wizard.start("owner", "field")
    before = farms.export("owner")
    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="FIELD_TRANSLATION_UNAVAILABLE"):
        wizard.start("owner", "field", "fr")
    assert farms.export("owner") == before
    monkeypatch.delenv("AGRONAUT_CORPUS_DIR")
    assert wizard.get("owner", "field") == english
    with pytest.raises(ValueError, match="no matching translation"):
        translate("This is a newly changed English question.", "fr")


def test_translations_preserve_placeholders_and_free_text_is_never_rewritten():
    bundle = json.loads((Path(__file__).resolve().parents[2] / "field_guides/planning_questions.json").read_text())
    assert len(bundle["entries"]) > 150
    for language in ("fr", "zh-Hant"):
        assert normalize_answer({"language": language}, "kc_source", "oui") == "oui"
        assert normalize_answer({"language": language}, "crop", "未知") == "未知"
    assert normalize_answer({"language": "fr"}, "depletion", "inconnu") == "unknown"
    assert normalize_answer({"language": "zh-Hant"}, "event.surface_drained", "是") == "yes"

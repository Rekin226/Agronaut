"""Compact live pages never omit tasks silently or turn missing timing into all-clear."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_overview import FarmOverview
from agronaut_agent.farm_overview_text import parse_overview_args, render_overview
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_field_workflow import setup as setup


@pytest.mark.parametrize("arguments,expected", [("", ("en", 1)), ("fr", ("fr", 1)),
    ("zh-Hant 2", ("zh-Hant", 2)), (" 3 ", ("en", 3)), ("en 999999", ("en", 999999))])
def test_explicit_language_and_page(arguments, expected):
    assert parse_overview_args(arguments) == expected


@pytest.mark.parametrize("arguments", ["en 0", "-1", "en 1 extra", "de", "fr 01", "en 1.0", "en １", "1 fr", "1000000"])
def test_malformed_command_is_not_guessed(arguments):
    with pytest.raises(ValueError, match="Use /overview"):
        parse_overview_args(arguments)


@pytest.mark.parametrize("language,title", [("en", "Farm overview"), ("fr", "Vue des exploitations"), ("zh-Hant", "農場總覽")])
def test_pages_fit_utf16_limit_and_every_task_keeps_context(setup, language, title):
    workflow, _ = setup
    CropRecords(workflow.db).plan_task("owner", "field", "cycle", task(), request_id="task")
    report = FarmOverview(workflow.db).get("owner", "farm", now=datetime(2026, 7, 9, tzinfo=timezone.utc))
    farm = report["farms"][0]
    farm["farm"]["name"] = "FARM🌱" * 100
    unit = farm["units"][0]
    unit["unit"]["name"] = "UNIT🌱" * 100
    unit["unit"]["parent_unit_id"] = "parent"
    unit["unit"]["parent_name"] = "PARENT🌱" * 100
    cycle = unit["cycles"][0]
    cycle["cycle"]["crop"] = "CROP🌱" * 100
    original = cycle["attention"][0]
    original.update(trigger=dict(kind='crop_record', id='source-id', reason='REASON🌱'*200),
                    trigger_review=dict(state='superseded', needs_review=True))
    cycle["attention"] = [dict(original, title=f"TASK-{index:02d} " + "🌱" * 500,
                               source="SOURCE🌱" * 100) for index in range(30)]
    before = deepcopy(report)
    replies, page = [], 1
    while True:
        reply = render_overview(report, language, page)
        assert len(reply.encode("utf-16-le")) // 2 < 3500
        assert title in reply and "FARM" in reply and "UNIT" in reply and "CROP" in reply
        replies.append(reply)
        if f"/overview {language} {page+1}" not in reply:
            break
        page += 1
        assert page < 100
    assert len(replies) > 1
    for index in range(30):
        assert sum(reply.count(f"TASK-{index:02d}") for reply in replies) == 1
    assert report == before
    assert f"/overview {language} 1" in render_overview(report, language, page+1)


@pytest.mark.parametrize("language,unknown", [("en", "timing is unknown"), ("fr", "échéances inconnues"), ("zh-Hant", "無法判定工作期限")])
def test_unknown_timezone_and_empty_units_are_explicit(setup, language, unknown):
    workflow, _ = setup
    CropRecords(workflow.db).plan_task("owner", "field", "cycle", task(), request_id="task")
    workflow.db.execute("UPDATE farms SET timezone=NULL WHERE id='farm'")
    report = FarmOverview(workflow.db).get("owner", "farm")
    text = render_overview(report, language)
    assert unknown in text
    assert "2026-07-01" in text
    assert "Synthetic harvest inspection" not in text  # Timing was withheld by the snapshot.
    assert render_overview(FarmOverview(workflow.db).get("nobody"), language)


def test_surrogate_in_imported_name_does_not_break_message_encoding(setup):
    workflow, _ = setup
    report = FarmOverview(workflow.db).get("owner", "farm")
    report["farms"][0]["farm"]["name"] = "Imported\ud800name\n/command"
    rendered = render_overview(report)
    rendered.encode("utf-8")
    assert "\n/command" not in rendered

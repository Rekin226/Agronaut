"""Explicit farm-budget commits/releases through the real no-model forms."""

from datetime import date

from agent.tests.test_resource_ui import add_job, click, fill_period, start
from agronaut_agent.tests.test_resource_ledger import clock as clock


def with_plan(tmp_path):
    at, brain, field, hydro = start(tmp_path)
    add_job(at, field, "field-work", 1, "600")
    fill_period(at)
    click(at, "Save resource scenario")
    saved = brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"][0]
    return at, brain, field, hydro, saved


def fill_budget(at, capacity="1000"):
    prefix = "resource_ledger_budget_new_"
    at.text_input(key=prefix+"resource_key").set_value("borehole")
    at.selectbox(key=prefix+"kind").set_value("water")
    at.date_input(key=prefix+"start").set_value(date(2026, 10, 19))
    at.date_input(key=prefix+"end").set_value(date(2026, 10, 25))
    at.text_input(key=prefix+"capacity").set_value(capacity)
    at.text_input(key=prefix+"source").set_value("Synthetic net supply allowance")


def ledger(brain, field):
    return brain.farm_resources("web", "operator", field["farm_id"], "ledger", {})


def select_commit(at, saved, pool):
    at.selectbox(key="resource_ledger_plan_new").set_value(saved["id"]).run(timeout=15)
    assert not at.exception
    prefix = f"resource_ledger_commit_new_{saved['id']}_"
    at.selectbox(key=prefix+"pool_shared-water").set_value(pool["id"])
    at.text_input(key=prefix+"source").set_value("Operator commits this field allocation")
    return prefix


def test_named_budget_commit_retry_release_and_history(tmp_path):
    at, brain, field, _, saved = with_plan(tmp_path)
    fill_budget(at)
    click(at, "Save shared budget")
    assert not at.error
    pool = ledger(brain, field)["budgets"][0]
    assert pool["remaining"] == "1000"
    select_commit(at, saved, pool)
    click(at, "Commit allocated resources")
    assert not at.error
    view = ledger(brain, field)
    first = view["active"][0]
    assert view["budgets"][0]["remaining"] == "400"
    click(at, "Commit allocated resources")
    assert not at.error and len(ledger(brain, field)["commitments"]) == 1
    prefix = f"resource_ledger_commit_{first['id']}_{saved['id']}_"
    at.text_input(key=prefix+"reason").set_value("Operator cancels this allocation")
    click(at, "Release commitment")
    assert not at.error
    assert ledger(brain, field)["active"] == [] and ledger(brain, field)["budgets"][0]["remaining"] == "1000"
    assert any(b.label == "Download budget and commitment history" for b in at.get("download_button"))
    record = brain.farm_resources("web", "operator", field["farm_id"], "get", dict(plan_id=saved["id"]))
    assert brain._farms.export(record["owner_id"])["water_days"] == []


def test_two_separate_unit_plans_cannot_overbook_and_bad_capacity_keeps_old_budget(tmp_path):
    at, brain, field, hydro, saved = with_plan(tmp_path)
    fill_budget(at)
    click(at, "Save shared budget")
    pool = ledger(brain, field)["budgets"][0]
    select_commit(at, saved, pool)
    click(at, "Commit allocated resources")
    original = ledger(brain, field)["active"][0]
    data = brain.farm_resources("web", "operator", field["farm_id"], "get", dict(plan_id=saved["id"]))["inputs"]
    data["jobs"][0]["unit_id"] = hydro["id"]
    second = brain.farm_resources("web", "operator", field["farm_id"], "save", dict(data=data, request_id="hydro-plan"))
    at.selectbox(key="resource_ledger_commit_choice").set_value(None).run(timeout=15)
    select_commit(at, second, pool)
    click(at, "Commit allocated resources")
    assert at.error and "insufficient" in at.error[0].value
    assert [r["id"] for r in ledger(brain, field)["active"]] == [original["id"]]
    prefix = f"resource_ledger_budget_{pool['id']}_"
    at.text_input(key=prefix+"capacity").set_value("500")
    at.text_input(key=prefix+"reason").set_value("Smaller allowance")
    click(at, "Save shared budget")
    assert at.error and "below active" in at.error[0].value
    assert ledger(brain, field)["budgets"][0]["data"]["capacity"] == "1000"
    at.text_input(key=prefix+"capacity").set_value("1200")
    click(at, "Save shared budget")
    assert not at.error
    updated = ledger(brain, field)["budgets"][0]
    prefix = f"resource_ledger_commit_new_{second['id']}_"
    assert at.selectbox(key=prefix+"pool_shared-water").value is None
    at.selectbox(key=prefix+"pool_shared-water").set_value(updated["id"])
    click(at, "Commit allocated resources")
    assert not at.error and ledger(brain, field)["budgets"][0]["remaining"] == "0"


def test_unknown_capacity_is_rejected_and_farm_switch_clears_commitment_form(tmp_path):
    at, brain, field, _, _ = with_plan(tmp_path)
    fill_budget(at, capacity="")
    click(at, "Save shared budget")
    assert at.error and ledger(brain, field)["budgets"] == []
    other = brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Other field", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception and not at.error
    assert at.text_input(key="resource_ledger_budget_new_capacity").value == ""
    assert at.selectbox(key="resource_ledger_plan_new").value is None

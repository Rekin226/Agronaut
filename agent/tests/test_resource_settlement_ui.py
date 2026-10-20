"""Real settlement forms preserve unknowns, overdrafts and referenced event evidence."""

from datetime import date, datetime, timezone

from agent.tests import session_keys
from agent.tests.test_resource_ledger_ui import fill_budget, ledger, select_commit, with_plan
from agent.tests.test_resource_ui import click
from agronaut_agent.tests.test_resource_ledger import clock as clock


def reserved(tmp_path):
    at, brain, field, _, saved = with_plan(tmp_path)
    fill_budget(at)
    click(at, "Save shared budget")
    pool = ledger(brain, field)["budgets"][0]
    select_commit(at, saved, pool)
    click(at, "Commit allocated resources")
    prior = ledger(brain, field)["active"][0]
    return at, brain, field, prior, pool


def fill(at, prior, amount="450", day=date(2026, 10, 19)):
    prefix = f"resource_settlement_{prior['id']}_"
    at.date_input(key=prefix+"start").set_value(day)
    at.date_input(key=prefix+"end").set_value(day)
    at.text_area(key=prefix+"attribution").set_value("Complete total for the stopped committed work, including maintenance")
    at.text_input(key=prefix+"source").set_value("Synthetic actual-use notebook")
    at.text_input(key=prefix+"reason").set_value("Reconcile full usage")
    for root in prior["amounts"]:
        at.text_input(key=prefix+root+"_amount").set_value(amount)
        at.selectbox(key=prefix+root+"_provenance").set_value("reported" if amount else None)
        at.text_input(key=prefix+root+"_source").set_value("Synthetic total" if amount else "Total remains unknown")
    return prefix


def test_settle_correct_overrun_unknown_and_explicit_zero(tmp_path):
    at, brain, field, prior, _ = reserved(tmp_path)
    fill(at, prior)
    click(at, "Record usage settlement")
    assert not at.error
    view = ledger(brain, field)
    assert view["budgets"][0]["recorded_use"] == "450" and view["budgets"][0]["remaining"] == "550"
    assert not any(b.label in ("Release commitment", "Commit allocated resources") for b in at.button)
    click(at, "Record usage settlement")
    assert not at.error and len(ledger(brain, field)["commitments"]) == 2
    prior = ledger(brain, field)["settlements"][0]
    fill(at, prior, "1200")
    click(at, "Record usage settlement")
    assert not at.error
    assert ledger(brain, field)["budgets"][0]["remaining"] == "-200"
    assert any("overdrawn" in w.value for w in at.warning)
    prior = ledger(brain, field)["settlements"][0]
    fill(at, prior, "")
    click(at, "Record usage settlement")
    assert not at.error
    assert ledger(brain, field)["budgets"][0]["reserved"] == "1200"
    prior = ledger(brain, field)["settlements"][0]
    fill(at, prior, "0")
    click(at, "Record usage settlement")
    assert not at.error and ledger(brain, field)["budgets"][0]["remaining"] == "1000"
    assert any(b.label == "Download budget and commitment history" for b in at.get("download_button"))


def test_unknown_keeps_allowance_and_stale_form_cannot_overwrite_a_correction(tmp_path):
    at, brain, field, prior, _ = reserved(tmp_path)
    fill(at, prior, "")
    click(at, "Record usage settlement")
    assert not at.error
    view = ledger(brain, field)
    assert view["budgets"][0]["reserved"] == "600" and view["budgets"][0]["remaining"] == "400"
    current = view["settlements"][0]
    fill(at, current, "450")
    changed = dict(current["settlement"], usage={root: dict(row, amount="350", provenance="reported") for root, row in current["settlement"]["usage"].items()})
    brain.farm_resources("web", "operator", field["farm_id"], "settle_commitment", dict(commitment_id=current["id"], data=changed,
        reason="Another session corrected the total", request_id="external"))
    click(at, "Record usage settlement")
    assert at.error and "superseded" in at.error[0].value
    assert ledger(brain, field)["budgets"][0]["recorded_use"] == "350"
    other = brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Other field", "production_system": "soil", "medium": "soil", "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert not any(key.startswith("resource_settlement_") for key in session_keys(at))


def test_linked_outcome_selection_preserves_quality_changes_without_releasing_use(tmp_path, monkeypatch):
    from agent.tests.test_resource_outcome_ui import ready
    from agronaut_agent.observation_quality import ObservationReviews

    at, brain, field, plan, measurement = ready(tmp_path)
    click(at, "Save outcome review")
    outcome = at.session_state["resource_outcome_result"]

    class Earlier(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 6, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("agronaut_agent.resource_ledger.datetime", Earlier)
    bindings = {}
    for pool in plan["inputs"]["resources"]:
        budget = brain.farm_resources("web", "operator", field["farm_id"], "save_budget", dict(request_id=pool["id"], data=dict(
            resource_key=pool["id"], kind=pool["kind"], unit=pool["unit"], capacity=pool["available"], source="Synthetic allowance", period=plan["inputs"]["period"])))
        bindings[pool["id"]] = budget["id"]
    prior = brain.farm_resources("web", "operator", field["farm_id"], "commit_plan", dict(plan_id=plan["id"], bindings=bindings,
        source="Explicit operator decision", accept_partial=False, request_id="commit"))
    at.run(timeout=15)
    at.selectbox(key="resource_ledger_commit_choice").set_value(prior["id"]).run(timeout=15)
    prefix = fill(at, prior, "", date(2026, 7, 7))
    root = next(key for key, b in prior["budget_snapshots"].items() if b["data"]["kind"] == "water")
    at.text_input(key=prefix+root+"_amount").set_value("1000")
    at.multiselect(key=prefix+root+"_outcome_ids").set_value([outcome["id"]])
    click(at, "Record usage settlement")
    assert not at.error
    settled = ledger(brain, field)["settlements"][0]
    assert settled["used_amounts"][root] == "1000" and settled["settlement"]["usage"][root]["provenance"] == "linked_outcomes"
    ObservationReviews(brain._farms.db).review(plan["owner_id"], field["id"], measurement["id"], "rejected", "Wrong reading",
        "Synthetic review", request_id="reject", expected_revision=None)
    at.run(timeout=15)
    assert not at.exception and any("needs review" in w.value for w in at.warning)
    prefix = f"resource_settlement_{settled['id']}_"
    at.text_input(key=prefix+"reason").set_value("Attempt to reuse old evidence")
    click(at, "Record usage settlement")
    assert at.error and "evidence changed" in at.error[0].value
    assert ledger(brain, field)["settlements"][0]["used_amounts"][root] == "1000"

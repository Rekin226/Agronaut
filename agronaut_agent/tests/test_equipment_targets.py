"""Budget-to-equipment linkage preserves timing, scope and reviewable evidence."""

import copy
import json
import subprocess
import sys

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.store import _Db
from agronaut_agent.tests.test_crop_coefficient_workflow import stage
from agronaut_agent.tests.test_equipment_consultation import ROOT, advance, answers
from agronaut_agent.tests.test_field_workflow import inputs, water
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_soil_workflow import adoption, measurement


def budget(workflow, task="drip_design", **changes):
    data = inputs()
    data["area_m2"] = 500 if task == "drip_design" else 1728
    data.update(changes)
    return workflow.save("owner", "field", "root_zone_budget", data, request_id="budget", cycle_id="cycle")


def link(wizard, source, task="drip_design", target=None, **changes):
    supplied = dict(answers(task), application_input="budget", target_calculation=source["id"],
                    target_day="2026-07-06", target_reason="Synthetic full-area applicability; uniform bucket is only a sizing scenario.",
                    target_accepted="use")
    supplied.update(changes)
    view = wizard.start("owner", "field")
    while view["next_field"] != target:
        view = wizard.answer("owner", "field", supplied[view["next_field"]], view["revision"])
    return view


@pytest.mark.parametrize("task", ["drip_design", "sprinkler_design"])
def test_adoption_requires_review_and_preserves_exact_target_across_restart_and_save(setup, task):
    workflow, farms = setup
    source = budget(workflow, task)
    original = copy.deepcopy(source)
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source, task, "target_accepted")
    for label in (source["id"], "END-OF-DAY", "2026-07-06", "UTC", "80%", "not a timed irrigation schedule"):
        assert label in view["question"]
    assert "application.gross_depth_mm" not in view["answers"]
    assert "application.net_depth_mm" not in view["answers"]
    with pytest.raises(ValueError, match="Reply use"):
        wizard.answer("owner", "field", "yes", view["revision"])
    assert wizard.get("owner", "field") == view
    wizard.answer("owner", "field", "use", view["revision"])
    db = _Db(workflow.db.path)
    try:
        resumed = FieldConsultation(db)
        ready = link(resumed, source, task)
        target = ready["water_target"]
        assert target["calculation_snapshot"] == source
        assert target["boundary"] == "end_of_local_day"
        assert target["state_as_of"] == "2026-07-06"
        requirement = source["result"]["result"]["days"][0]["requirement"]["values"]
        app = ready["inputs"]["application"]
        if task == "drip_design":
            assert app["gross_depth_mm"] == requirement["gross_irrigation_mm"]
            assert target["net_depth_mm"] == requirement["net_irrigation_mm"]
        else:
            assert app["net_depth_mm"] == requirement["net_irrigation_mm"]
            assert app["efficiency"] == .8  # Overrides no value: original manual 75% question was skipped.
        assert source["id"] in app["source"] and target["reason"] in app["source"]
        assert farms.export("owner")["field_intakes"][0]["data"]["target_calculation"] == source
        saved = resumed.save("owner", "field", ready["draft_id"], ready["revision"])
        assert saved["calculation"]["water_target"] == target
        assert saved["calculation"]["inputs"] == ready["inputs"]
        assert saved["calculation"]["result"] == ready["preview"]
        assert resumed.save("owner", "field", ready["draft_id"], ready["revision"]) == saved
        assert "END-OF-DAY" in render_intake(saved)
        review = execute_field_workflow(db, "owner", "field", "review_target", dict(calculation_id=saved["calculation"]["id"]))
        assert not review["needs_review"]
        assert review["water_target"] == target
        assert workflow.get("owner", "field", source["id"]) == original
        assert workflow.list("owner", "field")["water_days"] == []
    finally:
        db._conn.close()


@pytest.mark.parametrize("change", ["area", "cycle", "unit", "task", "timezone"])
def test_invalid_source_selection_does_not_advance(setup, change):
    workflow, _ = setup
    data = inputs()
    data["area_m2"] = 501 if change == "area" else 500
    unit = "field2" if change == "unit" else "field"
    cycle = None if change == "cycle" else "cycle2" if change == "unit" else "cycle"
    task = "crop_water" if change == "task" else "root_zone_budget"
    if task == "crop_water":
        for key in ("soil", "initial_state", "application_efficiency", "events"):
            data.pop(key)
    source = workflow.save("owner", unit, task, data, request_id="source", cycle_id=cycle)
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source, target="target_calculation")
    if change == "timezone":
        workflow.db.execute("UPDATE farms SET timezone='Europe/Berlin' WHERE id='farm'")
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", source["id"], view["revision"])
    assert wizard.get("owner", "field") == view


@pytest.mark.parametrize("change", ["date", "unknown"])
def test_unknown_or_outside_window_target_is_not_zero(setup, change):
    workflow, _ = setup
    source = budget(workflow, **({"initial_state": None} if change == "unknown" else {}))
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source, target="target_day")
    with pytest.raises(ValueError, match="unknown|choose a date"):
        wizard.answer("owner", "field", "2026-07-07" if change == "date" else "2026-07-06", view["revision"])
    assert wizard.get("owner", "field") == view


def test_below_threshold_zero_is_explicit_not_missing(setup):
    workflow, _ = setup
    source = budget(workflow, initial_state=dict(as_of="2026-07-05", depletion_mm=0, source="Synthetic field capacity"))
    ready = link(FieldConsultation(workflow.db), source)
    assert ready["water_target"]["net_depth_mm"] == 0
    assert ready["inputs"]["application"]["gross_depth_mm"] == 0


def test_backtracking_removes_adopted_amounts_and_allows_manual_target(setup):
    workflow, _ = setup
    source = budget(workflow)
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source, target="target_accepted")
    view = wizard.answer("owner", "field", "use", view["revision"])
    assert "application.gross_depth_mm" in view["answers"]
    view = wizard.answer("owner", "field", "", view["revision"], back=True)
    assert view["next_field"] == "target_accepted"
    assert "application.gross_depth_mm" not in view["answers"]
    while view["next_field"] != "application_input":
        view = wizard.answer("owner", "field", "", view["revision"], back=True)
    view = wizard.answer("owner", "field", "manual", view["revision"])
    assert view["next_field"] == "application.gross_depth_mm"
    assert "target_calculation" not in view["answers"]
    ready = advance(wizard)
    assert "water_target" not in ready
    assert ready["inputs"]["application"]["gross_depth_mm"] == 2


def evidence_source(workflow, farms, kind):
    base = budget(workflow)
    if kind == "water":
        record = workflow.record_day("owner", "field", water(), request_id="water", cycle_id="cycle")
        return workflow.replay("owner", "field", base["id"], [record["id"]], request_id="replay"), record
    if kind == "soil":
        measurement(farms)
        return workflow.apply_soil_state("owner", "field", **adoption(base)), None
    if kind == "stage":
        record = stage(CropRecords(workflow.db))
        return workflow.revise_crop("owner", "field", base["id"], {"coefficient": base["inputs"]["crop"]["coefficient"]},
                                    "Synthetic stage link", request_id="stage", stage_record_ids=[record["id"]]), record
    return base, None


def change_evidence(workflow, kind, record):
    if kind == "water":
        workflow.record_day("owner", "field", water(rainfall_mm=8), request_id="correct-water",
                            cycle_id="cycle", supersedes_id=record["id"])
    elif kind == "soil":
        ObservationReviews(workflow.db).review("owner", "field", "soil", "rejected", "Bad sample", "Synthetic review",
                                               request_id="soil-review", expected_revision=None)
    elif kind == "stage":
        CropRecords(workflow.db).record_stage("owner", "field", "cycle", dict(record["data"], stage="Corrected stage"),
                                             request_id="correct-stage", supersedes_id=record["id"])
    else:
        CropRecords(workflow.db).correct_planting("owner", "field", "cycle", "onion", "2026-07-01", "Fixture",
                                                "Correct crop", request_id="planting", expected_revision=None)


@pytest.mark.parametrize("kind", ["water", "soil", "stage", "planting"])
@pytest.mark.parametrize("when", ["before_adoption", "before_save", "after_save"])
def test_evidence_changes_are_rejected_or_flagged_without_rewriting_plan(setup, kind, when):
    workflow, farms = setup
    source, record = evidence_source(workflow, farms, kind)
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source, target="target_accepted" if when == "before_adoption" else None)
    if when == "after_save":
        saved = wizard.save("owner", "field", view["draft_id"], view["revision"])
    change_evidence(workflow, kind, record)
    if when == "before_adoption":
        with pytest.raises(ValueError, match="review|corrected"):
            wizard.answer("owner", "field", "use", view["revision"])
        assert wizard.get("owner", "field") == view
    elif when == "before_save":
        with pytest.raises(ValueError, match="review|corrected"):
            wizard.save("owner", "field", view["draft_id"], view["revision"])
        assert wizard.get("owner", "field") == view
    else:
        review = workflow.review_target("owner", "field", saved["calculation"]["id"])
        assert review["needs_review"] and review["issues"]
        assert workflow.get("owner", "field", saved["calculation"]["id"]) == saved["calculation"]
        assert wizard.save("owner", "field", view["draft_id"], view["revision"]) == saved
    assert workflow.get("owner", "field", source["id"]) == source


def test_legacy_equipment_drafts_keep_manual_questions(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, target="application_input")
    row = workflow.db.query("SELECT payload FROM field_intake_drafts WHERE unit_id='field'")[0]
    payload = json.loads(row[0])
    payload["intake_version"] = 3
    workflow.db.execute("UPDATE field_intake_drafts SET payload=? WHERE unit_id='field'", (json.dumps(payload),))
    view = wizard.get("owner", "field")
    assert view["next_field"] == "application.gross_depth_mm"
    assert "water_target" not in advance(wizard)


def test_recent_numbered_choice_and_account_export_erasure(setup):
    workflow, farms = setup
    source = budget(workflow)
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source, target="target_calculation")
    assert "1. " + source["id"] in view["question"]
    view = wizard.answer("owner", "field", "1", view["revision"])
    assert view["answers"]["target_calculation"] == source
    ready = link(wizard, source)
    saved = wizard.save("owner", "field", ready["draft_id"], ready["revision"])["calculation"]
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.review_target(owner, unit, saved["id"])
    exported = farms.export("owner")
    assert next(c for c in exported["calculations"] if c["id"] == saved["id"])["water_target"]["calculation_snapshot"] == source
    other = wizard.start("other", "field3")
    farms.purge("owner")
    assert wizard.get("other", "field3") == other
    assert not workflow.db.query("SELECT 1 FROM field_calculations WHERE id=?", (saved["id"],))


@pytest.mark.parametrize("kind", ["historical", "forecast", "scenario"])
def test_selected_weather_classification_stays_visible(setup, kind):
    workflow, _ = setup
    data = inputs()
    data["weather"][0]["kind"] = kind
    if kind == "forecast":
        data["weather"][0]["issued_at"] = "2026-07-05T12:00:00+00:00"
    source = budget(workflow, weather=data["weather"])
    view = link(FieldConsultation(workflow.db), source)
    assert view["water_target"]["weather_kind"] == kind
    assert "selected-day weather: " + kind in render_intake(view)


def test_web_chat_can_adopt_and_save_target_without_model(tmp_path):
    from agronaut_agent.core import AgronautAgent
    from agronaut_agent.tests.test_unit_setup import NoModel
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path / "target-chat.sqlite3", chat_model=NoModel())
    unit = brain.create_unit("web", "operator", dict(farm=dict(name="Home", timezone="UTC"),
        unit=dict(name="Field", production_system="soil", medium="soil", environment="open_field",
                  water_strategy="fully_irrigated", irrigation_method="drip")))
    data = inputs()
    data["area_m2"] = 500
    source = brain.field_workflow("web", "operator", "save", dict(task="root_zone_budget", inputs=data, request_id="budget"))
    owner = brain._conv.get_or_create_user("web", "operator")
    supplied = dict(answers("drip_design"), application_input="budget", target_calculation=source["id"],
                    target_day="2026-07-06", target_reason="Synthetic full-area applicability", target_accepted="use")
    _route_turn(brain, "operator", "/fieldplan", None)
    view = brain._field_intake.get(owner, unit["id"])
    while view["status"] != "ready":
        reply = _route_turn(brain, "operator", supplied[view["next_field"]], None)
        view = brain._field_intake.get(owner, unit["id"])
        assert view["question"] in reply
    assert source["id"] in reply and "END-OF-DAY" in reply
    saved_reply = _route_turn(brain, "operator", "save", None)
    assert "Saved irrigation-equipment calculation" in saved_reply
    assert source["id"] in saved_reply
    records = brain.field_workflow("web", "operator", "list", {})
    saved_id = next(r["id"] for r in records["calculations"] if r["task"] == "drip_design")
    assert brain.field_workflow("web", "operator", "review_target", dict(calculation_id=saved_id))["water_target"]["calculation_snapshot"] == source
    assert records["water_days"] == []


def test_stdlib_cli_adopts_and_reviews_saved_water_target(tmp_path):
    db, path = str(tmp_path / "target-cli.sqlite3"), tmp_path / "request.json"
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=ROOT,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    command("farm", "--db", db, "create", "--input", str(ROOT / "examples/soil_unit.json"))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path))
    data = inputs()
    data["area_m2"] = 500
    data["site"]["timezone"] = "Africa/Accra"
    source = operation("save", dict(task="root_zone_budget", inputs=data, request_id="budget"))
    supplied = dict(answers("drip_design"), application_input="budget", target_calculation=source["id"],
                    target_day="2026-07-06", target_reason="Synthetic full-area applicability", target_accepted="use")
    view = operation("start_intake", {})
    while view["status"] != "ready":
        view = operation("answer_intake", dict(answer=supplied[view["next_field"]], expected_revision=view["revision"]))
    saved = operation("save_intake", dict(draft_id=view["draft_id"], expected_revision=view["revision"]))["calculation"]
    review = operation("review_target", dict(calculation_id=saved["id"]))
    assert not review["needs_review"]
    assert review["water_target"]["calculation_snapshot"] == source
    assert operation("list", {})["water_days"] == []

"""Equipment questions preserve the native contracts and never record actions."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.equipment_consultation import questions
from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.irrigation_io import evaluate_drip_request, evaluate_sprinkler_request
from agronaut_agent.store import _Db
from agronaut_agent.tests.test_field_workflow import setup as setup

ROOT = Path(__file__).resolve().parents[2]


def example(task):
    return json.loads((ROOT / "examples" / f"{task}.json").read_text())


def answers(task):
    payload = example(task)
    result = dict(task=task, cycle_id="1", application_input="manual")
    for key, question in questions(task).items():
        section, name = key.split(".")
        value = payload[section][name]
        result[key] = "yes" if value is True else str(value * 100 if question.percent else value)
    return result


def advance(wizard, task="drip_design", target=None, owner="owner", unit="field", **changes):
    supplied = dict(answers(task), **changes)
    view = wizard.start(owner, unit)
    while view["next_field"] != target:
        view = wizard.answer(owner, unit, supplied[view["next_field"]], view["revision"])
    return view


@pytest.mark.parametrize("task,evaluate", [("drip_design", evaluate_drip_request), ("sprinkler_design", evaluate_sprinkler_request)])
def test_complete_equipment_preview_matches_native_engine_and_saves_only_a_plan(setup, task, evaluate):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, task)
    assert view["status"] == "ready"
    assert view["inputs"] == example(task)
    assert view["preview"]["result"] == evaluate(example(task))
    assert workflow.list("owner", "field")["calculations"] == []
    report = render_intake(view)
    for label in ("EXPERIMENTAL", "Materials", "Sources", "candidate runtime", "Reply save"):
        assert label in report
    saved = wizard.save("owner", "field", view["draft_id"], view["revision"])
    assert saved["calculation"]["inputs"] == example(task)
    assert saved["calculation"]["cycle_id"] == "cycle"
    assert saved["calculation"]["result"] == view["preview"]
    assert wizard.save("owner", "field", view["draft_id"], view["revision"]) == saved
    assert "No irrigation or equipment operation was recorded" in render_intake(saved)
    assert workflow.list("owner", "field")["water_days"] == []
    assert wizard.get("owner", "field") is None


@pytest.mark.parametrize("task,key,value", [
    ("drip_design", "layout.terrain", "sloping"),
    ("drip_design", "layout.row_count", "1.5"),
    ("drip_design", "layout.rows_per_zone", "11"),
    ("drip_design", "emitter.pressure_compensating", "no"),
    ("drip_design", "emitter.nominal_flow_lph", "unknown"),
    ("drip_design", "emitter.max_head_m", "5"),
    ("drip_design", "main.roughness_mm", "2"),
    ("drip_design", "supply.max_static_head_m", "1"),
    ("drip_design", "headworks.max_filter_loss_m", "0.1"),
    ("drip_design", "application.wetted_fraction", "0"),
    ("drip_design", "application.gross_depth_mm", "nan"),
    ("drip_design", "limits.viscosity_m2_s", "inf"),
    ("drip_design", "limits.source", ""),
    ("sprinkler_design", "sprinkler.pattern", "half_circle"),
    ("sprinkler_design", "sprinkler.reference_head_m", "50"),
    ("sprinkler_design", "application.efficiency", "101"),
])
def test_invalid_answers_leave_current_question_unchanged(setup, task, key, value):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, task, key)
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", value, view["revision"])
    assert wizard.get("owner", "field") == view


def test_sprinkler_count_product_rejected_at_count_question(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, "sprinkler_design", "layout.sprinklers_per_lateral", **{"layout.lateral_count": "1000"})
    with pytest.raises(ValueError, match="10,000"):
        wizard.answer("owner", "field", "11", view["revision"])
    assert wizard.get("owner", "field") == view


@pytest.mark.parametrize("task", ["drip_design", "sprinkler_design"])
def test_infeasible_equipment_is_visible_without_an_operating_schedule(setup, task):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, task, **{"supply.daily_available_l": "0"})
    assert view["preview"]["feasible"] is False
    assert "FAIL — no schedule" in render_intake(view)
    assert view["preview"]["binding_constraints"]
    saved = wizard.save("owner", "field", view["draft_id"], view["revision"])
    assert saved["calculation"]["result"]["feasible"] is False
    assert workflow.list("owner", "field")["water_days"] == []


def test_equipment_restart_backtracking_revision_and_method_switch(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = advance(wizard, target="emitter.nominal_flow_lph")
    other = _Db(workflow.db.path)
    try:
        resumed = FieldConsultation(other)
        assert resumed.start("owner", "field") == view
        changed = resumed.answer("owner", "field", "3", view["revision"])
        with pytest.raises(ValueError, match="changed"):
            wizard.answer("owner", "field", "2", view["revision"])
        back = resumed.answer("owner", "field", "", changed["revision"], back=True)
        assert back["next_field"] == "emitter.nominal_flow_lph"
        assert "emitter.nominal_flow_lph" not in back["answers"]
        while back["next_field"] != "task":
            back = resumed.answer("owner", "field", "", back["revision"], back=True)
        switched = resumed.answer("owner", "field", "4", back["revision"])
        assert switched["answers"] == dict(task="sprinkler_design")
        ready = advance(resumed, "sprinkler_design")
        assert ready["inputs"] == example("sprinkler_design")
    finally:
        other._conn.close()


def test_unit_ownership_export_and_erasure_include_equipment_answers(setup):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    a = advance(wizard, target="emitter.source")
    b = advance(wizard, "sprinkler_design", target="sprinkler.source", unit="field2", cycle_id="0")
    with pytest.raises(ValueError):
        wizard.get("other", "field")
    with pytest.raises(ValueError, match="changed"):
        wizard.answer("owner", "field2", "source", a["revision"])
    exported = {d["unit_id"]: d for d in farms.export("owner")["field_intakes"]}
    assert exported["field"]["data"] == a["answers"]
    assert exported["field2"]["data"] == b["answers"]
    farms.purge("owner")
    assert not wizard.active("owner", "field") and not wizard.active("owner", "field2")


@pytest.mark.parametrize("version", [1, 2])
def test_older_water_drafts_keep_original_menu(setup, version):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = wizard.start("owner", "field")
    payload = json.loads(workflow.db.query("SELECT payload FROM field_intake_drafts WHERE unit_id='field'")[0][0])
    payload["intake_version"] = version
    workflow.db.execute("UPDATE field_intake_drafts SET payload=? WHERE unit_id='field'", (json.dumps(payload),))
    view = wizard.get("owner", "field")
    assert "Choose 3" not in view["question"]
    with pytest.raises(ValueError, match="listed"):
        wizard.answer("owner", "field", "3", view["revision"])
    assert wizard.get("owner", "field") == view


def test_stdlib_cli_completes_and_saves_equipment_intake(tmp_path):
    path, db = tmp_path / "request.json", str(tmp_path / "equipment.sqlite3")
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    command("farm", "--db", db, "create", "--input", str(ROOT / "examples/soil_unit.json"))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path))
    view = operation("start_intake", {})
    supplied = answers("drip_design")
    while view["status"] != "ready":
        view = operation("answer_intake", dict(answer=supplied[view["next_field"]], expected_revision=view["revision"]))
    saved = operation("save_intake", dict(draft_id=view["draft_id"], expected_revision=view["revision"]))
    assert saved["calculation"]["inputs"] == example("drip_design")
    assert operation("get_intake", {}) is None


@pytest.mark.parametrize("channel,task", [("web", "drip_design"), ("cli", "sprinkler_design"), ("whatsapp", "drip_design")])
def test_chat_equipment_preview_and_save_without_model(tmp_path, channel, task):
    from agronaut_agent.channels.commands import dispatch
    from agronaut_agent.core import AgronautAgent
    from agronaut_agent.tests.test_unit_setup import NoModel
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path / "equipment-chat.sqlite3", chat_model=NoModel())
    unit = brain.create_unit(channel, "operator", dict(farm=dict(name="Home", timezone="UTC"),
        unit=dict(name="Field", production_system="soil", environment="open_field", medium="soil",
                  water_strategy="rainfed", irrigation_method=None)))
    owner = brain._conv.get_or_create_user(channel, "operator")
    def send(message):
        if channel == "web":
            return _route_turn(brain, "operator", message, None)
        if message.startswith("/"):
            return dispatch(brain, channel, "operator", message).text
        return brain.handle_message(channel, "operator", message)
    assert "Choose 3" in send("/fieldplan")
    supplied = answers(task)
    view = brain._field_intake.get(owner, unit["id"])
    while view["status"] != "ready":
        reply = send(supplied[view["next_field"]])
        view = brain._field_intake.get(owner, unit["id"])
        assert view["question"] in reply
    assert "EXPERIMENTAL" in reply and "candidate runtime" in reply
    assert brain.field_workflow(channel, "operator", "list", {})["calculations"] == []
    assert "Saved irrigation-equipment calculation" in send("save")
    records = brain.field_workflow(channel, "operator", "list", {})
    saved = brain.field_workflow(channel, "operator", "get", dict(calculation_id=records["calculations"][0]["id"]))
    assert saved["inputs"] == example(task)
    assert records["water_days"] == []


def test_equipment_chat_back_cancel_reset_and_unit_switch_preserve_scope(tmp_path):
    from agronaut_agent.channels.commands import dispatch
    from agronaut_agent.tests.test_unit_agent import agent_and_units
    from agronaut_agent.tests.test_unit_setup import NoModel

    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit("cli", "one", "field")
    dispatch(brain, "cli", "one", "/fieldplan")
    brain.handle_message("cli", "one", "3")
    view = brain._field_intake.get(owner, "field")
    assert "text reply" in brain.handle_message("cli", "one", "flat", fact_text="image of field")
    assert brain._field_intake.get(owner, "field") == view
    assert "Choose 1" in dispatch(brain, "cli", "one", "/back").text
    brain.handle_message("cli", "one", "4")
    brain.select_unit("cli", "one", "hydro")
    assert "Select a soil unit" in dispatch(brain, "cli", "one", "/fieldplan").text
    dispatch(brain, "cli", "one", "/cancel")
    assert brain._field_intake.active(owner, "field")
    brain.select_unit("cli", "one", "field")
    assert "Irrigation-equipment" in dispatch(brain, "cli", "one", "/fieldplan").text
    assert "Irrigation-equipment intake cancelled" in dispatch(brain, "cli", "one", "/cancel").text
    dispatch(brain, "cli", "one", "/fieldplan")
    brain.handle_message("cli", "one", "3")
    brain.reset("cli", "one")
    assert not brain._field_intake.active(owner, "field")

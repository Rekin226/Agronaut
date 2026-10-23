"""Resumable field-water intake, strict inputs, ownership and atomic save."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.field_consultation import FieldConsultation, render_intake
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_field_workflow import setup as setup

ANSWERS = dict(task="2", cycle_id="1", crop="tomato", area_m2="1 hectare", start="2026-07-06", days="1",
    stage="mid-season", kc="1.15", kc_source="Synthetic supplied coefficient", weather_input="manual", weather_kind="scenario",
    weather_method="Supplied grass-reference ET for each local day", weather_source="Synthetic ET/rain scenario",
    fc="32", wp="15", root_depth="0.8", p="40", soil_source="Synthetic soil assumptions", depletion="60",
    initial_source="Synthetic depletion before window", efficiency="80", efficiency_source="Synthetic efficiency",
    event_source="Synthetic no-irrigation/no-runoff assumptions", et0_0="4", rain_0="0", irrigation_0="0", runoff_0="0")


def complete(wizard, owner="owner", unit="field", **changes):
    answers = dict(ANSWERS, **changes)
    view = wizard.start(owner, unit)
    while view["status"] == "collecting":
        view = wizard.answer(owner, unit, answers[view["next_field"]], view["revision"])
    return view


def test_complete_preview_then_save_is_atomic_idempotent_and_not_actual_water(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = complete(wizard)
    assert view["inputs"]["area_m2"] == 10000
    assert view["inputs"]["soil"]["field_capacity"] == .32
    assert view["inputs"]["weather"][0]["et0_mm_day"] == 4
    assert workflow.list("owner", "field")["calculations"] == []
    result = wizard.save("owner", "field", view["draft_id"], view["revision"])
    assert result["calculation"]["result"] == view["preview"]
    assert result["calculation"]["cycle_id"] == "cycle"
    assert wizard.save("owner", "field", view["draft_id"], view["revision"]) == result
    assert wizard.get("owner", "field") is None
    assert workflow.list("owner", "field")["water_days"] == []
    assert "No completed irrigation" in render_intake(result)


def test_unknown_initial_state_remains_unknown_and_demand_needs_no_soil(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = complete(wizard, depletion="unknown")
    assert "initial_state" not in view["inputs"]
    assert view["preview"]["result"]["days"][0]["requirement"]["values"]["gross_irrigation_l"] is None
    wizard.cancel("owner", "field", view["revision"])
    view = complete(wizard, task="1", cycle_id="0")
    assert "soil" not in view["inputs"]
    assert view["inputs"]["weather"][0]["rainfall_mm"] is None
    assert view["preview"]["result"]["days"][0]["demand"]["values"]["etc_mm_day"] == pytest.approx(4.6)


def test_drafts_resume_across_connections_and_stale_answers_fail(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    first = wizard.start("owner", "field")
    second = wizard.answer("owner", "field", "2", first["revision"])
    with pytest.raises(ValueError, match="changed"):
        wizard.answer("owner", "field", "1", first["revision"])
    other = _Db(workflow.db.path)
    try:
        resumed = FieldConsultation(other).start("owner", "field")
        assert resumed == second
        back = FieldConsultation(other).answer("owner", "field", "", resumed["revision"], back=True)
        assert back["next_field"] == "task" and back["answers"] == {}
        with pytest.raises(ValueError, match="changed"):
            wizard.cancel("owner", "field", second["revision"])
        assert wizard.start("owner", "field")["revision"] == back["revision"]
    finally:
        other._conn.close()


@pytest.mark.parametrize("field,value", [("task", "automatic"), ("area_m2", "1"), ("area_m2", "nan ha"),
    ("area_m2", "-1 ha"), ("start", "2026-06-01"), ("days", "1.5"), ("days", "0"), ("kc", "nan"),
    ("wp", "40"), ("p", "100"), ("efficiency", "0"), ("depletion", "500"), ("rain_0", "unknown"),
    ("runoff_0", "1"), ("root_depth", "-1"), ("weather_kind", "guessed"), ("kc_source", "")])
def test_invalid_answers_leave_the_draft_at_the_same_question(setup, field, value):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = wizard.start("owner", "field")
    while view["next_field"] != field:
        view = wizard.answer("owner", "field", ANSWERS[view["next_field"]], view["revision"])
    with pytest.raises(ValueError):
        wizard.answer("owner", "field", value, view["revision"])
    assert wizard.get("owner", "field") == view


def test_unit_owner_export_and_erasure_are_isolated(setup):
    workflow, farms = setup
    wizard = FieldConsultation(workflow.db)
    a = wizard.start("owner", "field")
    b = wizard.start("owner", "field2")
    c = wizard.start("other", "field3")
    with pytest.raises(ValueError):
        wizard.get("other", "field")
    with pytest.raises(ValueError):
        wizard.answer("owner", "field2", "1", a["revision"])
    assert wizard.get("owner", "field2") == b
    exported = farms.export("owner")["field_intakes"]
    assert {r["id"] for r in exported} == {a["draft_id"], b["draft_id"]}
    json.dumps(exported, allow_nan=False)
    farms.purge("owner")
    assert not wizard.active("owner", "field")
    assert wizard.get("other", "field3") == c


def test_current_cycle_amendment_cannot_be_saved_with_stale_crop_assumptions(setup):
    from agronaut_agent.crop_records import CropRecords
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = complete(wizard)
    CropRecords(workflow.db).correct_planting("owner", "field", "cycle", "onion", "2026-07-01", "Fixture",
        "Correct crop", request_id="amendment", expected_revision=None)
    with pytest.raises(ValueError, match="crop must match"):
        wizard.save("owner", "field", view["draft_id"], view["revision"])
    assert wizard.get("owner", "field")["revision"] == view["revision"]
    assert workflow.list("owner", "field")["calculations"] == []


def test_schema_v8_upgrade_keeps_data_and_private_restorable_backup(setup):
    workflow, _ = setup
    db = workflow.db
    path = db.path
    db.execute("DROP TABLE field_intake_drafts")
    db.execute("PRAGMA user_version=8")
    upgraded = _Db(path)
    try:
        assert upgraded.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
        assert FieldConsultation(upgraded).start("owner", "field")["next_field"] == "task"
        backup = next(path.parent.glob(f"{path.name}.pre-v{SCHEMA_VERSION}-*.bak"))
        assert backup.stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(backup) as old:
            assert old.execute("PRAGMA user_version").fetchone()[0] == 8
            assert old.execute("SELECT name FROM production_units WHERE id='field'").fetchone()[0] == "Field"
            assert old.execute("SELECT 1 FROM sqlite_master WHERE name='field_intake_drafts'").fetchone() is None
    finally:
        upgraded._conn.close()


def test_failed_migration_rolls_back_without_new_version_or_tables(setup, monkeypatch):
    from agronaut_agent import field_consultation
    workflow, _ = setup
    workflow.db.execute("DROP TABLE field_intake_drafts")
    workflow.db.execute("PRAGMA user_version=8")
    monkeypatch.setattr(field_consultation, "SCHEMA", "CREATE TABLE broken (")
    with pytest.raises(sqlite3.OperationalError):
        _Db(workflow.db.path)
    assert workflow.db.query("PRAGMA user_version")[0][0] == 8
    assert workflow.db.query("SELECT name FROM sqlite_master WHERE name='field_intake_drafts'") == []


def test_stdlib_cli_completes_a_demand_intake(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path, db = tmp_path / "request.json", str(tmp_path / "intake.sqlite3")
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    command("farm", "--db", db, "create", "--input", str(root / "examples/soil_unit.json"))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path))
    view = operation("start_intake", {})
    while view["status"] != "ready":
        value = "1" if view["next_field"] == "task" else ANSWERS[view["next_field"]]
        view = operation("answer_intake", dict(answer=value, expected_revision=view["revision"]))
    result = operation("save_intake", dict(draft_id=view["draft_id"], expected_revision=view["revision"]))
    assert result["calculation"]["task"] == "crop_water"
    assert operation("get_intake", {}) is None


def test_forecast_requires_aware_issuance_and_local_day_length(setup):
    workflow, _ = setup
    workflow.db.execute("UPDATE farms SET timezone='Europe/Berlin' WHERE id='farm'")
    wizard = FieldConsultation(workflow.db)
    view = wizard.start("owner", "field")
    answers = dict(ANSWERS, task="1", cycle_id="0", start="2026-03-29", weather_kind="forecast",
                   issued_at="2026-03-28T12:00:00+00:00")
    while view["next_field"] != "issued_at":
        view = wizard.answer("owner", "field", answers[view["next_field"]], view["revision"])
    with pytest.raises(ValueError, match="timezone"):
        wizard.answer("owner", "field", "2026-03-28T12:00:00", view["revision"])
    while view["status"] != "ready":
        view = wizard.answer("owner", "field", answers[view["next_field"]], view["revision"])
    assert view["inputs"]["weather"][0]["period_hours"] == 23
    assert view["inputs"]["weather"][0]["kind"] == "forecast"


def test_cannot_save_incomplete_or_cancel_after_a_concurrent_edit(setup):
    workflow, _ = setup
    wizard = FieldConsultation(workflow.db)
    view = wizard.start("owner", "field")
    with pytest.raises(ValueError, match="remaining"):
        wizard.save("owner", "field", view["draft_id"], view["revision"])
    wizard.answer("owner", "field", "1", view["revision"])
    with pytest.raises(ValueError, match="changed"):
        wizard.cancel("owner", "field", view["revision"])
    with pytest.raises(ValueError, match="changed"):
        wizard.save("owner", "field", view["draft_id"], view["revision"])

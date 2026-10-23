"""Cross-domain work snapshots respect owners, local dates and current task evidence."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agro_model.farm import ProductionCycle
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_overview import FarmOverview
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.unit_context import UnitContexts

NOW = datetime(2026, 7, 8, 18, tzinfo=timezone.utc)


def by_unit(report):
    return {u["unit"]["id"]: u for f in report["farms"] for u in f["units"]}


def test_owned_multi_farm_snapshot_uses_each_local_day_without_selection_or_writes(setup):
    workflow, farms = setup
    db, records = workflow.db, CropRecords(workflow.db)
    db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm2'")
    db.execute("UPDATE production_units SET production_system='hydroponics' WHERE id='field2'")
    for suffix in ("", "2"):
        records.plan_task("owner", "field"+suffix, "cycle"+suffix,
            task(due=dict(basis="date", start="2026-07-08", end="2026-07-08")), request_id="plan")
    contexts = UnitContexts(db)
    contexts.select("owner", "field2")
    before, selected = farms.export("owner"), contexts.describe_active("owner")
    report = FarmOverview(db).get("owner", now=NOW)
    units = by_unit(report)
    assert report["farm_count"] == 2 and report["unit_count"] == 2
    assert units["field"]["cycles"][0]["attention"][0]["timing"] == "due"
    assert units["field2"]["cycles"][0]["attention"][0]["timing"] == "overdue"
    assert units["field2"]["unit"]["production_system"] == "hydroponics"
    assert {f["local_date"] for f in report["farms"]} == {"2026-07-08", "2026-07-09"}
    assert "field3" not in units and farms.export("owner") == before
    assert contexts.describe_active("owner") == selected
    json.dumps(report, allow_nan=False)
    assert FarmOverview(db).get("nobody")["farms"] == []
    with pytest.raises(ValueError, match="unknown farm"):
        FarmOverview(db).get("other", "farm")
    assert FarmOverview(db).get("owner", "farm", now=NOW)["unit_count"] == 1


def test_task_revisions_and_closed_cycle_work_are_visible(setup):
    workflow, _ = setup
    records, view = CropRecords(workflow.db), FarmOverview(workflow.db)
    planned = records.plan_task("owner", "field", "cycle", task(), request_id="first")
    records.plan_task("owner", "field", "cycle", task(title="Revised task"),
                      request_id="second", supersedes_id=planned["id"])
    records.correct_planting("owner", "field", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                             request_id="plant-correct", expected_revision=None)
    records.close_cycle("owner", "field", "cycle", "2026-07-06", "Ended", "Synthetic",
                         request_id="close", expected_revision=None)
    cycle = by_unit(view.get("owner", now=NOW))["field"]["cycles"][0]
    assert cycle["status"] == "closed" and len(cycle["tasks"]) == 1
    item = cycle["attention"][0]
    assert item["title"] == "Revised task" and item["needs_review"]
    assert item["original_window"]["start"] == "2026-07-06"
    assert item["effective_window"]["start"] == "2026-07-07"
    assert len(item["review_reasons"]) == 2


def test_completed_task_with_corrected_evidence_requires_review(setup):
    from agronaut_agent.tests.test_crop_records import harvest

    workflow, _ = setup
    records = CropRecords(workflow.db)
    planned = records.plan_task("owner", "field", "cycle", task(), request_id="plan")
    actual = records.record_harvest("owner", "field", "cycle", harvest(), request_id="harvest")
    records.resolve_task("owner", "field", planned["id"], "completed", "Done", "Synthetic", request_id="done",
                         completed_on=actual["data"]["day"], completion_record=dict(kind="crop_record", id=actual["id"]))
    view = FarmOverview(workflow.db)
    assert by_unit(view.get("owner", now=NOW))["field"]["cycles"][0]["attention"] == []
    records.void_harvest("owner", "field", actual["id"], "Wrong batch", request_id="void")
    flagged = by_unit(view.get("owner", now=NOW))["field"]["cycles"][0]["attention"][0]
    assert flagged["status"] == "completed" and flagged["needs_review"]
    assert flagged["completion_evidence_state"] == "superseded"


@pytest.mark.parametrize("zone", [None, "Not/A_Zone"])
def test_unknown_timezone_never_becomes_utc_or_all_clear(setup, zone):
    workflow, _ = setup
    CropRecords(workflow.db).plan_task("owner", "field", "cycle", task(), request_id="plan")
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (zone,))
    report = FarmOverview(workflow.db).get("owner", "farm", now=NOW)
    farm = report["farms"][0]
    assert farm["issue"] and farm["local_date"] is None
    cycle = farm["units"][0]["cycles"][0]
    assert cycle["status"] == "date_unknown" and cycle["task_counts"] is None
    assert cycle["unclassified_task_count"] == 1


def test_empty_and_future_cycles_and_irrigation_zones_remain_separate(setup):
    from agronaut_agent.irrigation_zones import IrrigationZones

    workflow, farms = setup
    from agronaut_agent.tests.test_irrigation_zones import zone as zone_data
    zone = IrrigationZones(workflow.db).create("owner", "field", zone_data(), request_id="zone")
    farms.create_cycle("owner", ProductionCycle("future", zone["id"], "tomato", "2099-01-01"))
    report = FarmOverview(workflow.db).get("owner", "farm", now=NOW)
    units = by_unit(report)
    assert report["unit_count"] == 2
    assert units[zone["id"]]["unit"]["parent_unit_id"] == "field"
    assert units[zone["id"]]["cycle_counts"]["future_dated"] == 1
    assert units["field"]["cycle_counts"]["open"] == 1
    assert units["field"]["cycles"][0]["tasks"] == []


def test_stdlib_cli_overview_does_not_select_a_different_unit(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db = str(tmp_path / "overview.sqlite3")
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "farm", "--db", db, *args],
                                 cwd=root, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    created = command("create", "--input", str(root / "examples/soil_unit.json"))
    before = command("list")
    report = command("overview", "--farm", created["created"]["farm_id"])
    assert report["unit_count"] == 1 and report["farms"][0]["units"][0]["cycles"] == []
    assert command("list") == before

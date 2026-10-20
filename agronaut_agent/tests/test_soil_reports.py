"""Lab-result qualifiers, immutable corrections and private scoped soil evidence."""

import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from agro_model.farm import ProductionCycle
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.soil_reports import SoilReports
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, _Db
from agronaut_agent.tests.test_field_workflow import setup as setup


def report(**changes):
    data = dict(entry="spring-sample", sampled_on="2026-06-20", reported_on="2026-06-25", lab_name="Synthetic lab",
        lab_report_id="R-1", sample_id="A-1", location="North field composite", depth_top_cm=0, depth_bottom_cm=15,
        subsample_count=None, sampling_source="Synthetic sampling notebook", report_source="Synthetic lab report page 1",
        results=[dict(id="p-bray", analyte="Phosphorus", value="12.00", qualifier="eq", unit="ppm P", method="Bray-1"),
                 dict(id="p-olsen", analyte="Phosphorus", value="0.010", qualifier="lt", unit="mg/kg P", method="Olsen"),
                 dict(id="missing", analyte="Nitrogen", value=None, qualifier="not_reported", unit=None, method=None),
                 dict(id="nd", analyte="Example trace analyte", value=None, qualifier="not_detected", unit="mg/kg", method=None)])
    data.update(changes)
    return data


def test_preserves_methods_precision_detection_limits_and_unknowns_without_derived_values(setup):
    workflow, farms = setup
    service = SoilReports(workflow.db)
    data = report()
    before = farms.export("owner")
    saved = service.save("owner", "field", data, cycle_id="cycle", request_id="soil")
    assert saved["data"] == data and saved["evidence_status"] == "operator_transcribed"
    assert service.save("owner", "field", data, cycle_id="cycle", request_id="soil") == saved
    assert service.get("owner", "field", saved["id"]) == saved
    after = farms.export("owner")
    assert after["soil_tests"] == [saved]
    assert {k:v for k,v in after.items() if k != "soil_tests"} == {k:v for k,v in before.items() if k != "soil_tests"}
    section = service.list("owner", "field", "cycle")
    assert section["current"][0]["data"]["results"][1]["value"] == "0.010"
    assert "totals" not in section and "recommendations" not in section


def test_corrections_void_restore_stale_writes_and_old_retries_preserve_history(setup):
    workflow, _ = setup
    service = SoilReports(workflow.db)
    first = service.save("owner", "field", report(), request_id="first")
    second = service.save("owner", "field", report(location="Corrected zone"), request_id="second", supersedes_id=first["id"], reason="Location typo")
    with pytest.raises(ValueError, match="latest"):
        service.void("owner", "field", first["id"], "stale", request_id="bad")
    void = service.void("owner", "field", second["id"], "Wrong source document", request_id="void")
    assert service.list("owner", "field")["current"] == []
    assert service.void("owner", "field", second["id"], "Wrong source document", request_id="void") == void
    restored = service.save("owner", "field", report(), request_id="restore", supersedes_id=void["id"], reason="Confirmed original document")
    assert service.save("owner", "field", report(), request_id="first") == first
    rows = service.list("owner", "field")["history"]
    assert [r["superseded"] for r in rows] == [True, True, True, False]
    assert rows[-1]["id"] == restored["id"] and rows[2]["voided"]
    for payload in (dict(request_id="duplicate"), dict(request_id="missing-reason", supersedes_id=restored["id"]),
                    dict(request_id="restore", supersedes_id=void["id"], reason="Changed operation")):
        with pytest.raises(ValueError):
            service.save("owner", "field", report(), **payload)


@pytest.mark.parametrize("kind", ["future", "order", "one_depth", "depth_order", "depth_nan", "count_bool", "empty_rows", "duplicate_row", "float_value",
    "nonfinite", "huge_exponent", "embedded_qualifier", "nd_value", "unknown_qualifier", "blank_method", "missing_source", "extra_field"])
def test_invalid_transcriptions_are_atomic(setup, kind):
    workflow, farms = setup
    data = report()
    if kind == "future":
        data["reported_on"] = "9999-01-01"
    elif kind == "order":
        data["reported_on"] = "2026-06-19"
    elif kind == "one_depth":
        data["depth_top_cm"] = None
    elif kind == "depth_order":
        data["depth_top_cm"] = 20
    elif kind == "depth_nan":
        data["depth_top_cm"] = float("nan")
    elif kind == "count_bool":
        data["subsample_count"] = True
    elif kind == "empty_rows":
        data["results"] = []
    elif kind == "duplicate_row":
        data["results"][1]["id"] = "p-bray"
    elif kind in ("float_value", "nonfinite", "huge_exponent", "embedded_qualifier"):
        data["results"][0]["value"] = {"float_value": 12.0, "nonfinite": "NaN", "huge_exponent": "1e99999", "embedded_qualifier": "<12"}[kind]
    elif kind == "nd_value":
        data["results"][3]["value"] = "0"
    elif kind == "unknown_qualifier":
        data["results"][0]["qualifier"] = "probably"
    elif kind == "blank_method":
        data["results"][0]["method"] = ""
    elif kind == "missing_source":
        data["report_source"] = ""
    else:
        data["fertilizer_rate"] = 100
    before = farms.export("owner")
    with pytest.raises(ValueError):
        SoilReports(workflow.db).save("owner", "field", data, request_id="bad")
    assert farms.export("owner") == before


def test_cycle_assignment_is_exact_and_history_is_not_summed_across_seasons(setup):
    workflow, farms = setup
    service = SoilReports(workflow.db)
    farms.create_cycle("owner", ProductionCycle("next", "field", "tomato", "2026-07-01"))
    first = service.save("owner", "field", report(), cycle_id="cycle", request_id="first")
    service.save("owner", "field", report(entry="unassigned"), request_id="unit-only")
    service.save("owner", "field2", report(), cycle_id="cycle2", request_id="other-unit")
    with pytest.raises(ValueError):
        service.save("owner", "field", report(), cycle_id="next", request_id="move", supersedes_id=first["id"], reason="Move")
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            service.get(owner, unit, first["id"])
    with pytest.raises(ValueError):
        service.list("owner", "field", "cycle2")
    records = CropRecords(workflow.db)
    records.close_cycle("owner", "field", "cycle", "2026-07-10", "Ended", "Synthetic", request_id="close", expected_revision=None)
    service.save("owner", "field", report(entry="later-report", sampled_on="2026-07-11", reported_on="2026-07-12"), cycle_id="cycle", request_id="later")
    a, b = records.compare_seasons("owner", "field", ["cycle", "next"])["seasons"]
    assert len(a["soil_tests"]["current"]) == 2 and b["soil_tests"]["current"] == []
    assert len(service.list("owner", "field")["current"]) == 1
    other = farms.export("other")
    farms.purge("owner")
    assert workflow.db.query("SELECT * FROM soil_test_reports") == []
    assert farms.export("other") == other


def test_v10_migration_backup_and_rollback_preserve_existing_data(setup, monkeypatch):
    from agronaut_agent import soil_reports
    from agronaut_agent.farm_store import FarmStore

    workflow, farms = setup
    before = farms.export("owner")
    path = workflow.db.path
    workflow.db.execute("DROP TABLE soil_test_reports")
    workflow.db.execute("PRAGMA user_version=10")
    workflow.db._conn.close()
    real = soil_reports.migrate_soil_reports

    def fail(conn):
        real(conn)
        raise RuntimeError("Synthetic migration failure")

    monkeypatch.setattr(soil_reports, "migrate_soil_reports", fail)
    with pytest.raises(RuntimeError):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 10
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='soil_test_reports'").fetchone() is None
    raw.close()
    backup = next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert backup.stat().st_mode & 0o777 == 0o600
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 10
    old.close()
    monkeypatch.setattr(soil_reports, "migrate_soil_reports", real)
    reopened = _Db(path)
    assert FarmStore(reopened).export("owner") == before
    reopened._conn.close()


def test_stdlib_cli_and_dispatch_discover_save_and_list(setup, tmp_path):
    workflow, _ = setup
    owner = ConversationStore(workflow.db).get_or_create_user("cli", "local")
    workflow.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    schema = execute_field_workflow(workflow.db, owner, "field", "describe_soil_tests", {})
    assert "record_soil_test" in schema["operations"]
    path = tmp_path/"soil.json"
    path.write_text(json.dumps(dict(data=report(), request_id="cli")))
    cmd = [sys.executable, "-S", "-m", "agronaut_agent.cli", "field", "--db", str(workflow.db.path), "--unit", "field"]
    result = subprocess.run([*cmd, "record_soil_test", "--input", str(path)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    saved = json.loads(result.stdout)
    path.write_text("{}")
    result = subprocess.run([*cmd, "list_soil_tests", "--input", str(path)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout)["current"][0]["id"] == saved["id"]


def test_unknown_depths_and_exact_zero_signed_or_scientific_values_are_retained(setup):
    workflow, _ = setup
    data = report(depth_top_cm=None, depth_bottom_cm=None)
    values = ["0.000", "-1.25", "1.00e+2"]
    data["results"] = [dict(id=str(i), analyte="As reported", value=v, qualifier="eq", unit=None, method=None) for i,v in enumerate(values)]
    data["results"].append(dict(id="texture", analyte="Texture class", value="Sandy loam", qualifier="text", unit=None, method="As described by laboratory"))
    original = deepcopy(data)
    saved = SoilReports(workflow.db).save("owner", "field", data, request_id="literal")
    assert saved["data"] == original

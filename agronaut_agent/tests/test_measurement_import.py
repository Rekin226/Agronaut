"""Batch field evidence: previews, atomicity, ownership, retries and offline CSV CLI."""

import copy
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.crop_records import CropRecords, execute_crop_records
from agronaut_agent.measurement_import import COLUMNS, MAX_ROWS, MeasurementImports, csv_rows
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_measurement_entry import reading


def rows():
    return [dict(request_id="notebook-a:1", cycle_id="cycle", data=reading()),
            dict(request_id="notebook-a:2", cycle_id=None,
                 data=reading(quantity="rainfall", value=4.5, unit="mm", source="Synthetic 雨量, station A"))]


def csv_text(data):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
    writer.writeheader()
    for row in data:
        writer.writerow(dict(request_id=row["request_id"], cycle_id=row.get("cycle_id"), **row["data"]))
    return stream.getvalue()


def test_preview_writes_nothing_save_retries_preserve_reviews_and_data_rights(setup):
    workflow, farms = setup
    imports = MeasurementImports(workflow.db)
    before = farms.export("owner")
    preview = imports.preview("owner", "field", rows())
    assert preview["status"] == "preview" and preview["new_count"] == 2
    assert farms.export("owner") == before
    # Preview must perform no INSERT/UPDATE/DELETE, even followed by rollback.
    statements = []
    workflow.db._conn.set_trace_callback(statements.append)
    assert imports.preview("owner", "field", rows())["preview_token"] == preview["preview_token"]
    workflow.db._conn.set_trace_callback(None)
    assert not any(s.lstrip().split()[0].upper() in ("INSERT", "UPDATE", "DELETE") for s in statements)
    saved = imports.save("owner", "field", rows(), preview["preview_token"])
    identifiers = [e["measurement"]["id"] for e in saved["entries"]]
    records = CropRecords(workflow.db)
    review = records.review_measurement("owner", "field", identifiers[0], "rejected", "Bad sample", "Fixture",
                                       request_id="reject", expected_revision=None)
    retried = imports.save("owner", "field", rows(), preview["preview_token"])
    assert retried["new_count"] == 0 and retried["existing_count"] == 2
    assert [e["measurement"]["id"] for e in retried["entries"]] == identifiers
    assert retried["entries"][0]["measurement"]["quality_review"] == review
    assert retried["entries"][0]["measurement"]["effective_quality"] == "rejected"
    assert retried["entries"][0]["measurement"]["recorded_at"] == saved["entries"][0]["measurement"]["recorded_at"]
    assert len(farms.export("owner")["observations"]) == 2
    assert workflow.list("owner", "field")["water_days"] == []
    assert records.get("owner", "field", "cycle")["harvest_totals"] == []
    farms.purge("owner")
    assert all(not values for values in farms.export("owner").values())
    assert farms.export("other")["units"]


@pytest.mark.parametrize("change", ["unit", "future", "cycle", "missing", "boolean", "quality", "overflow"])
def test_invalid_late_row_leaves_no_partial_import(setup, change):
    workflow, farms = setup
    imports = MeasurementImports(workflow.db)
    data = rows()
    if change == "unit":
        data[1]["data"]["unit"] = "L"
    elif change == "future":
        data[1]["data"]["observed_at"] = "2099-01-01T00:00:00+00:00"
    elif change == "cycle":
        data[1]["cycle_id"] = "cycle2"
    elif change == "missing":
        data[1].pop("data")
    elif change == "boolean":
        data[1]["data"]["value"] = True
    elif change == "overflow":
        data[1]["data"]["value"] = 10**1000
    else:
        data[1]["data"]["quality"] = "checked"
    before = farms.export("owner")
    with pytest.raises(ValueError, match="row 2"):
        imports.preview("owner", "field", data)
    assert farms.export("owner") == before


def test_save_rechecks_current_conflicts_and_rolls_back_prior_new_row(setup):
    workflow, farms = setup
    imports = MeasurementImports(workflow.db)
    data = rows()
    preview = imports.preview("owner", "field", data)
    conflicting = copy.deepcopy(data[1])
    conflicting["data"]["value"] = 8
    CropRecords(workflow.db).record_measurement("owner", "field", **conflicting)
    before = farms.export("owner")
    with pytest.raises(ValueError, match="row 2.*different data"):
        imports.save("owner", "field", data, preview["preview_token"])
    assert farms.export("owner") == before


def test_preview_does_not_allow_later_closed_cycle_event(setup):
    workflow, farms = setup
    imports = MeasurementImports(workflow.db)
    data = rows()
    data[1]["cycle_id"] = "cycle"
    data[1]["data"]["observed_at"] = "2026-07-10T12:00:00+00:00"
    preview = imports.preview("owner", "field", data)
    CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-08", "Finished", "Synthetic",
                                         request_id="close", expected_revision=None)
    before = farms.export("owner")
    with pytest.raises(ValueError, match="row 2"):
        imports.save("owner", "field", data, preview["preview_token"])
    assert farms.export("owner") == before


def test_payload_and_unit_bound_preview_and_ownership(setup):
    workflow, farms = setup
    imports = MeasurementImports(workflow.db)
    data = rows()
    token = imports.preview("owner", "field", data)["preview_token"]
    changed = copy.deepcopy(data)
    changed[1]["data"]["source"] = "Different notebook"
    before = farms.export("owner")
    with pytest.raises(ValueError, match="differs from the preview"):
        imports.save("owner", "field", changed, token)
    with pytest.raises(ValueError, match="differs from the preview"):
        imports.save("owner", "field2", data, token)
    for method in (imports.preview, lambda owner, unit, data: imports.save(owner, unit, data, token)):
        with pytest.raises(ValueError, match="unknown production unit"):
            method("other", "field", data)
    assert farms.export("owner") == before


@pytest.mark.parametrize("data", [[], {}, [rows()[0], rows()[0]], [rows()[0]] * (MAX_ROWS + 1)])
def test_batch_shape_and_duplicate_identity(setup, data):
    workflow, _ = setup
    with pytest.raises(ValueError):
        MeasurementImports(workflow.db).preview("owner", "field", data)
    assert CropRecords(workflow.db).list_measurements("owner", "field")["measurements"] == []


def test_csv_explicit_mapping_utf8_bom_multiline_and_offsets(setup):
    workflow, _ = setup
    data = rows()
    data[0]["data"]["source"] = "Synthetic notebook\nInstrument and depth supplied"
    data[1]["data"]["observed_at"] = "2026-07-06T23:00:00+08:00"
    parsed = csv_rows("\ufeff" + csv_text(data))
    assert parsed == data
    preview = execute_crop_records(workflow.db, "owner", "field", "preview_measurements", dict(rows=parsed))
    saved = execute_crop_records(workflow.db, "owner", "field", "import_measurements",
                                 dict(rows=parsed, preview_token=preview["preview_token"]))
    assert saved["entries"][1]["measurement"]["observed_at"].endswith("+08:00")


@pytest.mark.parametrize("value", ["", "unknown", "NaN", "Infinity", "1,5", "5 mm", "1_000", "1e999", "1e-999", "1e-999999999999999999999"])
def test_csv_missing_ambiguous_or_nonfinite_values_are_not_zero(value):
    data = rows()
    data[1]["data"]["value"] = value
    with pytest.raises(ValueError, match="CSV record 2"):
        csv_rows(csv_text(data))


@pytest.mark.parametrize("content", ["", ",".join(COLUMNS) + "\n", ",".join(COLUMNS[:-1]),
    ",".join((*COLUMNS[:-1], "source")), ",".join(COLUMNS) + "\nshort,row\n",
    ",".join(COLUMNS) + '\n"unclosed', "a" * 2_000_001])
def test_csv_malformed_headers_records_and_limits(content):
    with pytest.raises(ValueError):
        csv_rows(content)


@pytest.mark.parametrize("system", ["soil", "hydroponics", "aquaponics"])
def test_supported_systems_preserve_forecast_provenance(setup, system):
    workflow, _ = setup
    workflow.db.execute("UPDATE production_units SET production_system=? WHERE id='field'", (system,))
    data = rows()
    data[1]["data"].update(provenance="forecast", observed_at="2099-01-01T00:00:00+08:00")
    imports = MeasurementImports(workflow.db)
    preview = imports.preview("owner", "field", data)
    result = imports.save("owner", "field", data, preview["preview_token"])
    assert result["entries"][1]["measurement"]["provenance"] == "forecast"
    assert result["entries"][1]["measurement"]["quality"] == "unverified"


def test_maximum_sized_batch_and_csv_overflow(setup):
    workflow, _ = setup
    data = [dict(rows()[1], request_id=f"sensor:{index}") for index in range(MAX_ROWS)]
    imports = MeasurementImports(workflow.db)
    parsed = csv_rows(csv_text(data))
    preview = imports.preview("owner", "field", parsed)
    assert imports.save("owner", "field", parsed, preview["preview_token"])["new_count"] == MAX_ROWS
    with pytest.raises(ValueError, match="at most 1000"):
        csv_rows(csv_text(data + [data[0]]))


def test_stdlib_cli_previews_and_imports_csv_then_lists_and_retries(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db = str(tmp_path / "batch.sqlite3")
    csv_path = tmp_path / "records.csv"
    data = rows()
    data[0]["cycle_id"] = None
    csv_path.write_text(csv_text(data), encoding="utf-8")

    def command(*args, code=0):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == code, result.stdout + result.stderr
        return json.loads(result.stdout)

    command("farm", "--db", db, "create", "--input", str(root / "examples/soil_unit.json"))
    preview = command("crop", "--db", db, "preview_measurements", "--csv", str(csv_path))
    assert command("crop", "--db", db, "list_measurements")["measurements"] == []
    command("crop", "--db", db, "import_measurements", "--csv", str(csv_path), code=2)
    result = command("crop", "--db", db, "import_measurements", "--csv", str(csv_path),
                     "--preview-token", preview["preview_token"])
    assert result["new_count"] == 2
    again = command("crop", "--db", db, "import_measurements", "--csv", str(csv_path),
                    "--preview-token", preview["preview_token"])
    assert again["existing_count"] == 2
    assert len(command("crop", "--db", db, "list_measurements")["measurements"]) == 2
    command("crop", "--db", db, "list_measurements", "--csv", str(csv_path), code=2)

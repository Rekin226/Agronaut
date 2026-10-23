"""Plan-to-observation comparisons preserve basis, revision history and ownership."""

import json
import sqlite3
import subprocess
import sys
from decimal import localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import FarmResources, execute_farm_resources
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, _Db
from agronaut_agent.tests.test_equipment_consultation import example
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_resource_designs import resource_plan, specification


def measurement(workflow, quantity="irrigation_volume", value=1000, *, key="water", unit="field", cycle="cycle", owner="owner", **changes):
    data = dict(quantity=quantity, value=value, unit="h" if quantity == "runtime" else "mm" if quantity == "irrigation_depth" else "L",
                observed_at="2026-07-07T12:00:00+00:00", provenance="measured", source="Synthetic complete event total including maintenance")
    data.update(changes)
    return CropRecords(workflow.db).record_measurement(owner, unit, data, cycle_id=cycle, request_id=key)


def scenario(workflow):
    source = workflow.save("owner", "field", "drip_design", example("drip_design"), cycle_id="cycle", request_id="equipment")
    service = FarmResources(workflow.db)
    spec = specification(source)
    plan = service.save("owner", "farm", resource_plan(service.design_job("owner", "farm", spec), spec), request_id="plan")
    water = measurement(workflow)
    pump = measurement(workflow, "runtime", 2, key="pump")
    data = dict(actual_day="2026-07-07", water=dict(observation_id=water["id"], expected_review_id=None),
                pump=dict(observation_id=pump["id"], expected_review_id=None),
                attribution="Both readings cover this complete event including maintenance, on the mapped tank and pump",
                source="Synthetic operator attribution")
    return service, plan, data


def test_evaluate_is_read_only_and_save_retains_exact_plan_and_measurement_basis(setup):
    workflow, farms = setup
    service, plan, data = scenario(workflow)
    before = farms.export("owner")
    result = service.evaluate_outcome("owner", "farm", plan["id"], "irrigate", data)
    assert farms.export("owner") == before
    assert result["plan_snapshot"]["design_evidence"] == plan["snapshot"]["irrigation_designs"][0]
    assert result["comparisons"][0]["relation"] == "below_allowance"
    assert result["comparisons"][1]["relation"] == "above_allowance"
    for metric, value in zip(result["comparisons"], (1000, 2)):
        planned = plan["result"]["allocations"][0]["assigned"][metric["resource_id"]]
        assert Fraction(metric["observed_minus_allowance"]["exact"]) == Fraction(value)-Fraction(planned)
        assert metric["quality"] == "unverified" and metric["provenance"] == "measured"
    saved = service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="first")
    assert service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="first") == saved
    after = farms.export("owner")
    assert after["resource_outcomes"] == [saved]
    assert {k: v for k, v in after.items() if k != "resource_outcomes"} == {k: v for k, v in before.items() if k != "resource_outcomes"}
    listed = service.list_outcomes("owner", "farm", plan["id"])["reviews"]
    assert not listed[0]["superseded"] and not listed[0]["review"]["needs_review"]


def test_missing_metric_is_unknown_and_zero_is_an_explicit_observation(setup):
    workflow, _ = setup
    service, plan, data = scenario(workflow)
    zero = measurement(workflow, value=0, key="zero")
    data.update(water=dict(observation_id=zero["id"], expected_review_id=None), pump=None)
    result = service.evaluate_outcome("owner", "farm", plan["id"], "irrigate", data)
    assert result["comparisons"][0]["observed_total"]["exact"] == "0/1"
    assert result["comparisons"][1]["observed_total"] is None
    assert result["comparisons"][1]["observed_minus_allowance"] is None
    assert result["comparisons"][1]["relation"] == "unknown"
    with pytest.raises(ValueError, match="at least one"):
        service.evaluate_outcome("owner", "farm", plan["id"], "irrigate", dict(data, water=None))


@pytest.mark.parametrize("kind", ["depth", "forecast", "wrong_day", "unassigned", "other_unit", "other_owner", "wrong_review", "future_day", "missing_attribution", "unknown_field"])
def test_incompatible_or_unattributed_measurements_are_rejected(setup, kind):
    workflow, farms = setup
    service, plan, data = scenario(workflow)
    if kind == "depth":
        record = measurement(workflow, "irrigation_depth", key="other")
    elif kind == "forecast":
        record = measurement(workflow, key="other", provenance="forecast")
    elif kind == "unassigned":
        record = measurement(workflow, key="other", cycle=None)
    elif kind == "other_unit":
        record = measurement(workflow, key="other", unit="field2", cycle="cycle2")
    elif kind == "other_owner":
        record = measurement(workflow, key="other", unit="field3", cycle="cycle3", owner="other")
    else:
        record = None
    if record:
        data["water"] = dict(observation_id=record["id"], expected_review_id=None)
    if kind == "wrong_day":
        data["actual_day"] = "2026-07-08"
    elif kind == "future_day":
        data["actual_day"] = "9999-01-01"
    elif kind == "wrong_review":
        data["water"]["expected_review_id"] = "not-current"
    elif kind == "missing_attribution":
        data["attribution"] = ""
    elif kind == "unknown_field":
        data["completed"] = True
    before = farms.export("owner")
    with pytest.raises(ValueError):
        service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="bad")
    assert farms.export("owner") == before


def test_quality_cas_revision_correction_and_old_retry_preserve_history(setup):
    workflow, farms = setup
    service, plan, data = scenario(workflow)
    first = service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="first")
    quality = ObservationReviews(workflow.db).review("owner", "field", data["water"]["observation_id"], "rejected",
        "Wrong attribution", "Synthetic review", request_id="reject", expected_revision=None)
    rows = service.list_outcomes("owner", "farm", plan["id"])["reviews"]
    assert rows[0]["review"]["needs_review"]
    assert rows[0]["comparisons"] == first["comparisons"]
    assert rows[0]["observations"]["water"]["effective_quality"] == "unverified"
    assert service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="first") == first
    with pytest.raises(ValueError, match="measurement review changed"):
        service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="stale", supersedes_id=first["id"])
    data["water"]["expected_review_id"] = quality["id"]
    with pytest.raises(ValueError, match="non-rejected"):
        service.evaluate_outcome("owner", "farm", plan["id"], "irrigate", data)
    replacement = measurement(workflow, value=900, key="replacement")
    data.update(water=dict(observation_id=replacement["id"], expected_review_id=None), source="Corrected attribution to replacement event total")
    second = service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="second", supersedes_id=first["id"])
    with pytest.raises(ValueError, match="outcome review changed"):
        service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="branch", supersedes_id=first["id"])
    with pytest.raises(ValueError, match="different outcome data"):
        service.save_outcome("owner", "farm", plan["id"], "irrigate", dict(data, source="Changed"), request_id="second", supersedes_id=first["id"])
    listed = service.list_outcomes("owner", "farm", plan["id"])["reviews"]
    assert [r["superseded"] for r in listed] == [True, False]
    assert not listed[-1]["review"]["needs_review"]
    assert farms.export("owner")["resource_outcomes"] == [first, second]


def test_scope_and_unallocated_jobs_are_rejected_and_erasure_cascades(setup):
    workflow, farms = setup
    service, plan, data = scenario(workflow)
    for owner, farm in (("other", "farm"), ("owner", "farm2")):
        for operation, payload in (("outcome_inputs", dict(plan_id=plan["id"], job_id="irrigate")),
                                   ("evaluate_outcome", dict(plan_id=plan["id"], job_id="irrigate", data=data)),
                                   ("list_outcomes", dict(plan_id=plan["id"]))):
            with pytest.raises(ValueError):
                execute_farm_resources(workflow.db, owner, farm, operation, payload)
    inputs = json.loads(json.dumps(plan["inputs"]))
    inputs["resources"][0]["available"] = "0"
    unallocated = service.save("owner", "farm", inputs, request_id="unallocated")
    with pytest.raises(ValueError, match="allocated"):
        service.evaluate_outcome("owner", "farm", unallocated["id"], "irrigate", data)
    service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="first")
    other_before = farms.export("other")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other") == other_before
    assert workflow.db.query("SELECT * FROM resource_outcome_reviews") == []


def test_day_shift_uses_saved_timezone_and_large_values_keep_exact_differences(setup):
    workflow, _ = setup
    service, plan, data = scenario(workflow)
    late = measurement(workflow, value=1e100, key="late", observed_at="2026-07-07T23:30:00-02:00")
    data.update(actual_day="2026-07-08", water=dict(observation_id=late["id"], expected_review_id=None), pump=None)
    with localcontext() as ctx:
        ctx.prec = 2
        result = service.evaluate_outcome("owner", "farm", plan["id"], "irrigate", data)
    assert result["actual_minus_planned_days"] == 1 and result["timezone"] == "UTC"
    row = result["comparisons"][0]
    assert Fraction(row["observed_minus_allowance"]["exact"]) == Fraction("1e100")-Fraction(row["planned_allowance"]["exact"])
    assert Fraction(row["observed_minus_allowance"]["value"]) == Fraction(row["observed_minus_allowance"]["exact"])


def test_plan_assumption_changes_are_flags_not_rewrites_of_comparison(setup):
    workflow, _ = setup
    service, plan, data = scenario(workflow)
    first = service.save_outcome("owner", "farm", plan["id"], "irrigate", data, request_id="first")
    CropRecords(workflow.db).correct_planting("owner", "field", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction", request_id="amend", expected_revision=None)
    current = service.list_outcomes("owner", "farm", plan["id"])["reviews"][0]
    assert current["review"]["plan"]["needs_review"]
    assert current["plan_snapshot"] == first["plan_snapshot"]
    assert current["comparisons"] == first["comparisons"]


def test_v9_upgrade_backup_and_failed_migration_rollback(setup, monkeypatch):
    from agronaut_agent import resource_outcomes
    workflow, farms = setup
    service, plan, _ = scenario(workflow)
    path = workflow.db.path
    before = farms.export("owner")
    workflow.db.execute("DROP TABLE resource_outcome_reviews")
    workflow.db.execute("PRAGMA user_version=9")
    workflow.db._conn.close()
    real = resource_outcomes.migrate_resource_outcomes

    def fail(conn):
        real(conn)
        raise RuntimeError("Synthetic failed migration")

    monkeypatch.setattr(resource_outcomes, "migrate_resource_outcomes", fail)
    with pytest.raises(RuntimeError, match="failed migration"):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 9
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='resource_outcome_reviews'").fetchone() is None
    assert raw.execute("SELECT count(*) FROM farm_resource_plans").fetchone()[0] == 1
    raw.close()
    backup = next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert backup.stat().st_mode & 0o777 == 0o600
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 9
    old.close()
    monkeypatch.setattr(resource_outcomes, "migrate_resource_outcomes", real)
    reopened = _Db(path)
    from agronaut_agent.farm_store import FarmStore
    assert FarmStore(reopened).export("owner") == before
    assert reopened.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    reopened._conn.close()


def test_stdlib_cli_evaluate_save_and_list_outcomes(setup, tmp_path):
    workflow, _ = setup
    service, plan, data = scenario(workflow)
    owner = ConversationStore(workflow.db).get_or_create_user("cli", "local")
    workflow.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    # Ownership is determined by the current farm/unit; source snapshots retain history.
    path = tmp_path/"outcome.json"
    payload = dict(plan_id=plan["id"], job_id="irrigate", data=data)

    def call(operation, request):
        path.write_text(json.dumps(request))
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(workflow.db.path),
            "--farm", "farm", operation, "--input", str(path)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)

    assert call("evaluate_outcome", payload)["comparisons"][0]["relation"] == "below_allowance"
    saved = call("save_outcome", dict(payload, request_id="cli-review"))
    assert call("list_outcomes", dict(plan_id=plan["id"]))["reviews"][0]["id"] == saved["id"]
